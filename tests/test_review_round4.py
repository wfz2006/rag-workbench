"""Fourth review: failure recovery, concurrency and presentation contracts."""

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import Mock

import pytest

from rag_workbench.application.generation import GenerationJob
from rag_workbench.application.pipeline import RagPipeline
from rag_workbench.application.teaching import teaching_mode
from rag_workbench.config import load_file_settings
from rag_workbench.domain.errors import AppError
from rag_workbench.domain.models import AnswerResult, Inspection

from .fakes import FakeProvider
from .test_knowledge import TEXT


def test_delete_uses_snapshot_even_when_all_originals_are_missing(knowledge, embedder):
    first, second = knowledge.ingest_many([("first.txt", TEXT), ("second.txt", TEXT + b" second")])
    for document in (first, second):
        knowledge._raw_path(document).unlink()
    embedder.encode_documents = Mock(side_effect=AssertionError("Deletion must not embed"))
    active = knowledge.repo.get_state("active")
    knowledge.delete(first.id)
    assert {c.document_id for c in knowledge.repo.chunks()} == {second.id}
    assert knowledge.repo.get_state("active")["signature"] == active["signature"]
    knowledge.delete(second.id)
    assert not knowledge.repo.chunks() and not knowledge.repo.documents()
    embedder.encode_documents.assert_not_called()


@pytest.mark.parametrize("status", ["pending", "failed"])
def test_delete_unindexed_document_does_not_load_or_rebuild(knowledge, status, monkeypatch):
    old = knowledge.ingest("old.txt", TEXT)
    knowledge._raw_path(old).unlink()
    with pytest.raises(AppError):
        knowledge.ingest("waiting.txt", b"valid pending content")
    waiting = next(d for d in knowledge.repo.documents() if d.id != old.id)
    knowledge.repo.save_document(waiting.model_copy(update={"status": status}))
    active = knowledge.repo.get_state("active")
    monkeypatch.setattr(knowledge.store, "load", Mock(side_effect=AssertionError("no load")))
    monkeypatch.setattr(knowledge, "_rebuild", Mock(side_effect=AssertionError("no rebuild")))
    knowledge.delete(waiting.id)
    assert knowledge.repo.get_state("active") == active
    assert [d.id for d in knowledge.repo.documents()] == [old.id]


def test_failed_delete_publish_keeps_previous_snapshot(knowledge, monkeypatch):
    document = knowledge.ingest("a.txt", TEXT)
    active = knowledge.repo.get_state("active")
    monkeypatch.setattr(knowledge.repo, "publish", Mock(side_effect=OSError("disk full")))
    with pytest.raises(AppError):
        knowledge.delete(document.id)
    assert knowledge.repo.get_state("active") == active
    assert knowledge.repo.documents()[0].id == document.id


@pytest.mark.parametrize("damage", ["missing", "json", "schema"])
def test_restore_errors_are_application_errors(knowledge, damage):
    document = knowledge.ingest("a.txt", TEXT)
    knowledge.delete(document.id)
    if damage == "missing":
        (knowledge.root / "trash" / f"{document.id}.txt").unlink()
    else:
        (knowledge.root / "trash" / f"{document.id}.json").write_text(
            "{invalid" if damage == "json" else "{}"
        )
    with pytest.raises(AppError):
        knowledge.restore(document.id)


@pytest.mark.parametrize(
    "values", [{"top_k": 0}, {"top_k": "bad"}, {"chunk_size": 100, "chunk_overlap": 200}]
)
def test_settings_model_validation_is_wrapped(tmp_path, values):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(values))
    with pytest.raises(AppError) as caught:
        load_file_settings(path, env_file=None)
    assert caught.value.code == "invalid_settings_file"


