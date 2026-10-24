import json
import logging
import re
from collections.abc import Callable

from pydantic import ValidationError

from rag_workbench.application.knowledge import KnowledgeService
from rag_workbench.application.prompts import (
    EVIDENCE_SYSTEM,
    GENERAL_PREFIX,
    GENERAL_SYSTEM,
    GROUNDED_SYSTEM,
)
from rag_workbench.application.query_features import contains_quote, contains_title, question_type
from rag_workbench.application.teaching import (
    HINT_EVIDENCE_SCOPE,
    HINT_GENERATION_SCOPE,
    apply_domain_profile,
    apply_teaching_profile,
    contextual_query,
    teaching_mode,
    validate_teaching_hint,
)
from rag_workbench.domain.errors import AppError
from rag_workbench.domain.models import (
    AnswerResult,
    ChatMessage,
    EvidenceDecision,
    GenerationEvent,
    GenerationPhase,
    GroundedAnswer,
    Inspection,
    RetrievalResult,
)
from rag_workbench.domain.ports import Cancellation, ChatProvider

logger = logging.getLogger(__name__)


def bounded_history(history: list[ChatMessage] | None) -> list[ChatMessage]:
    turns: list[list[ChatMessage]] = []
    for message in history or []:
        if message.role == "user":
            turns.append([message])
        elif turns and len(turns[-1]) == 1:
            turns[-1].append(message)
    result: list[ChatMessage] = []
    budget = 16000
    for turn in reversed(turns[-6:]):
        cost = sum(len(message.content) for message in turn)
        if cost > budget:
            break
        budget -= cost
        result[0:0] = turn
    return result


def evidence_payload(question: str, sources: list[RetrievalResult]) -> str:
    qtype = question_type(question)
    evidence = [
        {
            "chunk_id": r.chunk.id,
            "document": r.chunk.document_name,
            "page": r.chunk.page,
            "original_page": r.original_page,
            "retrieval_role": "context" if r.context_for else "primary",
            "contains_title": contains_title(r.chunk.text),
            "contains_quote": contains_quote(r.chunk.text),
            "text": r.chunk.text,
        }
        for r in sources
    ]
    if qtype == "title":
        evidence.sort(key=lambda item: not item["contains_title"])
    elif qtype == "quote":
        evidence.sort(key=lambda item: not item["contains_quote"])
    return json.dumps(
        {
            "question": question,
            "question_type": qtype,
            "evidence": evidence,
        },
        ensure_ascii=False,
    )


def limit_grounded_answer(answer: GroundedAnswer, question: str, settings) -> GroundedAnswer:
    if question_type(question) not in {"general", "meaning"}:
        return answer
    claims = list(answer.claims)
    if settings.max_answer_claims is not None:
        claims = claims[: settings.max_answer_claims]
    if settings.max_answer_chars is not None:
        total = sum(len(claim.text) for claim in claims)
        while len(claims) > 1 and total > settings.max_answer_chars:
            total -= len(claims.pop().text)
    if len(claims) == len(answer.claims):
        return answer
    return answer.model_copy(update={"claims": claims})


def normalize_grounded_answer(answer: GroundedAnswer, support: set[str]) -> GroundedAnswer:
    """Validate source authorization and remove inline duplicates before style checks."""
    claims = []
    for claim in answer.claims:
        ids = list(
            dict.fromkeys(
                identity.lower() if re.fullmatch(r"[0-9a-fA-F]{64}", identity) else identity
                for identity in claim.chunk_ids
            )
        )
        inline = {identity.lower() for identity in re.findall(r"\[([0-9a-fA-F]{64})\]", claim.text)}
        text = re.sub(r"\[[0-9a-fA-F]{64}\]", "", claim.text).strip()
        if not text or not set(ids).issubset(support) or not inline.issubset(ids):
            raise AppError("invalid_citation", "回答包含无效引用，已阻止展示，请重试。")
        claims.append(claim.model_copy(update={"text": text, "chunk_ids": ids}))
    return answer.model_copy(update={"claims": claims})


