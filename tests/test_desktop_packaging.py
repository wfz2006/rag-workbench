import hashlib
import json
import runpy
import zipfile
from pathlib import Path

import pytest

seal_release = runpy.run_path(
    str(Path(__file__).resolve().parents[1] / "scripts/build_desktop.py")
)["seal_release"]


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