def test_file_chunk_pair_does_not_inherit_incompatible_environment_sibling(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_CHUNK_SIZE", "500")
    monkeypatch.setenv("RAG_CHUNK_OVERLAP", "120")
    path = tmp_path / "settings.json"
    path.write_text('{"chunk_size":100}')
    config = load_file_settings(path, env_file=None)
    assert config.chunk_size == 100 and config.chunk_overlap == 60


@pytest.mark.parametrize("field", ["teaching_profile_path", "domain_profile_path"])
@pytest.mark.parametrize("value", ["", "   "])
def test_empty_optional_profile_is_disabled(tmp_path, field, value):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({field: value}))
    assert getattr(load_file_settings(path, env_file=None), field) is None


@pytest.mark.parametrize("field", ["data_dir", "model_cache"])
@pytest.mark.parametrize("value", ["", "   ", "disabled"])
def test_storage_paths_cannot_be_empty_or_disabled(tmp_path, field, value):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({field: value}))
    with pytest.raises(AppError) as caught:
        load_file_settings(path, env_file=None)
    assert caught.value.code == "invalid_settings_file"


def test_saving_does_not_block_snapshot_and_rejects_late_cancel():
    entered, release = Event(), Event()
    result = AnswerResult(
        answer="body", mode="general", debug=Inspection(question="q", reason="original reason")
    )

    def save(_):
        entered.set()
        assert release.wait(3)

    job = GenerationJob("q", lambda *_: result, save).start()
    try:
        assert entered.wait(1)
        with ThreadPoolExecutor() as pool:
            future = pool.submit(job.snapshot)
            try:
                snapshot = future.result(timeout=0.3)
                assert snapshot.phase == "saving" and not snapshot.done
                assert job.cancel() is False
            finally:
                release.set()
    finally:
        release.set()
        job.wait()
    assert job.snapshot().result.answer == "body"


def test_persistence_failure_retains_original_debug_reason():
    result = AnswerResult(
        answer="body", mode="general", debug=Inspection(question="q", reason="real evidence reason")
    )
    job = GenerationJob("q", lambda *_: result, Mock(side_effect=OSError("disk full"))).start()
    job.wait()
    assert job.snapshot().result.debug.reason == "real evidence reason"
    assert job.snapshot().result.error_code == "persistence_error"


def test_hint_repair_resets_phase_and_sends_constraints(knowledge):
    knowledge.ingest("a.txt", TEXT)

    class Repair(FakeProvider):
        attempts = 0

        def complete(self, system, user, **kwargs):
            text = super().complete(system, user, **kwargs)
            if "claims" in system:
                self.attempts += 1
                payload = json.loads(text)
                payload["claims"][0]["text"] = (
                    "不合规的直接答案。" if self.attempts == 1 else "你能先确认哪个状态？"
                )
                return json.dumps(payload)
            return text

    provider, events = Repair(), []
    result = RagPipeline(knowledge, provider, teaching_profile="提示").answer(
        "保管只给提示", on_event=events.append
    )
    assert result.mode == "knowledge_base"
    phases = [event.phase for event in events]
    assert phases.count("generating_grounded") == 2
    assert "纠正" in provider.calls[-1][0]


def test_uppercase_evidence_ids_are_canonicalized_without_retry(knowledge):
    knowledge.ingest("a.txt", TEXT)

    class Upper(FakeProvider):
        def complete(self, system, user, **kwargs):
            value = json.loads(super().complete(system, user, **kwargs))
            value["supporting_chunk_ids"] = [x.upper() for x in value["supporting_chunk_ids"]]
            return json.dumps(value)

    provider = Upper()
    assert RagPipeline(knowledge, provider).inspect("保管").route == "knowledge_base"
    assert len(provider.calls) == 1


@pytest.mark.parametrize(
    "question",
    [
        "你刚给的提示看懂了？现在给我完整答案",
        "谢谢给的提示! 请直接给代码",
        "给的提示明白了吗?现在完整讲解",
    ],
)
def test_latest_sentence_controls_teaching_mode(question):
    assert teaching_mode(question, []) == "explain"


