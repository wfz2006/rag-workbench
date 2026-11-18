"""Material workspace UI uses local data and explicit remote-call consent."""

import csv
import io
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]


def _export_downloads(app):
    return [
        item for item in app.get("download_button")
        if item.key.startswith("materials_download_")
    ]


def _app(service, settings, monkeypatch):
    from rag_workbench.ui import materials

    monkeypatch.setattr(materials, "MaterialsService", lambda *_: service)
    monkeypatch.setattr(materials, "_test_settings", settings, raising=False)
    return AppTest.from_string(
        "from types import SimpleNamespace\n"
        "from rag_workbench.ui import materials\n"
        "materials.materials_page(SimpleNamespace(settings=materials._test_settings, "
        "pipeline=SimpleNamespace(provider=None)))\n",
        default_timeout=30,
    ).run()


def test_material_workspace_is_reachable_without_key(settings, monkeypatch):
    from rag_workbench.bootstrap import build_services
    from rag_workbench.ui import app as ui_app

    from .fakes import FakeEmbedder, FakeProvider

    services = build_services(settings, embedder=FakeEmbedder(), provider=FakeProvider())
    monkeypatch.setattr(ui_app, "load_settings", lambda: settings)
    monkeypatch.setattr(ui_app, "resources", lambda *args: services)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    assert "材料文献" in app.sidebar.radio[0].options
    app.sidebar.radio[0].set_value("材料文献").run()
    assert not app.exception
    assert any(title.value == "材料文献" for title in app.title)
    assert any("上传" in item.value for item in app.info)


def test_import_and_source_preview_do_not_require_remote_key(settings, monkeypatch):
    from rag_workbench.application.materials import MaterialsService
    from rag_workbench.ui.materials import _import_uploads

    service = MaterialsService(settings)
    upload = SimpleNamespace(name="paper.txt", getvalue=lambda: b"Sample S1 mobility 0.12.")
    documents, errors = _import_uploads(service, [upload], "main", None, "text")
    assert not errors
    assert len(documents) == 1
    app = _app(service, settings, monkeypatch)
    assert not app.exception
    assert app.button(key="materials_extract").disabled
    assert any("尚未配置" in item.value for item in app.caption)
    assert "Sample S1" in " ".join(item.value for item in app.text)
    assert len(service.repo.runs()) == 0


def test_review_and_downloads_keep_candidates_separate(settings, monkeypatch):
    from rag_workbench.application.materials import MaterialsService
    from rag_workbench.domain.materials import EvidenceReference, MaterialRecord

    service = MaterialsService(settings)
    document = service.ingest("paper.txt", b"Material Alpha has mobility 0.12.")
    block = service.repo.blocks(document.id)[0]
    record = MaterialRecord(
        id=uuid4().hex,
        document_id=document.id,
        domain="ofet",
        material_name="Alpha",
        property_name="mobility",
        value_raw="0.12",
        evidence=[
            EvidenceReference(block_id=block.id, quote=block.text, field=field)
            for field in ("material_name", "property_name", "value_raw")
        ],
    )
    service.add_record(record)
    app = _app(service, settings, monkeypatch)
    assert not app.exception
    assert app.checkbox(key="materials_approved_only").value is True
    app.button(key="materials_approve").click().run()
    assert app.error
    assert service.repo.record(record.id).status == "candidate"
    app.checkbox(key="materials_review_confirm").check()
    app.button(key="materials_approve").click().run()
    assert not app.exception
    assert service.repo.record(record.id).status == "approved"
    assert any("已批准" in message.value for message in app.success)
    app.button(key="materials_prepare_export").click().run()
    downloads = _export_downloads(app)
    assert {item.label for item in downloads} == {"下载 CSV", "下载完整数据包"}
    assert app.text_area(key=f"materials_json_{record.id}_2_record")


