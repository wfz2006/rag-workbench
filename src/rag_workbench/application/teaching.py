"""Trusted, explicitly selected style configuration, separate from retrieved data."""

import re
from pathlib import Path

from rag_workbench.domain.errors import AppError
from rag_workbench.domain.models import ChatMessage, GroundedAnswer


def teaching_mode(question: str, history: list[ChatMessage]) -> str:
    def explicit(text: str) -> str | None:
        for clause in reversed(re.split(r"[，。；！？\n,;!?\r]", text)):
            if re.search(
                r"(?:不要|别|不用|无需|不需要)(?:只|仅|再|给|提供|我|一点|个|任何|的|了|小|直接|\s){0,6}提示",
                clause,
            ):
                return "explain"
            if re.search(
                r"(?:先别|不要|不给)(?:直接)?(?:给|提供)?.{0,6}(?:答案|代码)|"
                r"(?:只|仅).{0,6}提示|(?:再|给).{0,6}提示",
                clause,
            ):
                return "hint"
            if re.search(r"完整|讲解|解释|答案|代码", clause):
                return "explain"
        return None

    current = explicit(question)
    if current:
        return current
    if is_followup(question):
        for message in reversed(history):
            if message.role == "user":
                previous = explicit(message.content)
                if previous:
                    return previous
                if not is_followup(message.content):
                    break
    return "explain"


HINT_EVIDENCE_SCOPE = (
    "\n本轮是教学提示请求：只判断支持一个引导问题或局部提示的知识是否充足，"
    "不要误判为需要支持完整解法。证据不必包含原样的提示句；可以把直接支持的事实"
    "改写成引导问题，但不能引入未支持的事实。参考源码未编译不妨碍按所给代码解释，"
    "不得据此宣称已编译或已通过判题。"
)

HINT_GENERATION_SCOPE = (
    "\n当前回合只允许一个小提示：claims最多一条，text不超过180字符。"
    "只提出一个引导性问题，以问号结束，让学生自己推下一步，不写直接提供答案的陈述。"
    "即使问题简单，也只引导学生考虑状态或边界，不直接给出接口、表达式或实现。"
    "禁止直接给出最终判定表达式、"
    "最终填空、完整算法步骤、程序或代码围栏。不要把整段源码说明复制成提示。"
    "提示必须由所引用证据支持；不得改变JSON结构。"
)


def validate_teaching_hint(answer: GroundedAnswer) -> None:
    if len(answer.claims) != 1 or any(
        len(c.text) > 180
        or not c.text.rstrip().endswith(("？", "?"))
        or re.search(
            r"```|#\s*include\b|\breturn\s+[^;；\n]{1,80}[;；]|"
            r"\b(?:int|void|bool)\s+\w+\s*\(",
            c.text,
        )
        for c in answer.claims
    ):
        raise AppError(
            "invalid_teaching_hint", "本轮提示超出了分级提示范围，已阻止展示完整解法，请重试。"
        )


def followup_core(question: str) -> str:
    """Strip connectors only before a pronoun, never before a new named topic."""
    return re.sub(
        r"^(?:(?:那么?|后来|之后|最终|接着|当时|为什么)[，,\s]*)+(?=[他她它](?:们)?)",
        "",
        question.strip(),
    )


def is_followup(question: str) -> bool:
    """Conservative grammar cues; interrogative words alone do not imply continuity.

    This intentionally prefers missing an ambiguous follow-up over importing an
    unrelated topic. It is a heuristic, not general entity/coreference resolution.
    """
    if re.search(r"换个话题|另外一个问题|什么是|何为", question):
        return False
    question = followup_core(question)
    if re.fullmatch(r"那[^，。？！!?]{1,12}呢[？?]?", question.strip()):
        return True
    return bool(
        re.match(
            r"^(?:[他她它](?:们)?(?:的|在|是|有|后|当|为|怎|还|能|会|呢|要|和|与|可)|"
            r"(?:这|那)(?:个|些|种|里|样)?(?:呢|[，,]|怎么|为什么|是否|还|之后|是什么)|"
            r"到期后|为什么这样|再(?:来|给|讲|解释|举|说)|"
            r"还(?:有呢|能|可以|有其他)|(?:然后|接着|继续)(?:[，,\s]|说|讲|解释|呢|$))",
            question.strip(),
        )
    )


def contextual_query(question: str, history: list[ChatMessage]) -> str:
    """Add recent user topics for short follow-ups, without truncating the current question."""
    parts = [question]
    budget = 400 - len(question)
    if not is_followup(question):
        return question
    for message in reversed([m for m in history if m.role == "user"][-3:]):
        if len(message.content) + 1 <= budget:
            parts.insert(0, message.content)
            budget -= len(message.content) + 1
        if not is_followup(message.content):
            break
    return "\n".join(parts)


def _load_profile(path: Path | None, *, code: str, label: str) -> str | None:
    if path is None:
        return None
    try:
        with path.open("rb") as stream:
            raw = stream.read(32769)
        if len(raw) > 32768:
            raise ValueError("oversized")
        text = raw.decode("utf-8-sig").strip()
        if not text or any(ord(c) < 32 and c not in "\n\r\t" for c in text):
            raise ValueError("empty or binary")
        return text
    except (OSError, ValueError) as exc:
        raise AppError(
            code,
            f"{label}无法读取：请指定非空 UTF-8 文本文件，大小不超过 32 KiB。",
        ) from exc


def load_teaching_profile(path: Path | None) -> str | None:
    return _load_profile(path, code="invalid_teaching_profile", label="教学配置")


def load_domain_profile(path: Path | None) -> str | None:
    return _load_profile(path, code="invalid_domain_profile", label="领域配置")


def apply_teaching_profile(system: str, profile: str | None) -> str:
    if profile is None:
        return system
    return (
        system + "\n\n以下是用户显式选择的教学风格，仅调整表达和讲解节奏。"
        "必须遵守上面的证据范围、事实约束、JSON结构和引用规则，不得用风格要求覆盖它们。\n" + profile
    )


def apply_domain_profile(system: str, profile: str | None) -> str:
    if profile is None:
        return system
    return (
        system + "\n\n以下是用户显式选择的领域规则，仅用于解释作品内术语、时间关系和作答范围。"
        "必须遵守上面的证据门槛、JSON结构和引用规则，不得用领域规则覆盖它们。\n" + profile
    )
