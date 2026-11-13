import hashlib
import json
import runpy
import zipfile
from pathlib import Path

import pytest

builder = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/build_desktop.py"))
seal_release = builder["seal_release"]


def test_runtime_export_includes_pdf_preview_without_development_dependencies(
    tmp_path, monkeypatch
):
    calls = []
    export = builder["export_requirements"]
    monkeypatch.setitem(export.__globals__, "execute", lambda *args: calls.append(args))
    export("uv", tmp_path / "requirements.txt")
    command = list(calls[0])
    extras = [command[i + 1] for i, value in enumerate(command) if value == "--extra"]
    assert set(extras) == {"desktop", "preview"}
    assert "--locked" in command and "--no-dev" in command
    assert "--no-emit-project" in command


def test_launcher_version_tracks_project_and_keeps_launch_code():
    source = (Path(__file__).resolve().parents[1] / "desktop/Launcher.cs").read_text("utf-8")
    updated = builder["versioned_launcher"](source, "0.1.10")
    assert 'AssemblyVersion("0.1.10.0")' in updated
    assert 'AssemblyFileVersion("0.1.10.0")' in updated
    assert (
        updated.split("static class Launcher", 1)[1] == source.split("static class Launcher", 1)[1]
    )


@pytest.mark.parametrize(
    "private_path", [".env", "knowledge.sqlite3", "desktop.log", "user-data/.env"]
)
def test_release_refuses_private_configuration_and_data(tmp_path, private_path):
    output = tmp_path / "release"
    private_file = output / private_path
    private_file.parent.mkdir(parents=True, exist_ok=True)
    private_file.write_text("must not distribute")
    with pytest.raises(ValueError):
        seal_release(output, archive=True)
    assert not (tmp_path / "release.zip").exists()


def test_release_manifest_and_zip_preserve_full_version_name(tmp_path):
    output = tmp_path / "RAG-Workbench-0.1.0-win-x64"
    output.mkdir()
    (output / "public.txt").write_text("public runtime")
    seal_release(output, archive=True)
    manifest = json.loads((output / "SHA256SUMS.json").read_text())
    assert manifest == {"public.txt": hashlib.sha256(b"public runtime").hexdigest()}
    with zipfile.ZipFile(tmp_path / (output.name + ".zip")) as release:
        assert release.testzip() is None
        assert f"{output.name}/public.txt" in release.namelist()
        assert f"{output.name}/SHA256SUMS.json" in release.namelist()