def test_invalid_review_json_keeps_candidate_and_surfaces_error(settings, monkeypatch):
    from rag_workbench.application.materials import MaterialsService
    from rag_workbench.domain.materials import MaterialRecord

    service = MaterialsService(settings)
    document = service.ingest("paper.txt", b"Material Alpha mobility 0.12.")
    record = MaterialRecord(
        id=uuid4().hex,
        document_id=document.id,
        domain="ofet",
        material_name="Alpha",
        property_name="mobility",
        value_raw="0.12",
    )
    service.add_record(record)
    app = _app(service, settings, monkeypatch)
    app.text_area(key=f"materials_json_{record.id}_1_record").set_value(json.dumps([]))
    app.button(key="materials_save_json").click().run()
    assert not app.exception
    assert app.error
    assert service.repo.record(record.id).revision == 1


def test_normal_review_form_submits_changed_value_with_evidence(settings, monkeypatch):
    from rag_workbench.application.materials import MaterialsService
    from rag_workbench.domain.materials import EvidenceReference, MaterialRecord

    service = MaterialsService(settings)
    document = service.ingest("paper.txt", b"Alpha mobility 0.12; revised value 0.13.")
    block = service.repo.blocks(document.id)[0]
    record = MaterialRecord(
        id=uuid4().hex,
        document_id=document.id,
        material_name="Alpha",
        property_name="mobility",
        value_raw="0.12",
        evidence=[
            EvidenceReference(field=field, block_id=block.id, quote=block.text)
            for field in ("material_name", "property_name", "value_raw")
        ],
    )
    service.add_record(record)
    app = _app(service, settings, monkeypatch)
    app.text_input(key=f"materials_edit_{record.id}_1_value_raw").set_value("0.13")
    app.button(key="materials_save_candidate").click().run()
    assert not app.exception
    updated = service.repo.record(record.id)
    assert updated.value_raw == "0.13"
    assert updated.revision == 2
    assert updated.status == "candidate"


def test_manual_candidate_form_works_without_api(settings, monkeypatch):
    from rag_workbench.application.materials import MaterialsService

    service = MaterialsService(settings)
    document = service.ingest("paper.txt", b"Alpha mobility 0.12.")
    app = _app(service, settings, monkeypatch)
    prefix = f"materials_new_{document.id}"
    app.text_input(key=f"{prefix}_material_name").set_value("Alpha")
    app.text_input(key=f"{prefix}_property_name").set_value("mobility")
    app.text_input(key=f"{prefix}_value_raw").set_value("0.12")
    app.button(key="materials_add_candidate").click().run()
    assert not app.exception
    rows = service.repo.records(document.id)
    assert len(rows) == 1
    assert rows[0].material_name == "Alpha"
    assert rows[0].status == "candidate"
    assert len(service.repo.runs()) == 0


