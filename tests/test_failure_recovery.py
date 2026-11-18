import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from rag_workbench.application.knowledge import KnowledgeService
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


@pytest.mark.parametrize("stop", ["before_commit", "after_commit"])
def test_delete_interruption_preserves_document_or_recoverable_copy(knowledge, stop):
    document = knowledge.ingest("manual.txt", TEXT)
    knowledge.assign_sources({document.id: ("original-book", 42)})
    document = knowledge.list_documents()[0]
    program = """
import os
import sys
from pathlib import Path

from rag_workbench.application.knowledge import KnowledgeService
from rag_workbench.config import Settings
from tests.fakes import FakeEmbedder

root, document_id, stop = sys.argv[1:]
settings = Settings(_env_file=None, data_dir=Path(root), chunk_size=100, chunk_overlap=10)
knowledge = KnowledgeService(settings, FakeEmbedder())
publish = knowledge.repo.publish

def interrupted_publish(*args):
    if stop == "after_commit":
        publish(*args)
    os._exit(91)

knowledge.repo.publish = interrupted_publish
knowledge.delete(document_id)
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", program, str(knowledge.root), document.id, stop],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 91, result.stderr.decode(errors="replace")

    reopened = KnowledgeService(knowledge.settings, knowledge.embedder)
    if stop == "before_commit":
        assert reopened.list_documents() == [document]
        assert reopened._raw_path(document).read_bytes() == TEXT
        assert reopened.retrieve("保管")[0].chunk.document_id == document.id
        reopened.delete(document.id)
    assert reopened.list_documents() == []
    restored = reopened.restore(document.id)
    assert restored == document
    assert reopened._raw_path(restored).read_bytes() == TEXT
    assert reopened.retrieve("保管")[0].chunk.document_id == document.id


@pytest.mark.parametrize("damaged_file", ["metadata", "original"])
def test_delete_preserves_corrupt_existing_archive_and_active_snapshot(knowledge, damaged_file):
    document = knowledge.ingest("manual.txt", TEXT)
    active = knowledge.repo.get_state("active")
    trash = knowledge.root / "trash"
    suffix = ".json" if damaged_file == "metadata" else document.extension
    damaged = trash / f"{document.id}{suffix}"
    damaged.write_bytes(b"corrupt previous archive")

    with pytest.raises(AppError):
        knowledge.delete(document.id)

    assert damaged.read_bytes() == b"corrupt previous archive"
    assert knowledge.repo.get_state("active") == active
    assert knowledge.list_documents() == [document]
    assert knowledge._raw_path(document).read_bytes() == TEXT


def test_delete_after_same_content_reupload_preserves_both_source_records(knowledge):
    first = knowledge.ingest("manual.txt", TEXT)
    knowledge.assign_sources({first.id: ("first-book", 12)})
    first = knowledge.list_documents()[0]
    knowledge.delete(first.id)

    latest = knowledge.ingest("revision.md", TEXT)
    knowledge.assign_sources({latest.id: ("second-book", 42)})
    latest = knowledge.list_documents()[0]
    assert latest.id == first.id
    knowledge.delete(latest.id)

    trash = knowledge.root / "trash"
    archived_metadata = [
        path for path in trash.rglob(f"{first.id}.json")
        if path.read_bytes() == first.model_dump_json().encode()
    ]
    assert archived_metadata
    assert archived_metadata[0].with_suffix(first.extension).read_bytes() == TEXT
    assert knowledge.restore(latest.id) == latest


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


def test_delete_succeeds_when_original_file_is_already_missing(knowledge):
    document = knowledge.ingest("manual.txt", TEXT)
    knowledge.assign_sources({document.id: ("missing-book", 7)})
    knowledge._raw_path(document).unlink()

    knowledge.delete(document.id)

    assert knowledge.list_documents() == []
    trash_meta = knowledge.root / "trash" / f"{document.id}.json"
    assert trash_meta.is_file()
    with pytest.raises(AppError) as caught:
        knowledge.restore(document.id)
    assert caught.value.code == "original_missing"
