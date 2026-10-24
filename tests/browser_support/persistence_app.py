"""Explicit browser test fixture. Never imported by the normal or desktop entrypoint."""

import streamlit as st

from rag_workbench.bootstrap import build_services
from rag_workbench.ui import app
from tests.fakes import FakeEmbedder, FakeProvider


@st.cache_resource
def resources(cache_key, _settings):
    services = build_services(_settings, embedder=FakeEmbedder(), provider=FakeProvider())

    def disk_failure(*_):
        raise OSError("test persistence failure")

    services.repo.save_turn = disk_failure
    return services


app.resources = resources
app.main()
