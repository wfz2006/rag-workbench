from typing import Literal

from pydantic import Field

from rag_workbench.domain.materials import SourceId
from rag_workbench.domain.models import Contract

Stance = Literal["pending", "support", "counter", "limit"]
STANCE_LABELS = {"pending": "待核实", "support": "支持", "counter": "反例", "limit": "条件限制"}
ORIGIN_LABELS = {"chat": "问答引用", "star": "知识星图", "material": "材料记录"}


class EvidenceSource(Contract):
    origin: Literal["chat", "star", "material"]
    document_id: SourceId
    name: str
    quote: str = Field(min_length=1)
    page: int | None = Field(default=None, ge=1)
    mapped_page: int | None = Field(default=None, ge=1)
    locator: str
    library: Literal["knowledge", "materials"] = "knowledge"
    message_id: int | None = None
    graph_item: str | None = None
    record_id: str | None = None
    record_revision: int | None = None
    field: str | None = None

    def location(self) -> str:
        if self.mapped_page is not None:
            return f"来源映射第 {self.mapped_page} 页"
        if self.page is not None:
            return f"第 {self.page} 页"
        return "文本片段"


class EvidenceCard(Contract):
    id: str
    claim_id: str
    source: EvidenceSource
    stance: Stance
    note: str
    revision: int
    created_at: str
