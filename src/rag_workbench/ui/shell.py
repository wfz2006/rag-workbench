"""Desktop-style navigation. Every visible action uses existing application services."""

import html
import uuid
from pathlib import Path

import streamlit as st

from rag_workbench.application.packs import PackCatalog
from rag_workbench.bootstrap import Services
from rag_workbench.ui.session_state import collect_finished_jobs

PAGES = ["AI 聊天", "知识库管理", "知识包", "系统设置", "检索调试"]
PACK_STATE_KEYS = (
    "workspace_page",
    "session_id",
    "answer_jobs",
    "unsaved_turns",
    "inspection",
    "conversation_filter",
    "editing_failed_turn",
    "upload_errors",
    "flash",
    "settings_feedback",
    "settings_feedback_request",
    "settings_pending_rebuild",
    "session_limit",
    "pack_build_result",
    "pack_lint_result",
)


def session_title(session: dict, jobs: dict, unsaved: dict) -> str:
    if session.get("custom_title"):
        return session["title"]
    job = jobs.get(session["id"])
    target = getattr(job, "replaces_assistant_id", None)
    if target is not None and target == session.get("first_assistant_id"):
        return job.question[:80]
    for turn in reversed(unsaved.get(session["id"], [])):
        target = getattr(turn, "replaces_assistant_id", None)
        if target is not None and target == session.get("first_assistant_id"):
            return turn.question[:80]
    if session["message_count"] == 0 and session["title"] == "新会话":
        if session["id"] in jobs:
            return jobs[session["id"]].question[:80]
        if turns := unsaved.get(session["id"]):
            return turns[0].question[:80]
    return session["title"]


def clear_conversation_filter() -> None:
    st.session_state["conversation_filter"] = ""


def navigate(page: str) -> None:
    if page != st.session_state.get("workspace_page"):
        clear_editing()
    st.session_state["workspace_page"] = page


def clear_editing() -> None:
    st.session_state.pop("editing_failed_turn", None)


def _pack_options(services: Services) -> tuple[list, dict]:
    entries = PackCatalog(services.settings).list()
    options = [None] + [entry.pack_id for entry in entries]
    labels = {None: "部署默认工作区"}
    for entry in entries:
        status = {"draft": "草稿", "reviewed": "已审核", "frozen": "已冻结"}[entry.status]
        if not entry.has_data:
            status += " · 未建索引"
        labels[entry.pack_id] = f"{entry.title}（{status}）"
    return options, labels


def _busy() -> bool:
    collect_finished_jobs()
    return bool(st.session_state.get("answer_jobs")) or bool(
        st.session_state.get("unsaved_turns")
    )


def activate_pack(pack_id: str | None) -> None:
    if _busy():
        st.session_state["pending_pack_select"] = st.session_state.get("active_pack")
        st.session_state["pack_switch_error"] = (
            "有正在生成或未保存的回答，暂不能切换知识包。"
        )
        st.rerun()
        return
    for key in PACK_STATE_KEYS:
        st.session_state.pop(key, None)
    st.session_state["active_pack"] = pack_id
    # Widget-backed state can only be changed before the widget is built next run.
    st.session_state["pending_pack_select"] = pack_id
    st.session_state["pending_page"] = "AI 聊天"
    st.rerun()


def switch_pack() -> None:
    target = st.session_state.get("active_pack_select")
    if target == st.session_state.get("active_pack"):
        return
    activate_pack(target)


def new_conversation(services: Services) -> None:
    collect_finished_jobs()
    current = st.session_state.get("session_id")
    if (
        not current
        or services.repo.messages(current)
        or current in st.session_state.get("answer_jobs", {})
        or current in st.session_state.get("unsaved_turns", {})
        or services.repo.get_state(f"session_title:{current}")
        or any(s["id"] == current and s["title"] != "新会话" for s in services.repo.sessions())
    ):
        busy = set(st.session_state.get("answer_jobs", {})) | set(
            st.session_state.get("unsaved_turns", {})
        )
        st.session_state["session_id"] = (
            services.repo.empty_session(busy) or services.repo.new_session()
        )
    st.session_state["conversation_filter"] = ""
    st.session_state.pop("editing_failed_turn", None)
    st.session_state["focus_composer"] = True
    navigate("AI 聊天")


def select_conversation(identity: str) -> None:
    st.session_state.pop("editing_failed_turn", None)
    st.session_state["session_id"] = identity
    navigate("AI 聊天")


def inject_styles() -> None:
    assets = Path(__file__).parent / "assets"
    stylesheet = "\n".join(
        (assets / name).read_text(encoding="utf-8")
        for name in ("claude-tokens.css", "workbench.css")
    )
    st.html(f"<style>{stylesheet}</style>")


