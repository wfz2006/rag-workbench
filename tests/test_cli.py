import pytest

from rag_workbench.cli import main, sample_workspace
from rag_workbench.domain.errors import AppError


def test_sample_clear_requires_marker_and_never_touches_production(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sandbox = tmp_path / "data" / "sandbox"
    sandbox.mkdir(parents=True)
    production = tmp_path / "data" / "production"
    production.mkdir()
    important = production / "private.txt"
    important.write_text("preserve")
    with pytest.raises(AppError):
        sample_workspace(clear=True)
    (sandbox / ".rag-sandbox").write_text("marker")
    sample_workspace(clear=True)
    assert important.read_text() == "preserve"
    assert not sandbox.exists()


def test_unmarked_existing_sandbox_never_adopted_as_disposable(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "examples").mkdir()
    sandbox = tmp_path / "data" / "sandbox"
    sandbox.mkdir(parents=True)
    (sandbox / "private.txt").write_text("not a sample")
    with pytest.raises(AppError):
        sample_workspace(clear=False)
    assert not (sandbox / ".rag-sandbox").exists()


def test_cli_stats_and_invalid_question_exit_codes(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    monkeypatch.setattr("sys.argv", ["rag", "--data-dir", str(tmp_path), "stats"])
    assert main() == 0
    assert '"index_status": "empty"' in capsys.readouterr().out
    monkeypatch.setattr("sys.argv", ["rag", "--data-dir", str(tmp_path), "ask", ""])
    assert main() == 1


def test_empty_generation_damage_is_reported(knowledge):
    knowledge.rebuild()
    active = knowledge.repo.get_state("active")
    (knowledge.root / "indexes" / active["generation"] / "index.faiss").write_bytes(b"bad")
    assert knowledge.stats()["index_status"] == "corrupt"
    with pytest.raises(AppError):
        knowledge.retrieve("问题")


def test_cli_works_with_ascii_stdout_environment(tmp_path):
    import os
    import subprocess
    import sys

    environment = {**os.environ, "PYTHONIOENCODING": "ascii", "DEEPSEEK_API_KEY": ""}
    completed = subprocess.run(
        [sys.executable, "-m", "rag_workbench.cli", "--data-dir", str(tmp_path), "ask", "hi"],
        env=environment,
        capture_output=True,
        timeout=30,
    )
    assert completed.returncode == 1
    assert "DEEPSEEK_API_KEY" in completed.stdout.decode("utf-8")
    assert b"UnicodeEncodeError" not in completed.stderr


def test_reset_settings_recovers_invalid_saved_config(settings, monkeypatch):
    from rag_workbench.infrastructure.repository import Repository

    repo = Repository(settings.data_dir / "metadata.sqlite3")
    repo.set_state("ui_settings", {"chunk_size": 100, "chunk_overlap": 500})
    monkeypatch.setattr("sys.argv", ["rag", "--data-dir", str(settings.data_dir), "reset-settings"])
    assert main() == 0
    assert repo.get_state("ui_settings") == {}
