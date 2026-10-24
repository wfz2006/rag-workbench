import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

from rag_workbench.desktop import (
    DesktopError,
    available_port,
    prepare_profile,
    run,
    server_environment,
    stop_server,
    wait_for_server,
)


def test_second_instance_cannot_modify_a_locked_profile(tmp_path):
    pytest.importorskip("webview")
    from filelock import FileLock

    with FileLock(tmp_path / "desktop.lock"), pytest.raises(DesktopError, match="另一个窗口"):
        run(tmp_path / "nonexistent-bundle", tmp_path)
    assert not (tmp_path / ".env").exists()


def test_profile_never_copies_developer_secrets_or_documents(tmp_path):
    bundle = tmp_path / "bundle"
    (bundle / "resources").mkdir(parents=True)
    (bundle / "resources/config.toml").write_text("[server]\nheadless=true\n")
    (bundle / ".env").write_text("DEEPSEEK_API_KEY=private-secret")
    (bundle / "data").mkdir()
    (bundle / "data/private.txt").write_text("private knowledge")
    profile = tmp_path / "profile"
    prepare_profile(bundle, profile)
    assert "private-secret" not in (profile / ".env").read_text()
    assert not (profile / "data/private.txt").exists()
    (profile / ".env").write_text("DEEPSEEK_API_KEY=user-key")
    (profile / ".streamlit/config.toml").write_text("# user configuration")
    prepare_profile(bundle, profile)
    assert (profile / ".env").read_text() == "DEEPSEEK_API_KEY=user-key"
    assert (profile / ".streamlit/config.toml").read_text() == "# user configuration"


def test_profile_model_seed_is_copied_once(tmp_path):
    bundle = tmp_path / "bundle"
    (bundle / "resources").mkdir(parents=True)
    (bundle / "resources/config.toml").write_text("")
    model = "models--BAAI--bge-small-zh-v1.5"
    source = bundle / "models" / model
    source.mkdir(parents=True)
    (source / "public-model").write_text("weights")
    profile = tmp_path / "user-data"
    previous_staging = profile / "models" / f"{model}.copying"
    previous_staging.mkdir(parents=True)
    (previous_staging / "retained").write_text("preserve interrupted copies")
    prepare_profile(bundle, profile)
    assert (previous_staging / "retained").exists()
    assert (profile / "models" / model / "public-model").read_text() == "weights"
    (profile / "models" / model / "public-model").write_text("user-model")
    prepare_profile(bundle, profile)
    assert (profile / "models" / model / "public-model").read_text() == "user-model"


def test_desktop_ignores_shell_credentials_and_project_profile(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "do-not-inherit")
    monkeypatch.setenv("RAG_DATA_DIR", "private-corpus")
    monkeypatch.setenv("STREAMLIT_SERVER_ADDRESS", "0.0.0.0")
    monkeypatch.setenv("PYTHONPATH", "developer-source")
    env = server_environment()
    assert "DEEPSEEK_API_KEY" not in env
    assert "RAG_DATA_DIR" not in env
    assert "PYTHONPATH" not in env
    assert env["STREAMLIT_SERVER_ADDRESS"] == "127.0.0.1"


def test_health_probe_fails_fast_for_dead_process():
    process = Mock()
    process.poll.return_value = 1
    with pytest.raises(RuntimeError, match="本地服务"):
        wait_for_server(process, available_port(), timeout=1)


def test_health_probe_times_out_when_service_never_listens():
    process = Mock()
    process.poll.return_value = None
    with pytest.raises(TimeoutError):
        wait_for_server(process, available_port(), timeout=0.2)


def test_shutdown_terminates_real_child_process():
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    stop_server(process)
    assert process.poll() is not None
    stop_server(process)


def test_manifest_and_icon_are_real_assets():
    from PIL import Image

    root = Path(__file__).resolve().parents[1]
    with Image.open(root / "src/rag_workbench/ui/assets/brand/workbench.ico") as icon:
        assert (16, 16) in icon.ico.sizes()
        assert (256, 256) in icon.ico.sizes()
