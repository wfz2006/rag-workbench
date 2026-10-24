"""Second external review: isolated workspaces, fake services, no paid requests."""

import json
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from rag_workbench.application.pipeline import RagPipeline
from rag_workbench.application.teaching import contextual_query, teaching_mode
from rag_workbench.bootstrap import build_services, load_settings, save_ui_settings
from rag_workbench.config import Settings
from rag_workbench.domain.errors import AppError
from rag_workbench.domain.models import ChatMessage

from .fakes import FakeEmbedder, FakeProvider
from .test_knowledge import TEXT


@pytest.mark.parametrize(
    "question",
    [
        "罗辑是怎么发现黑暗森林法则的？",
        "古筝行动是怎么实施的？",
        "程心为什么没有按下按钮？",
        "哈希表冲突怎么解决？",
        "快速排序为什么不稳定？",
        "那些文件的格式有哪些？",
        "再生能源是什么？",
    ],
)
def test_explicit_topic_does_not_inherit_context(question):
    history = [ChatMessage(role="user", content="红黑树删除，只给提示")]
    assert contextual_query(question, history) == question
    assert teaching_mode(question, history) == "explain"


def test_pronoun_followup_uses_latest_subject():
    history = [
        ChatMessage(role="user", content=t)
        for t in [
            "叶文洁在红岸基地做了什么？",
            "罗辑是怎么发现黑暗森林法则的？",
        ]
    ]
    assert contextual_query("他后来呢？", history) == history[-1].content + "\n他后来呢？"


def test_parse_failure_isolated_in_batch_and_no_orphans(knowledge):
    result = knowledge.ingest_many(
        [
            ("first.txt", TEXT),
            ("empty.txt", b""),
            ("bad.pdf", b"not pdf"),
            ("last.md", b"valid last document"),
            ("", b"invalid name"),
        ]
    )
    assert [d.status for d in result] == ["ready", "failed", "failed", "ready", "failed"]
    assert len(knowledge.repo.documents()) == 2
    assert len(list((knowledge.root / "originals").iterdir())) == 2
    assert knowledge.retrieve("保管")


def test_rejected_batch_creates_no_metadata_or_raw_files(knowledge):
    result = knowledge.ingest_many([("empty.txt", b""), ("broken.pdf", b"invalid")])
    assert all(d.status == "failed" and d.error_code for d in result)
    assert not knowledge.repo.documents()
    assert not list((knowledge.root / "originals").iterdir())
    assert knowledge.repo.get_state("active") is None


def test_ingest_cannot_drop_broken_ready_document(knowledge):
    existing = knowledge.ingest("existing.txt", TEXT)
    active = knowledge.repo.get_state("active")
    knowledge._raw_path(existing).unlink()
    with pytest.raises(AppError, match="原始文件"):
        knowledge.ingest_many([("new.md", b"valid content")])
    assert knowledge.repo.get_state("active") == active
    assert next(d for d in knowledge.repo.documents() if d.id == existing.id).status == "ready"


def test_content_embedding_error_isolated_to_new_document(settings):
    from rag_workbench.application.knowledge import KnowledgeService

    class PerDocumentFailure(FakeEmbedder):
        def encode_documents(self, texts):
            if any("oversized" in text for text in texts):
                raise AppError("embedding_input_too_long", "test content limit")
            return super().encode_documents(texts)

    service = KnowledgeService(settings, PerDocumentFailure())
    outcome = service.ingest_many([("good.txt", TEXT), ("bad.txt", b"oversized")])
    assert [d.status for d in outcome] == ["ready", "failed"]
    assert all(c.document_id == outcome[0].id for c in service.repo.chunks())


@pytest.mark.parametrize("damage", ["missing", "corrupt", "limit"])
def test_ready_document_rebuild_failure_preserves_snapshot(knowledge, damage):
    document = knowledge.ingest("a.txt", TEXT * 80)
    active = knowledge.repo.get_state("active")
    chunks = knowledge.repo.chunks()
    if damage == "missing":
        knowledge._raw_path(document).unlink()
    elif damage == "corrupt":
        knowledge._raw_path(document).write_bytes(b"damaged")
    else:
        knowledge.settings.max_text_chars = 1000
    with pytest.raises(AppError):
        knowledge.rebuild()
    assert knowledge.repo.get_state("active") == active
    assert knowledge.repo.chunks() == chunks
    assert knowledge.repo.documents()[0].status == "ready"
    assert knowledge.retrieve("保管")


def test_locked_pair_cannot_persist_invalid_dormant_values(settings, monkeypatch):
    services = build_services(settings, embedder=FakeEmbedder(), provider=FakeProvider())
    old = {"chunk_size": 1000, "chunk_overlap": 600}
    services.repo.set_state("ui_settings", old)
    monkeypatch.setenv("RAG_CHUNK_OVERLAP", "50")
    services.settings = load_settings(Settings(_env_file=None, data_dir=settings.data_dir))
    with pytest.raises(AppError, match="保存|分块"):
        save_ui_settings(services, {"chunk_size": 400})
    assert services.repo.get_state("ui_settings") == old


