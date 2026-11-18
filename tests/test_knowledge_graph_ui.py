"""Exercise the star map's native selection callbacks and evidence rendering."""

import json
from types import SimpleNamespace

from streamlit.testing.v1 import AppTest


def _app(monkeypatch, tmp_path, *, empty=False, knowledge_first=False):
    from rag_workbench.ui import knowledge_graph as ui

    names = ("有机薄膜.pdf", "薄膜工艺.pdf", "器件设计.pdf")
    papers = tuple(
        SimpleNamespace(id=str(i), name=name, source_page=None) for i, name in enumerate(names)
    )
    proof = (
        SimpleNamespace(document_id="0", document_name=names[0], page=4, text="PTB7 薄膜迁移率"),
        SimpleNamespace(document_id="1", document_name=names[1], page=9, text="PTB7 材料与工艺"),
    )
    semantic = SimpleNamespace(
        id="semantic", source="0", target="1", kind="semantic", score=0.81,
        material=None, evidence=proof,
    )
    shared = SimpleNamespace(
        id="shared", source="0", target="1", kind="shared_material", score=None,
        material="PTB7", evidence=proof,
    )
    concepts = (
        SimpleNamespace(id="p", label="PTB7", kind="material", evidence=proof[:1]),
        SimpleNamespace(id="s", label="旋涂", kind="method", evidence=proof[1:]),
        SimpleNamespace(id="m", label="迁移率", kind="property", evidence=proof),
    )
    discussed = SimpleNamespace(
        id="co-occurrence", source="p", target="s", kind="co_occurrence",
        label="同段提及", evidence=proof[:1],
    )
    reviewed = SimpleNamespace(
        id="reviewed", source="p", target="m", kind="reviewed",
        label="测量指标", evidence=proof,
    )

    def build(_knowledge, center, **_limits):
        if center in {"0", "1"}:
            nodes = papers[:2] if center == "0" else (papers[1], papers[0])
            return SimpleNamespace(nodes=nodes, edges=(semantic, shared), material_warning=None)
        return SimpleNamespace(nodes=(papers[2],), edges=(), material_warning=None)

    monkeypatch.setattr(ui, "graph_documents", lambda _knowledge: () if empty else papers)
    monkeypatch.setattr(ui, "build_document_graph", build)

    def focus(center, **_limits):
        selected = [node for node in concepts if node.id == center]
        edges = tuple(
            edge for edge in (discussed, reviewed) if center in (edge.source, edge.target)
        )
        neighbors = {node for edge in edges for node in (edge.source, edge.target)} - {center}
        nodes = (*selected, *(node for node in concepts if node.id in neighbors))
        return SimpleNamespace(nodes=nodes, edges=edges)

    monkeypatch.setattr(
        ui, "build_concept_map", lambda _knowledge: SimpleNamespace(
            nodes=() if empty else concepts, focus=focus, material_warning=None,
        ), raising=False,
    )
    rendered = {}
    monkeypatch.setattr(ui, "_test_explorer", rendered, raising=False)

    def mount(*, data, key):
        rendered.update(data=data, key=key)

    monkeypatch.setattr(ui, "_concept_renderer", lambda: mount, raising=False)
    services = SimpleNamespace(
        knowledge=object(),
        settings=SimpleNamespace(data_dir=tmp_path, spoiler_max_page=None),
    )
    monkeypatch.setattr(ui, "_test_services", services, raising=False)
    app = AppTest.from_string(
        "from rag_workbench.ui import knowledge_graph as ui\n"
        "ui.knowledge_graph_page(ui._test_services)\n"
    ).run()
    if not knowledge_first:
        app.radio(key="knowledge_graph_mode").set_value("文献关系").run()
    return app


def _select_chart_item(app, kind, index):
    # Simulate the native ECharts widget protocol. Browser hit targets are checked separately.
    widget_states = app._tree.get_widget_states()
    widget_states.widgets.add(
        id=app.get("echarts_chart")[0].proto.id,
        string_value=json.dumps({
            "selection": {
                "selected": [{"series_index": 0, "data_type": kind, "data_indices": [index]}],
                "areas": [],
            },
        }),
    )
    return app._run(widget_states)


