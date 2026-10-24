import json

import numpy as np
import pytest

from rag_workbench.application.knowledge import KnowledgeService
from rag_workbench.domain.errors import AppError

TEXT = "资料保管期限为三年。到期后应进行复核。".encode()


def test_empty_kb_does_not_load_embedding(knowledge, embedder):
    embedder.fail = True
    assert knowledge.retrieve("问题") == []
    assert knowledge.stats()["chunks"] == 0
    assert knowledge.stats()["index_status"] == "empty"


def test_ingest_dedup_same_name_different_content_and_restart(knowledge, settings, embedder):
    doc = knowledge.ingest("manual.txt", TEXT)
    duplicate = knowledge.ingest("another.md", TEXT)
    assert duplicate.id == doc.id
    other = knowledge.ingest("manual.txt", "另一个文件的内容。".encode())
    assert other.id != doc.id
    assert knowledge.stats()["documents"] == 2
    restarted = KnowledgeService(settings, embedder)
    results = restarted.retrieve("保管期限？")
    assert results[0].chunk.document_id == doc.id
    assert results[0].chunk.text == TEXT.decode()
    assert results[0].score == pytest.approx(1.0)


def test_path_traversal_is_display_name_only(knowledge):
    doc = knowledge.ingest("../../private.txt", TEXT)
    assert doc.name == "private.txt"
    assert (knowledge.root / "originals" / f"{doc.id}.txt").is_file()


def test_delete_rebuild_and_restore_raw(knowledge):
    doc = knowledge.ingest("manual.txt", TEXT)
    ids = [r.chunk.id for r in knowledge.retrieve("保管")]
    knowledge.rebuild()
    assert [r.chunk.id for r in knowledge.retrieve("保管")] == ids
    knowledge.delete(doc.id)
    assert knowledge.retrieve("保管") == []
    assert knowledge.stats()["documents"] == 0
    assert list((knowledge.root / "trash").glob(f"{doc.id}.*"))
    knowledge.restore(doc.id)
    assert knowledge.stats()["documents"] == 1


def test_embedding_failure_preserves_old_snapshot(knowledge, embedder):
    knowledge.ingest("manual.txt", TEXT)
    embedder.fail = True
    with pytest.raises(AppError):
        knowledge.ingest("new.txt", "新材料".encode())
    embedder.fail = False
    assert knowledge.retrieve("保管")[0].chunk.text == TEXT.decode()
    # Global model outage blocks publication, rather than declaring valid new content bad.
    assert sorted(d.status for d in knowledge.list_documents()) == ["pending", "ready"]


def test_publish_failure_preserves_old_metadata_and_index(knowledge, monkeypatch):
    doc = knowledge.ingest("manual.txt", TEXT)
    active = knowledge.repo.get_state("active")

    def fail(*args):
        raise OSError("disk full")

    monkeypatch.setattr(knowledge.repo, "publish", fail)
    with pytest.raises(AppError):
        knowledge.delete(doc.id)
    assert knowledge.repo.get_state("active") == active
    assert knowledge.retrieve("保管")[0].chunk.document_id == doc.id


def test_corrupt_index_is_error_not_miss(knowledge):
    knowledge.ingest("manual.txt", TEXT)
    generation = knowledge.repo.get_state("active")["generation"]
    path = knowledge.root / "indexes" / generation / "index.faiss"
    path.write_bytes(b"broken")
    with pytest.raises(AppError, match="索引"):
        knowledge.retrieve("保管")
    knowledge.rebuild()
    assert knowledge.retrieve("保管")


def test_same_dimension_different_model_is_incompatible(knowledge, embedder):
    knowledge.ingest("manual.txt", TEXT)
    embedder.signature = "another-model-same-dimension"
    with pytest.raises(AppError, match="重新索引"):
        knowledge.retrieve("保管")


def test_changed_chunk_configuration_requires_rebuild(knowledge):
    knowledge.ingest("manual.txt", TEXT)
    knowledge.settings = type(knowledge.settings).model_validate(
        {
            **knowledge.settings.model_dump(),
            "chunk_size": 80,
        }
    )
    assert knowledge.stats()["index_status"] == "incompatible"
    with pytest.raises(AppError):
        knowledge.retrieve("保管")


def test_invalid_vectors_fail_safely(knowledge, monkeypatch):
    monkeypatch.setattr(knowledge.embedder, "encode_documents", lambda texts: np.zeros((1, 2)))
    with pytest.raises(AppError, match="向量"):
        knowledge.ingest("zero.txt", TEXT)
    assert knowledge.stats()["chunks"] == 0


def test_manifest_tampering_is_detected(knowledge):
    knowledge.ingest("manual.txt", TEXT)
    generation = knowledge.repo.get_state("active")["generation"]
    path = knowledge.root / "indexes" / generation / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["chunk_ids"] = ["invented"]
    path.write_text(json.dumps(manifest))
    with pytest.raises(AppError):
        knowledge.retrieve("保管")
