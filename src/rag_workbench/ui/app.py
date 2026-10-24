import hashlib
from pathlib import Path

import streamlit as st

from rag_workbench.bootstrap import Services, build_services, environment_owned_keys, load_settings
from rag_workbench.config import Settings
from rag_workbench.domain.errors import AppError
from rag_workbench.ui.chat import chat_page
from rag_workbench.ui.pages import debug_page, knowledge_page, settings_page
from rag_workbench.ui.session_state import collect_finished_jobs, monitor_answer_jobs
from rag_workbench.ui.shell import inject_styles, install_interactions, sidebar


@st.cache_resource(show_spinner=False)
def resources(cache_key: str, _settings: Settings) -> Services:
    return build_services(_settings)


def main() -> None:
    st.set_page_config(
        page_title="RAG Workbench",
        page_icon=str(Path(__file__).parent / "assets" / "brand" / "workbench.png"),
        layout="wide",
        initial_sidebar_state="auto",
    )
    inject_styles()
    try:
        settings = load_settings()
        for warning in settings.configuration_warnings:
            st.warning(warning)
        key = hashlib.sha256(
            (
                settings.model_dump_json()
                + settings.api_key.get_secret_value()
                + str(sorted(environment_owned_keys(settings)))
            ).encode()
        ).hexdigest()
        services = resources(key, settings)
        collect_finished_jobs()
        monitor_answer_jobs()
        page, session_id = sidebar(services)
        if "flash" in st.session_state:
            st.success(st.session_state.pop("flash"))
        for error in st.session_state.pop("upload_errors", []):
            st.error(error)
        if page != "AI 聊天":
            with st.container(key="privacy_note"):
                st.caption("本地知识工作台 / " + page)
        if page == "AI 聊天":
            chat_page(services, session_id)
        elif page == "知识库管理":
            knowledge_page(services)
        elif page == "系统设置":
            settings_page(services)
        else:
            debug_page(services)
        install_interactions(session_id, page)
    except AppError as exc:
        st.error(str(exc))
    except Exception as exc:
        # Do not reveal exception strings that may contain API credentials or private text.
        st.error(f"工作空间暂不可用（{type(exc).__name__}）。请检查配置、数据库与磁盘权限后刷新。")
