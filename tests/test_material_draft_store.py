"""Durable, version-checked drafts never change approved material records."""

import math
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from rag_workbench.domain.errors import AppError
from rag_workbench.infrastructure.material_draft_store import MaterialDraftStore


def save(store, key="edit_record_1", values=None, **kwargs):
    return store.save(
        key, {"value_raw": "0.12"} if values is None else values,
        document_id=kwargs.pop("document_id", "paper-a"),
        kind=kwargs.pop("kind", "review"),
        record_id=kwargs.pop("record_id", "record-a"),
        base_revision=kwargs.pop("base_revision", 1),
        **kwargs,
    )


def test_draft_survives_reopening_in_independent_workspace_database(tmp_path):
    root = tmp_path / "materials"
    store = MaterialDraftStore(root)
    saved = save(store, values={"value_raw": "0.13", "evidence": "[{unfinished"})
    reopened = MaterialDraftStore(root)

    assert saved.version == 1
    assert reopened.get(saved.key) == saved
    assert saved.values["evidence"] == "[{unfinished"
    assert reopened.list("paper-a") == [saved]
    assert reopened.list("paper-b") == []
    assert (root / "drafts.sqlite3").is_file()
    assert not (root / "materials.sqlite3").exists()


def test_cas_update_and_delete_protect_newer_drafts(tmp_path):
    store = MaterialDraftStore(tmp_path)
    first = save(store)
    second = save(store, values={"value_raw": "0.14"}, expected_version=first.version)
    assert second.version == 2
    assert second.base_revision == first.base_revision

    for operation in (
        lambda: save(store),
        lambda: save(store, values={"value_raw": "stale"}, expected_version=1),
        lambda: store.delete(first.key, expected_version=1),
    ):
        with pytest.raises(AppError) as error:
            operation()
        assert error.value.code == "material_draft_conflict"
    assert store.get(first.key) == second
    store.delete(first.key, expected_version=2)
    assert store.get(first.key) is None
    with pytest.raises(AppError) as error:
        save(store, expected_version=2)
    assert error.value.code == "material_draft_conflict"


def test_concurrent_instances_cannot_both_replace_same_version(tmp_path):
    store = MaterialDraftStore(tmp_path)
    save(store)
    other = MaterialDraftStore(tmp_path)
    barrier = Barrier(2)

    def update(instance, value):
        barrier.wait()
        try:
            return save(instance, values={"value_raw": value}, expected_version=1).version
        except AppError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        one = pool.submit(update, store, "0.2")
        two = pool.submit(update, other, "0.3")
        outcomes = [one.result(), two.result()]
    assert sorted(map(str, outcomes)) == ["2", "material_draft_conflict"]
    assert store.get("edit_record_1").version == 2


def test_revisions_and_workspaces_keep_independent_drafts(tmp_path):
    one = MaterialDraftStore(tmp_path / "one")
    two = MaterialDraftStore(tmp_path / "two")
    first = save(one)
    newer = save(one, key="edit_record_2", base_revision=2, values={"value_raw": "0.3"})
    save(one, key="manual", kind="manual", record_id=None, base_revision=None)
    assert one.list()[:2] != []
    assert one.list()[0].key == "manual"
    assert one.get(first.key) == first
    assert one.get(newer.key) == newer
    assert two.list() == []
    save(two, values={"value_raw": "another workspace"})
    assert one.get(first.key).values != two.get(first.key).values


@pytest.mark.parametrize("values", [[], {1: "key"}, {"bad": {1, 2}}, {"bad": (1, 2)},
                                    {"bad": math.nan}, {"bad": math.inf},
                                    {"large": "字" * 400000}])
def test_invalid_or_oversized_payload_does_not_change_saved_draft(tmp_path, values):
    store = MaterialDraftStore(tmp_path)
    original = save(store)
    with pytest.raises(AppError) as error:
        save(store, values=values, expected_version=original.version)
    assert error.value.code == "material_draft_invalid"
    assert store.get(original.key) == original


