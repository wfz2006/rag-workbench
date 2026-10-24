"""Isolated review fixture; explicit simulated API, never shipped in the desktop wheel."""

import json
import os
from pathlib import Path

import streamlit as st

from rag_workbench.bootstrap import build_services, load_settings
from rag_workbench.config import Settings
from rag_workbench.domain.errors import AppError
from rag_workbench.ui import app
from tests.fakes import FakeEmbedder, FakeProvider

ROOT = Path(__file__).resolve().parents[2] / "artifacts"
WORKSPACE = Path(os.environ["RAG_DATA_DIR"]).resolve()
assert WORKSPACE.is_relative_to(ROOT.resolve())


class Provider(FakeProvider):
    def complete(self, system, user, **kwargs):
        if not (WORKSPACE / ".api-enabled").exists():
            raise AppError("missing_api_key", "测试 API 未启用；这是显式隔离验收。")
        if kwargs.get("json_mode"):
            evidence = json.loads(user)["evidence"]
            ids = [entry["chunk_id"] for entry in evidence][:2]
            if "supporting_chunk_ids" in system:
                return json.dumps(
                    {
                        "answerable": True,
                        "reason": "测试资料包含对应字段。",
                        "supporting_chunk_ids": ids,
                    }
                )
            first, second = ids[-1], ids[0]
            return json.dumps(
                {
                    "claims": [
                        {"text": "```cpp\nvector<int> xs;\n```", "chunk_ids": [first]},
                        {"text": "独立正文应在代码框之外。", "chunk_ids": [second]},
                        {"text": "| 字段 | 值 |\n| --- | --- |\n| a | b |", "chunk_ids": [second]},
                    ]
                }
            )
        return "这是模拟通用正文。"


@st.cache_resource
def resources(key, _settings):
    services = build_services(_settings, embedder=FakeEmbedder(), provider=Provider())
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    (WORKSPACE / ".review-fixture").write_text("round5")
    with (WORKSPACE / "resource-builds.txt").open("a") as stream:
        stream.write(key + "\n")
    return services


app.load_settings = lambda: load_settings(Settings(_env_file=None, data_dir=WORKSPACE, api_key=""))
app.resources = resources
app.main()
