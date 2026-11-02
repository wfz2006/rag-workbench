"""The source viewer isolates documents, focus requests and original-file errors."""

import pytest
from streamlit.testing.v1 import AppTest

from rag_workbench.application.materials import MaterialsService
from rag_workbench.ui import material_viewer

from .test_material_literature import pdf_bytes


def _app(monkeypatch, service, document):
    monkeypatch.setattr(material_viewer, "_test_service", service, raising=False)
    monkeypatch.setattr(material_viewer, "_test_document", document, raising=False)
    return AppTest.from_string(
        "import streamlit as st\n"
        "from rag_workbench.ui import material_viewer as v\n"
        "docs=[d for d in v._test_service.repo.documents() if "
        "d.id==v._test_document.id or d.parent_id==v._test_document.id]\n"
        "blocks=[b for d in docs for b in v._test_service.repo.blocks(d.id)]\n"
        "v.render_source_viewer(v._test_service,v._test_document,blocks)\n",
        default_timeout=30,
    ).run()


def test_pdf_viewer_focuses_supplement_page_and_preserves_manual_page(monkeypatch, settings):
    pytest.importorskip("pypdfium2")
    service = MaterialsService(settings)
    main = service.ingest("main.pdf", pdf_bytes("Main page"))
    supplement = service.ingest(
        "SI.pdf", pdf_bytes("SI ONE", "SI TWO exact evidence"),
        role="supplement", parent_id=main.id,
    )
    block = service.repo.blocks(supplement.id)[1]
    app = _app(monkeypatch, service, main)
    app.session_state["materials_source_focus"] = {
        "document_id": supplement.id, "block_id": block.id, "quote": "exact evidence",
    }
    app.run()
    assert not app.exception
    assert app.selectbox(key="materials_viewer_document").value == supplement.id
    assert app.number_input(key=f"materials_viewer_{supplement.id}_page").value == 2
    assert any(item.value == "exact evidence" for item in app.text)
    assert app.get("iframe")
    app.number_input(key=f"materials_viewer_{supplement.id}_page").set_value(1).run()
    assert app.number_input(key=f"materials_viewer_{supplement.id}_page").value == 1
    assert "materials_source_focus" not in app.session_state


def test_text_focus_and_outside_scope_are_safe(monkeypatch, settings):
    service = MaterialsService(settings)
    main = service.ingest("main.txt", b"Alpha mobility 0.12.")
    outside = service.ingest("outside.txt", b"PRIVATE OUTSIDE TEXT.")
    other = service.repo.blocks(outside.id)[0]
    app = _app(monkeypatch, service, main)
    app.session_state["materials_source_focus"] = {
        "document_id": outside.id, "block_id": other.id, "quote": other.text,
    }
    app.run()
    assert not app.exception
    assert app.warning
    assert "PRIVATE OUTSIDE TEXT" not in " ".join(item.value for item in app.text)
    assert "Alpha mobility" in " ".join(item.value for item in app.text)


def test_missing_original_keeps_source_text_and_controls(monkeypatch, settings):
    service = MaterialsService(settings)
    document = service.ingest("paper.pdf", pdf_bytes("Original evidence text"))
    (service.repo.originals / f"{document.id}.pdf").unlink()
    app = _app(monkeypatch, service, document)
    assert not app.exception
    assert app.error
    assert "Original evidence text" in " ".join(item.value for item in app.text)
    assert app.selectbox(key="materials_viewer_document")


def test_no_renderer_keeps_download_and_evidence(monkeypatch, settings):
    from rag_workbench.domain.errors import AppError

    def unavailable(*args, **kwargs):
        raise AppError("material_preview_unavailable", "预览组件缺失测试")

    monkeypatch.setattr(material_viewer, "render_pdf_page", unavailable)
    service = MaterialsService(settings)
    document = service.ingest("paper.pdf", pdf_bytes("Source remains readable"))
    app = _app(monkeypatch, service, document)
    assert not app.exception
    assert app.error
    assert {item.label for item in app.get("download_button")} == {"下载当前原件"}
    assert "Source remains readable" in " ".join(item.value for item in app.text)


