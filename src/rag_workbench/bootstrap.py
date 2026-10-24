import re
from dataclasses import dataclass

from pydantic import ValidationError

from rag_workbench.application.generation import GenerationJob
from rag_workbench.application.knowledge import KnowledgeService
from rag_workbench.application.pipeline import RagPipeline, bounded_history
from rag_workbench.application.teaching import load_domain_profile, load_teaching_profile
from rag_workbench.config import EDITABLE_SETTINGS, Settings
from rag_workbench.domain.errors import AppError
from rag_workbench.domain.models import AnswerResult, ChatMessage
from rag_workbench.domain.ports import ChatProvider, Embedder
from rag_workbench.infrastructure.embedding import BgeEmbedder
from rag_workbench.infrastructure.llm import OpenAIChatProvider
from rag_workbench.infrastructure.repository import Repository


@dataclass
class Services:
    settings: Settings
    knowledge: KnowledgeService
    pipeline: RagPipeline
    repo: Repository

    def ask(self, session_id: str, question: str) -> AnswerResult:
        result = self.pipeline.answer(question, self.history(session_id))
        self.repo.save_turn(session_id, question, result)
        return result

    def history(self, session_id: str, *, before_id: int | None = None) -> list[ChatMessage]:
        history: list[ChatMessage] = []
        messages = self.repo.messages(session_id)
        if before_id is not None:
            messages = [message for message in messages if message["id"] < before_id]
        for user, assistant in zip(messages[::2], messages[1::2], strict=False):
            if user["role"] != "user" or assistant["role"] != "assistant":
                continue
            result = (
                AnswerResult.model_validate_json(assistant["result"])
                if assistant["result"]
                else None
            )
            if result is None or result.mode not in {"knowledge_base", "general"}:
                continue
            content = re.sub(r"\[[0-9a-f]{64}\]", "〔历史来源〕", assistant["content"])
            if len(user["content"]) > 30000 or len(content) > 30000:
                continue
            history.extend(
                [
                    ChatMessage(role="user", content=user["content"]),
                    ChatMessage(role="assistant", content=content),
                ]
            )
        return bounded_history(history)

    def start_answer(
        self, session_id: str, question: str, *, retry_assistant_id: int | None = None
    ) -> GenerationJob:
        before_id = (
            self.repo.validate_retry(session_id, retry_assistant_id)
            if retry_assistant_id is not None
            else None
        )
        history = self.history(session_id, before_id=before_id)
        return GenerationJob(
            question,
            lambda publish, token: self.pipeline.answer(
                question, history, on_event=publish, cancellation=token
            ),
            lambda result: (
                self.repo.replace_failed_turn(session_id, retry_assistant_id, question, result)
                if retry_assistant_id is not None
                else self.repo.save_turn(session_id, question, result)
            ),
            replaces_assistant_id=retry_assistant_id,
        ).start()


def load_settings(base: Settings | None = None) -> Settings:
    base = base or Settings()
    repository = Repository(base.data_dir.resolve() / "metadata.sqlite3")
    saved = repository.get_state("ui_settings") or {}
    return _effective_settings(base, saved, recover=True)


def environment_owned_keys(settings: Settings | None = None) -> set[str]:
    return set((settings or Settings())._environment_keys)


def _effective_settings(base: Settings, saved: dict, *, recover: bool = False) -> Settings:
    locked = environment_owned_keys(base)
    values = {
        key: value for key, value in saved.items() if key in EDITABLE_SETTINGS and key not in locked
    }

    def validate(selected: dict) -> Settings:
        result = Settings(_env_file=base._deployment_env_file, **{**base.model_dump(), **selected})
        result._environment_keys = set(base._environment_keys)
        return result

    try:
        return validate(values)
    except ValidationError:
        if not recover:
            raise
        # Recover the coupled chunk pair first; never rewrite the user's saved values.
        for key in ("chunk_size", "chunk_overlap"):
            values.pop(key, None)
        try:
            result = validate(values)
        except ValidationError:
            result = validate({})
        result._configuration_warnings = [
            "保存的页面设置与当前部署配置不兼容；本次使用兼容的默认值。"
            "原保存值未被覆盖，请进入系统设置检查后重新保存。"
        ]
        return result


def build_services(
    settings: Settings | None = None,
    *,
    embedder: Embedder | None = None,
    provider: ChatProvider | None = None,
) -> Services:
    settings = settings or load_settings()
    teaching_profile = load_teaching_profile(settings.teaching_profile_path)
    domain_profile = load_domain_profile(settings.domain_profile_path)
    knowledge = KnowledgeService(settings, embedder or BgeEmbedder(settings))
    return Services(
        settings,
        knowledge,
        RagPipeline(
            knowledge,
            provider or OpenAIChatProvider(settings),
            teaching_profile=teaching_profile,
            domain_profile=domain_profile,
        ),
        knowledge.repo,
    )


def save_ui_settings(services: Services, values: dict) -> Settings:
    locked = environment_owned_keys(services.settings)
    safe = {
        key: value
        for key, value in values.items()
        if key in EDITABLE_SETTINGS and key not in locked
    }
    old = services.repo.get_state("ui_settings") or {}
    # Recover pre-existing environment collisions first; validate new edits strictly.
    baseline = _effective_settings(services.settings, old, recover=True)
    validated = _effective_settings(baseline, safe)
    stored = {**old, **{k: getattr(validated, k) for k in safe}}
    if {"chunk_size", "chunk_overlap"} & safe.keys():
        size = stored.get("chunk_size", Settings.model_fields["chunk_size"].default)
        overlap = stored.get("chunk_overlap", Settings.model_fields["chunk_overlap"].default)
        if overlap >= size:
            raise AppError(
                "settings_conflict",
                "未保存：分块参数在移除环境配置后会冲突。请先调整环境配置，"
                "再一起保存 Chunk Size 与 Overlap；原保存值未被覆盖。",
            )
    services.repo.set_state("ui_settings", stored)
    return validated


def reset_ui_settings(services: Services) -> Settings:
    deployment = {
        key: value
        for key, value in services.settings.model_dump().items()
        if key not in EDITABLE_SETTINGS
    }
    defaults = Settings(_env_file=services.settings._deployment_env_file, **deployment)
    services.repo.set_state("ui_settings", {})
    return defaults
