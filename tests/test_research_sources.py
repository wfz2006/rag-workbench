import pytest

from rag_workbench.application.research import ResearchService
from rag_workbench.domain.errors import AppError
from rag_workbench.domain.models import Chunk, Document, RetrievalResult


def test_same_name_resolves_by_id_and_mapping_does_not_change_physical_page(settings, monkeypatch):
    from rag_workbench.bootstrap import build_services

    services = build_services(settings)
    first = Document(id="a" * 64, name="same.pdf", extension=".pdf", size=1)
    second = first.model_copy(update={"id": "b" * 64})
    services.repo.save_document(first)
    services.repo.save_document(second)
    service = ResearchService(services.knowledge)
    source = service.chat_source(RetrievalResult(
        chunk=Chunk(id="chunk", document_id=second.id, document_name=second.name, text="data",
                    page=1, ordinal=0, start=0, end=4),
        original_page=30, score=1, passed_threshold=True,
    ), 3)
    assert source.page == 1 and source.mapped_page == 30
    seen = []
    monkeypatch.setattr(
        services.knowledge, "verified_original", lambda doc: seen.append(doc.id) or b"x",
    )
    assert service.resolve_source(source) == b"x"
    assert seen == [second.id]
    with pytest.raises(AppError, match="原件"):
        service.resolve_source(source.model_copy(update={"document_id": "c" * 64}))


def test_current_reading_range_applies_to_saved_cards(settings):
    from rag_workbench.bootstrap import build_services
    from rag_workbench.domain.research import EvidenceSource

    services = build_services(settings.model_copy(update={"spoiler_max_page": 10}))
    document = Document(
        id="a" * 64, name="paper.pdf", extension=".pdf", size=1, status="ready",
        source_page=40,
    )
    services.repo.save_document(document)
    research = services.research
    question = research.repo.create_question("研究问题")
    claim = research.repo.add_claim(question, "主张")
    source = EvidenceSource(origin="chat", document_id=document.id, name=document.name,
                            page=1, mapped_page=4, quote="old quote", locator="chunk")
    research.repo.capture(claim, source)
    assert not research.visible(source)
    assert b"old quote" not in research.export_markdown(question)
    with pytest.raises(AppError, match="阅读范围"):
        research.resolve_source(source)
