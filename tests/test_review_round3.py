"""Third external review regressions. No remote model requests or production data."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from rag_workbench.application.pipeline import RagPipeline
from rag_workbench.application.teaching import teaching_mode
from rag_workbench.domain.errors import AppError
from rag_workbench.domain.models import AnswerResult, Inspection

from .fakes import FakeProvider
from .test_knowledge import TEXT


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("uppercase", [False, True])
def test_hint_is_validated_after_authorized_inline_citations_removed(knowledge, stream, uppercase):
    knowledge.ingest("guide.txt", TEXT)

    class InlineHint(FakeProvider):
        def complete(self, system, user, **kwargs):
            output = super().complete(system, user, **kwargs)
            if "claims" in system:
                payload = json.loads(output)
                identity = payload["claims"][0]["chunk_ids"][0]
                if uppercase:
                    identity = identity.upper()
                # Under the 180-char limit when clean, above it with duplicate inline IDs.
                payload["claims"][0]["text"] = (
                    "你能先根据资料确认" + "当前状态" * 20 + f"吗？[{identity}][{identity}]"
                )
                return json.dumps(payload)
            return output

    provider = InlineHint()
    result = RagPipeline(knowledge, provider, teaching_profile="只给一个问题").answer(
        "保管问题，只给提示",
        on_event=(lambda _: None) if stream else None,
    )
    assert result.mode == "knowledge_base"
    assert result.answer.count(f"[{result.sources[0].chunk.id}]") == 1
    assert len(provider.calls) == 2  # evidence + valid hint, no false repair


def test_unauthorized_inline_hint_citation_is_still_rejected(knowledge):
    knowledge.ingest("guide.txt", TEXT)

    class InvalidHint(FakeProvider):
        def complete(self, system, user, **kwargs):
            text = super().complete(system, user, **kwargs)
            if "claims" in system:
                payload = json.loads(text)
                payload["claims"][0]["text"] = "你能先确认状态吗？[" + "f" * 64 + "]"
                return json.dumps(payload)
            return text

    result = RagPipeline(knowledge, InvalidHint(), teaching_profile="提示").answer("保管只给提示")
    assert result.error_code == "invalid_citation"


@pytest.mark.parametrize(
    "question",
    [
        "不要给提示",
        "别给提示了",
        "不用给提示",
        "不要给提示直接给答案",
        "无需再给提示",
        "不需要只给提示",
    ],
)
def test_negative_hint_request_is_not_reversed(question):
    assert teaching_mode(question, []) == "explain"


def test_blocked_new_documents_automatically_recover_on_next_import(knowledge):
    old = knowledge.ingest("old.txt", TEXT)
    old_path = knowledge._raw_path(old)
    old_path.unlink()
    active = knowledge.repo.get_state("active")
    with pytest.raises(AppError):
        knowledge.ingest("blocked.txt", b"valid but blocked upload")
    blocked = next(d for d in knowledge.repo.documents() if d.id != old.id)
    assert blocked.status == "pending"
    assert knowledge.repo.get_state("active") == active
    old_path.write_bytes(TEXT)
    knowledge.ingest("trigger.md", b"new import after original recovery")
    assert all(d.status == "ready" for d in knowledge.repo.documents())
    assert blocked.id in {c.document_id for c in knowledge.repo.chunks()}


def test_duplicate_import_also_retries_pending_after_global_recovery(knowledge):
    old = knowledge.ingest("old.txt", TEXT)
    original = knowledge._raw_path(old)
    original.unlink()
    with pytest.raises(AppError):
        knowledge.ingest("waiting.md", b"valid staged content")
    original.write_bytes(TEXT)
    knowledge.ingest("duplicate.txt", TEXT)
    assert all(d.status == "ready" for d in knowledge.repo.documents())
    assert len({c.document_id for c in knowledge.repo.chunks()}) == 2


def test_document_failure_preserves_original_error_code(knowledge, embedder, monkeypatch):
    def too_long(_):
        raise AppError("embedding_input_too_long", "content limit")

    monkeypatch.setattr(embedder, "encode_documents", too_long)
    with pytest.raises(AppError) as caught:
        knowledge.ingest("long.txt", TEXT)
    assert caught.value.code == "embedding_input_too_long"
    assert knowledge.repo.documents()[0].error_code == "embedding_input_too_long"


@pytest.mark.parametrize("first", ["unknown", "contradictory", "duplicate"])
def test_evidence_logic_is_repaired_once_and_duplicates_normalized(knowledge, first):
    knowledge.ingest("a.txt", TEXT)

    class EvidenceRepair(FakeProvider):
        attempts = 0

        def complete(self, system, user, **kwargs):
            output = super().complete(system, user, **kwargs)
            self.attempts += 1
            if self.attempts == 1:
                value = json.loads(output)
                if first == "unknown":
                    value["supporting_chunk_ids"] = ["unknown"]
                elif first == "contradictory":
                    value["answerable"] = False
                else:
                    value["supporting_chunk_ids"] *= 2
                return json.dumps(value)
            return output

    provider = EvidenceRepair()
    inspection = RagPipeline(knowledge, provider).inspect("保管期限")
    assert inspection.route == "knowledge_base"
    assert len(inspection.evidence.supporting_chunk_ids) == 1
    assert provider.attempts == (1 if first == "duplicate" else 2)
    if first != "duplicate":
        assert "supporting_chunk_ids" in provider.calls[-1][0]
        assert "纠正" in provider.calls[-1][0]


def test_persistent_invalid_evidence_remains_error(knowledge):
    knowledge.ingest("a.txt", TEXT)
    provider = FakeProvider(invalid_id=True)
    inspection = RagPipeline(knowledge, provider).inspect("保管")
    assert inspection.route == "error" and inspection.error_code == "invalid_evidence"
    assert len(provider.calls) == 2


def test_context_neighbors_keep_real_lexical_fields(knowledge):
    knowledge.settings.top_k = 1
    knowledge.settings.retrieval_context_window = 1
    knowledge.ingest("many.txt", TEXT * 12)
    candidates = knowledge.retrieve("保管")
    neighbors = [r for r in candidates if r.context_for]
    assert neighbors and all(r.lexical_score > 0 and r.lexical_passed for r in neighbors)


def test_restore_preserves_trusted_source_metadata(knowledge):
    document = knowledge.ingest("page.md", TEXT)
    knowledge.assign_sources({document.id: ("source-group", 42)})
    knowledge.delete(document.id)
    restored = knowledge.restore(document.id)
    assert restored.source_group == "source-group" and restored.source_page == 42
    candidate = knowledge.retrieve("保管")[0]
    assert candidate.original_page == 42


def test_finished_tasks_collected_globally_and_timeline_preserved(monkeypatch):
    from rag_workbench.ui import session_state as ui_state

    result = AnswerResult(
        answer="first answer",
        mode="general",
        error_code="persistence_error",
        debug=Inspection(question="first"),
    )
    job = Mock()
    job.snapshot.return_value = SimpleNamespace(
        done=True, question="first", result=result, created_at="2026-10-24T01:00:00+00:00"
    )
    state = {"answer_jobs": {"session": job}}
    monkeypatch.setattr(ui_state.st, "session_state", state)
    assert ui_state.collect_finished_jobs()
    assert state["answer_jobs"] == {}
    turn = state["unsaved_turns"]["session"][0]
    messages = [
        {
            "id": 1,
            "created_at": "2026-10-24T01:01:00+00:00",
            "role": "user",
            "content": "second",
            "result": None,
        }
    ]
    timeline = ui_state.conversation_timeline(messages, [turn])
    assert timeline[0] is turn and timeline[1] == messages[0]
    ui_state.discard_unsaved_turn("session", turn.key)
    assert "session" not in state["unsaved_turns"]


def test_generic_cli_accepts_explicit_settings_file(tmp_path, monkeypatch, capsys):
    from rag_workbench import cli

    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps(
            {
                "chunk_size": 500,
                "chunk_overlap": 60,
                "top_k": 10,
                "teaching_profile_path": "disabled",
            }
        )
    )
    captured = []

    def build(settings):
        captured.append(settings)
        return SimpleNamespace(knowledge=SimpleNamespace(stats=lambda: {"index_status": "empty"}))

    monkeypatch.setattr(cli, "build_services", build)
    monkeypatch.setattr(
        "sys.argv",
        [
            "rag-workbench",
            "--settings-file",
            str(path),
            "--data-dir",
            str(tmp_path / "kb"),
            "stats",
        ],
    )
    assert cli.main() == 0
    assert captured[0].chunk_size == 500 and captured[0].top_k == 10
    assert captured[0].data_dir == tmp_path / "kb"
    assert "empty" in capsys.readouterr().out


@pytest.mark.parametrize(
    "payload",
    ["[]", "{broken", '{"api_key":"test-secret"}', '{"typo":3}', '"text"', "x" * 65537],
    ids=["array", "malformed", "secret", "unknown", "scalar", "oversized"],
)
def test_settings_file_rejects_bad_shape_unknown_fields_and_credentials(tmp_path, payload):
    from rag_workbench.config import load_file_settings

    path = tmp_path / "bad.json"
    path.write_text(payload)
    with pytest.raises(AppError) as caught:
        load_file_settings(path, env_file=None)
    assert caught.value.code == "invalid_settings_file"
    assert "test-secret" not in str(caught.value)


def test_file_settings_precedence_and_relative_paths(tmp_path, monkeypatch):
    from rag_workbench.bootstrap import load_settings
    from rag_workbench.config import load_file_settings
    from rag_workbench.infrastructure.repository import Repository

    path = tmp_path / "pack.json"
    path.write_text(
        json.dumps({"data_dir": "kb", "top_k": 10, "domain_profile_path": "profile/story.md"})
    )
    monkeypatch.setenv("RAG_TOP_K", "2")
    config = load_file_settings(path, env_file=None)
    repo = Repository(config.data_dir / "metadata.sqlite3")
    repo.set_state("ui_settings", {"top_k": 3})
    assert load_settings(config).top_k == 10
    assert config.domain_profile_path == tmp_path / "profile/story.md"
    assert config.data_dir == tmp_path / "kb"
    assert repo.get_state("ui_settings") == {"top_k": 3}


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost.evil.com",
        "http://127.0.0.1.evil.com",
        "http://localhost@evil.com",
        "ftp://localhost",
        "https://",
    ],
)
def test_llm_base_url_rejects_non_loopback_http(url):
    from rag_workbench.config import Settings

    with pytest.raises(ValueError):
        Settings(_env_file=None, llm_base_url=url)


@pytest.mark.parametrize(
    "url",
    [
        "https://api.example.com/v1",
        "http://localhost:8000/v1",
        "http://127.0.0.1:8000/v1",
        "http://[::1]:8000/v1",
    ],
)
def test_llm_base_url_accepts_https_and_exact_loopback(url):
    from rag_workbench.config import Settings

    assert Settings(_env_file=None, llm_base_url=url).llm_base_url == url


@pytest.mark.parametrize(
    "field",
    [
        "data_dir",
        "model_cache",
        "teaching_profile_path",
        "domain_profile_path",
        "retrieval_expansions_path",
    ],
)
def test_settings_file_rejects_paths_escaping_the_pack(tmp_path, field):
    from rag_workbench.config import load_file_settings

    pack = tmp_path / "pack"
    pack.mkdir()
    path = pack / "settings.json"
    path.write_text(json.dumps({field: "../outside"}), encoding="utf-8")

    with pytest.raises(AppError) as caught:
        load_file_settings(path, env_file=None)
    assert caught.value.code == "invalid_settings_file"


@pytest.mark.parametrize(
    "field",
    [
        "data_dir",
        "model_cache",
        "teaching_profile_path",
        "domain_profile_path",
        "retrieval_expansions_path",
    ],
)
def test_settings_file_rejects_absolute_paths(tmp_path, field):
    from rag_workbench.config import load_file_settings

    pack = tmp_path / "pack"
    pack.mkdir()
    path = pack / "settings.json"
    path.write_text(json.dumps({field: str(tmp_path / "outside")}), encoding="utf-8")

    with pytest.raises(AppError) as caught:
        load_file_settings(path, env_file=None)
    assert caught.value.code == "invalid_settings_file"


def test_embedding_revision_defaults_to_pinned_commit():
    from rag_workbench.config import Settings

    assert Settings(_env_file=None).embedding_revision == "7999e1d3359715c523056ef9478215996d62a620"


def test_embedding_signature_includes_library_version(monkeypatch):
    from rag_workbench.config import Settings
    from rag_workbench.infrastructure import embedding

    settings = Settings(_env_file=None)
    monkeypatch.setattr(embedding, "_library_version", lambda: "one")
    first = embedding.BgeEmbedder(settings).signature
    monkeypatch.setattr(embedding, "_library_version", lambda: "two")
    second = embedding.BgeEmbedder(settings).signature

    assert first != second


def test_collection_is_idempotent_and_retains_live_jobs(monkeypatch):
    from rag_workbench.ui import session_state as ui_state

    result = AnswerResult(
        answer="unsaved",
        mode="general",
        error_code="persistence_error",
        debug=Inspection(question="q"),
    )
    completed = Mock()
    completed.snapshot.return_value = SimpleNamespace(done=True, question="q", result=result)
    live = Mock()
    live.snapshot.return_value = SimpleNamespace(done=False)
    state = {"answer_jobs": {"done": completed, "running": live}}
    monkeypatch.setattr(ui_state.st, "session_state", state)
    assert ui_state.collect_finished_jobs()
    assert not ui_state.collect_finished_jobs()
    assert len(state["unsaved_turns"]["done"]) == 1
    assert state["answer_jobs"] == {"running": live}


def test_unsaved_state_migrates_legacy_tuples_and_reload_instances(monkeypatch):
    from rag_workbench.ui import session_state as ui_state

    result = AnswerResult(answer="body", mode="general", debug=Inspection(question="q"))
    reloaded = SimpleNamespace(
        key=17, question="old", result=result, created_at="2026-10-24T01:00:00+00:00"
    )
    state = {
        "unsaved_turns": {"session": [("legacy", result), reloaded]},
        "unsaved_turn_sequence": 18,
    }
    monkeypatch.setattr(ui_state.st, "session_state", state)
    assert ui_state.collect_finished_jobs()
    assert [turn.question for turn in state["unsaved_turns"]["session"]] == ["legacy", "old"]
    assert state["unsaved_turns"]["session"][1].key == 17
    assert not ui_state.collect_finished_jobs()