def test_only_validated_image_and_geometry_enter_preview_html():
    from rag_workbench.infrastructure.material_preview import PreviewPage

    page = PreviewPage(b"test-png-bytes", 100, 200)
    html = material_viewer._page_html(page, 150, (.1, .2, .4, .5))
    assert "data:image/png;base64," in html
    assert "width:150%" in html
    assert "选定证据块区域" in html
    assert "<script" not in html
    assert "application/pdf" not in html
    assert "选定证据块区域" not in material_viewer._page_html(page, 100)


def test_switching_from_supplement_to_main_resets_viewer_scope(monkeypatch, settings):
    service = MaterialsService(settings)
    main = service.ingest("paper.pdf", pdf_bytes("Main PDF source"))
    supplement = service.ingest(
        "SI.txt", b"Supplement text source", role="supplement", parent_id=main.id,
    )
    app = _app(monkeypatch, service, supplement)
    assert app.selectbox(key="materials_viewer_document").value == supplement.id

    monkeypatch.setattr(material_viewer, "_test_document", main)
    app.run()

    assert not app.exception
    assert app.selectbox(key="materials_viewer_document").value == main.id
    assert app.number_input(key=f"materials_viewer_{main.id}_page").value == 1
    app.selectbox(key="materials_viewer_document").set_value(supplement.id).run()
    app.run()
    assert app.selectbox(key="materials_viewer_document").value == supplement.id


def test_explicit_focus_can_select_supplement_after_scope_switch(monkeypatch, settings):
    service = MaterialsService(settings)
    main = service.ingest("paper.pdf", pdf_bytes("Main PDF source"))
    supplement = service.ingest(
        "SI.txt", b"Supplement exact quote", role="supplement", parent_id=main.id,
    )
    block = service.repo.blocks(supplement.id)[0]
    app = _app(monkeypatch, service, supplement)
    monkeypatch.setattr(material_viewer, "_test_document", main)
    app.session_state["materials_source_focus"] = {
        "document_id": supplement.id, "block_id": block.id, "quote": "exact quote",
    }
    app.run()
    assert not app.exception
    assert app.selectbox(key="materials_viewer_document").value == supplement.id
    assert any(item.value == "exact quote" for item in app.text)


def test_source_reset_clears_old_quote_without_changing_page_and_allows_new_focus(
    monkeypatch, settings,
):
    service = MaterialsService(settings)
    document = service.ingest("paper.pdf", pdf_bytes("First page", "Second exact evidence"))
    block = service.repo.blocks(document.id)[1]
    app = _app(monkeypatch, service, document)
    focus = {"document_id": document.id, "block_id": block.id, "quote": "exact evidence"}
    app.session_state["materials_source_focus"] = focus
    app.run()
    assert any(item.value == "exact evidence" for item in app.text)

    app.session_state["materials_source_reset"] = True
    app.run()
    assert not app.exception
    assert not any(item.value == "exact evidence" for item in app.text)
    assert app.number_input(key=f"materials_viewer_{document.id}_page").value == 2
    assert "materials_source_reset" not in app.session_state

    app.session_state["materials_source_reset"] = True
    app.session_state["materials_source_focus"] = focus
    app.run()
    assert any(item.value == "exact evidence" for item in app.text)


def test_empty_page_waits_for_input_without_hiding_original_text(monkeypatch, settings):
    service = MaterialsService(settings)
    document = service.ingest("paper.pdf", pdf_bytes("Evidence while page is empty"))
    app = _app(monkeypatch, service, document)
    page_key = f"materials_viewer_{document.id}_page"
    app.number_input(key=page_key).set_value(None).run()
    assert not app.exception
    assert app.number_input(key=page_key).value is None
    assert any("请输入" in item.value for item in app.info)
    assert "Evidence while page is empty" in " ".join(item.value for item in app.text)
    app.number_input(key=page_key).set_value(1).run()
    assert not app.exception
    assert app.get("iframe")
