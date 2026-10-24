"""Chat presentation. Workers own generation; only the main thread renders Streamlit."""

import html

import streamlit as st

from rag_workbench.application.generation import GenerationJob
from rag_workbench.application.prompts import GENERAL_PREFIX
from rag_workbench.bootstrap import Services
from rag_workbench.domain.models import AnswerResult
from rag_workbench.ui.components import answer_panel
from rag_workbench.ui.session_state import (
    RunningTurn,
    UnsavedTurn,
    conversation_timeline,
    discard_unsaved_turn,
)
from rag_workbench.ui.shell import conversation_header, navigate, session_title

PHASE_LABELS = {
    "retrieving": "正在检索资料",
    "evidence": "正在核验证据是否充分",
    "generating_general": "正在生成通用回答 · 非知识库来源",
    "generating_grounded": "正在生成有据回答",
    "validating": "正在校验回答与引用",
    "stopping": "正在停止生成",
    "saving": "正在保存记录",
}


def _submit(services: Services, session_id: str) -> None:
    question = st.session_state.get("question", "")
    jobs = st.session_state.setdefault("answer_jobs", {})
    if isinstance(question, str) and question.strip() and session_id not in jobs:
        editing = st.session_state.get("editing_failed_turn", {})
        retry_id = editing.get("assistant_id") if editing.get("session_id") == session_id else None
        jobs[session_id] = services.start_answer(
            session_id, question.strip(), retry_assistant_id=retry_id
        )
        st.session_state.pop("editing_failed_turn", None)


def refill_question(question: str, session_id: str, assistant_id: int) -> None:
    st.session_state["question"] = question
    st.session_state["focus_composer"] = True
    st.session_state["editing_failed_turn"] = {
        "session_id": session_id,
        "assistant_id": assistant_id,
        "original_question": question,
    }


def cancel_edit() -> None:
    st.session_state.pop("editing_failed_turn", None)
    st.session_state["question"] = ""


def retry_question(services: Services, session_id: str, question: str, assistant_id: int) -> None:
    jobs = st.session_state.setdefault("answer_jobs", {})
    if session_id not in jobs:
        jobs[session_id] = services.start_answer(
            session_id, question, retry_assistant_id=assistant_id
        )
        st.session_state.pop("editing_failed_turn", None)


def _failed_actions(services: Services, session_id: str, message: dict, question: str) -> None:
    with st.container(key=f"failed_actions_{message['id']}", horizontal=True):
        busy = session_id in st.session_state.get("answer_jobs", {})
        st.button(
            "重试此问题",
            key=f"retry_{message['id']}",
            type="tertiary",
            disabled=busy,
            on_click=retry_question,
            args=(services, session_id, question, message["id"]),
        )
        st.button(
            "重新编辑问题",
            key=f"edit_{message['id']}",
            type="tertiary",
            disabled=busy,
            on_click=refill_question,
            args=(question, session_id, message["id"]),
        )
        if st.button(
            "移除此失败回合", key=f"remove_failed_{message['id']}", type="tertiary", disabled=busy
        ):
            services.repo.delete_failed_turn(session_id, message["id"])
            st.session_state.pop("editing_failed_turn", None)
            st.rerun()


@st.fragment(run_every=0.2)
def _running_answer(job: GenerationJob) -> None:
    snapshot = job.snapshot()
    if snapshot.done:
        st.rerun()
    with st.chat_message("assistant", avatar=":material/asterisk:"):
        label = PHASE_LABELS.get(snapshot.phase, "正在准备回答")
        st.html(
            '<div class="generation-status" role="status" data-generation-key="'
            + html.escape(job.created_at, quote=True)
            + '" data-replaces-assistant-id="'
            + str(job.replaces_assistant_id or "")
            + '"><span class="generation-mark" '
            'aria-hidden="true">✳</span><span>' + html.escape(label) + "</span>"
            '<span class="generation-elapsed" aria-hidden="true">'
            f"{snapshot.elapsed:.0f}s</span></div>"
        )
        if snapshot.text:
            with st.container(key="stream_body"):
                st.markdown(snapshot.text.removeprefix(GENERAL_PREFIX))
        elif snapshot.phase == "generating_grounded":
            st.caption("引用核验通过后展示正文。")


