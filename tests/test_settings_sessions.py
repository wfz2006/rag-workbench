import sqlite3

import pytest

from rag_workbench.bootstrap import build_services, load_settings, save_ui_settings
from rag_workbench.config import Settings
from rag_workbench.infrastructure.repository import Repository

from .fakes import FakeEmbedder, FakeProvider


def test_settings_persist_without_secret_and_validate(settings):
    services = build_services(settings, embedder=FakeEmbedder(), provider=FakeProvider())
    save_ui_settings(services, {"top_k": 8, "chunk_size": 120})
    loaded = load_settings(settings)
    assert loaded.top_k == 8
    assert loaded.chunk_size == 120
    assert "api_key" not in services.repo.get_state("ui_settings")


def test_v1_database_migrates_sessions_and_epoch(tmp_path):
    path = tmp_path / "metadata.sqlite3"
    db = sqlite3.connect(path)
    db.execute(
        "CREATE TABLE sessions (id TEXT PRIMARY KEY, title TEXT NOT NULL, created_at TEXT NOT NULL)"
    )
    db.execute(
        "INSERT INTO sessions VALUES ('s', '旧会话', '2026-01-01T00:00:00+00:00')"
    )
    db.execute("PRAGMA user_version=1")
    db.commit()
    db.close()

    repo = Repository(path)

    assert repo.sessions()[0]["updated_at"] == "2026-01-01T00:00:00+00:00"
    assert repo.get_state("knowledge_epoch")


def test_environment_overrides_saved_settings(settings, monkeypatch):
    services = build_services(settings, embedder=FakeEmbedder(), provider=FakeProvider())
    save_ui_settings(services, {"top_k": 8})
    monkeypatch.setenv("RAG_TOP_K", "3")
    loaded = load_settings(Settings(_env_file=None, data_dir=settings.data_dir))
    assert loaded.top_k == 3


def test_conversation_survives_restart_and_contains_snapshots(settings):
    services = build_services(settings, embedder=FakeEmbedder(), provider=FakeProvider())
    session = services.repo.new_session()
    services.ask(session, "简单介绍一下你自己")
    restarted = build_services(settings, embedder=FakeEmbedder(), provider=FakeProvider())
    messages = restarted.repo.messages(session)
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert '"mode":"general"' in messages[1]["result"]


def test_environment_combination_validated_before_persist(settings, monkeypatch):
    monkeypatch.setenv("RAG_CHUNK_SIZE", "400")
    current = Settings(_env_file=None, data_dir=settings.data_dir)
    services = build_services(current, embedder=FakeEmbedder(), provider=FakeProvider())
    with pytest.raises(ValueError):
        save_ui_settings(services, {"chunk_size": 1000, "chunk_overlap": 500})
    assert load_settings(current).chunk_size == 400
    assert services.repo.get_state("ui_settings") is None


def test_lowercase_dotenv_keys_take_precedence(settings, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("rag_top_k=3\n", encoding="utf-8")
    services = build_services(settings, embedder=FakeEmbedder(), provider=FakeProvider())
    services.repo.set_state("ui_settings", {"top_k": 8})
    assert load_settings(Settings(data_dir=settings.data_dir)).top_k == 3


def test_large_answer_does_not_break_followup(settings):
    from rag_workbench.domain.models import AnswerResult, Inspection

    services = build_services(settings, embedder=FakeEmbedder(), provider=FakeProvider())
    session = services.repo.new_session()
    services.repo.save_turn(
        session,
        "长回答请求",
        AnswerResult(answer="字" * 30001, mode="general", debug=Inspection(question="长回答请求")),
    )
    assert services.ask(session, "继续聊一个问题").mode == "general"
    assert len(services.repo.messages(session)) == 4
