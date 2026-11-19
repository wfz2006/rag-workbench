import re
from functools import cached_property

from rag_workbench.application.knowledge import KnowledgeService
from rag_workbench.domain.errors import AppError
from rag_workbench.domain.models import RetrievalResult
from rag_workbench.domain.research import ORIGIN_LABELS, STANCE_LABELS, EvidenceSource
from rag_workbench.infrastructure.material_preview import load_original
from rag_workbench.infrastructure.material_repository import MaterialsRepository
from rag_workbench.infrastructure.research_repository import ResearchRepository


class ResearchService:
    def __init__(self, knowledge: KnowledgeService):
        self.knowledge = knowledge

    @cached_property
    def repo(self) -> ResearchRepository:
        return ResearchRepository(self.knowledge.repo)

    @staticmethod
    def chat_source(result: RetrievalResult, message_id: int) -> EvidenceSource:
        chunk = result.chunk
        return EvidenceSource(
            origin="chat", document_id=chunk.document_id, name=chunk.document_name,
            quote=chunk.text, page=chunk.page, mapped_page=result.original_page,
            locator=chunk.id, message_id=message_id,
        )

    def visible(self, source: EvidenceSource) -> bool:
        limit = self.knowledge.settings.spoiler_max_page
        if limit is None:
            return True
        if source.library == "materials":
            return False
        document = next((doc for doc in self.knowledge.repo.documents()
                         if doc.id == source.document_id and doc.status == "ready"), None)
        if document is None:
            return False
        markers = [int(page) for page in re.findall(r"〔p(\d{4,6})〕", source.quote)]
        return max(source.page or 0, source.mapped_page or 0,
                   document.source_page or 0, *markers) <= limit

    def resolve_source(self, source: EvidenceSource) -> bytes:
        if not self.visible(source):
            raise AppError("research_scope", "当前阅读范围内无法打开此原件。")
        if source.library == "materials":
            root = self.knowledge.root / "materials"
            if (root / "materials.sqlite3").is_file():
                repo = MaterialsRepository(root)
                if document := repo.document(source.document_id):
                    return load_original(repo, document)
        else:
            for document in self.knowledge.repo.documents():
                if document.id == source.document_id and document.extension == ".pdf":
                    return self.knowledge.verified_original(document)
        raise AppError("research_original", "原件不可用。重新导入同一原件可恢复预览。")

    def export_markdown(self, question_id: str) -> bytes:
        title, claims, cards = self.repo.board(question_id)
        lines = ["# " + _inline(title)]
        for claim_id, claim in claims:
            lines.extend(("", "## " + _inline(claim)))
            selected = [card for card in cards
                        if card.claim_id == claim_id and self.visible(card.source)]
            if not selected:
                lines.extend(("", "暂无证据"))
            for card in selected:
                source = card.source
                revision = (
                    f" · 材料记录修订 {source.record_revision}" if source.record_revision else ""
                )
                lines.extend((
                    "", f"**{STANCE_LABELS[card.stance]}** · {ORIGIN_LABELS[source.origin]}"
                    f" · {_inline(source.name)} · {source.location()}{revision}", "",
                    f"原件 SHA-256 `{source.document_id}`", "",
                ))
                lines.extend(_literal(source.quote))
                if card.note:
                    lines.extend(("", "判断笔记", "", *_literal(card.note)))
        return ("\n".join(lines) + "\n").encode("utf-8")


def _inline(text: str) -> str:
    return re.sub(r"([\\`*_{}\[\]()#+.!|<>])", r"\\\1", " ".join(text.split()))


def _literal(text: str) -> list[str]:
    size = max((len(run) for run in re.findall(r"`+", text)), default=0)
    fence = "`" * max(size + 1, 3)
    return [fence, *text.splitlines(), fence]
