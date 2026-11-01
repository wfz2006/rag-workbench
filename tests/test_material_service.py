import csv
import io
import json
import zipfile

import pytest

from rag_workbench.application.materials import MaterialsService
from rag_workbench.config import Settings
from rag_workbench.domain.errors import AppError

TEXT = b"Material OSC-A has mobility 0.25 cm2/Vs. Annealed at 100 C."


class ExtractionProvider:
    def __init__(self, *, bad_quote=False, crash_at=None):
        self.calls = []
        self.bad_quote = bad_quote
        self.crash_at = crash_at

    def complete(self, system, user, **kwargs):
        payload = json.loads(user)
        self.calls.append(payload)
        if self.crash_at == len(self.calls):
            raise AppError("llm_timeout", "test timeout")
        found = next((b for b in payload["blocks"] if "OSC-A" in b["text"]), None)
        if found is None:
            return '{"records":[]}'
        quote = "invented quote" if self.bad_quote else TEXT.decode()
        return json.dumps({"records": [{
            "material_name": "OSC-A", "property_name": "mobility", "value_raw": "0.25",
            "unit_raw": "cm2/Vs", "evidence": [
                {"field": field, "block_id": found["block_id"], "quote": quote}
                for field in ("material_name", "property_name", "value_raw", "unit_raw")
            ],
        }]})


def service(tmp_path, provider=None):
    return MaterialsService(Settings(_env_file=None, data_dir=tmp_path), provider)


def test_extract_approve_and_lossless_bundle(tmp_path):
    provider = ExtractionProvider()
    svc = service(tmp_path, provider)
    doc = svc.ingest("paper.txt", TEXT)
    assert svc.ingest("repeat.txt", TEXT).id == doc.id
    run = svc.extract(doc.id, domain="ofet")
    assert run.status == "completed" and run.record_count == 1
    row = svc.repo.records(doc.id)[0]
    assert row.status == "candidate"
    assert list(csv.DictReader(io.StringIO(svc.export_csv().decode("utf-8-sig")))) == []
    saved = svc.review(row.id, row, status="approved")
    assert saved.revision == row.revision + 1
    assert saved.value_normalized == 0.25
    rows = list(csv.DictReader(io.StringIO(svc.export_csv().decode("utf-8-sig"))))
    assert len(rows) == 1 and rows[0]["material_name"] == "OSC-A"
    with zipfile.ZipFile(io.BytesIO(svc.export_bundle())) as z:
        data = json.loads(z.read("dataset.json"))
        assert data["schema_version"] == 1
        assert data["records"][0]["id"] == saved.id
        block_ids = {b["id"] for b in data["blocks"]}
        assert all(e["block_id"] in block_ids for e in data["records"][0]["evidence"])
        assert data["documents"][0]["id"] == doc.id
        assert "originals/" + doc.id + ".txt" in z.namelist()
    svc.extract(doc.id, domain="ofet")
    assert len(svc.repo.records()) == 1
    assert svc.repo.record(row.id).status == "approved"
    assert svc.repo.record(row.id).revision == saved.revision


def test_unlocated_or_cross_document_quotes_rejected(tmp_path):
    svc = service(tmp_path, ExtractionProvider(bad_quote=True))
    doc = svc.ingest("paper.txt", TEXT)
    with pytest.raises(AppError, match="证据"):
        svc.extract(doc.id)
    assert svc.repo.runs(doc.id)[0].status == "failed"
    assert svc.repo.records() == []


def test_approval_requires_every_populated_field_evidence(tmp_path):
    svc = service(tmp_path, ExtractionProvider())
    doc = svc.ingest("paper.txt", TEXT)
    svc.extract(doc.id)
    row = svc.repo.records()[0]
    edited = row.model_copy(update={"conditions": "100 C"})
    with pytest.raises(AppError, match="conditions"):
        svc.review(row.id, edited, status="approved")
    assert svc.repo.record(row.id).status == "candidate"
    foreign = svc.ingest("other.txt", b"Other evidence.")
    ref = row.evidence[0].model_copy(update={
        "block_id": svc.repo.blocks(foreign.id)[0].id, "quote": "Other evidence.",
    })
    with pytest.raises(AppError, match="证据"):
        svc.review(row.id, row.model_copy(update={"evidence": [ref]}), status="candidate")


def test_all_long_document_text_and_linked_supplement_is_sent(tmp_path):
    provider = ExtractionProvider()
    svc = service(tmp_path, provider)
    doc = svc.ingest("long.txt", ("A" * 25000 + " LASTPAGE").encode())
    si = svc.ingest("supplement.txt", b"SUPPLEMENT-END", role="supplement", parent_id=doc.id)
    run = svc.extract(doc.id)
    sent = "".join(b["text"] for call in provider.calls for b in call["blocks"])
    assert sent == "A" * 25000 + " LASTPAGE" + "SUPPLEMENT-END"
    assert run.batch_total >= 3 and run.batch_completed == run.batch_total
    assert any(b["document_id"] == si.id for c in provider.calls for b in c["blocks"])