def test_copy_text_excludes_only_system_annotations(knowledge):
    from rag_workbench.ui.components import answer_presentation

    knowledge.ingest("a.txt", TEXT)
    result = RagPipeline(knowledge, FakeProvider()).answer("保管")
    display, copy = answer_presentation(result)
    assert "〔来源 1〕" in display and "〔来源" not in copy
    assert "保管期限为三年" in copy
    general = RagPipeline(knowledge, FakeProvider(answerable=False)).answer("保管")
    display, copy = answer_presentation(general)
    assert not display.startswith("通用模式") and not copy.startswith("通用模式")


def test_running_session_remains_visible_with_question_title():
    from rag_workbench.ui.shell import session_title

    session = {"id": "s", "title": "新会话", "message_count": 0}
    job = type("Job", (), {"question": "正在生成的问题"})()
    assert session_title(session, {"s": job}, {}) == "正在生成的问题"


def test_document_order_uses_names_for_equal_upload_timestamps(knowledge):
    from rag_workbench.ui.pages import ordered_documents

    first = knowledge.ingest("B.txt", TEXT)
    second = first.model_copy(update={"name": "A.txt", "id": "f" * 64})
    newest = first.model_copy(update={"name": "C.txt", "created_at": "2099-01-01T00:00:00+00:00"})
    assert [d.name for d in ordered_documents([first, second, newest])] == [
        "C.txt",
        "A.txt",
        "B.txt",
    ]


def test_plain_copy_preserves_code_and_deliberate_whitespace():
    from rag_workbench.application.prompts import GENERAL_PREFIX
    from rag_workbench.ui.components import answer_presentation

    body = '    indented\n\n\n```cpp\nvector<int> xs;\n\n\n// "quoted" & literal\n```\n'
    result = AnswerResult(
        answer=GENERAL_PREFIX + body, mode="general", debug=Inspection(question="q")
    )
    assert answer_presentation(result)[1] == body


def test_remove_failed_turn_only_deletes_its_own_pair(knowledge):
    repo = knowledge.repo
    session = repo.new_session()
    other = repo.new_session()
    ok = AnswerResult(answer="ok", mode="general", debug=Inspection(question="q"))
    failed = AnswerResult(answer="error", mode="error", debug=Inspection(question="q"))
    repo.save_turn(session, "first", ok)
    repo.save_turn(session, "failed", failed)
    repo.save_turn(session, "last", ok)
    messages = repo.messages(session)
    with pytest.raises(AppError):
        repo.delete_failed_turn(other, messages[3]["id"])
    with pytest.raises(AppError):
        repo.delete_failed_turn(session, messages[1]["id"])
    repo.delete_failed_turn(session, messages[3]["id"])
    assert [m["content"] for m in repo.messages(session)] == ["first", "ok", "last", "ok"]


def test_delete_keeps_old_index_signature_when_new_settings_require_rebuild(knowledge):
    first, second = knowledge.ingest_many([("a.txt", TEXT), ("b.txt", TEXT + b" b")])
    original = knowledge.repo.get_state("active")["signature"]
    knowledge.settings.chunk_size += 20
    assert knowledge.signature != original
    knowledge.delete(first.id)
    assert knowledge.repo.get_state("active")["signature"] == original
    assert {c.document_id for c in knowledge.repo.chunks()} == {second.id}


def test_reset_only_clears_ui_overrides_and_preserves_deployment(knowledge, monkeypatch):
    from rag_workbench.bootstrap import Services, reset_ui_settings

    monkeypatch.setenv("RAG_TOP_K", "9")
    knowledge.repo.set_state("ui_settings", {"show_debug": True, "chunk_size": 500})
    services = Services(knowledge.settings, knowledge, None, knowledge.repo)
    session = knowledge.repo.new_session()
    result = reset_ui_settings(services)
    assert result.top_k == 9 and result.chunk_size == 400
    assert result.data_dir == knowledge.settings.data_dir
    assert knowledge.repo.get_state("ui_settings") == {}
    assert knowledge.repo.sessions()[0]["id"] == session
