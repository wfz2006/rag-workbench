"""Explicit isolated UI acceptance fixture: no paid API and no production workspace."""

import json
import os
import time
from pathlib import Path

import streamlit as st

from rag_workbench.bootstrap import build_services, load_settings
from rag_workbench.config import Settings
from rag_workbench.domain.errors import AppError
from rag_workbench.ui import app, pages
from tests.fakes import FakeEmbedder, FakeProvider

ROOT = Path(__file__).resolve().parents[2] / "artifacts"
WORKSPACE = Path(os.environ["RAG_DATA_DIR"]).resolve()
assert WORKSPACE.is_relative_to(ROOT.resolve())


class Provider(FakeProvider):
    def complete(self, system, user, **kwargs):
        if not (WORKSPACE / ".api-enabled").exists():
            raise AppError(
                "missing_api_key", "尚未配置 DEEPSEEK_API_KEY。请配置后刷新页面，再重试问题。"
            )
        output = super().complete(system, user, **kwargs)
        if "claims" in system:
            data = json.loads(output)
            identity = data["claims"][0]["chunk_ids"][0]
            return json.dumps(
                {
                    "claims": [
                        {"text": "记录的保管期限为三年。", "chunk_ids": [identity]},
                        {"text": "到期后应进行复核。", "chunk_ids": [identity]},
                    ]
                }
            )
        return output

    def stream(self, system, user, **kwargs):
        question = json.loads(user)["question"] if kwargs.get("json_mode") else user
        if question == "等待生成中":
            yield "正在等待测试控制释放。"
            _wait(".release-generate", kwargs["cancellation"])
        yield from super().stream(system, user, **kwargs)


def _wait(name, token=None):
    deadline = time.monotonic() + 30
    while not (WORKSPACE / name).exists():
        if token:
            token.raise_if_cancelled()
        if time.monotonic() > deadline:
            raise AppError("test_timeout", "测试控制未释放")
        time.sleep(0.05)


def build(config):
    services = build_services(config, embedder=FakeEmbedder(), provider=Provider())
    save = services.repo.save_turn

    def controlled_save(session_id, question, result):
        if "未保存" in question:
            raise OSError("explicit test-only persistence failure")
        if "慢保存" in question:
            _wait(".release-save")
        save(session_id, question, result)

    services.repo.save_turn = controlled_save
    return services


@st.cache_resource
def resources(key, _settings):
    services = build(_settings)
    (WORKSPACE / ".review-fixture").write_text("round4")
    return services


app.load_settings = lambda: load_settings(Settings(_env_file=None, data_dir=WORKSPACE, api_key=""))
app.resources = resources
pages.build_services = build
app.main()
