"""Build the existing evidence JSON through source/field selection."""

import json

import streamlit as st

from rag_workbench.infrastructure.parsing import quote_in_source
from rag_workbench.ui.material_drafts import set_field


def _message(prefix, text, error=True):
    st.session_state[f"{prefix}_evidence_notice"] = (text, error)


def _change_source(prefix, blocks):
    block = blocks[st.session_state[f"{prefix}_source"]]
    st.session_state[f"{prefix}_quote"] = block.text[:10000]


def _add(prefix, blocks):
    try:
        evidence = json.loads(st.session_state[f"{prefix}_evidence"])
        if not isinstance(evidence, list):
            raise ValueError
    except (ValueError, TypeError):
        _message(prefix, "现有证据 JSON 无效，请先在高级编辑中修正，未覆盖原输入。")
        return
    block = blocks[st.session_state[f"{prefix}_source"]]
    quote = st.session_state[f"{prefix}_quote"]
    fields = st.session_state[f"{prefix}_fields"]
    if not fields or not quote_in_source(quote, block.text):
        _message(prefix, "请选择字段，并确保引文与所选原文的文字和数值完全一致。")
        return
    for field in fields:
        entry = {"field": field, "block_id": block.id, "quote": quote}
        if entry not in evidence:
            evidence.append(entry)
    set_field(prefix, "evidence", json.dumps(evidence, ensure_ascii=False, indent=2))
    _message(prefix, "原文证据已关联到草稿；保存后才会写入记录。", False)


def _remove(prefix):
    try:
        evidence = json.loads(st.session_state[f"{prefix}_evidence"])
        if not isinstance(evidence, list):
            raise ValueError
        removed = set(st.session_state.get(f"{prefix}_remove_refs", []))
        evidence = [item for index, item in enumerate(evidence) if index not in removed]
        set_field(prefix, "evidence", json.dumps(evidence, ensure_ascii=False, indent=2))
        st.session_state[f"{prefix}_remove_refs"] = []
    except (ValueError, TypeError):
        _message(prefix, "现有证据 JSON 无效，未移除内容。")


def evidence_editor(prefix, blocks, labels):
    with st.expander(
        "关联原文证据（无需填写 JSON）", key=f"{prefix}_linker", on_change="rerun",
    ):
        if notice := st.session_state.pop(f"{prefix}_evidence_notice", None):
            (st.error if notice[1] else st.success)(notice[0])
        if not blocks:
            st.info("当前文献没有原文块，请先导入可解析的原文。")
            return
        source_map = {block.id: block for block in blocks}
        selected = st.selectbox(
            "证据原文块", list(source_map), key=f"{prefix}_source",
            format_func=lambda identity: (
                (f"第 {source_map[identity].page} 页" if source_map[identity].page else "文本")
                + f" · {source_map[identity].text[:65]}"
            ),
            on_change=_change_source, args=(prefix, source_map),
        )
        st.text(source_map[selected].text)
        st.multiselect(
            "这段引文支持的字段", list(labels), default=["value_raw"],
            format_func=labels.get, key=f"{prefix}_fields",
        )
        if f"{prefix}_quote" not in st.session_state:
            st.session_state[f"{prefix}_quote"] = source_map[selected].text[:10000]
        st.text_area(
            "原文引文（可缩短，请保留原文）", key=f"{prefix}_quote", height=100,
            max_chars=10000,
        )
        st.button(
            "关联到字段证据", key=f"{prefix}_add_evidence",
            on_click=_add, args=(prefix, source_map),
        )
        try:
            evidence = json.loads(st.session_state[f"{prefix}_evidence"])
        except (ValueError, TypeError):
            evidence = None
        if isinstance(evidence, list) and evidence:
            options = list(range(len(evidence)))
            current = st.session_state.get(f"{prefix}_remove_refs", [])
            if any(index not in options for index in current):
                st.session_state[f"{prefix}_remove_refs"] = []
            st.multiselect(
                "移除已关联证据", options, key=f"{prefix}_remove_refs",
                format_func=lambda index: (
                    f"{index + 1} · {labels.get(evidence[index].get('field'), '未知字段')} · "
                    f"{str(evidence[index].get('quote', ''))[:55]}"
                    if isinstance(evidence[index], dict) else f"{index + 1} · 格式待修正"
                ),
            )
            st.button(
                "移除所选证据", key=f"{prefix}_remove_evidence",
                disabled=not st.session_state.get(f"{prefix}_remove_refs"),
                on_click=_remove, args=(prefix,),
            )