def install_interactions(session_id: str, page: str) -> None:
    """Only trusted project code is executable; user/model text never enters this script."""
    script = (Path(__file__).parent / "assets" / "interactions.js").read_text(encoding="utf-8")
    focus = "true" if st.session_state.pop("focus_composer", False) else "false"
    notice = "true" if st.session_state.pop("focus_settings_feedback", False) else "false"
    st.html(
        '<span hidden class="interaction-scope" data-session="'
        + html.escape(session_id, quote=True)
        + '" data-page="'
        + html.escape(page, quote=True)
        + f'" data-focus="{focus}" data-notice="{notice}" data-render="{uuid.uuid4().hex}"></span>'
        + f"<script>{script}</script>",
        unsafe_allow_javascript=True,
    )


def sidebar(services: Services) -> tuple[str, str]:
    st.session_state.setdefault("workspace_page", "AI 聊天")
    sessions = services.repo.sessions()
    if not sessions:
        services.repo.new_session()
        sessions = services.repo.sessions()
    identities = {session["id"] for session in sessions}
    if st.session_state.get("session_id") not in identities:
        st.session_state["session_id"] = sessions[0]["id"]
    with st.sidebar:
        st.html('<div class="workspace-brand">Rag<span>Workbench</span></div>')
        options, labels = _pack_options(services)
        if len(options) > 1:
            current = st.session_state.get("active_pack")
            if current not in options:
                current = None
                st.session_state["active_pack"] = None
            if st.session_state.get("active_pack_select") not in options:
                st.session_state["active_pack_select"] = current
            busy = _busy()
            st.selectbox(
                "知识包",
                options,
                index=options.index(current),
                format_func=labels.get,
                key="active_pack_select",
                on_change=switch_pack,
                disabled=busy,
                help="切换后使用该知识包独立的数据目录与配置。",
            )
            if busy:
                st.caption("生成中，暂不能切换知识包。")
        st.button(
            "新建会话",
            icon=":material/add:",
            key="new_conversation",
            width="stretch",
            on_click=new_conversation,
            args=(services,),
        )
        page = st.radio(
            "工作空间",
            PAGES,
            key="workspace_page",
            on_change=clear_editing,
            label_visibility="collapsed",
            width="stretch",
        )
        with st.container(key="recents_header", horizontal=True, vertical_alignment="center"):
            st.caption("最近使用")
            with st.popover("搜索会话", icon=":material/search:", key="session_search"):
                query = st.text_input(
                    "搜索会话",
                    key="conversation_filter",
                    placeholder="输入会话标题",
                    icon=":material/search:",
                ).strip()
        with st.container(key="session_list"):
            jobs = st.session_state.get("answer_jobs", {})
            unsaved = st.session_state.get("unsaved_turns", {})
            if query:
                with st.container(key="active_search_filter", horizontal=True):
                    st.text(f"筛选中：{query}")
                    st.button("清除筛选", type="tertiary", on_click=clear_conversation_filter)
            filtered = [
                s
                for s in sessions
                if query.casefold() in session_title(s, jobs, unsaved).casefold()
                and (
                    s["message_count"] > 0
                    or s["id"] == st.session_state["session_id"]
                    or s["id"] in st.session_state.get("unsaved_turns", {})
                    or s["id"] in jobs
                )
            ]
            limit = st.session_state.get("session_limit", 20)
            for session in filtered[:limit]:
                selected = session["id"] == st.session_state["session_id"]
                title = session_title(session, jobs, unsaved)
                st.button(
                    title,
                    key=f"session_{session['id']}",
                    width="stretch",
                    type="primary" if selected else "secondary",
                    help=title,
                    on_click=select_conversation,
                    args=(session["id"],),
                )
            if not filtered:
                st.caption("没有找到匹配的会话。")
            if len(filtered) > limit and st.button("显示更多会话", key="more_sessions"):
                st.session_state["session_limit"] = limit + 20
                st.rerun()
        with st.popover("管理当前会话", icon=":material/more_horiz:", key="session_manage"):
            current = next(s for s in sessions if s["id"] == st.session_state["session_id"])
            title = st.text_input(
                "会话名称", value=current["title"], max_chars=80, key=f"rename_{current['id']}"
            )
            if st.button("重命名会话", disabled=not title.strip()):
                services.repo.rename_session(current["id"], title)
                st.rerun()
            confirmed = st.checkbox("确认删除当前会话及其消息", key=f"remove_{current['id']}")
            busy = current["id"] in st.session_state.get("answer_jobs", {})
            if st.button("删除会话", disabled=not confirmed or busy):
                services.repo.delete_session(current["id"])
                st.session_state.get("unsaved_turns", {}).pop(current["id"], None)
                st.session_state.pop("session_id", None)
                st.rerun()
        with st.container(key="sidebar_footer"):
            st.html(
                '<div class="local-profile"><span class="profile-monogram">R</span>'
                "<div><strong>本地工作空间</strong><span>个人知识库</span></div></div>"
            )
            st.caption(
                "API 已配置"
                if services.settings.api_key.get_secret_value().strip()
                else "尚未配置 API · 可先管理资料"
            )
    return page, st.session_state["session_id"]


def conversation_header(title: str) -> None:
    st.html(f'<div class="conversation-header"><span>{html.escape(title)}</span></div>')
