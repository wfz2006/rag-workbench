"""Explore sourced knowledge points and their local literature."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

import streamlit as st

from rag_workbench.application.concept_graph import build_concept_map
from rag_workbench.application.knowledge_graph import (
    GraphEdge,
    build_document_graph,
    graph_documents,
)
from rag_workbench.bootstrap import Services
from rag_workbench.domain.errors import AppError
from rag_workbench.domain.models import Document

if TYPE_CHECKING:
    from rag_workbench.application.concept_graph import ConceptEdge, ConceptMap
    from rag_workbench.application.knowledge_graph import GraphEvidence

_SCOPE_KEY = "knowledge_graph_scope"
_CENTER_KEY = "knowledge_graph_center"
_EDGE_KEY = "knowledge_graph_edge"
_CHART_KEY = "knowledge_graph_active_chart"
_MODE_KEY = "knowledge_graph_mode"
_SEMANTIC = "#83c8e7"
_MATERIAL = "#f2b88c"
_EXPLORER_ASSETS = Path(__file__).with_name("assets") / "star_chart"
_KIND_STYLES = {
    "material": ("材料", _MATERIAL),
    "method": ("方法", "#8fd3bf"),
    "property": ("性质", "#bdadf2"),
    "topic": ("主题", _SEMANTIC),
}
_DOCUMENT_LEGEND = (
    '<div class="wb-star-legend"><span><i></i>语义相似</span>'
    '<span><i></i>共同材料</span></div>'
)
_STYLE = """
<style>
.st-key-knowledge_graph_page [data-testid="stText"] {
    white-space: pre-wrap; overflow-wrap: anywhere; font-family: var(--wb-font, sans-serif);
}
.st-key-knowledge_graph_page [data-testid="stEChartsChart"] {
    border-radius: 12px; overflow: hidden;
}
.wb-star-legend {display:flex; flex-wrap:wrap; gap:0.4rem 1.1rem; font-size:0.84rem;}
.wb-star-legend span {display:inline-flex; align-items:center; gap:0.4rem;}
.wb-star-legend i {display:inline-block; width:1.5rem; border-top:2px solid #4a90ae;}
.wb-star-legend span + span i {border-top:2px dashed #ba6e42;}
</style>
"""


@lru_cache(maxsize=1)
def _concept_renderer():
    # Register only trusted packaged assets. Indexed labels and quotes go through component data.
    bundle = (_EXPLORER_ASSETS / "vendor" / "echarts.min.js").read_text(encoding="utf-8")
    explorer = (_EXPLORER_ASSETS / "explorer.js").read_text(encoding="utf-8")
    return st.components.v2.component(
        "rag_workbench_star_explorer",
        html=(_EXPLORER_ASSETS / "explorer.html").read_text(encoding="utf-8"),
        css=(_EXPLORER_ASSETS / "explorer.css").read_text(encoding="utf-8"),
        js=f"{bundle}\nconst {{ init }} = WorkbenchECharts;\n{explorer}",
    )


def _document_labels(documents: tuple[Document, ...]) -> dict[str, str]:
    duplicates = Counter(item.name for item in documents)
    return {
        item.id: f"{item.name} · {item.id[:8]}" if duplicates[item.name] > 1 else item.name
        for item in documents
    }


def _edge_title(edge: GraphEdge | ConceptEdge) -> str:
    if edge.kind == "co_occurrence":
        return "同段提及"
    if edge.kind == "reviewed":
        return f"已审核记录 · {edge.label}" if edge.label else "已审核记录"
    if edge.kind == "shared_material":
        return f"共同材料 · {edge.material}" if edge.material else "共同材料"
    return f"语义相似 · {edge.score:.2f}" if edge.score is not None else "语义相似"


def _chart_spec(
    nodes: tuple[Document, ...], edges: tuple[GraphEdge, ...],
    center_id: str, names: dict[str, str],
) -> dict:
    pair_counts = Counter(tuple(sorted((edge.source, edge.target))) for edge in edges)
    pair_positions = Counter()
    sources: dict[str, dict[str, None]] = {node.id: {} for node in nodes}
    links = []
    for edge in edges:
        for excerpt in edge.evidence:
            if excerpt.document_id in sources:
                sources[excerpt.document_id][_source_title(excerpt)] = None
        dashed = edge.kind == "shared_material"
        pair = tuple(sorted((edge.source, edge.target)))
        count = pair_counts[pair]
        position = pair_positions[pair]
        pair_positions[pair] += 1
        curve = 0.38 * (position / (count - 1) - 0.5) if count > 1 else 0.06
        links.append({
            "id": edge.id,
            "source": pair[0],
            "target": pair[1],
            "name": _edge_title(edge),
            "lineStyle": {
                "color": _MATERIAL if dashed else _SEMANTIC,
                "width": 2.5 if dashed else 1.8,
                "opacity": 0.83,
                "type": "dashed" if dashed else "solid",
                "curveness": curve,
            },
        })
    stars = []
    for node in nodes:
        center = node.id == center_id
        color = _MATERIAL if center else _SEMANTIC
        rows = list(sources[node.id])[:3]
        if not rows:
            page = f" · 第 {node.source_page} 页" if node.source_page is not None else ""
            rows = [node.name + page]
        tooltip = "\n".join(" ".join(row.split())[:105] for row in rows)
        stars.append({
            "id": node.id,
            "name": names[node.id],
            "symbolSize": 26 if center else 19,
            "itemStyle": {"color": color, "shadowColor": color, "shadowBlur": 9 if center else 3},
            "label": {"fontWeight": 650 if center else 400},
            "tooltip": {"formatter": tooltip.replace("{", "｛").replace("}", "｝")},
        })
    return {
        "backgroundColor": "#111c2a",
        "animation": False,
        "tooltip": {
            "trigger": "item", "renderMode": "richText", "confine": True,
            "backgroundColor": "#22334a", "borderColor": "#52718e",
            "textStyle": {"color": "#f3f8fc"},
        },
        "series": [{
            "id": "document-network", "type": "graph", "layout": "force",
            "roam": True, "zoom": 1.45, "draggable": True, "selectedMode": "single",
            "force": {
                "initLayout": "circular", "edgeLength": [85, 135], "repulsion": 160,
                "gravity": 0.18, "layoutAnimation": False,
            },
            "label": {
                "show": True, "position": "bottom", "distance": 6, "color": "#dce8f5",
                "fontSize": 12, "width": 120, "overflow": "truncate",
                "fontFamily": '"Segoe UI", "Microsoft YaHei", sans-serif',
            },
            "emphasis": {"lineStyle": {"width": 4, "opacity": 1}},
            "select": {"itemStyle": {"borderColor": "#fff", "borderWidth": 2},
                       "lineStyle": {"width": 4}},
            "data": stars, "links": links,
        }],
    }


def _apply_selection(chart_key: str, node_ids: tuple[str, ...], edge_ids: tuple[str, ...]) -> None:
    state = st.session_state
    if state.get(_CHART_KEY) != chart_key:
        return
    selected = state.get(chart_key, {}).get("selection", {}).get("selected", ())
    edge_id = None
    for group in selected:
        if group.get("series_index") != 0:
            continue
        kind = group.get("data_type")
        ids = node_ids if kind == "node" else edge_ids if kind == "edge" else ()
        for index in group.get("data_indices", ()):
            if type(index) is not int or not 0 <= index < len(ids):
                continue
            if kind == "node" and ids[index] != state.get(_CENTER_KEY):
                state[_CENTER_KEY] = ids[index]
                state[_EDGE_KEY] = None
                return
            if kind == "edge":
                edge_id = ids[index]
    # Native deselection clears details as well. The keyboard selectbox remains usable.
    state[_EDGE_KEY] = edge_id


def _pick_edge(chart_key: str, picker_key: str) -> None:
    state = st.session_state
    if state.get(_CHART_KEY) == chart_key:
        state[_EDGE_KEY] = state.get(picker_key)


def _source_title(excerpt: GraphEvidence) -> str:
    page = f"第 {excerpt.page} 页" if excerpt.page is not None else "无固定页码"
    return f"{excerpt.document_name} · {page}"


def _source_excerpts(evidence: tuple[GraphEvidence, ...]) -> None:
    for excerpt in evidence:
        with st.container(border=True):
            st.text(_source_title(excerpt))
            st.text(excerpt.text)


def _edge_evidence(edge: GraphEdge, names: dict[str, str]) -> None:
    if edge.kind == "semantic":
        st.subheader(_edge_title(edge))
        st.caption("两段原文内容相近。分数表示文本相似度。")
    else:
        st.subheader("共同材料")
        if edge.material:
            st.text(edge.material)
        st.caption("两份资料的已审核记录都提到了这一材料。")
    st.text(f"{names[edge.source]}  ↔  {names[edge.target]}")
    _source_excerpts(edge.evidence)


def _clear_focus() -> None:
    for key in (_CENTER_KEY, _EDGE_KEY, _CHART_KEY):
        st.session_state.pop(key, None)


def _center_picker(label: str, names: dict[str, str]) -> str:
    state = st.session_state
    if state.get(_CENTER_KEY) not in names:
        state.pop(_CENTER_KEY, None)
        state.pop(_EDGE_KEY, None)
    return st.selectbox(label, tuple(names), format_func=names.__getitem__, key=_CENTER_KEY)


def _render_neighborhood(
    scope: tuple[str, int | None], center_id: str, names: dict[str, str],
    nodes: tuple[Document, ...], all_edges: tuple[GraphEdge, ...],
) -> GraphEdge | None:
    state = st.session_state
    node_ids = tuple(node.id for node in nodes)
    edges = {edge.id: edge for edge in all_edges}
    edge_ids = tuple(edges)
    # Never apply an event from a different ordered graph or workspace.
    identity = json.dumps((scope, center_id, node_ids, edge_ids)).encode()
    view_id = hashlib.sha256(identity).hexdigest()[:20]
    chart_key = f"knowledge_graph_chart_{view_id}"
    if state.get(_CHART_KEY) != chart_key:
        state.pop(_EDGE_KEY, None)
    state[_CHART_KEY] = chart_key
    st.html(_DOCUMENT_LEGEND)
    st.echarts_chart(
        _chart_spec(nodes, all_edges, center_id, names),
        key=chart_key,
        theme=None,
        height=456,
        renderer="svg",
        on_select=lambda: _apply_selection(chart_key, node_ids, edge_ids),
        alt="文献关系图。实线表示语义相似，虚线表示共同材料。",
    )
    if not edges:
        return None

    if state.get(_EDGE_KEY) not in edges:
        state.pop(_EDGE_KEY, None)
    labels = {
        edge.id: f"{_edge_title(edge)} · {names[edge.source]} ↔ {names[edge.target]}"
        for edge in edges.values()
    }
    selected_id = state.get(_EDGE_KEY)
    picker_key = f"knowledge_graph_edge_picker_{view_id}"
    if state.get(picker_key) != selected_id:
        state[picker_key] = selected_id
    st.selectbox(
        "查看连线依据", edge_ids, index=None, key=picker_key,
        format_func=labels.__getitem__, placeholder="选择一条关联",
        on_change=_pick_edge, args=(chart_key, picker_key),
    )
    return edges.get(selected_id)


def _concept_payload(concepts: ConceptMap) -> dict[str, object]:
    def sources(evidence: tuple[GraphEvidence, ...]) -> list[dict[str, str]]:
        return [{"title": _source_title(item), "text": item.text} for item in evidence]

    duplicates = Counter(node.label for node in concepts.nodes)
    nodes = []
    neighborhoods = {}
    visible_edges: dict[str, ConceptEdge] = {}
    for node in concepts.nodes:
        kind_label, color = _KIND_STYLES[node.kind]
        label = f"{node.label} · {kind_label}" if duplicates[node.label] > 1 else node.label
        nodes.append({
            "id": node.id, "label": label, "kind": node.kind,
            "color": color, "kindLabel": kind_label, "evidence": sources(node.evidence),
        })
        view = concepts.focus(node.id, max_neighbors=12)
        neighborhoods[node.id] = {
            "nodes": [neighbor.id for neighbor in view.nodes],
            "edges": [edge.id for edge in view.edges],
        }
        visible_edges.update((edge.id, edge) for edge in view.edges)
    return {
        "initial": concepts.nodes[0].id,
        "nodes": nodes,
        "edges": [{
            "id": edge.id, "source": edge.source, "target": edge.target,
            "kind": edge.kind, "title": _edge_title(edge), "evidence": sources(edge.evidence),
        } for edge in visible_edges.values()],
        "neighborhoods": neighborhoods,
    }


def _concept_view(services: Services, scope: tuple[str, int | None]) -> None:
    concepts = build_concept_map(services.knowledge)
    if concepts.material_warning:
        st.warning(concepts.material_warning)
    if not concepts.nodes:
        st.info("当前知识库暂无可展示的知识点。可导入含正文的资料或审核材料记录后再查看。")
        return
    identity = hashlib.sha256(json.dumps(scope).encode()).hexdigest()[:20]
    _concept_renderer()(data=_concept_payload(concepts), key=f"knowledge_star_{identity}")


def _document_view(services: Services, scope: tuple[str, int | None]) -> None:
    st.caption("点资料查看周边关联，点连线查看原文片段与页码。")
    documents = graph_documents(services.knowledge)
    if not documents:
        st.info("当前知识库还没有可显示的资料。请在「知识库管理」上传文件并完成索引。")
        return
    names = _document_labels(documents)
    center_id = _center_picker("当前文献", names)
    graph = build_document_graph(services.knowledge, center_id)
    if not graph.nodes:
        st.info("这份资料当前不可用。请重新选择一份资料。")
        return
    if graph.material_warning:
        st.warning(graph.material_warning)
    for node in graph.nodes:
        names.setdefault(node.id, node.name)
    edge = _render_neighborhood(scope, center_id, names, graph.nodes, graph.edges)
    if edge is not None:
        _edge_evidence(edge, names)
    elif not graph.edges:
        st.info("这份资料暂无可展示的关联。可以换一份文献继续探索。")


def knowledge_graph_page(services: Services) -> None:
    scope = (str(services.settings.data_dir.resolve()), services.settings.spoiler_max_page)
    state = st.session_state
    if state.get(_SCOPE_KEY) != scope:
        _clear_focus()
        state.pop(_MODE_KEY, None)
        state[_SCOPE_KEY] = scope

    with st.container(key="knowledge_graph_page"):
        st.html(_STYLE)
        st.title("知识星图")
        mode = st.radio(
            "浏览方式", ("知识点", "文献关系"), horizontal=True, label_visibility="collapsed",
            key=_MODE_KEY, on_change=_clear_focus,
        )
        try:
            if mode == "知识点":
                _concept_view(services, scope)
            else:
                _document_view(services, scope)
        except AppError as exc:
            st.warning(str(exc))
