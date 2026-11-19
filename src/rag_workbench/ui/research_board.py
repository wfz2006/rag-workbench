import sqlite3

import streamlit as st

from rag_workbench.application.research import ResearchService
from rag_workbench.domain.errors import AppError
from rag_workbench.domain.research import ORIGIN_LABELS, STANCE_LABELS, EvidenceCard
from rag_workbench.infrastructure.material_preview import render_pdf_page
from rag_workbench.ui.research_capture import choice_labels


@st.dialog("原文预览", width="large")
def _view_original(service: ResearchService, card: EvidenceCard) -> None:
    source = card.source
    st.text(f"{source.name} · PDF 第 {source.page} 页")
    try:
        data = service.resolve_source(source)
        if source.page is not None:
            preview = render_pdf_page(data, source.page, quote=source.quote)
            st.image(preview.png, width="stretch")
    except AppError as exc:
        st.warning(str(exc))
    st.text(source.quote)


@st.dialog("核对证据")
def _edit_card(service: ResearchService, card: EvidenceCard) -> None:
    source = card.source
    st.text(f"{source.name} · {source.location()}")
    st.text(source.quote)
    with st.form(f"research_card_edit_{card.id}"):
        stance = st.selectbox(
            "证据标记", list(STANCE_LABELS), index=list(STANCE_LABELS).index(card.stance),
            format_func=STANCE_LABELS.__getitem__,
        )
        note = st.text_area("判断笔记", card.note, max_chars=10000)
        save = st.form_submit_button("保存判断", type="primary")
    confirm = st.checkbox("确认从此主张移除这张证据卡")
    remove = st.button("移除证据卡", disabled=not confirm)
    if save or remove:
        try:
            if remove:
                service.repo.remove(card.id, card.revision)
            else:
                service.repo.annotate(card.id, card.revision, stance, note)
        except AppError as exc:
            st.error(str(exc))
        except sqlite3.Error:
            st.error("修改暂未保存，请检查数据库后重试。")
        else:
            st.rerun()


def research_board_page(service: ResearchService) -> None:
    st.title("研究证据板")
    repo = service.repo
    questions = dict(repo.questions())
    with st.expander("新建研究问题", expanded=not questions), st.form("research_new_question"):
        title = st.text_input("问题标题", max_chars=240)
        if st.form_submit_button("创建问题"):
            try:
                st.session_state["research_question"] = repo.create_question(title)
            except AppError as exc:
                st.error(str(exc))
            else:
                st.rerun()
    if not questions:
        st.info("可从问答引用、知识星图或材料文献收录第一条证据。")
        return
    if st.session_state.get("research_question") not in questions:
        st.session_state["research_question"] = next(iter(questions))
    question = st.selectbox(
        "研究问题", list(questions), format_func=choice_labels(questions).__getitem__,
        key="research_question",
    )
    title, claims, cards = repo.board(question)
    cards = [card for card in cards if service.visible(card.source)]
    with st.expander("编辑问题与主张"):
        with st.form(f"research_question_title_{question}"):
            new_title = st.text_input("问题标题", title, max_chars=240)
            if st.form_submit_button("保存标题"):
                try:
                    repo.rename_question(question, new_title)
                except AppError as exc:
                    st.error(str(exc))
                else:
                    st.rerun()
        with st.form(f"research_add_claim_{question}", clear_on_submit=True):
            value = st.text_area("主张内容", max_chars=2000)
            if st.form_submit_button("添加主张"):
                try:
                    repo.add_claim(question, value)
                except AppError as exc:
                    st.error(str(exc))
                else:
                    st.rerun()
    st.download_button(
        "导出 Markdown", service.export_markdown(question),
        file_name="研究证据笔记.md", mime="text/markdown", on_click="ignore",
    )
    filter_stance = st.selectbox("证据标记", [None, *STANCE_LABELS],
                                format_func=lambda value: STANCE_LABELS.get(value, "全部"))
    for claim_id, claim in claims:
        with st.container(border=True):
            st.subheader(claim)
            evidence = [card for card in cards if card.claim_id == claim_id]
            pending = sum(card.stance == "pending" for card in evidence)
            st.caption(f"{len(evidence)} 条证据 · {pending} 条待核实")
            with st.expander("编辑主张"), st.form(f"research_edit_claim_{claim_id}"):
                statement = st.text_area("主张内容", claim, max_chars=2000)
                if st.form_submit_button("保存主张"):
                    try:
                        repo.edit_claim(claim_id, statement)
                    except AppError as exc:
                        st.error(str(exc))
                    else:
                        st.rerun()
            if not evidence:
                st.caption("暂无证据")
            for card in evidence:
                if filter_stance is None or filter_stance == card.stance:
                    _show_card(service, card)


def _show_card(service: ResearchService, card: EvidenceCard) -> None:
    source = card.source
    revision = f" · 材料记录修订 {source.record_revision}" if source.record_revision else ""
    with st.container(border=True):
        st.text(f"{STANCE_LABELS[card.stance]} · {source.name} · {source.location()}{revision}")
        st.caption(ORIGIN_LABELS[source.origin])
        st.text(source.quote)
        if card.note:
            st.caption("判断笔记")
            st.text(card.note)
        if (
            source.page is not None and source.name.lower().endswith(".pdf")
            and st.button("看原文", key=f"research_view_{card.id}", type="tertiary")
        ):
            _view_original(service, card)
        if st.button("核对证据", key=f"research_annotate_{card.id}", type="tertiary"):
            _edit_card(service, card)
