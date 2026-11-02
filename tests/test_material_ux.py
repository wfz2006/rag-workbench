"""State transitions in the real materials page, with isolated local documents."""

from uuid import uuid4

from rag_workbench.application.materials import MaterialsService
from rag_workbench.domain.errors import AppError
from rag_workbench.domain.materials import MaterialRecord

from .test_material_ui import _app, _export_downloads


def _record(service, document, name="Alpha"):
    return service.add_record(MaterialRecord(
        id=uuid4().hex, document_id=document.id, material_name=name,
        property_name="mobility", value_raw="0.12",
    ))


def test_consent_is_reset_when_document_or_model_scope_changes(settings, monkeypatch):
    settings = settings.model_copy(update={"api_key": type(settings.api_key)("fake-key")})
    service = MaterialsService(settings)
    first = service.ingest("first.txt", b"Alpha mobility 0.12.")
    second = service.ingest("second.txt", b"Beta mobility 0.13.")
    app = _app(service, settings, monkeypatch)
    app.selectbox(key="materials_selected_doc").set_value(first.id).run()
    app.checkbox(key="materials_extract_consent").check().run()
    assert not app.button(key="materials_extract").disabled
    app.selectbox(key="materials_selected_doc").set_value(second.id).run()
    assert not app.checkbox(key="materials_extract_consent").value
    assert app.button(key="materials_extract").disabled
    app.checkbox(key="materials_extract_consent").check().run()
    app.selectbox(key="materials_domain").set_value("opv").run()
    assert not app.checkbox(key="materials_extract_consent").value


def test_manual_success_clears_form_and_cannot_repeat_old_values(settings, monkeypatch):
    service = MaterialsService(settings)
    document = service.ingest("paper.txt", b"Alpha mobility 0.12.")
    app = _app(service, settings, monkeypatch)
    prefix = f"materials_new_{document.id}"
    values = {"material_name": "Alpha", "property_name": "mobility", "value_raw": "0.12"}
    for field, value in values.items():
        app.text_input(key=f"{prefix}_{field}").set_value(value)
    app.button(key="materials_add_candidate").click().run()
    assert not app.exception
    assert app.text_input(key=f"{prefix}_material_name").value == ""
    app.button(key="materials_add_candidate").click().run()
    assert len(service.repo.records(document.id)) == 1
    assert app.error


def test_record_draft_survives_selection_change_and_is_not_saved_implicitly(settings, monkeypatch):
    service = MaterialsService(settings)
    doc = service.ingest("paper.txt", b"Alpha Beta mobility 0.12 0.13.")
    first, second = _record(service, doc), _record(service, doc, "Beta")
    app = _app(service, settings, monkeypatch)
    app.selectbox(key="materials_selected_record").set_value(first.id).run()
    key = f"materials_edit_{first.id}_1_value_raw"
    app.text_input(key=key).set_value("0.13").run()
    app.selectbox(key="materials_selected_record").set_value(second.id).run()
    app.selectbox(key="materials_selected_record").set_value(first.id).run()
    assert not app.exception
    assert app.text_input(key=key).value == "0.13"
    assert service.repo.record(first.id).value_raw == "0.12"


def test_export_failure_retires_old_bundle_but_csv_remains_available(settings, monkeypatch):
    service = MaterialsService(settings)
    document = service.ingest("paper.txt", b"Alpha mobility 0.12.")
    _record(service, document)
    app = _app(service, settings, monkeypatch)
    app.checkbox(key="materials_approved_only").uncheck().run()
    app.button(key="materials_prepare_export").click().run()
    assert len(_export_downloads(app)) == 2

    def fail(*args, **kwargs):
        raise AppError("material_original_missing", "原件缺失测试")

    monkeypatch.setattr(service, "export_bundle", fail)
    app.button(key="materials_prepare_export").click().run()
    assert not app.exception
    assert {item.label for item in _export_downloads(app)} == {"下载 CSV"}
    assert app.error
    app.run()
    assert any("原件缺失测试" in item.value for item in app.error)


def test_navigation_restores_current_document_record_and_draft(settings, monkeypatch):
    from streamlit.testing.v1 import AppTest

    from rag_workbench.bootstrap import build_services
    from rag_workbench.ui import app as ui_app

    from .fakes import FakeEmbedder, FakeProvider
    from .test_material_ui import ROOT

    service = MaterialsService(settings)
    first = service.ingest("first.txt", b"Alpha mobility 0.12.")
    service.ingest("second.txt", b"Beta mobility 0.13.")
    record = _record(service, first)
    services = build_services(settings, embedder=FakeEmbedder(), provider=FakeProvider())
    monkeypatch.setattr(ui_app, "load_settings", lambda: settings)
    monkeypatch.setattr(ui_app, "resources", lambda *args: services)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    app.sidebar.radio[0].set_value("材料文献").run()
    app.selectbox(key="materials_selected_doc").set_value(first.id).run()
    key = f"materials_edit_{record.id}_1_value_raw"
    app.text_input(key=key).set_value("0.13").run()
    app.sidebar.radio[0].set_value("系统设置").run()
    app.sidebar.radio[0].set_value("材料文献").run()
    assert not app.exception
    assert app.selectbox(key="materials_selected_doc").value == first.id
    assert app.text_input(key=key).value == "0.13"


def test_manual_creation_reveals_new_candidate_when_filter_hid_candidates(settings, monkeypatch):
    service = MaterialsService(settings)
    document = service.ingest("paper.txt", b"Alpha mobility 0.12.")
    _record(service, document)
    app = _app(service, settings, monkeypatch)
    app.selectbox(key=f"materials_filter_{document.id}").set_value("approved").run()
    prefix = f"materials_new_{document.id}"
    fields = (("material_name", "Alpha"), ("property_name", "mobility"), ("value_raw", "0.12"))
    for field, value in fields:
        app.text_input(key=f"{prefix}_{field}").set_value(value)
    app.button(key="materials_add_candidate").click().run()
    assert not app.exception
    assert app.selectbox(key=f"materials_filter_{document.id}").value == "all"
    assert app.selectbox(key="materials_selected_record").value in {
        record.id for record in service.repo.records(document.id)
    }
