import sqlite3

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
