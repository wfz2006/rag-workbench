"""Manual reproduction notes, grounded in a paper and its own supplements."""

from rag_workbench.domain.errors import AppError
from rag_workbench.domain.materials import EvidenceBlock, LiteratureDocument
from rag_workbench.domain.reproduction import ReproductionStep
from rag_workbench.infrastructure.material_repository import MaterialsRepository
from rag_workbench.infrastructure.parsing import quote_in_source


class ReproductionChecklist:
    def __init__(self, repository: MaterialsRepository):
        self.repo = repository

    def documents(self, paper_id: str) -> list[LiteratureDocument]:
        paper = self.repo.document(paper_id)
        if paper is None or paper.role != "main":
            raise AppError("reproduction_paper", "请选择一篇正文论文。")
        return [paper, *(doc for doc in self.repo.documents() if doc.parent_id == paper_id)]

    def steps(self, paper_id: str) -> list[ReproductionStep]:
        return self.repo.reproduction_steps(paper_id)

    def source(self, step: ReproductionStep) -> tuple[LiteratureDocument, EvidenceBlock] | None:
        if not step.block_id:
            return None
        for doc in self.documents(step.paper_id):
            for block in self.repo.blocks(doc.id):
                if block.id == step.block_id and quote_in_source(step.quote, block.text):
                    return doc, block
        return None

    def save(self, step: ReproductionStep) -> ReproductionStep:
        step = ReproductionStep.model_validate(step.model_dump())
        self.documents(step.paper_id)
        if step.status == "not_reported" and not step.note:
            raise AppError("reproduction_review", "请记录核查范围，例如正文方法和补充材料的章节。")
        if (step.status == "verified" or step.block_id or step.quote) and not (
            step.block_id and step.quote and self.source(step)
        ):
            raise AppError(
                "reproduction_source", "请选择本篇论文的正文或补充材料，填写原文引文。",
            )
        return self.repo.save_reproduction_step(step)

    def remove(self, step: ReproductionStep) -> None:
        self.repo.remove_reproduction_step(step)
