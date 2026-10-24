"""Read official PyPI metadata; does not install packages."""

import json
import urllib.request

for name in (
    "streamlit",
    "openai",
    "sentence-transformers",
    "faiss-cpu",
    "numpy",
    "pydantic",
    "pydantic-settings",
    "pypdf",
    "pytest",
    "ruff",
    "filelock",
    "reportlab",
):
    with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/json", timeout=20) as response:
        info = json.load(response)["info"]
    print(name, info["version"], info["requires_python"])
