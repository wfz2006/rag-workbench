import hashlib
import json

import pytest
from pydantic import ValidationError

from rag_workbench.application.materials import MaterialsService
from rag_workbench.domain.errors import AppError
from rag_workbench.domain.materials import EvidenceReference, MaterialRecord
from rag_workbench.infrastructure.material_draft_store import DraftSnapshot
from rag_workbench.ui.material_recovery import _recovery_issue
from rag_workbench.ui.materials import _editor_defaults, _manual_identity

from .test_material_ui import _app

LEGACY_FIELDS = (
    "material_name", "sample_label", "property_name", "value_raw", "unit_raw",
    "material_structure", "device_structure", "process", "conditions", "measurement_method",
    "carrier", "regime", "statistic",
)


def _hash(payload, prefix=""):
    return hashlib.sha256((prefix + json.dumps(
        payload, sort_keys=True, ensure_ascii=False,
    )).encode()).hexdigest()


def _paper(settings):
    service = MaterialsService(settings)
    document = service.ingest("paper.txt", b"Alpha mobility 0.25 cm2/Vs was measured.")
    supplement = service.ingest(
        "supplement.txt", "Alpha was annealed at 180 °C.".encode(),
        role="supplement", parent_id=document.id,
    )
    return service, document, supplement


def _record(service, document):
    block = service.repo.blocks(document.id)[0]
    return MaterialRecord(
        id="observation", document_id=document.id, domain="ofet",
        material_name="Alpha", property_name="mobility", value_raw="0.25", unit_raw="cm2/Vs",
        evidence=[EvidenceReference(field=field, block_id=block.id, quote=block.text) for field in (
            "material_name", "property_name", "value_raw", "unit_raw",
        )],
    )


def test_structured_values_keep_supplement_evidence_and_reject_partial_quantity(settings):
    service, document, supplement = _paper(settings)
    row = service.add_record(_record(service, document))
    si_block = service.repo.blocks(supplement.id)[0]
    source_block = service.repo.blocks(document.id)[0]
    updated = row.model_copy(update={
        "anneal_temperature": "80 °C", "observation_origin": "measured",
        "field_availability": {"film_thickness": "not_reported"},
        "evidence": [
            *row.evidence,
            EvidenceReference(
                field="anneal_temperature", block_id=si_block.id, quote=si_block.text,
            ),
            EvidenceReference(
                field="observation_origin", block_id=source_block.id, quote=source_block.text,
            ),
        ],
    })
    with pytest.raises(AppError, match="anneal_temperature"):
        service.review(row.id, updated, status="approved")
    updated = updated.model_copy(update={"anneal_temperature": "180 °C"})
    approved = service.review(row.id, updated, status="approved")
    assert service.repo.record(row.id) == approved
    assert approved.anneal_temperature == "180 °C"
    assert approved.observation_origin == "measured"
    assert approved.field_availability == {"film_thickness": "not_reported"}
    assert "180 °C" in service.export_csv().decode("utf-8-sig")
    with pytest.raises(ValidationError):
        MaterialRecord.model_validate({**approved.model_dump(), "film_thickness": "20 nm"})


def test_legacy_ids_and_old_drafts_remain_usable(settings):
    service, document, _ = _paper(settings)
    row = _record(service, document)
    payload = {field: getattr(row, field) for field in LEGACY_FIELDS}
    expected = _hash({
        "document_id": document.id, "domain": "ofet", **payload,
        "evidence": sorted([ref.model_dump() for ref in row.evidence], key=lambda ref: json.dumps(
            ref, sort_keys=True,
        )),
    })
    assert _manual_identity(row) == expected

    class Provider:
        def complete(self, *args, **kwargs):
            return json.dumps({"records": [{**payload, "evidence": [
                ref.model_dump() for ref in row.evidence
            ]}]})

    service.provider = Provider()
    service.extract(document.id)
    assert service.repo.records(document.id)[0].id == _hash(payload, document.id + "ofet")

    legacy_draft = DraftSnapshot(
        key=f"materials_new_{document.id}", values={"material_name": "Alpha"},
        document_id=document.id, kind="manual", record_id=None, base_revision=None,
        version=1, updated_at=row.created_at,
    )
    assert _recovery_issue(service, legacy_draft) is None
    structured_draft = DraftSnapshot(
        key=legacy_draft.key, values={
            "anneal_temperature": "180 °C", "availability__film_thickness": "not_extracted",
            "observation_origin": "measured",
        },
        document_id=document.id, kind="manual", record_id=None, base_revision=None,
        version=2, updated_at=row.created_at,
    )
    assert _recovery_issue(service, structured_draft) is None
    assert _editor_defaults(row.model_dump())["anneal_temperature"] == ""


def test_structured_editor_restores_draft_and_saves_evidence(settings, monkeypatch):
    service, document, supplement = _paper(settings)
    row = service.add_record(_record(service, document))
    prefix = f"materials_edit_{row.id}_1"
    app = _app(service, settings, monkeypatch)
    app.selectbox(key="materials_selected_doc").set_value(document.id).run()
    app.text_input(key=f"{prefix}_anneal_temperature").set_value("180 °C").run()
    app.selectbox(key=f"{prefix}_availability__film_thickness").set_value("not_reported").run()
    app.selectbox(key=f"{prefix}_observation_origin").set_value("measured").run()
    assert not app.exception

    reopened = _app(service, settings, monkeypatch)
    reopened.button(key="materials_restore_draft").click().run()
    assert reopened.text_input(key=f"{prefix}_anneal_temperature").value == "180 °C"
    assert reopened.selectbox(key=f"{prefix}_observation_origin").value == "measured"
    assert reopened.selectbox(key=f"{prefix}_availability__film_thickness").value == "not_reported"
    assert service.repo.record(row.id).anneal_temperature is None

    si_block = service.repo.blocks(supplement.id)[0]
    reopened.selectbox(key=f"{prefix}_source").set_value(si_block.id).run()
    reopened.multiselect(key=f"{prefix}_fields").set_value(["anneal_temperature"])
    reopened.button(key=f"{prefix}_add_evidence").click().run()
    source_block = service.repo.blocks(document.id)[0]
    reopened.selectbox(key=f"{prefix}_source").set_value(source_block.id).run()
    reopened.multiselect(key=f"{prefix}_fields").set_value(["observation_origin"])
    reopened.button(key=f"{prefix}_add_evidence").click().run()
    reopened.checkbox(key="materials_review_confirm").check().run()
    reopened.button(key="materials_approve").click().run()
    assert not reopened.exception
    approved = service.repo.record(row.id)
    assert approved.status == "approved"
    assert approved.anneal_temperature == "180 °C"
    assert approved.observation_origin == "measured"
    assert approved.field_availability == {"film_thickness": "not_reported"}