def test_partial_save_survives_recovered_environment_collision(settings, monkeypatch):
    services = build_services(settings, embedder=FakeEmbedder(), provider=FakeProvider())
    services.repo.set_state("ui_settings", {"chunk_size": 300, "chunk_overlap": 250})
    monkeypatch.setenv("RAG_CHUNK_SIZE", "200")
    services.settings = load_settings(Settings(_env_file=None, data_dir=settings.data_dir))
    save_ui_settings(services, {"top_k": 8})
    assert services.repo.get_state("ui_settings") == {
        "chunk_size": 300,
        "chunk_overlap": 250,
        "top_k": 8,
    }
    assert load_settings(Settings(_env_file=None, data_dir=settings.data_dir)).top_k == 8


def test_same_document_neighbor_retains_source_page(knowledge):
    knowledge.settings.top_k = 1
    knowledge.settings.retrieval_context_window = 1
    doc = knowledge.ingest("page.md", TEXT * 10)
    knowledge.assign_sources({doc.id: ("book", 42)})
    results = knowledge.retrieve("保管")
    assert len(results) > 1
    assert all(r.original_page == 42 for r in results)


def test_cross_page_expansion_is_bounded_and_fair(knowledge):
    knowledge.settings.top_k = 2
    knowledge.settings.retrieval_context_window = 1
    first, second, big = knowledge.ingest_many(
        [
            ("first.md", TEXT * 3),
            ("second.md", TEXT * 3 + b" second"),
            ("adjacent.md", b"unrelated neighbor paragraph. " * 150),
        ]
    )
    knowledge.assign_sources({first.id: ("book", 1), second.id: ("book", 3), big.id: ("book", 2)})
    results = knowledge.retrieve("保管")
    adjacent = [r for r in results if r.chunk.document_id == big.id]
    assert 0 < len(adjacent) <= 2
    assert all(
        r.chunk.ordinal
        in (0, len([c for c in knowledge.repo.chunks() if c.document_id == big.id]) - 1)
        for r in adjacent
    )
    for seed in results[:2]:
        assert any(r.context_for == seed.chunk.id for r in results[2:])


@pytest.mark.parametrize("allow_general", [False, True])
def test_withdrawal_updates_final_evidence_decision(knowledge, allow_general):
    knowledge.ingest("a.txt", TEXT)
    knowledge.settings.allow_general = allow_general

    class Withdraw(FakeProvider):
        def complete(self, system, user, **kwargs):
            if kwargs.get("json_mode") and "supporting_chunk_ids" not in system:
                return '{"claims":[]}'
            return super().complete(system, user, **kwargs)

    result = RagPipeline(knowledge, Withdraw()).answer("保管期限？")
    assert not result.debug.evidence.answerable
    assert not result.debug.evidence.supporting_chunk_ids
    assert "生成" in result.debug.evidence.reason


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("unauthorized", [False, True])
def test_inline_citation_cleanup_rejects_only_unauthorized_ids(knowledge, streaming, unauthorized):
    knowledge.ingest("a.txt", TEXT)

    class Inline(FakeProvider):
        def complete(self, system, user, **kwargs):
            result = super().complete(system, user, **kwargs)
            if kwargs.get("json_mode") and "supporting_chunk_ids" not in system:
                payload = json.loads(result)
                identity = "f" * 64 if unauthorized else payload["claims"][0]["chunk_ids"][0]
                payload["claims"][0]["text"] += f"[{identity}][{identity}]"
                return json.dumps(payload)
            return result

    result = RagPipeline(knowledge, Inline()).answer(
        "保管？", on_event=(lambda _: None) if streaming else None
    )
    if unauthorized:
        assert result.error_code == "invalid_citation"
    else:
        assert result.mode == "knowledge_base"
        assert result.answer.count(f"[{result.sources[0].chunk.id}]") == 1


def test_new_conversation_does_not_reuse_unsaved_session(settings, monkeypatch):
    from rag_workbench.ui import shell

    services = build_services(settings, embedder=FakeEmbedder(), provider=FakeProvider())
    unsaved = services.repo.new_session()
    state = {"session_id": unsaved, "unsaved_turns": {unsaved: ("q", object())}}
    monkeypatch.setattr(shell.st, "session_state", state)
    shell.new_conversation(services)
    assert state["session_id"] != unsaved
    assert unsaved in state["unsaved_turns"]
    current = state["session_id"]
    services.repo.save_turn(
        current, "q", RagPipeline(services.knowledge, FakeProvider()).answer("q")
    )
    shell.new_conversation(services)
    assert state["session_id"] not in (unsaved, current)


