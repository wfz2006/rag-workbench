"""Original pages beside records. PDF text is never injected into HTML."""

import base64

import streamlit as st

from rag_workbench.domain.errors import AppError
from rag_workbench.infrastructure.material_preview import (
    load_original,
    pdf_page_count,
    render_pdf_page,
)
from rag_workbench.infrastructure.parsing import quote_in_source


def _choose_block(prefix, blocks):
    selected = st.session_state[f"{prefix}_block"]
    block = next(block for block in blocks if block.id == selected)
    if block.page:
        st.session_state[f"{prefix}_page"] = block.page
    st.session_state[f"{prefix}_quote"] = ""


def _page_html(preview, zoom, bbox=None) -> str:
    # Only rendered PNG and validated numeric geometry enter this local frame.
    encoded = base64.b64encode(preview.png).decode("ascii")
    boxes = preview.quote_boxes or ((bbox,) if bbox else ())
    overlay = "".join(
        '<div aria-label="选定证据块区域" style="position:absolute;pointer-events:none;'
        f'left:{x0 * 100}%;top:{y0 * 100}%;width:{(x1 - x0) * 100}%;'
        f'height:{(y1 - y0) * 100}%;box-sizing:border-box;'
        'border:2px solid #c6613f;background:#c6613f22"></div>'
        for x0, y0, x1, y1 in boxes
    )
    return (
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<style>html,body{margin:0;padding:0;background:#eee;}'
        '.paper{position:relative;background:white;margin:0 auto;}'
        'img{display:block;width:100%;height:auto;}</style></head><body>'
        f'<div class="paper" style="width:{int(zoom)}%">'
        f'<img src="data:image/png;base64,{encoded}" alt="PDF 原始页面">'
        f'{overlay}</div></body></html>'
    )


def render_source_viewer(service, document, blocks, *, key_prefix="materials_viewer") -> None:
    """Consume an optional materials_source_focus once, scoped to this paper and its SI."""
    st.subheader("原文对照")
    related = [document] + [
        item for item in service.repo.documents() if item.parent_id == document.id
    ]
    documents = {item.id: item for item in related}
    allowed_blocks = {block.id: block for block in blocks if block.document_id in documents}
    document_key = f"{key_prefix}_document"
    scope_key = f"{key_prefix}_scope"
    if (
        st.session_state.get(scope_key) != document.id
        or st.session_state.get(document_key) not in documents
    ):
        st.session_state[document_key] = document.id
    st.session_state[scope_key] = document.id
    if st.session_state.pop("materials_source_reset", False):
        for key in list(st.session_state):
            if key.startswith(f"{key_prefix}_") and key.endswith("_quote"):
                st.session_state.pop(key, None)
    requested = st.session_state.pop("materials_source_focus", None)
    if requested and not isinstance(requested, dict):
        st.warning("原文定位请求无效，请重新选择字段证据。")
    elif requested:
        block = allowed_blocks.get(requested.get("block_id"))
        quote = requested.get("quote") or ""
        if (
            block is None or requested.get("document_id") != block.document_id
            or not isinstance(quote, str) or not quote_in_source(quote, block.text)
        ):
            st.warning("所选证据不属于当前论文及补充材料，未切换原文。")
        else:
            st.session_state[document_key] = block.document_id
            prefix = f"{key_prefix}_{block.document_id}"
            st.session_state[f"{prefix}_block"] = block.id
            st.session_state[f"{prefix}_quote"] = quote
            if block.page:
                st.session_state[f"{prefix}_page"] = block.page
    selected = st.selectbox(
        "原文文件", list(documents), key=document_key,
        format_func=lambda identity: documents[identity].name,
    )
    source = documents[selected]
    prefix = f"{key_prefix}_{selected}"
    source_blocks = [block for block in allowed_blocks.values() if block.document_id == selected]
    if source_blocks:
        if st.session_state.get(f"{prefix}_block") not in {block.id for block in source_blocks}:
            st.session_state[f"{prefix}_block"] = source_blocks[0].id
        identity = st.selectbox(
            "定位原文块", [block.id for block in source_blocks], key=f"{prefix}_block",
            format_func=lambda value: next(
                (f"第 {b.page} 页 · " if b.page else "文本 · ") + b.text[:50]
                for b in source_blocks if b.id == value
            ),
            on_change=_choose_block, args=(prefix, source_blocks),
        )
        block = allowed_blocks[identity]
    else:
        block = None
    quote = st.session_state.get(f"{prefix}_quote", "")
    if block is None or not isinstance(quote, str) or not quote_in_source(quote, block.text):
        quote = ""
    st.caption(f"原件：{source.name}")
    try:
        data = load_original(service.repo, source)
    except AppError as exc:
        st.error(str(exc))
        data = None
    if data is not None:
        if source.extension == ".pdf":
            try:
                count = pdf_page_count(data)
                page_key = f"{prefix}_page"
                current_page = st.session_state.get(page_key)
                if page_key not in st.session_state or (
                    current_page is not None and (
                        not isinstance(current_page, int) or isinstance(current_page, bool)
                        or not 1 <= current_page <= count
                    )
                ):
                    st.session_state[page_key] = 1
                page = st.number_input(
                    "PDF 页码", min_value=1, max_value=count, value=None, step=1,
                    key=page_key,
                )
                zoom = st.selectbox(
                    "页面缩放", [50, 75, 100, 125, 150, 200], index=2,
                    format_func=lambda value: f"{value}%", key=f"{prefix}_zoom",
                )
                if page is None:
                    st.info(f"请输入 1–{count} 之间的 PDF 页码；下方原文仍可核对。")
                else:
                    selected_block = block if block and block.page == page else None
                    bbox = selected_block.bbox if selected_block else None
                    excerpt = (quote or selected_block.text) if selected_block else ""
                    preview = render_pdf_page(
                        data, int(page), scale=zoom / 100 * 1.2, quote=excerpt,
                    )
                    st.iframe(_page_html(preview, zoom, bbox), height=620, alt="PDF 原始页面")
                    if preview.quote_boxes:
                        st.caption("橙框标出 PDF 文字层中匹配的位置。")
                    elif bbox:
                        st.caption("橙框为解析器提供的证据块区域，并非逐字引文框。")
                    else:
                        st.caption("当前页没有可靠的区域坐标；仅按原页及下方引文核对。")
                    if block and block.page and block.page != page:
                        st.caption(f"所选证据位于第 {block.page} 页，当前正在浏览第 {page} 页。")
            except AppError as exc:
                st.error(str(exc))
        st.download_button(
            "下载当前原件", data, file_name=source.name,
            mime="application/pdf" if source.extension == ".pdf" else "text/plain",
            key=f"{prefix}_download", on_click="ignore",
        )
    if quote:
        st.caption("所选字段引文")
        st.text(quote)
    if block:
        st.caption(
            f"解析原文 · {'PDF 第 ' + str(block.page) + ' 页' if block.page else '文本'}"
        )
        st.text(block.text)
        st.caption(f"原文块：{block.id}")
    else:
        st.info("当前文件没有可核对的解析块。")