class RagPipeline:
    def __init__(
        self,
        knowledge: KnowledgeService,
        provider: ChatProvider,
        *,
        teaching_profile: str | None = None,
        domain_profile: str | None = None,
    ):
        self.knowledge, self.provider = knowledge, provider
        self.teaching_profile = teaching_profile
        self.domain_profile = domain_profile

    @staticmethod
    def _phase(on_event: Callable[[GenerationEvent], None] | None, phase: GenerationPhase) -> None:
        if on_event:
            on_event(GenerationEvent(kind="phase", phase=phase))

    def _generate(
        self,
        system: str,
        user: str,
        *,
        history: list[ChatMessage] | None,
        json_mode: bool = False,
        on_event: Callable[[GenerationEvent], None] | None = None,
        cancellation: Cancellation | None = None,
        visible: bool = False,
        repair_instruction: str = "",
    ) -> str:
        task_question = json.loads(user)["question"] if json_mode else user
        hint = self.teaching_profile and teaching_mode(task_question, history or []) == "hint"
        base_system = system
        if base_system in {EVIDENCE_SYSTEM, GENERAL_SYSTEM, GROUNDED_SYSTEM}:
            system = apply_domain_profile(system, self.domain_profile)
        if base_system in {GENERAL_SYSTEM, GROUNDED_SYSTEM}:
            grounded = base_system == GROUNDED_SYSTEM
            system = apply_teaching_profile(system, self.teaching_profile)
            if hint:
                system += (
                    HINT_GENERATION_SCOPE
                    if grounded
                    else ("\n本轮只给一个简短引导问题或提示，不给最终答案、完整步骤或代码。")
                )
        elif base_system == EVIDENCE_SYSTEM and hint:
            system += HINT_EVIDENCE_SCOPE
        system += repair_instruction
        if cancellation:
            cancellation.raise_if_cancelled()
        if on_event is None:
            return self.provider.complete(
                system, user, json_mode=json_mode, history=bounded_history(history)
            )
        pieces = []
        stream = self.provider.stream(
            system,
            user,
            json_mode=json_mode,
            history=bounded_history(history),
            cancellation=cancellation,
        )
        try:
            for part in stream:
                if cancellation:
                    cancellation.raise_if_cancelled()
                pieces.append(part)
                if visible:
                    on_event(GenerationEvent(kind="delta", text=part))
        finally:
            close = getattr(stream, "close", None)
            if close:
                close()
        if cancellation:
            cancellation.raise_if_cancelled()
        text = "".join(pieces)
        if not text.strip():
            raise AppError("empty_response", "模型未返回有效内容，请重试。")
        return text

    def inspect(
        self,
        question: str,
        history: list[ChatMessage] | None = None,
        *,
        on_event: Callable[[GenerationEvent], None] | None = None,
        cancellation: Cancellation | None = None,
    ) -> Inspection:
        inspection = Inspection(question=question)
        try:
            if not question.strip() or len(question) > 2000:
                raise AppError("invalid_question", "请输入 1–2000 个字符的问题。")
            if cancellation:
                cancellation.raise_if_cancelled()
            self._phase(on_event, "retrieving")
            retrieval_question = question.strip()
            retrieval_question = contextual_query(retrieval_question, bounded_history(history))
            inspection.candidates = (
                self.knowledge.retrieve(retrieval_question, coverage_question=question.strip())
                if retrieval_question != question.strip()
                else self.knowledge.retrieve(retrieval_question)
            )
            if cancellation:
                cancellation.raise_if_cancelled()
            relevant = [r for r in inspection.candidates if r.eligible]
            if not relevant:
                inspection.reason = "知识库为空或没有片段通过相关性阈值。"
            else:
                self._phase(on_event, "evidence")
                decision = None
                available = {r.chunk.id for r in relevant}
                retry_instruction = ""
                for attempt in range(2):
                    text = self._generate(
                        EVIDENCE_SYSTEM,
                        evidence_payload(question, relevant),
                        json_mode=True,
                        history=history,
                        on_event=on_event,
                        cancellation=cancellation,
                        repair_instruction=retry_instruction,
                    )
                    try:
                        decision = EvidenceDecision.model_validate_json(text)
                        decision.supporting_chunk_ids = list(
                            dict.fromkeys(
                                identity.lower()
                                if re.fullmatch(r"[0-9a-fA-F]{64}", identity)
                                else identity
                                for identity in decision.supporting_chunk_ids
                            )
                        )
                        support = set(decision.supporting_chunk_ids)
                        if not support.issubset(available) or decision.answerable != bool(support):
                            raise AppError(
                                "invalid_evidence", "证据判断引用无效，未批准知识库回答。"
                            )
                        break
                    except (ValidationError, AppError) as exc:
                        if attempt == 0:
                            retry_instruction = (
                                "\n上次证据判断未通过校验，请纠正 JSON 格式或逻辑："
                                "supporting_chunk_ids 只能使用本轮 evidence 中的 ID；"
                                "answerable=true 时必须有支持片段，false 时必须为空。"
                            )
                            continue
                        raise AppError(
                            "invalid_evidence", "证据判断格式无效，未批准知识库回答，请重试。"
                        ) from exc
                if decision is None:
                    raise AppError(
                        "invalid_evidence", "证据判断格式无效，未批准知识库回答，请重试。"
                    )
                inspection.evidence = decision
                inspection.reason = decision.reason
                if decision.answerable:
                    inspection.route = "knowledge_base"
                    return inspection
            inspection.route = (
                "general" if self.knowledge.settings.allow_general else "insufficient_evidence"
            )
        except AppError as exc:
            inspection.route, inspection.reason, inspection.error_code = "error", str(exc), exc.code
        except Exception as exc:
            # Log only exception type: paths, documents, prompts and keys must not leak.
            logger.error("RAG inspection failed: %s", type(exc).__name__)
            inspection.route, inspection.error_code = "error", "internal_error"
            inspection.reason = "检索服务发生异常，请检查本地数据库、磁盘和配置后重试。"
        return inspection

    def answer(
        self,
        question: str,
        history: list[ChatMessage] | None = None,
        *,
        on_event: Callable[[GenerationEvent], None] | None = None,
        cancellation: Cancellation | None = None,
    ) -> AnswerResult:
        inspection = self.inspect(question, history, on_event=on_event, cancellation=cancellation)
        try:
            if cancellation:
                cancellation.raise_if_cancelled()
            if inspection.route == "error":
                return AnswerResult(
                    answer=inspection.reason,
                    mode="error",
                    debug=inspection,
                    error_code=inspection.error_code,
                )
            if inspection.route == "insufficient_evidence":
                return AnswerResult(
                    answer="当前资料不足以回答此问题，通用模型兜底已关闭。请补充文档或调整问题。",
                    mode="insufficient_evidence",
                    debug=inspection,
                )
            if inspection.route == "general":
                self._phase(on_event, "generating_general")
                prefix = GENERAL_PREFIX
                if on_event:
                    on_event(GenerationEvent(kind="delta", text=prefix))
                response = self._generate(
                    GENERAL_SYSTEM,
                    question,
                    history=history,
                    on_event=on_event,
                    cancellation=cancellation,
                    visible=True,
                )
                if not response.strip():
                    raise AppError("empty_response", "模型未返回有效内容，请重试。")
                return AnswerResult(
                    answer=prefix + response,
                    mode="general",
                    debug=inspection,
                )
            assert inspection.evidence is not None
            support = set(inspection.evidence.supporting_chunk_ids)
            sources = [r for r in inspection.candidates if r.chunk.id in support]
            hint = self.teaching_profile and teaching_mode(question, history or []) == "hint"
            for attempt in range(2 if hint else 1):
                self._phase(on_event, "generating_grounded")
                text = self._generate(
                    GROUNDED_SYSTEM,
                    evidence_payload(question, sources),
                    json_mode=True,
                    history=history,
                    on_event=on_event,
                    cancellation=cancellation,
                    repair_instruction=(
                        "\n请纠正上次提示：只给一个不超过180字符、以问号结尾的引导问题；"
                        "不直接给答案、完整代码或步骤，保留合法 claims JSON 与引用。"
                        if attempt
                        else ""
                    ),
                )
                self._phase(on_event, "validating")
                try:
                    answer = GroundedAnswer.model_validate_json(text)
                    if not answer.claims:
                        inspection.reason = "生成阶段复核后认为现有证据不足。"
                        inspection.evidence = EvidenceDecision(
                            answerable=False,
                            reason=inspection.reason + " 初次判断：" + inspection.evidence.reason,
                            supporting_chunk_ids=[],
                        )
                        inspection.route = (
                            "general"
                            if self.knowledge.settings.allow_general
                            else "insufficient_evidence"
                        )
                        return self._fallback(question, history, inspection, on_event, cancellation)
                    answer = normalize_grounded_answer(answer, support)
                    if hint:
                        validate_teaching_hint(answer)
                    answer = limit_grounded_answer(answer, question, self.knowledge.settings)
                    break
                except ValidationError as exc:
                    if hint and attempt == 0:
                        continue
                    raise AppError(
                        "invalid_answer",
                        "回答未通过结构验证或模型撤回了证据支持，请重试或补充资料。",
                    ) from exc
                except AppError as exc:
                    if hint and attempt == 0 and exc.code == "invalid_teaching_hint":
                        continue
                    raise
            cited: dict[str, None] = {}
            paragraphs = []
            for claim in answer.claims:
                cited.update(dict.fromkeys(claim.chunk_ids))
                references = " ".join(
                    f"[{identity}]" for identity in dict.fromkeys(claim.chunk_ids)
                )
                paragraphs.append(f"{claim.text}\n\n{references}")
            return AnswerResult(
                answer="\n\n".join(paragraphs),
                mode="knowledge_base",
                sources=[next(r for r in sources if r.chunk.id == identity) for identity in cited],
                debug=inspection,
            )
        except AppError as exc:
            inspection.route, inspection.error_code, inspection.reason = "error", exc.code, str(exc)
            return AnswerResult(
                answer=str(exc), mode="error", debug=inspection, error_code=exc.code
            )
        except Exception as exc:
            logger.error("RAG generation failed: %s", type(exc).__name__)
            inspection.route, inspection.error_code = "error", "internal_error"
            return AnswerResult(
                answer="回答服务发生异常，请稍后重试。",
                mode="error",
                debug=inspection,
                error_code="internal_error",
            )

    def _fallback(self, question, history, inspection, on_event, cancellation) -> AnswerResult:
        if inspection.route == "insufficient_evidence":
            return AnswerResult(
                answer="当前资料不足以回答此问题，通用模型兜底已关闭。",
                mode="insufficient_evidence",
                debug=inspection,
            )
        self._phase(on_event, "generating_general")
        prefix = GENERAL_PREFIX
        if on_event:
            on_event(GenerationEvent(kind="delta", text=prefix))
        response = self._generate(
            GENERAL_SYSTEM,
            question,
            history=history,
            on_event=on_event,
            cancellation=cancellation,
            visible=True,
        )
        if not response.strip():
            raise AppError("empty_response", "模型未返回有效内容，请重试。")
        return AnswerResult(answer=prefix + response, mode="general", debug=inspection)
