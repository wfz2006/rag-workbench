import html
import re

import streamlit as st
from markdown_it import MarkdownIt

from rag_workbench.application.prompts import GENERAL_PREFIX
from rag_workbench.domain.models import AnswerResult, Inspection

MODE_LABELS = {
    "knowledge_base": "知识库回答",
    "general": "通用模型回答",
    "error": "服务错误",
    "insufficient_evidence": "依据不足",
}


def ordered_sources(result: AnswerResult):
    """Use first citation order for old and new persisted answer snapshots alike."""
    sources = {source.chunk.id: source for source in result.sources}
    cited = dict.fromkeys(re.findall(r"\[([0-9a-f]{64})\]", result.answer))
    return [sources.pop(identity) for identity in cited if identity in sources] + list(
        sources.values()
    )


def _close_fences(text: str) -> str:
    """Close EOF fences at each claim boundary, never across the following claim."""
    lines = text.split("\n")
    for token in MarkdownIt("commonmark").parse(text):
        if token.type != "fence" or token.map is None:
            continue
        start, end = token.map
        # Container fences end implicitly when a blockquote/list ends. Only an
        # EOF fence can consume the next claim; appending after later prose
        # would open a new, empty container code block instead of closing it.
        if end < len(lines) and not (
            token.level == 0 and end == len(lines) - 1 and lines[-1] == ""
        ):
            continue
        closing = rf"[ \t>]*{re.escape(token.markup[0])}{{{len(token.markup)},}}[ \t]*"
        if end - 1 == start or not re.fullmatch(closing, lines[end - 1]):
            prefix = lines[start].split(token.markup, 1)[0]
            prefix = re.sub(r"[^\s>]", " ", prefix)
            text += "\n" + prefix + token.markup
    return text


def answer_presentation(result: AnswerResult) -> tuple[str, str]:
    """Isolate protocol claim boundaries; preserve Markdown syntax and plain copying."""
    numbers = {source.chunk.id: i for i, source in enumerate(ordered_sources(result), 1)}
    body = result.answer.removeprefix(GENERAL_PREFIX) if result.mode == "general" else result.answer
    if not numbers:
        return body, body
    display, plain, buffer = [], [], []
    for line in body.split("\n"):
        ids = re.findall(r"\[([0-9a-f]{64})\]", line)
        if not (
            ids
            and all(i in numbers for i in ids)
            and re.fullmatch(r"(?:\s*\[[0-9a-f]{64}\])+\s*", line)
        ):
            buffer.append(line)
            continue
        text = "\n".join(buffer).removeprefix("\n").removesuffix("\n")
        text = _close_fences(text)
        plain.append(text)
        tokens = MarkdownIt("commonmark").enable("table").parse(text)
        paragraph = next((t for t in reversed(tokens) if t.type == "paragraph_open"), None)
        inline = (
            tokens
            and tokens[-1].type == "paragraph_close"
            and tokens[-1].level == 0
            and paragraph
            and paragraph.map
            and paragraph.map[1] == len(text.split("\n"))
            and not text.endswith("\\")
        )
        markers = " ".join(f"〔来源 {numbers[i]}〕" for i in dict.fromkeys(ids))
        display.append(text + (" " if inline else "\n\n") + markers)
        buffer = []
    if buffer:
        tail = "\n".join(buffer).removeprefix("\n")
        display.append(tail)
        plain.append(tail)
    return "\n\n".join(display), "\n\n".join(plain)


def source_location(result) -> str:
    page = result.chunk.page or result.original_page
    offset = f"字符 {result.chunk.start + 1}–{result.chunk.end}"
    return f"页码 {page} · {offset}" if page else f"{offset} · 此格式无固定页码"


def inspection_panel(inspection: Inspection) -> None:
    st.caption(f"问答路由：{MODE_LABELS[inspection.route]} · {inspection.reason}")
    if inspection.evidence:
        decision = inspection.evidence
        st.write(f"证据充分性：{'支持回答' if decision.answerable else '不足以回答'}")
        st.caption(decision.reason)
        if decision.supporting_chunk_ids:
            with st.expander(f"支持证据 ID（{len(decision.supporting_chunk_ids)} 条）"):
                st.json({"supporting_chunk_ids": decision.supporting_chunk_ids}, expanded=True)
    elif inspection.route != "error":
        st.caption("未调用证据判断：没有通过阈值的片段。")
    if not inspection.candidates:
        st.info("没有检索片段。空知识库可在允许兜底时使用通用问答。")
    else:
        st.caption("候选按向量与关键词融合排序。向量过阈值或关键词覆盖达标后，仍须核验证据。")
    for i, result in enumerate(inspection.candidates, 1):
        status = "通过阈值" if result.passed_threshold else "低于阈值"
        if result.lexical_passed:
            status += " · 关键词匹配通过"
        if result.context_for:
            status += " · 相邻上下文"
        if inspection.evidence and result.chunk.id in inspection.evidence.supporting_chunk_ids:
            status += " · 已选为支持证据"
        with st.expander(f"候选 {i} · 相似度 {result.score:.4f} · {status}"):
            st.text(result.chunk.document_name)
            st.caption(source_location(result))
            st.caption(
                f"关键词得分（BM25）：{result.lexical_score:.4f} · "
                f"关键词准入：{'通过' if result.lexical_passed else '未通过'}"
            )
            st.code(result.chunk.id, language=None)
            st.text(result.chunk.text)


def answer_panel(result: AnswerResult, show_debug: bool, body_key: str | None = None) -> None:
    if result.error_code == "persistence_error":
        st.warning("回答已生成，但未保存到历史记录。请先复制正文，再检查磁盘或数据库。")
    label = "已停止" if result.error_code == "cancelled" else MODE_LABELS[result.mode]
    st.caption(label + (" · 非知识库来源" if result.mode == "general" else ""))
    display, plain = answer_presentation(result)
    if result.error_code == "cancelled":
        st.info(result.answer)
    elif result.mode == "error":
        st.error(result.answer)
    elif result.mode == "insufficient_evidence":
        st.info(result.answer)
    else:
        with st.container(key=body_key):
            st.markdown(display)
        if body_key:
            st.html(
                '<button type="button" class="copy-answer" data-copy-target="'
                + html.escape(body_key, quote=True)
                + '" data-copy-text="'
                + html.escape(plain, quote=True)
                + '" aria-label="复制回答"><span aria-live="polite">复制回答</span></button>'
            )
    if result.sources:
        with st.expander(f"查看 {len(result.sources)} 条引用来源"):
            st.caption("这是回答生成时的证据快照；原文档后来被删除也不会改写本次记录。")
            for i, source in enumerate(ordered_sources(result), 1):
                st.write(f"来源 {i}")
                st.text(source.chunk.document_name)
                st.caption(f"相似度 {source.score:.4f} · {source_location(source)}")
                if source.context_for:
                    st.caption("相邻上下文：分数为该片段的真实相似度，因同文档邻接关系补入。")
                st.code(source.chunk.id, language=None)
                st.text(source.chunk.text)
    if show_debug and (result.debug.candidates or result.debug.evidence):
        with st.expander("检索与路由详情"):
            inspection_panel(result.debug)
