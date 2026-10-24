"""Explicit simulated provider and persistence faults, isolated from production entrypoints."""

import json
import os
import time
from pathlib import Path

import streamlit as st

from rag_workbench.bootstrap import build_services, load_settings
from rag_workbench.config import Settings
from rag_workbench.domain.errors import AppError
from rag_workbench.ui import app
from tests.fakes import FakeEmbedder, FakeProvider

WORKSPACE = Path(os.environ["RAG_DATA_DIR"]).resolve()
assert WORKSPACE.is_relative_to(Path(__file__).resolve().parents[2] / "artifacts")


class Provider(FakeProvider):
    def stream(self, system, user, **kwargs):
        question = json.loads(user)["question"] if kwargs.get("json_mode") else user
        if question.startswith("故障"):
            if not (WORKSPACE / ".allow-retry").exists():
                raise AppError("test_failure", "明确注入的测试故障。")
            deadline = time.monotonic() + 40
            while not (WORKSPACE / ".release-retry").exists():
                kwargs["cancellation"].raise_if_cancelled()
                if time.monotonic() > deadline:
                    raise AppError("test_timeout", "测试未释放")
                time.sleep(0.05)
        yield from super().stream(system, user, **kwargs)

    def complete(self, system, user, **kwargs):
        if kwargs.get("json_mode"):
            entries = json.loads(user)["evidence"]
            ids = [e["chunk_id"] for e in entries[:2]]
            if "supporting_chunk_ids" in system:
                return json.dumps(
                    {
                        "answerable": True,
                        "reason": "测试资料包含信息。",
                        "supporting_chunk_ids": ids,
                    }
                )
            return json.dumps(
                {
                    "claims": [
                        {"text": "```python\nx=1", "chunk_ids": [ids[0]]},
                        {"text": "独立段落\\", "chunk_ids": [ids[-1]]},
                        {
                            "text": "参见[说明][1]\n\n[1]: https://example.com",
                            "chunk_ids": [ids[0]],
                        },
                        {"text": "最后的普通正文。", "chunk_ids": [ids[-1]]},
                    ]
                }
            )
        return "已回答：" + user


@st.cache_resource
def resources(key, _settings):
    services = build_services(_settings, embedder=FakeEmbedder(), provider=Provider())
    (WORKSPACE / ".review-fixture").write_text("round6")
    replace = services.repo.replace_failed_turn

    def controlled_replace(*args):
        if (WORKSPACE / ".fail-retry-save").exists():
            raise OSError("explicit simulated failure")
        return replace(*args)

    services.repo.replace_failed_turn = controlled_replace
    return services


app.load_settings = lambda: load_settings(Settings(_env_file=None, data_dir=WORKSPACE, api_key=""))
app.resources = resources
app.main()
