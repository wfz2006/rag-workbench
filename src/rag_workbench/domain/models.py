from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Section(Contract):
    text: str
    page: int | None = None


class Document(Contract):
    id: str
    name: str
    extension: str
    size: int
    status: Literal["pending", "ready", "failed"] = "pending"
    error: str | None = None
    error_code: str | None = None
    created_at: str = Field(default_factory=utc_now)
    source_group: str | None = Field(default=None, max_length=256)
    source_page: int | None = Field(default=None, ge=1)


class Chunk(Contract):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    document_id: str
    document_name: str
    text: str
    ordinal: int
    page: int | None = None
    start: int
    end: int


class ImportFailure(Contract):
    """A rejected upload is a batch outcome, not a persisted document or raw file."""

    name: str
    status: Literal["failed"] = "failed"
    error: str
    error_code: str


class RetrievalResult(Contract):
    chunk: Chunk
    score: float
    passed_threshold: bool
    context_for: str | None = None
    lexical_score: float = 0.0
    lexical_passed: bool = False
    original_page: int | None = None

    @property
    def eligible(self) -> bool:
        return self.passed_threshold or self.lexical_passed or self.context_for is not None


class EvidenceDecision(Contract):
    answerable: bool = Field(strict=True)
    reason: str = Field(min_length=1, max_length=2000)
    supporting_chunk_ids: list[str] = Field(max_length=20)


class Claim(Contract):
    text: str = Field(min_length=1, max_length=10000)
    chunk_ids: list[str] = Field(min_length=1, max_length=20)


class GroundedAnswer(Contract):
    claims: list[Claim] = Field(max_length=30)


AnswerMode = Literal["knowledge_base", "general", "error", "insufficient_evidence"]


class Inspection(Contract):
    question: str
    candidates: list[RetrievalResult] = Field(default_factory=list)
    evidence: EvidenceDecision | None = None
    route: AnswerMode = "general"
    reason: str = ""
    error_code: str | None = None


class AnswerResult(Contract):
    answer: str
    mode: AnswerMode
    sources: list[RetrievalResult] = Field(default_factory=list)
    debug: Inspection
    error_code: str | None = None
    persistence_error: str | None = None


class ChatMessage(Contract):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=30000)


GenerationPhase = Literal[
    "retrieving", "evidence", "generating_general", "generating_grounded", "validating"
]


class GenerationEvent(Contract):
    kind: Literal["phase", "delta"]
    phase: GenerationPhase | None = None
    text: str = ""
