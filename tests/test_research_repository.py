import pytest

from rag_workbench.domain.errors import AppError
from rag_workbench.domain.research import EvidenceSource
from rag_workbench.infrastructure.repository import Repository
from rag_workbench.infrastructure.research_repository import ResearchRepository


def test_capture_is_idempotent_and_annotations_are_versioned(tmp_path):
    path = tmp_path / "metadata.sqlite3"
    legacy = Repository(path)
    conversation = legacy.new_session()
    repo = ResearchRepository(legacy)
    question = repo.create_question("结构变化")
    claim = repo.add_claim(question, "溶剂交换")
    another = repo.add_claim(question, "对照")
    source = EvidenceSource(
        origin="chat", document_id="a" * 64, name="paper.pdf", quote="solvent exchange",
        page=4, locator="chunk-a", message_id=3,
    )
    card, created = repo.capture(claim, source)
    assert created
    repo.annotate(card, 1, "support", "作者报告")
    assert repo.capture(claim, source.model_copy(update={"message_id": 9})) == (card, False)
    assert repo.capture(another, source)[1]
    with pytest.raises(AppError, match="其他窗口"):
        repo.annotate(card, 1, "counter", "")
    reopened = ResearchRepository(Repository(path))
    cards = reopened.board(question)[2]
    assert next(item for item in cards if item.id == card).note == "作者报告"
    assert legacy.sessions()[0]["id"] == conversation
    assert not ResearchRepository(Repository(tmp_path / "other.db")).questions()
