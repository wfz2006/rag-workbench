import base64
import shutil
import subprocess
import sys
from pathlib import Path


def test_trial_tutorial_uses_project_version_and_embeds_screenshots(tmp_path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    root = Path(__file__).resolve().parents[1]
    script = scripts / "build_trial_tutorial.py"
    shutil.copyfile(root / "scripts" / script.name, script)
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.8.4"\n', encoding="utf-8")
    pixel = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO"
        "+jMuoAAAAASUVORK5CYII="
    )
    for name in (
        "materials-review-20261102/01-review-desktop.png",
        "materials-review-20261102/08-local-preview.png",
        "desktop-trial-20261102/materials-profile/example-page-1.png",
    ):
        screenshot = tmp_path / "artifacts" / name
        screenshot.parent.mkdir(parents=True, exist_ok=True)
        screenshot.write_bytes(pixel)
    output_dir = tmp_path / "dist"

    subprocess.run([sys.executable, script], cwd=tmp_path, check=True, capture_output=True)

    html = (output_dir / "RAG-Workbench-试用操作教程.html").read_text(encoding="utf-8")
    assert html.count("0.8.4") == 3
    assert "RAG-Workbench-Trial-0.8.4-win-x64.zip" in html
    assert html.count('src="data:image/png;base64,') == 3