def _composer(services: Services, session_id: str, job: GenerationJob | None) -> None:
    with st.container(key="composer"):
        editing = st.session_state.get("editing_failed_turn", {})
        if editing.get("session_id") == session_id:
            with st.container(key="edit_notice", horizontal=True):
                st.caption(
                    "正在重新编辑失败回合 · 发送将替换原回合："
                    + editing.get("original_question", "")[:48]
                )
                st.button(
                    "取消编辑", key="cancel_failed_edit", type="tertiary", on_click=cancel_edit
                )
        with st.container(key="prompt_surface"):
            st.chat_input(
                "询问资料内容，或开始一个新话题…",
                max_chars=2000,
                key="question",
                disabled=job is not None,
                on_submit=_submit,
                args=(services, session_id),
            )
            if job:
                st.button(
                    "停止生成",
                    icon=":material/stop:",
                    key="stop_generation",
                    help="停止接收当前回答",
                    on_click=job.cancel,
                    disabled=job.snapshot().phase in {"stopping", "saving"},
                )
        with st.container(key="composer_controls"):
            add, hint, model = st.columns([1, 5, 3], gap="small")
            with add.popover("添加资料", icon=":material/add:", key="composer_add"):
                st.caption("知识库")
                st.button(
                    "上传或管理文档",
                    icon=":material/attach_file:",
                    on_click=navigate,
                    args=("知识库管理",),
                    width="stretch",
                )
                st.button(
                    "打开检索调试",
                    icon=":material/search:",
                    on_click=navigate,
                    args=("检索调试",),
                    width="stretch",
                )
            hint.html(
                '<span class="composer-privacy" title="检索片段与必要对话可能发送至远程模型。">'
                "片段可能发送至模型 · 请核实信息。</span>"
            )
            with model.popover(services.settings.llm_model, key="composer_model"):
                st.html(
                    '<div class="model-menu-summary"><div><strong>'
                    + html.escape(services.settings.llm_model)
                    + "</strong><p>当前问答模型</p></div>"
                    '<span class="model-menu-check" aria-hidden="true">✓</span></div>'
                )
                st.button(
                    "模型与连接设置",
                    icon=":material/tune:",
                    on_click=navigate,
                    args=("系统设置",),
                    width="stretch",
                )


def chat_page(services: Services, session_id: str) -> None:
    jobs = st.session_state.setdefault("answer_jobs", {})
    unsaved = st.session_state.setdefault("unsaved_turns", {})
    job = jobs.get(session_id)
    messages = services.repo.messages(session_id)
    session = next((s for s in services.repo.sessions() if s["id"] == session_id), None)
    conversation_header(session_title(session, jobs, unsaved) if session else "新会话")
    if not messages and not job and session_id not in unsaved:
        with st.container(key="welcome_panel"):
            st.html(
                '<div class="welcome"><span class="welcome-mark" aria-hidden="true">✳</span>'
                "<h1>今天，想弄清楚什么？</h1></div>"
                '<p class="welcome-subtitle">开始一个会话，让资料成为答案的依据。</p>'
            )
            _knowledge_overview(services)
    else:
        st.html('<span class="conversation-active" aria-hidden="true"></span>')
    last_question = ""
    for message in conversation_timeline(messages, unsaved.get(session_id, []), job):
        if isinstance(message, RunningTurn):
            with st.chat_message("user"):
                st.text(message.job.question)
            _running_answer(message.job)
            continue
        if isinstance(message, UnsavedTurn):
            with st.chat_message("user"):
                st.text(message.question)
            with st.chat_message("assistant", avatar=":material/asterisk:"):
                if message.replaces_assistant_id:
                    _turn_anchor(message.replaces_assistant_id)
                answer_panel(
                    message.result,
                    services.settings.show_debug,
                    body_key=f"answer_body_unsaved_{message.key}",
                )
                st.button(
                    "移除此未保存记录",
                    key=f"discard_unsaved_{message.key}",
                    type="tertiary",
                    help="仅从当前窗口移除，不会保存到历史；请先复制需要保留的正文。",
                    on_click=discard_unsaved_turn,
                    args=(session_id, message.key),
                )
            continue
        avatar = ":material/asterisk:" if message["role"] == "assistant" else None
        with st.chat_message(message["role"], avatar=avatar):
            if message["result"]:
                _turn_anchor(message["id"])
                result = AnswerResult.model_validate_json(message["result"])
                answer_panel(
                    result,
                    services.settings.show_debug,
                    body_key=f"answer_body_{message['id']}",
                )
                if result.mode == "error":
                    _failed_actions(
                        services, session_id, message, last_question or result.debug.question
                    )
            else:
                last_question = message["content"]
                st.text(message["content"])
    _composer(services, session_id, job)


def _turn_anchor(assistant_id: int) -> None:
    st.html(f'<span class="turn-anchor" data-assistant-id="{assistant_id}" hidden></span>')


def _knowledge_overview(services: Services) -> None:
    stats = services.knowledge.stats()
    empty = stats["chunks"] == 0
    with st.container(key="knowledge_overview"):
        status_labels = {
            "empty": "尚未建立",
            "ready": "已就绪",
            "incompatible": "不兼容",
            "corrupt": "需修复",
        }
        status = status_labels.get(stats["index_status"], stats["index_status"])
        if not empty:
            st.caption(f"{stats['documents']} 份文档 · {stats['chunks']} 个文本块 · 索引{status}")
        if stats["error"]:
            st.warning(stats["error"])
        elif empty:
            st.caption(
                "添加文档开始检索，也可以先聊一个问题。"
                if services.settings.allow_general
                else "添加文档后，即可检索并追溯回答依据。"
            )
        with st.container(key="chat_intro_actions", horizontal=True, gap="small"):
            st.button(
                "上传文档" if empty else "查看知识库",
                icon=":material/add:",
                key="intro_upload",
                on_click=navigate,
                args=("知识库管理",),
            )
            st.button(
                "查看检索过程",
                key="intro_debug",
                on_click=navigate,
                args=("检索调试",),
            )
