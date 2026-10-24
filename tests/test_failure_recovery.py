import sqlite3
from pathlib import Path

import pytest

from rag_workbench.domain.errors import AppError

from .test_knowledge import TEXT


def test_sqlite_publish_constraint_failure_rolls_back_entire_snapshot(knowledge):
    knowledge.ingest("manual.txt", TEXT)
    docs = knowledge.repo.documents()
    chunks = knowledge.repo.chunks()
    active = knowledge.repo.get_state("active")
    with pytest.raises(sqlite3.IntegrityError):
        knowledge.repo.publish(docs, [chunks[0], chunks[0]], {"generation": "bad"})
    assert knowledge.repo.chunks() == chunks
    assert knowledge.repo.get_state("active") == active
    assert knowledge.retrieve("保管")[0].chunk.text == TEXT.decode()


def test_index_disk_write_failure_leaves_previous_generation(knowledge, monkeypatch):
    knowledge.ingest("manual.txt", TEXT)
    active = knowledge.repo.get_state("active")

    def fail(path, data):
        raise OSError("disk full")

    monkeypatch.setattr(knowledge.store, "_durable_write", fail)
    with pytest.raises(AppError):
        knowledge.rebuild()
    assert knowledge.repo.get_state("active") == active
    assert knowledge.retrieve("保管")[0].chunk.text == TEXT.decode()


def test_delete_archive_failure_rolls_back_catalog_and_index(knowledge, monkeypatch):
    document = knowledge.ingest("manual.txt", TEXT)
    before_docs = knowledge.repo.documents()
    before_chunks = knowledge.repo.chunks()
    before_active = knowledge.repo.get_state("active")

    def fail(_document):
        raise OSError("locked")

    monkeypatch.setattr(knowledge, "_archive_original", fail)
    with pytest.raises(AppError) as caught:
        knowledge.delete(document.id)

    assert caught.value.code == "delete_failed"
    assert knowledge.repo.documents() == before_docs
    assert knowledge.repo.chunks() == before_chunks
    assert knowledge.repo.get_state("active") == before_active
    assert knowledge._raw_path(document).exists()
    assert knowledge.retrieve("保管")[0].chunk.text == TEXT.decode()


def test_delete_archive_failure_removes_orphan_trash_metadata(knowledge, monkeypatch):
    document = knowledge.ingest("manual.txt", TEXT)

    def fail_with_metadata(target):
        (knowledge.root / "trash" / f"{target.id}.json").write_text("{}", encoding="utf-8")
        raise OSError("locked")

    monkeypatch.setattr(knowledge, "_archive_original", fail_with_metadata)
    with pytest.raises(AppError):
        knowledge.delete(document.id)

    assert not (knowledge.root / "trash" / f"{document.id}.json").exists()


def test_restore_cleanup_failure_keeps_restored_document(knowledge, monkeypatch):
    document = knowledge.ingest("manual.txt", TEXT)
    knowledge.delete(document.id)
    original_replace = Path.replace

    def fail_restored_archive(self, target):
        if "restored" in str(target):
            raise OSError("locked")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", fail_restored_archive)
    restored = knowledge.restore(document.id)

    assert restored.id == document.id
    assert document.id in {item.id for item in knowledge.list_documents()}
    assert knowledge._raw_path(document).exists()


def test_interrupted_raw_write_can_retry(knowledge, monkeypatch):
    from rag_workbench.infrastructure import vector_store

    original_replace = vector_store.os.replace

    def fail(source, destination):
        raise OSError("interrupted")

    monkeypatch.setattr(vector_store.os, "replace", fail)
    with pytest.raises(AppError):
        knowledge.ingest("manual.txt", TEXT)
    monkeypatch.setattr(vector_store.os, "replace", original_replace)
    assert knowledge.ingest("manual.txt", TEXT).status == "ready"
