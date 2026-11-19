import sqlite3
from collections import Counter

import streamlit as st

from rag_workbench.application.research import ResearchService
from rag_workbench.domain.errors import AppError
from rag_workbench.domain.research import EvidenceSource

CAPTURE_KEY = "research_capture_source"


def choice_labels(items: dict[str, str], new_option: str = "") -> dict[str, str]:
    counts = Counter(items.values())
    reserved = {*items.values(), new_option}
    result = {}
    for index, (identity, title) in enumerate(items.items(), start=1):
        label = title
        if counts[title] > 1 or title == new_option:
            label = f"{title} · {index}"
            while label in reserved:
                label += "·"
            reserved.add(label)
        result[identity] = label
    return result


def capture_button(source: EvidenceSource, *, key: str) -> None:
    st.button("收进证据板", key=key, on_click=_queue, args=(source,), type="tertiary")


def _queue(source: EvidenceSource) -> None:
    st.session_state[CAPTURE_KEY] = source


@st.dialog("收进证据板", dismissible=False)
def capture_dialog(service: ResearchService, source: EvidenceSource) -> None:
    if not service.visible(source):
        st.warning("这条证据已超出当前阅读范围。")
    else:
        st.text(f"{source.name} · {source.location()}")
        with st.expander("原文"):
            st.text(source.quote)
        questions = dict(service.repo.questions())
        question_labels = choice_labels(questions, "新建研究问题")
        question = st.selectbox(
            "研究问题", [*questions, None],
            format_func=lambda key: question_labels.get(key, "新建研究问题"),
        )
        title = st.text_input("问题标题", max_chars=240) if question is None else ""
        claims = dict(service.repo.board(question)[1]) if question else {}
        claim_labels = choice_labels(claims, "新建主张")
        claim = st.selectbox(
            "主张", [*claims, None], key=f"research_capture_claim_{question}",
            format_func=lambda key: claim_labels.get(key, "新建主张"),
        )
        statement = (st.text_area("主张内容", max_chars=2000, key=f"research_statement_{question}")
                     if claim is None else "")
        if st.button("确认收录", type="primary"):
            try:
                _, created = service.repo.capture_to(source, question, claim, title, statement)
            except AppError as exc:
                st.error(str(exc))
            except sqlite3.Error:
                st.error("证据暂未保存，请检查数据库后重试。")
            else:
                st.session_state.pop(CAPTURE_KEY, None)
                label = "已收录" if created else "已在此主张中"
                target = (claims.get(claim) or statement).strip()
                st.session_state["flash"] = f"{label} · {target}"
                st.rerun()
    if st.button("取消", key="research_capture_cancel"):
        st.session_state.pop(CAPTURE_KEY, None)
        st.rerun()
