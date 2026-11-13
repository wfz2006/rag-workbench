"""Versioned literature facts, kept separate from the conversation/RAG schema."""

from typing import Annotated, Literal, Self, get_args

from pydantic import Field, field_validator, model_validator

from rag_workbench.domain.models import Contract, utc_now

SourceId = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
EntityId = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")]
Domain = Literal["ofet", "opv", "general"]
StructuredField = Literal[
    "anneal_temperature", "anneal_duration", "deposition_method", "electrode", "dielectric",
    "channel_length", "channel_width", "measurement_environment", "measurement_temperature",
    "film_thickness",
]
FieldAvailability = Literal["not_reported", "not_applicable", "not_extracted"]
ObservationOrigin = Literal["measured", "calculated", "digitized", "cited"]
STRUCTURED_FIELDS = get_args(StructuredField)
FIELD_AVAILABILITIES = get_args(FieldAvailability)
OBSERVATION_ORIGINS = get_args(ObservationOrigin)
EvidenceField = Literal[
    "material_name", "sample_label", "property_name", "value_raw", "unit_raw",
    "material_structure", "device_structure", "process", "conditions",
    "measurement_method", "carrier", "regime", "statistic", StructuredField, "observation_origin",
]


class LiteratureDocument(Contract):
    id: SourceId
    name: str = Field(min_length=1, max_length=240)
    extension: str = Field(pattern=r"^\.[a-z0-9]{1,10}$")
    size: int = Field(gt=0, le=1_000_000_000)
    role: Literal["main", "supplement"] = "main"
    parent_id: SourceId | None = None
    parser: str = Field(min_length=1, max_length=160)
    created_at: str = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def parent_relationship(self) -> Self:
        if (self.role == "supplement") != (self.parent_id is not None):
            raise ValueError("补充材料必须指定主文献，主文献不能指定父文献。")
        if self.parent_id == self.id:
            raise ValueError("文献不能作为自己的补充材料。")
        return self


class EvidenceBlock(Contract):
    id: EntityId
    document_id: SourceId
    text: str = Field(min_length=1, max_length=2_000_000)
    page: int | None = Field(default=None, ge=1)
    kind: Literal["text", "table"] = "text"
    bbox: tuple[float, float, float, float] | None = None

    @field_validator("bbox")
    @classmethod
    def normalized_box(cls, value):
        if value is not None:
            x0, y0, x1, y1 = value
            if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
                raise ValueError("证据区域必须是归一化的有效矩形。")
        return value


class EvidenceReference(Contract):
    block_id: EntityId
    quote: str = Field(min_length=1, max_length=10000)
    field: EvidenceField


class MaterialRecord(Contract):
    id: EntityId
    document_id: SourceId
    domain: Domain = "general"
    material_name: str = Field(min_length=1, max_length=500)
    sample_label: str | None = Field(default=None, max_length=500)
    property_name: str = Field(min_length=1, max_length=500)
    value_raw: str = Field(min_length=1, max_length=1000)
    unit_raw: str | None = Field(default=None, max_length=200)
    value_normalized: float | None = Field(default=None, allow_inf_nan=False)
    unit_normalized: str | None = Field(default=None, max_length=200)
    material_structure: str | None = Field(default=None, max_length=20000)
    device_structure: str | None = Field(default=None, max_length=10000)
    process: str | None = Field(default=None, max_length=10000)
    conditions: str | None = Field(default=None, max_length=10000)
    measurement_method: str | None = Field(default=None, max_length=5000)
    carrier: str | None = Field(default=None, max_length=500)
    regime: str | None = Field(default=None, max_length=500)
    statistic: str | None = Field(default=None, max_length=1000)
    evidence: list[EvidenceReference] = Field(default_factory=list, max_length=100)
    status: Literal["candidate", "approved", "rejected"] = "candidate"
    revision: int = Field(default=1, ge=1)
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)
    anneal_temperature: str | None = Field(default=None, max_length=1000)
    anneal_duration: str | None = Field(default=None, max_length=1000)
    deposition_method: str | None = Field(default=None, max_length=1000)
    electrode: str | None = Field(default=None, max_length=1000)
    dielectric: str | None = Field(default=None, max_length=1000)
    channel_length: str | None = Field(default=None, max_length=1000)
    channel_width: str | None = Field(default=None, max_length=1000)
    measurement_environment: str | None = Field(default=None, max_length=1000)
    measurement_temperature: str | None = Field(default=None, max_length=1000)
    film_thickness: str | None = Field(default=None, max_length=1000)
    field_availability: dict[StructuredField, FieldAvailability] = Field(default_factory=dict)
    observation_origin: ObservationOrigin | None = None

    @field_validator("material_name", "property_name", "value_raw")
    @classmethod
    def meaningful_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("必填字段不能仅包含空白。")
        return value

    @field_validator(*STRUCTURED_FIELDS)
    @classmethod
    def optional_excerpt(cls, value: str | None) -> str | None:
        return (value.strip() or None) if value is not None else None

    @model_validator(mode="after")
    def availability_matches_excerpt(self) -> Self:
        if any(getattr(self, field) is not None for field in self.field_availability):
            raise ValueError("有原文值的字段不能同时标记为未报告、不适用或尚未提取。")
        return self


class ExtractionRun(Contract):
    id: EntityId
    document_id: SourceId
    domain: Domain = "general"
    model: str = Field(min_length=1, max_length=160)
    status: Literal["running", "completed", "failed"] = "running"
    batch_total: int = Field(default=0, ge=0)
    batch_completed: int = Field(default=0, ge=0)
    record_count: int = Field(default=0, ge=0)
    skipped_count: int = Field(default=0, ge=0)
    error_code: str | None = Field(default=None, max_length=160)
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def valid_progress(self) -> Self:
        if self.batch_completed > self.batch_total:
            raise ValueError("任务完成批数不能超过总批数。")
        return self