def test_late_batch_error_records_partial_progress(tmp_path):
    provider = ExtractionProvider(crash_at=2)
    svc = service(tmp_path, provider)
    doc = svc.ingest("long.txt", b"A" * 26000)
    with pytest.raises(AppError):
        svc.extract(doc.id)
    run = svc.repo.runs()[0]
    assert run.status == "failed" and run.batch_completed == 1
    assert run.error_code == "llm_timeout"


def test_bad_type_or_missing_optional_parser_is_explicit(tmp_path):
    svc = service(tmp_path)
    with pytest.raises(AppError):
        svc.ingest("bad.exe", b"MZ")
    assert svc.repo.documents() == []


def test_stale_review_and_unsafe_csv_cells(tmp_path):
    svc = service(tmp_path, ExtractionProvider())
    doc = svc.ingest("paper.txt", TEXT)
    svc.extract(doc.id)
    row = svc.repo.records()[0]
    svc.review(row.id, row, status="approved")
    with pytest.raises(AppError):
        svc.review(row.id, row, status="rejected")
    # Raw text in Excel must not execute as a spreadsheet formula.
    modified = svc.repo.record(row.id).model_copy(update={"sample_label": "=1+1"})
    svc.review(row.id, modified, status="candidate")
    rows = list(csv.DictReader(io.StringIO(
        svc.export_csv(approved_only=False).decode("utf-8-sig"),
    )))
    assert rows[0]["sample_label"].startswith("'")


def test_evidence_does_not_accept_partial_number_or_invented_condition(tmp_path):
    svc = service(tmp_path, ExtractionProvider())
    doc = svc.ingest("paper.txt", TEXT)
    svc.extract(doc.id)
    row = svc.repo.records()[0]
    with pytest.raises(AppError):
        svc.review(row.id, row.model_copy(update={"value_raw": "0.2"}), status="approved")
    ref = row.evidence[0].model_copy(update={"field": "conditions"})
    with pytest.raises(AppError):
        svc.review(row.id, row.model_copy(update={
            "conditions": "Annealed at 900 C", "evidence": [*row.evidence, ref],
        }), status="approved")


def test_units_case_and_duplicate_original_repair(tmp_path):
    svc = service(tmp_path, ExtractionProvider())
    doc = svc.ingest("paper.txt", TEXT)
    svc.extract(doc.id)
    row = svc.repo.records()[0]
    assert svc._normalized(row.model_copy(update={
        "value_raw": "1", "unit_raw": "MV",
    })).value_normalized is None
    assert svc._normalized(row.model_copy(update={
        "value_raw": "1", "unit_raw": "mV",
    })).value_normalized == .001
    original = svc.repo.originals / f"{doc.id}.txt"
    original.unlink()
    svc.ingest("paper.txt", TEXT)
    assert original.read_bytes() == TEXT


def test_bundle_includes_historical_evidence_and_uses_one_record_snapshot(tmp_path, monkeypatch):
    svc = service(tmp_path, ExtractionProvider())
    doc = svc.ingest("paper.txt", TEXT)
    si = svc.ingest("si.txt", TEXT + b" Supplement", role="supplement", parent_id=doc.id)
    svc.extract(doc.id)
    row = svc.repo.records()[0]
    refs = [e.model_copy(update={"block_id": svc.repo.blocks(si.id)[0].id}) for e in row.evidence]
    first = svc.review(row.id, row.model_copy(update={"evidence": refs}), status="approved")
    svc.review(first.id, first.model_copy(update={"evidence": row.evidence}), status="approved")
    real_records = svc.repo.records
    calls = []

    def changing_records(document_id=None):
        calls.append(True)
        return real_records(document_id) if len(calls) == 1 else []

    monkeypatch.setattr(svc.repo, "records", changing_records)
    with zipfile.ZipFile(io.BytesIO(svc.export_bundle())) as z:
        data = json.loads(z.read("dataset.json"))
        block_ids = {b["id"] for b in data["blocks"]}
        for revisions in data["revisions"].values():
            assert all(e["block_id"] in block_ids for r in revisions for e in r["evidence"])
        csv_rows = list(csv.DictReader(io.StringIO(z.read("measurements.csv").decode("utf-8-sig"))))
        assert [r["id"] for r in csv_rows] == [r["id"] for r in data["records"]]
    assert len(calls) == 1
