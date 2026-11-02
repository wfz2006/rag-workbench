"""Evidence navigation and review queue controls use the real page."""

from uuid import uuid4

from rag_workbench.application.materials import MaterialsService
from rag_workbench.domain.materials import EvidenceReference, MaterialRecord

from .test_material_ui import _app


def _candidate(service, document, *, evidence=False):
    block = service.repo.blocks(document.id)[0]
    return service.add_record(MaterialRecord(
        id=uuid4().hex, document_id=document.id, material_name="Alpha",
        property_name="mobility", value_raw="0.12",
        evidence=[EvidenceReference(field="value_raw", block_id=block.id, quote=block.text)]
        if evidence else [],
    ))


def test_field_evidence_action_targets_source_and_keeps_editor_draft(settings, monkeypatch):
    service = MaterialsService(settings)
    document = service.ingest("paper.txt", b"Alpha mobility 0.12.")
    record = _candidate(service, document, evidence=True)
    app = _app(service, settings, monkeypatch)
    key = f"materials_edit_{record.id}_1_material_name"
    app.text_input(key=key).set_value("Draft Alpha").run()
    app.button(key=f"materials_locate_{record.id}_0").click().run()
    assert not app.exception
    assert app.selectbox(key="materials_viewer_document").value == document.id
    assert app.selectbox(key=f"materials_viewer_{document.id}_block").value == (
        record.evidence[0].block_id
    )
    assert app.radio(key="materials_review_view").value == "source"
    assert app.text_input(key=key).value == "Draft Alpha"
    assert service.repo.record(record.id).material_name == "Alpha"


def test_next_pending_record_retains_each_record_draft(settings, monkeypatch):
    service = MaterialsService(settings)
    document = service.ingest("paper.txt", b"Alpha mobility 0.12.")
    first, second = _candidate(service, document), _candidate(service, document)
    app = _app(service, settings, monkeypatch)
    app.selectbox(key="materials_selected_record").set_value(first.id).run()
    key = f"materials_edit_{first.id}_1_value_raw"
    app.text_input(key=key).set_value("0.13").run()
    app.button(key="materials_next_candidate").click().run()
    assert not app.exception
    assert app.selectbox(key="materials_selected_record").value == second.id
    app.selectbox(key="materials_selected_record").set_value(first.id).run()
    assert app.text_input(key=key).value == "0.13"


def test_next_pending_advances_through_all_candidates(settings, monkeypatch):
    service = MaterialsService(settings)
    document = service.ingest("paper.txt", b"Alpha mobility 0.12.")
    for _ in range(3):
        _candidate(service, document)
    identities = [record.id for record in service.repo.records(document.id)]
    app = _app(service, settings, monkeypatch)
    app.selectbox(key="materials_selected_record").set_value(identities[0]).run()
    for expected in (identities[1], identities[2], identities[0]):
        app.button(key="materials_next_candidate").click().run()
        assert not app.exception
        assert app.selectbox(key="materials_selected_record").value == expected


def test_same_quote_supporting_several_fields_is_grouped_for_review(settings, monkeypatch):
    service = MaterialsService(settings)
    document = service.ingest("paper.txt", b"Alpha mobility 0.12.")
    block = service.repo.blocks(document.id)[0]
    service.add_record(MaterialRecord(
        id=uuid4().hex, document_id=document.id, material_name="Alpha",
        property_name="mobility", value_raw="0.12",
        evidence=[EvidenceReference(field=field, block_id=block.id, quote=block.text)
                  for field in ("material_name", "property_name", "value_raw")],
    ))
    app = _app(service, settings, monkeypatch)
    actions = [button for button in app.button if button.key.startswith("materials_locate_")]
    assert len(actions) == 1
    assert any("材料名称、性能名称、原始数值" in item.value for item in app.caption)


def test_manual_recovery_after_cleanup_failure_reuses_saved_observation(settings, monkeypatch):
    from rag_workbench.domain.errors import AppError
    from rag_workbench.infrastructure.material_draft_store import MaterialDraftStore

    service = MaterialsService(settings)
    document = service.ingest("paper.txt", b"Alpha mobility 0.12.")
    app = _app(service, settings, monkeypatch)
    prefix = f"materials_new_{document.id}"
    for field, value in (("material_name", "Alpha"), ("property_name", "mobility"),
                         ("value_raw", "0.12")):
        app.text_input(key=f"{prefix}_{field}").set_value(value).run()

    original_delete = MaterialDraftStore.delete

    def fail(*args, **kwargs):
        raise AppError("material_draft_storage_error", "草稿清理失败测试")

    monkeypatch.setattr(MaterialDraftStore, "delete", fail)
    app.button(key="materials_add_candidate").click().run()
    assert len(service.repo.records(document.id)) == 1
    assert any("记录已保存" in item.value for item in app.warning)
    monkeypatch.setattr(MaterialDraftStore, "delete", original_delete)
    reopened = _app(service, settings, monkeypatch)
    reopened.button(key="materials_restore_draft").click().run()
    reopened.button(key="materials_add_candidate").click().run()
    assert not reopened.exception
    assert len(service.repo.records(document.id)) == 1


def test_concurrent_manual_revision_does_not_clear_unsaved_candidate(settings, monkeypatch):
    service = MaterialsService(settings)
    document = service.ingest("paper.txt", b"Alpha mobility 0.12 0.13.")
    app = _app(service, settings, monkeypatch)
    prefix = f"materials_new_{document.id}"
    for field, value in (("material_name", "Alpha"), ("property_name", "mobility"),
                         ("value_raw", "0.12")):
        app.text_input(key=f"{prefix}_{field}").set_value(value).run()
    original_save = service.repo.save_record

    def concurrent_save(record, expected_revision=None):
        if expected_revision is None:
            created = original_save(record)
            original_save(
                created.model_copy(update={"value_raw": "0.13"}),
                expected_revision=created.revision,
            )
        return original_save(record, expected_revision=expected_revision)

    monkeypatch.setattr(service.repo, "save_record", concurrent_save)
    app.button(key="materials_add_candidate").click().run()
    assert not app.exception
    assert app.error
    assert service.repo.records(document.id)[0].value_raw == "0.13"
    assert app.text_input(key=f"{prefix}_value_raw").value == "0.12"
