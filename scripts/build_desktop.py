"""Build a self-contained, relocatable Windows x64 folder. Never copy private profiles."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def execute(*args: str | Path) -> None:
    subprocess.run([str(arg) for arg in args], cwd=ROOT, check=True)


def seal_release(output: Path, *, archive: bool) -> None:
    """Refuse an in-use/private profile, then inventory the pristine public release."""
    if (output / "user-data").exists() or (output / "data").exists():
        raise ValueError("Never distribute a used desktop profile or development data.")
    paths = [path for path in sorted(output.rglob("*")) if path.is_file()]
    if any(path.name == ".env" or path.suffix in {".sqlite3", ".log"} for path in paths):
        raise ValueError("Private configuration, database or logs found in release.")
    hashes = {
        path.relative_to(output).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in paths
        if path.name != "SHA256SUMS.json"
    }
    (output / "SHA256SUMS.json").write_text(json.dumps(hashes, indent=2), encoding="utf-8")
    if archive:
        target = output.parent / (output.name + ".zip")
        with zipfile.ZipFile(target, "x", zipfile.ZIP_DEFLATED, compresslevel=5) as zip_file:
            for path in output.rglob("*"):
                if path.is_file():
                    zip_file.write(path, Path(output.name) / path.relative_to(output))
        print(f"Archive: {target} ({target.stat().st_size / 1024**2:.1f} MiB)")


def build(output: Path, *, include_model: bool, archive: bool) -> None:
    if sys.platform != "win32" or sys.version_info[:2] != (3, 12):
        raise SystemExit("Use the project's Windows x64 Python 3.12 environment.")
    uv = shutil.which("uv")
    if not uv:
        raise SystemExit("uv is required to export and install the locked runtime dependencies.")
    compiler = Path(os.environ["WINDIR"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    if not compiler.is_file():
        raise SystemExit("Windows .NET Framework 4.x compiler not found.")
    base = Path(sys.base_prefix)
    if not (base / "BUILD").is_file():
        raise SystemExit("Use uv's self-contained Python 3.12, not a copied virtual environment.")
    output.mkdir(parents=True, exist_ok=False)
    runtime = output / "runtime"
    print("Copying standalone Python...", flush=True)

    def exclude(directory: str, entries: list[str]) -> list[str]:
        return [
            name
            for name in entries
            if name
            in {
                "__pycache__",
                "site-packages",
                "Scripts",
                "include",
                "libs",
                "tcl",
                "EXTERNALLY-MANAGED",
            }
        ]

    shutil.copytree(base, runtime, ignore=exclude)
    # These paths are relative to the relocated runtime. Ignore registry/PYTHONPATH/user site.
    (runtime / "python312._pth").write_text(
        ".\nLib\nDLLs\nLib/site-packages\nimport site\n", encoding="utf-8"
    )
    work = ROOT / "artifacts" / f"desktop-build-{time.time_ns()}"
    work.mkdir(parents=True)
    requirements = work / "requirements.txt"
    execute(
        uv,
        "export",
        "--quiet",
        "--no-header",
        "--locked",
        "--extra",
        "desktop",
        "--no-dev",
        "--no-emit-project",
        "--output-file",
        requirements,
    )
    shutil.copyfile(requirements, output / "requirements.lock.txt")
    execute(
        uv,
        "pip",
        "install",
        "--python",
        runtime / "python.exe",
        "--link-mode",
        "copy",
        "--require-hashes",
        "-r",
        requirements,
    )
    execute(uv, "build", "--wheel", "--out-dir", work)
    wheel = next(work.glob("*.whl"))
    execute(
        uv,
        "pip",
        "install",
        "--python",
        runtime / "python.exe",
        "--link-mode",
        "copy",
        "--no-deps",
        wheel,
    )
    resources = output / "resources"
    resources.mkdir()
    shutil.copyfile(ROOT / "app.py", resources / "app.py")
    shutil.copyfile(ROOT / ".streamlit/config.toml", resources / "config.toml")
    for name in ("README.md", "ARCHITECTURE.md", "THIRD_PARTY_NOTICES.md", ".env.example"):
        shutil.copyfile(ROOT / name, resources / name)
    shutil.copytree(ROOT / "licenses", resources / "licenses")
    shutil.copyfile(ROOT / "docs/DESKTOP.md", output / "使用说明.md")
    shutil.copyfile(ROOT / "docs/REVIEW_ROUND9_2026-10-24.md", output / "修复说明.md")
    shutil.copyfile(ROOT / "docs/ICON_PROMPT.txt", resources / "ICON_PROMPT.txt")
    icon = ROOT / "src/rag_workbench/ui/assets/brand/workbench.ico"
    shutil.copyfile(icon, output / "workbench.ico")
    if include_model:
        model = "models--BAAI--bge-small-zh-v1.5"
        source = ROOT / ".cache/models" / model
        if not list(source.glob("snapshots/*/model.safetensors")):
            raise SystemExit(
                "BGE model cache is missing; omit --include-model or download it first."
            )
        shutil.copytree(source, output / "models" / model, ignore=shutil.ignore_patterns("*.lock"))
    execute(
        compiler,
        "/nologo",
        "/target:winexe",
        "/platform:x64",
        "/utf8output",
        "/reference:System.Windows.Forms.dll",
        f"/win32icon:{icon}",
        f"/win32manifest:{ROOT / 'desktop/app.manifest'}",
        f"/out:{output / 'RAG Workbench.exe'}",
        ROOT / "desktop/Launcher.cs",
    )
    # Export package provenance and installed license metadata, without relying on build paths.
    execute(
        runtime / "python.exe",
        "-I",
        "-B",
        "-c",
        "import importlib.metadata as m,json,pathlib; "
        "p=[{'name':d.metadata['Name'],'version':d.version,"
        "'license':d.metadata.get('License-Expression') or d.metadata.get('License',''),"
        "'license_files':[str(f) for f in d.files or [] if 'license' in str(f).lower()]} "
        "for d in m.distributions()]; "
        "import sys; pathlib.Path(sys.argv[1]).write_text("
        "json.dumps(p,ensure_ascii=False,indent=2),encoding='utf-8')",
        output / "THIRD_PARTY_MANIFEST.json",
    )
    print("Checking isolated runtime imports...", flush=True)
    execute(
        runtime / "python.exe",
        "-I",
        "-B",
        "-c",
        "import streamlit,webview,torch,faiss,sentence_transformers,rag_workbench; "
        "print('Standalone runtime imports OK')",
    )
    seal_release(output, archive=archive)
    print(f"Desktop ready: {output / 'RAG Workbench.exe'}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist/RAG-Workbench-0.1.9-win-x64")
    parser.add_argument("--include-model", action="store_true")
    parser.add_argument("--zip", action="store_true")
    args = parser.parse_args()
    build(args.output.resolve(), include_model=args.include_model, archive=args.zip)


if __name__ == "__main__":
    main()
