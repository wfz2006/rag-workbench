"""Evidence editing and extraction consent stay local until an explicit action."""

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import SecretStr

from rag_workbench.application.materials import MaterialsService
from rag_workbench.domain.materials import MaterialRecord

from .test_material_ui import _app

REQUIRED_FIELDS = ["material_name", "property_name", "value_raw"]


def _candidate(service):
    document = service.ingest("paper.txt", b"Alpha mobility 0.12.")
    record = MaterialRecord(
        id=uuid4().hex,
        document_id=document.id,
        material_name="Alpha",
        property_name="mobility",
        value_raw="0.12",
    )
    service.add_record(record)
    return document, record


def _link_required_evidence(app, prefix):
    app.multiselect(key=f"{prefix}_fields").set_value(REQUIRED_FIELDS)
    app.button(key=f"{prefix}_add_evidence").click().run()
    assert not app.exception


def test_graphical_evidence_editor_can_approve_without_writing_json(settings, monkeypatch):
    service = MaterialsService(settings)
    _, record = _candidate(service)
    app = _app(service, settings, monkeypatch)
    prefix = f"materials_edit_{record.id}_1"

    _link_required_evidence(app, prefix)
    assert len(json.loads(app.text_area(key=f"{prefix}_evidence").value)) == 3
    assert service.repo.record(record.id).evidence == []
    app.checkbox(key="materials_review_confirm").check().run()
    app.button(key="materials_approve").click().run()

    assert not app.exception
    saved = service.repo.record(record.id)
    assert saved.status == "approved"
    assert {item.field for item in saved.evidence} == set(REQUIRED_FIELDS)
    assert saved.revision == 2


def test_repeated_graphical_link_keeps_one_copy_per_field(settings, monkeypatch):
    service = MaterialsService(settings)
    _, record = _candidate(service)
    app = _app(service, settings, monkeypatch)
    prefix = f"materials_edit_{record.id}_1"
    _link_required_evidence(app, prefix)
    first = app.text_area(key=f"{prefix}_evidence").value

    app.button(key=f"{prefix}_add_evidence").click().run()

    assert not app.exception
    assert app.text_area(key=f"{prefix}_evidence").value == first
    assert len(json.loads(first)) == 3
    assert service.repo.record(record.id).revision == 1


def test_invalid_quote_does_not_replace_existing_evidence_or_field_draft(settings, monkeypatch):
    service = MaterialsService(settings)
    _, record = _candidate(service)
    app = _app(service, settings, monkeypatch)
    prefix = f"materials_edit_{record.id}_1"
    _link_required_evidence(app, prefix)
    first = app.text_area(key=f"{prefix}_evidence").value
    app.text_input(key=f"{prefix}_material_name").set_value("Draft Alpha").run()
    app.text_area(key=f"{prefix}_quote").set_value("This quote is absent from the paper.")

    app.button(key=f"{prefix}_add_evidence").click().run()

    assert not app.exception
    assert any("完全一致" in item.value for item in app.error)
    assert app.text_area(key=f"{prefix}_evidence").value == first
    assert app.text_input(key=f"{prefix}_material_name").value == "Draft Alpha"
    saved = service.repo.record(record.id)
    assert saved.material_name == "Alpha"
    assert saved.evidence == []


def test_bad_evidence_json_survives_graphical_link_error(settings, monkeypatch):
    service = MaterialsService(settings)
    _, record = _candidate(service)
    app = _app(service, settings, monkeypatch)
    prefix = f"materials_edit_{record.id}_1"
    bad_json = '[{"field": "value_raw", unfinished'
    app.text_area(key=f"{prefix}_evidence").set_value(bad_json).run()

    app.button(key=f"{prefix}_add_evidence").click().run()

    assert not app.exception
    assert any("JSON 无效" in item.value for item in app.error)
    assert app.text_area(key=f"{prefix}_evidence").value == bad_json
    assert app.session_state["materials_drafts"][prefix]["evidence"] == bad_json
    assert service.repo.record(record.id).revision == 1


def test_workspace_switch_clears_drafts_even_for_same_document_id(settings, monkeypatch):
    from rag_workbench.ui import materials

    first_service = MaterialsService(settings)
    document = first_service.ingest("paper.txt", b"Alpha mobility 0.12.")
    second_settings = settings.model_copy(update={"data_dir": settings.data_dir / "other"})
    second_service = MaterialsService(second_settings)
    second_document = second_service.ingest("paper.txt", b"Alpha mobility 0.12.")
    assert second_document.id == document.id
    app = _app(first_service, settings, monkeypatch)
    prefix = f"materials_new_{document.id}"
    app.text_input(key=f"{prefix}_material_name").set_value("Workspace One Draft").run()
    assert prefix in app.session_state["materials_drafts"]

    monkeypatch.setattr(materials, "MaterialsService", lambda *_: second_service)
    monkeypatch.setattr(materials, "_test_settings", second_settings)
    app.run()

    assert not app.exception
    assert app.text_input(key=f"{prefix}_material_name").value == ""
    assert prefix not in app.session_state.get("materials_drafts", {})
    assert first_service.repo.records() == second_service.repo.records() == []


def _provider_settings(settings):
    return settings.model_copy(
        update={
            "api_key": SecretStr("synthetic-local-test-key"),
            "llm_model": "synthetic-model",
            "llm_base_url": "https://provider.invalid/v1",
        }
    )


def test_adding_supplement_revokes_existing_extraction_consent(settings, monkeypatch):
    service = MaterialsService(settings, SimpleNamespace(settings=_provider_settings(settings)))
    main = service.ingest("main.txt", b"Alpha mobility 0.12.")
    app = _app(service, settings, monkeypatch)
    app.checkbox(key="materials_extract_consent").check().run()
    assert not app.button(key="materials_extract").disabled

    service.ingest(
        "si.txt", b"Supplementary sample conditions.", role="supplement", parent_id=main.id,
    )
    app.run()

    assert not app.exception
    assert app.checkbox(key="materials_extract_consent").value is False
    assert app.button(key="materials_extract").disabled
    assert service.repo.runs() == []


@pytest.mark.parametrize(
    ("field", "value"),
    [("llm_model", "changed-synthetic-model"), ("llm_base_url", "https://changed.invalid/v1")],
)
def test_actual_provider_change_revokes_extraction_consent(settings, monkeypatch, field, value):
    provider = SimpleNamespace(settings=_provider_settings(settings))
    service = MaterialsService(settings, provider)
    service.ingest("paper.txt", b"Alpha mobility 0.12.")
    app = _app(service, settings, monkeypatch)
    app.checkbox(key="materials_extract_consent").check().run()
    assert not app.button(key="materials_extract").disabled

    provider.settings = provider.settings.model_copy(update={field: value})
    app.run()

    assert not app.exception
    assert app.checkbox(key="materials_extract_consent").value is False
    assert app.button(key="materials_extract").disabled
    assert service.repo.runs() == []
