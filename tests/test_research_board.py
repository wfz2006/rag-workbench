from streamlit.testing.v1 import AppTest

from rag_workbench.bootstrap import build_services
from rag_workbench.domain.research import EvidenceSource
from rag_workbench.ui import research_board as ui
from rag_workbench.ui.research_capture import choice_labels


def test_duplicate_names_cannot_point_to_another_question_or_the_create_option():
    labels = choice_labels({"a": "同名", "b": "同名", "c": "新建研究问题", "d": "同名 · 1"},
                           "新建研究问题")
    assert len(set(labels.values())) == 4
    assert "新建研究问题" not in labels.values()
    assert labels["d"] == "同名 · 1"


def test_board_edits_and_exports_stored_annotation(settings, monkeypatch):
    service = build_services(settings).research
    monkeypatch.setattr(ui, "_test_service", service, raising=False)
    app = AppTest.from_string(
        "from rag_workbench.ui import research_board as ui\n"
        "ui.research_board_page(ui._test_service)"
    ).run()
    app.text_input[0].set_value("结构变化")
    next(w for w in app.button if w.label == "创建问题").click().run()
    assert not app.exception
    question = service.repo.questions()[0][0]
    app.text_area[0].set_value("比较两个阶段")
    next(w for w in app.button if w.label == "添加主张").click().run()
    claim = service.repo.board(question)[1][0][0]
    card, _ = service.repo.capture(claim, EvidenceSource(
        origin="star", document_id="a" * 64, name="同名论文.pdf", locator="chunk",
        page=4, quote="original text\n```\ncontinued",
    ))
    service.repo.annotate(card, 1, "support", "核实过")
    app.run()
    assert not app.exception
    assert any(item.value == "核实过" for item in app.text)
    note = service.export_markdown(question).decode()
    assert "**支持** · 知识星图" in note
    assert "同名论文\\.pdf · 第 4 页" in note
    assert "````\noriginal text\n```\ncontinued\n````" in note
    assert "核实过" in note
    assert "a" * 64 in note