def test_json_values_are_copied_and_sql_keys_are_parameters(tmp_path):
    store = MaterialDraftStore(tmp_path)
    values = {"nested": [{"flag": True, "missing": None, "count": 3, "value": 1.2}]}
    key = "key'; DROP TABLE drafts; --"
    snapshot = save(store, key=key, values=values)
    values["nested"][0]["count"] = 99
    snapshot.values["nested"][0]["count"] = 88
    assert store.get(key).values["nested"][0]["count"] == 3
    assert len(store.list()) == 1


def test_identity_of_existing_draft_cannot_be_moved_to_different_source(tmp_path):
    store = MaterialDraftStore(tmp_path)
    original = save(store)
    with pytest.raises(AppError):
        save(store, document_id="other-paper", expected_version=1)
    assert store.get(original.key) == original


def test_corrupt_json_is_actionable_and_never_silently_removed(tmp_path):
    store = MaterialDraftStore(tmp_path)
    save(store)
    with sqlite3.connect(tmp_path / "drafts.sqlite3") as db:
        db.execute("UPDATE drafts SET values_json='not json'")
    with pytest.raises(AppError) as error:
        store.get("edit_record_1")
    assert error.value.code == "material_draft_invalid"
    with sqlite3.connect(tmp_path / "drafts.sqlite3") as db:
        assert db.execute("SELECT count(*) FROM drafts").fetchone()[0] == 1


def test_storage_failure_is_sanitized_and_prior_draft_survives(tmp_path, monkeypatch):
    store = MaterialDraftStore(tmp_path)
    original = save(store)
    original_connect = sqlite3.connect

    def fail(*args, **kwargs):
        raise sqlite3.OperationalError("private path / secret fake")

    monkeypatch.setattr(sqlite3, "connect", fail)
    with pytest.raises(AppError) as error:
        save(store, expected_version=1)
    assert error.value.code == "material_draft_storage_error"
    assert "secret" not in str(error.value)
    monkeypatch.setattr(sqlite3, "connect", original_connect)
    assert store.get(original.key) == original


@pytest.mark.parametrize("operation", ["update", "delete"])
def test_delete_recreate_never_reuses_version_for_stale_writer(tmp_path, operation):
    store = MaterialDraftStore(tmp_path)
    first = save(store)
    store.delete(first.key, expected_version=first.version)
    recreated = save(store, values={"value_raw": "0.14"})

    with pytest.raises(AppError) as error:
        if operation == "update":
            save(store, values={"value_raw": "stale"}, expected_version=first.version)
        else:
            store.delete(first.key, expected_version=first.version)
    assert error.value.code == "material_draft_conflict"
    assert store.get(first.key) == recreated
    assert recreated.version > first.version


def test_version_one_database_initializes_clock_above_existing_versions(tmp_path):
    store = MaterialDraftStore(tmp_path)
    original = save(store)
    with sqlite3.connect(tmp_path / "drafts.sqlite3") as db:
        db.execute("DROP TABLE IF EXISTS draft_version_clock")
        db.execute("UPDATE drafts SET version=41")
        db.execute(
            "INSERT INTO drafts SELECT 'already-deleted', values_json, document_id, kind, "
            "record_id, base_revision, 9999, updated_at FROM drafts LIMIT 1",
        )
        db.execute("DELETE FROM drafts WHERE key='already-deleted'")
        db.execute("PRAGMA user_version=1")

    upgraded = MaterialDraftStore(tmp_path)
    assert upgraded.get(original.key).values == original.values
    assert upgraded.get(original.key).version == 41
    other = save(upgraded, key="other-editor")
    assert other.version > 9999
    upgraded.delete(original.key, expected_version=41)
    recreated = save(upgraded)
    assert recreated.version > other.version


@pytest.mark.parametrize("damage", ["table", "row"])
def test_current_schema_missing_version_clock_is_not_silently_reset(tmp_path, damage):
    store = MaterialDraftStore(tmp_path)
    save(store)
    with sqlite3.connect(tmp_path / "drafts.sqlite3") as db:
        if damage == "table":
            db.execute("DROP TABLE draft_version_clock")
        else:
            db.execute("DELETE FROM draft_version_clock")
    with pytest.raises(AppError) as error:
        MaterialDraftStore(tmp_path)
    assert error.value.code == "material_draft_storage_error"
