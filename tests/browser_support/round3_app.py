"""Test-only failure/recovery UI. Never loaded by application or desktop entrypoints."""

import time

import streamlit as st

from rag_workbench.bootstrap import build_services
from rag_workbench.domain.errors import AppError
from rag_workbench.ui import app
from tests.fakes import FakeEmbedder, FakeProvider


@st.cache_resource
def resources(key, _settings):
    class ControlledProvider(FakeProvider):
        def complete(self, system, user, **kwargs):
            if not kwargs.get("json_mode"):
                return "验收回答：" + user
            return super().complete(system, user, **kwargs)

        def stream(self, system, user, **kwargs):
            if user.startswith("等待后台"):
                deadline = time.monotonic() + 20
                while not (_settings.data_dir / ".release-job").exists():
                    kwargs["cancellation"].raise_if_cancelled()
                    if time.monotonic() > deadline:
                        raise AppError("test_timeout", "测试控制未释放")
                    time.sleep(0.05)
            yield from super().stream(system, user, **kwargs)

    services = build_services(_settings, embedder=FakeEmbedder(), provider=ControlledProvider())
    (_settings.data_dir / ".review-fixture").write_text("round3 browser fixture")
    save = services.repo.save_turn

    def controlled_save(session_id, question, result):
        if question.startswith(("未保存", "等待后台")):
            raise OSError("injected persistence failure")
        save(session_id, question, result)

    services.repo.save_turn = controlled_save
    return services


app.resources = resources
app.main()
st.html(
    '<span hidden id="qa-jobs" data-count="'
    + str(len(st.session_state.get("answer_jobs", {})))
    + '"></span>'
)
