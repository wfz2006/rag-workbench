"""Precompile the packaged desktop shell, server and home page before sealing the release."""

import py_compile
import sys
from importlib import import_module
from pathlib import Path


def main() -> None:
    runtime = Path(sys.executable).resolve().parent
    if not (runtime / "python312._pth").is_file():
        raise SystemExit("Run with the staged Windows desktop runtime.")

    for name in (
        "rag_workbench.desktop", "webview.menu", "streamlit.web.cli", "rag_workbench.ui.app",
    ):
        import_module(name)

    lib = runtime / "Lib"
    sources = set()
    for module in tuple(sys.modules.values()):
        origin = getattr(getattr(module, "__spec__", None), "origin", None)
        if isinstance(origin, str) and origin.endswith(".py"):
            path = Path(origin).resolve()
            if path.is_relative_to(lib):
                sources.add(path)

    for path in sorted(sources):
        # Source hashes survive relocation and installer timestamp rounding. A changed
        # source file invalidates its bytecode even though the launcher uses -B.
        py_compile.compile(
            str(path), dfile=path.relative_to(runtime).as_posix(), doraise=True,
            invalidation_mode=py_compile.PycInvalidationMode.CHECKED_HASH,
        )
    print(f"Precompiled {len(sources)} desktop startup modules.")


if __name__ == "__main__":
    main()
