import json

import pytest

from rag_workbench.application.packs import PackBuilder, PackSpec
from rag_workbench.cli import main, sample_workspace
from rag_workbench.config import Settings
from rag_workbench.domain.errors import AppError

from .fakes import FakeEmbedder


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


def test_cli_lists_and_lints_knowledge_packs(tmp_path, monkeypatch, capsys):
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path / "active",
        packs_dir=tmp_path / "packs",
        packs_data_dir=tmp_path / "data",
        chunk_size=100,
        chunk_overlap=10,
    )
    PackBuilder(settings, FakeEmbedder()).build(
        PackSpec(
            pack_id="demo-pack",
            title="示例",
            license_status="internal_only",
            chunk_size=100,
            chunk_overlap=10,
            similarity_threshold=0.0,
        ),
        [("a.txt", "保管期限为三年。".encode())],
    )
    monkeypatch.setenv("RAG_PACKS_DIR", str(settings.packs_dir))
    monkeypatch.setenv("RAG_PACKS_DATA_DIR", str(settings.packs_data_dir))

    monkeypatch.setattr("sys.argv", ["rag", "pack-list"])
    assert main() == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed["packs"][0]["pack_id"] == "demo-pack"

    monkeypatch.setattr("sys.argv", ["rag", "pack-lint", "demo-pack"])
    assert main() == 0
    lint = json.loads(capsys.readouterr().out)
    assert lint["pack_id"] == "demo-pack"
    assert not [check for check in lint["checks"] if check["status"] == "fail"]

    manifest = next(
        json.loads(line)
        for line in (settings.packs_dir / "demo-pack" / "generated_manifest.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    )
    (settings.packs_dir / "demo-pack" / manifest["path"]).write_text(
        "被篡改。", encoding="utf-8"
    )
    monkeypatch.setattr("sys.argv", ["rag", "pack-lint", "demo-pack"])
    assert main() == 1
    lint = json.loads(capsys.readouterr().out)
    assert any(check["status"] == "fail" for check in lint["checks"])


def test_cli_pack_flag_uses_pack_settings_and_data(tmp_path, monkeypatch, capsys):
    from rag_workbench.infrastructure.embedding import BgeEmbedder

    settings = Settings(
        _env_file=None,
        data_dir=tmp_path / "active",
        packs_dir=tmp_path / "packs",
        packs_data_dir=tmp_path / "data",
        chunk_size=100,
        chunk_overlap=10,
        similarity_threshold=0.0,
    )

    class SignedFakeEmbedder(FakeEmbedder):
        def __init__(self):
            self.signature = BgeEmbedder(settings).signature

    PackBuilder(settings, SignedFakeEmbedder()).build(
        PackSpec(
            pack_id="demo-pack",
            title="示例",
            license_status="internal_only",
            chunk_size=100,
            chunk_overlap=10,
            similarity_threshold=0.0,
        ),
        [("a.txt", "保管期限为三年。".encode())],
    )
    monkeypatch.setenv("RAG_PACKS_DIR", str(settings.packs_dir))
    monkeypatch.setenv("RAG_PACKS_DATA_DIR", str(settings.packs_data_dir))
    monkeypatch.setattr("sys.argv", ["rag", "--pack", "demo-pack", "stats"])
    assert main() == 0
    stats = json.loads(capsys.readouterr().out)
    assert stats["documents"] == 1
    assert stats["index_status"] == "ready"


def test_cli_pack_eval_requires_holdout_and_cost_confirmation(
    tmp_path, monkeypatch, capsys
):
    from .test_pack_studio import prepare_frozen

    settings, _result, _pack_settings, _knowledge, _candidates, _freeze = prepare_frozen(
        tmp_path
    )
    monkeypatch.setenv("RAG_PACKS_DIR", str(settings.packs_dir))
    monkeypatch.setenv("RAG_PACKS_DATA_DIR", str(settings.packs_data_dir))
    monkeypatch.setattr(
        "sys.argv",
        ["rag", "--pack", "studio-pack", "pack-eval", "--split", "holdout"],
    )
    assert main() == 1
    assert "holdout_confirmation_required" in capsys.readouterr().err

    monkeypatch.setattr(
        "sys.argv",
        [
            "rag",
            "--pack",
            "studio-pack",
            "pack-eval",
            "--split",
            "holdout",
            "--online",
            "--confirm-holdout",
        ],
    )
    assert main() == 1
    assert "cost_confirmation_required" in capsys.readouterr().err


def test_reset_settings_recovers_invalid_saved_config(settings, monkeypatch):
    from rag_workbench.infrastructure.repository import Repository

    repo = Repository(settings.data_dir / "metadata.sqlite3")
    repo.set_state("ui_settings", {"chunk_size": 100, "chunk_overlap": 500})
    monkeypatch.setattr("sys.argv", ["rag", "--data-dir", str(settings.data_dir), "reset-settings"])
    assert main() == 0
    assert repo.get_state("ui_settings") == {}
