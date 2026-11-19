from typing import Literal
from uuid import uuid4

from pydantic import Field, field_validator

from rag_workbench.domain.materials import EntityId, SourceId
from rag_workbench.domain.models import Contract

STAGES = {"preparation": "制备", "processing": "处理", "characterization": "表征"}
STATUSES = {"pending": "尚未核实", "verified": "已核实", "not_reported": "文中未报告"}


class ReproductionStep(Contract):
    id: EntityId = Field(default_factory=lambda: uuid4().hex)
    paper_id: SourceId
    stage: Literal["preparation", "processing", "characterization"]
    title: str = Field(min_length=1, max_length=240)
    status: Literal["pending", "verified", "not_reported"] = "pending"
    note: str = Field(default="", max_length=4000)
    block_id: EntityId | None = None
    quote: str = Field(default="", max_length=10000)
    revision: int = Field(default=0, ge=0)

    @field_validator("title", "note", "quote", mode="before")
    @classmethod
    def strip_text(cls, value):
        return value.strip() if isinstance(value, str) else value