def test_export_is_prepared_once_and_invalidates_on_filter_change(settings, monkeypatch):
    from rag_workbench.application.materials import MaterialsService

    service = MaterialsService(settings)
    service.ingest("paper.txt", b"Alpha mobility 0.12.")
    calls = []
    original = service.export_bundle

    def export(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(service, "export_bundle", export)
    app = _app(service, settings, monkeypatch)
    assert not calls
    app.button(key="materials_prepare_export").click().run()
    assert not app.exception
    assert len(calls) == 1
    assert _export_downloads(app)
    app.run()
    assert len(calls) == 1
    app.checkbox(key="materials_approved_only").uncheck().run()
    assert not _export_downloads(app)
    assert len(calls) == 1


def test_export_downloads_share_a_snapshot_when_another_window_revises(settings, monkeypatch):
    from rag_workbench.application.materials import MaterialsService
    from rag_workbench.domain.materials import EvidenceReference, MaterialRecord

    service = MaterialsService(settings)
    other = MaterialsService(settings)
    doc = service.ingest("paper.txt", b"Alpha threshold voltage 1.0 V; revised 2.0 V.")
    block, = service.repo.blocks(doc.id)
    values = dict(material_name="Alpha", property_name="threshold voltage",
                  value_raw="1.0", unit_raw="V")
    row = service.add_record(MaterialRecord(
        id="alpha", document_id=doc.id, **values,
        evidence=[EvidenceReference(field=k, block_id=block.id, quote=block.text) for k in values],
    ))
    service.review(row.id, row, status="approved")
    service.add_record(row.model_copy(update={"id": "beta", "evidence": []}))
    app = _app(service, settings, monkeypatch)
    original = service.export_csv

    def revise_after_csv(*args, **kwargs):
        output = original(*args, **kwargs)
        current = other.repo.record(row.id)
        other.review(current.id, current.model_copy(update={"value_raw": "2.0"}), status="approved")
        return output

    with monkeypatch.context() as patch:
        patch.setattr(service, "export_csv", revise_after_csv)
        app.button(key="materials_prepare_export").click().run()
    assert not app.exception
    assert len(_export_downloads(app)) == 2
    assert other.repo.record(row.id).revision == 3
    output = app.session_state["materials_prepared_export"]
    with zipfile.ZipFile(io.BytesIO(output["bundle"])) as archive:
        assert output["csv"] == archive.read("measurements.csv")
        dataset = json.loads(archive.read("dataset.json"))
    exported, = dataset["records"]
    assert (exported["id"], exported["revision"], exported["value_raw"]) == ("alpha", 2, "1.0")
    assert [r["revision"] for r in dataset["revisions"]["alpha"]] == [1, 2]

    app.run()
    assert not _export_downloads(app)
    app.checkbox(key="materials_approved_only").uncheck().run()
    app.button(key="materials_prepare_export").click().run()
    output = app.session_state["materials_prepared_export"]
    with zipfile.ZipFile(io.BytesIO(output["bundle"])) as archive:
        assert output["csv"] == archive.read("measurements.csv")
    rows = csv.DictReader(io.StringIO(output["csv"].decode("utf-8-sig")))
    assert {(r["id"], r["revision"], r["status"]) for r in rows} == {
        ("alpha", "3", "approved"), ("beta", "1", "candidate"),
    }


def test_export_keeps_prepared_csv_when_bundle_fails_on_unreadable_original(settings, monkeypatch):
    from rag_workbench.application.materials import MaterialsService
    from rag_workbench.domain.materials import EvidenceReference, MaterialRecord

    service = MaterialsService(settings)
    doc = service.ingest("paper.txt", b"Alpha threshold voltage 1.0 V.")
    block, = service.repo.blocks(doc.id)
    values = dict(
        material_name="Alpha", property_name="threshold voltage", value_raw="1.0", unit_raw="V",
    )
    row = service.add_record(MaterialRecord(
        id="alpha", document_id=doc.id, **values,
        evidence=[EvidenceReference(field=k, block_id=block.id, quote=block.text) for k in values],
    ))
    service.review(row.id, row, status="approved")
    (service.repo.originals / f"{doc.id}{doc.extension}").unlink()

    app = _app(service, settings, monkeypatch)
    app.button(key="materials_prepare_export").click().run()
    assert not app.exception
    assert any("CSV 仍可下载" in message.value for message in app.error)
    assert [button.label for button in _export_downloads(app)] == ["下载 CSV"]
    output = app.session_state["materials_prepared_export"]
    rows = list(csv.DictReader(io.StringIO(output["csv"].decode("utf-8-sig"))))
    assert [(r["id"], r["value_raw"]) for r in rows] == [("alpha", "1.0")]


def test_supplement_preview_matches_its_extraction_scope(settings, monkeypatch):
    from rag_workbench.application.materials import MaterialsService

    service = MaterialsService(settings)
    main = service.ingest("main.txt", b"MAIN_ONLY evidence.")
    supplement = service.ingest(
        "si.txt", b"SUPPLEMENT_ONLY evidence.", role="supplement", parent_id=main.id
    )
    app = _app(service, settings, monkeypatch)
    app.selectbox(key="materials_selected_doc").set_value(supplement.id).run()
    assert not app.exception
    rendered = " ".join(item.value for item in app.text)
    assert "SUPPLEMENT_ONLY" in rendered
    assert "MAIN_ONLY" not in rendered
    assert any("1 份文献" in item.value for item in app.caption)