def test_nested_job_failure_does_not_launch_owned_editor(monkeypatch, tmp_path):
    from rag_workbench import desktop

    popen = Mock(side_effect=PermissionError(5, "denied"))
    message = Mock()
    monkeypatch.setattr(desktop.subprocess, "Popen", popen)
    monkeypatch.setattr(desktop, "_message", message)
    desktop.open_config_file(tmp_path / ".env")
    assert popen.call_count == 1  # ordinary Popen would put unsaved editor in kill-on-close job
    assert message.call_count == 1 and ".env" in message.call_args.args[0]


def test_tcp_table_grows_and_non_listeners_are_rejected(monkeypatch):
    import ctypes
    import socket

    from rag_workbench import desktop

    calls = []

    def api(buffer, size, *_):
        calls.append(buffer)
        size._obj.value = 52
        if len(calls) < 3:
            return 122
        ctypes.c_uint32.from_buffer(buffer).value = 2
        for i, state in enumerate([5, 2]):
            row = (ctypes.c_uint32 * 6).from_buffer(buffer, 4 + i * 24)
            row[0], row[2], row[5] = state, socket.htons(8123 + i), 123
        return 0

    monkeypatch.setattr(desktop.sys, "platform", "win32")
    monkeypatch.setattr(
        desktop.ctypes, "WinDLL", lambda _: SimpleNamespace(GetExtendedTcpTable=api)
    )
    assert not desktop.listener_owned_by(8123, 123)
    calls.clear()
    assert desktop.listener_owned_by(8124, 123)
    assert len(calls) == 3


def test_profile_can_be_disabled_despite_dotenv(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("RAG_TEACHING_PROFILE_PATH=teaching.md\n", encoding="utf-8")
    monkeypatch.setenv("RAG_TEACHING_PROFILE_PATH", "disabled")
    assert Settings(_env_file=env).teaching_profile_path is None


def test_santi_eval_uses_frozen_config_without_shell_exports(tmp_path, monkeypatch):
    from scripts.santi_settings import santi_settings

    monkeypatch.setenv("RAG_CHUNK_OVERLAP", "80")
    monkeypatch.setenv("RAG_TOP_K", "3")
    config = santi_settings(tmp_path, env_file=None)
    assert config.chunk_size == 500 and config.chunk_overlap == 60
    assert config.top_k == 10 and config.similarity_threshold == 0.45
    assert not config.allow_general and config.teaching_profile_path is None
    assert config.embedding_revision == "7999e1d3359715c523056ef9478215996d62a620"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell launcher")
def test_santi_powershell_launcher_uses_same_settings_and_restores_environment(tmp_path):
    import shutil
    import subprocess
    from pathlib import Path

    from scripts.santi_settings import CONFIG_PATH, santi_settings

    root = Path(__file__).resolve().parents[1]
    (tmp_path / "knowledge_packs/santi").mkdir(parents=True)
    shutil.copyfile(CONFIG_PATH, tmp_path / "knowledge_packs/santi/settings.json")
    shutil.copyfile(root / "start-santi.ps1", tmp_path / "start-santi.ps1")
    (tmp_path / "start.ps1").write_text(
        "param([int]$Port)\n"
        "$result = @{Port=$Port}\n"
        "Get-ChildItem Env:RAG_* | ForEach-Object { $result[$_.Name]=$_.Value }\n"
        "$target = Join-Path $PSScriptRoot 'env.json'\n"
        "$result | ConvertTo-Json | Set-Content -Encoding UTF8 $target\n",
        encoding="utf-8",
    )
    (tmp_path / "test.ps1").write_text(
        "$env:RAG_CHUNK_SIZE='555'\n"
        "& (Join-Path $PSScriptRoot 'start-santi.ps1') -Port 8769\n"
        "if ($env:RAG_CHUNK_SIZE -ne '555') { throw 'Environment not restored' }\n",
        encoding="utf-8",
    )
    subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(tmp_path / "test.ps1"),
        ],
        check=True,
        capture_output=True,
        timeout=20,
    )
    actual = json.loads((tmp_path / "env.json").read_text(encoding="utf-8-sig"))
    expected = santi_settings(tmp_path, env_file=None)
    assert actual["RAG_CHUNK_SIZE"] == str(expected.chunk_size)
    assert actual["RAG_CHUNK_OVERLAP"] == str(expected.chunk_overlap)
    assert actual["RAG_TOP_K"] == str(expected.top_k)
    assert actual["RAG_SIMILARITY_THRESHOLD"] == str(expected.similarity_threshold)
    assert actual["RAG_TEACHING_PROFILE_PATH"] == "disabled"
    assert actual["Port"] == 8769