def _edge_picker(app):
    return next(widget for widget in app.selectbox if widget.label == "查看连线依据")


def test_graph_clicks_show_edge_evidence_and_refocus_the_next_document(monkeypatch, tmp_path):
    app = _app(monkeypatch, tmp_path)
    assert not app.exception
    chart = app.get("echarts_chart")[0].proto
    initial_chart = chart.id
    stars = json.loads(chart.spec)["series"][0]["data"]
    assert stars[1]["tooltip"]["formatter"] == "薄膜工艺.pdf · 第 9 页"

    _select_chart_item(app, "edge", 0)
    assert not app.exception
    assert any("语义相似 · 0.81" in item.value for item in app.subheader)
    assert _edge_picker(app).value == "semantic"

    _edge_picker(app).set_value("shared").run()
    assert not app.exception
    assert any("共同材料" in item.value for item in app.subheader)
    assert {item.value for item in app.text} >= {"PTB7 薄膜迁移率", "PTB7 材料与工艺"}
    assert any("第 4 页" in item.value for item in app.text)
    assert any("第 9 页" in item.value for item in app.text)

    initial_picker = _edge_picker(app).proto.id
    _select_chart_item(app, "node", 1)
    assert not app.exception
    assert app.selectbox(key="knowledge_graph_center").value == "1"
    assert app.get("echarts_chart")[0].proto.id != initial_chart
    assert _edge_picker(app).proto.id != initial_picker
    assert _edge_picker(app).value is None
    assert not any("PTB7 薄膜迁移率" in item.value for item in app.text)

    _select_chart_item(app, "node", 1)
    assert app.selectbox(key="knowledge_graph_center").value == "0"
    assert _edge_picker(app).value is None


def test_graph_ignores_invalid_indices_and_handles_empty_library(monkeypatch, tmp_path):
    app = _app(monkeypatch, tmp_path)
    _select_chart_item(app, "node", -1)
    assert not app.exception
    assert app.selectbox(key="knowledge_graph_center").value == "0"

    app = _app(monkeypatch, tmp_path, empty=True)
    assert not app.exception
    assert not app.get("echarts_chart")
    assert any("知识库管理" in item.value for item in app.info)


def test_a_new_workspace_clears_graph_selection(monkeypatch, tmp_path):
    from rag_workbench.ui import knowledge_graph as ui

    app = _app(monkeypatch, tmp_path)
    _select_chart_item(app, "edge", 1)
    assert any("共同材料" in item.value for item in app.subheader)

    ui._test_services.settings.data_dir = tmp_path / "second"
    app.run()
    assert not app.exception
    assert app.radio(key="knowledge_graph_mode").value == "知识点"
    assert all(item.value != "共同材料" for item in app.subheader)
    assert not app.get("echarts_chart")
    assert ui._test_explorer["data"]["initial"] == "p"


def test_knowledge_points_are_default_and_explore_evidence_and_neighbors(monkeypatch, tmp_path):
    from rag_workbench.ui import knowledge_graph as ui

    app = _app(monkeypatch, tmp_path, knowledge_first=True)
    assert not app.exception
    assert not app.get("echarts_chart")
    data = ui._test_explorer["data"]
    assert data["initial"] == "p"
    nodes = {node["id"]: node for node in data["nodes"]}
    assert {node["label"] for node in nodes.values()} == {"PTB7", "旋涂", "迁移率"}
    assert nodes["s"]["evidence"] == [{
        "title": "薄膜工艺.pdf · 第 9 页", "text": "PTB7 材料与工艺",
    }]
    edges = {edge["id"]: edge for edge in data["edges"]}
    assert edges["reviewed"]["kind"] == "reviewed"
    assert edges["reviewed"]["evidence"][0]["title"] == "有机薄膜.pdf · 第 4 页"
    assert data["neighborhoods"]["s"] == {"nodes": ["s", "p"], "edges": ["co-occurrence"]}


def test_empty_knowledge_view_keeps_filenames_out_of_the_graph(monkeypatch, tmp_path):
    app = _app(monkeypatch, tmp_path, empty=True, knowledge_first=True)
    assert not app.exception
    assert not app.get("echarts_chart")
    assert any("知识点" in item.value for item in app.info)
