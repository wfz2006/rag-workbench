import csv
import io
import json
import zipfile

import pytest

from rag_workbench.application.material_analysis import load_observations
from rag_workbench.application.materials import MaterialsService
from rag_workbench.config import Settings
from rag_workbench.domain.errors import AppError
from rag_workbench.domain.materials import EvidenceReference, MaterialRecord

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


def test_extract_keeps_grounded_candidates_when_a_peer_has_invalid_evidence(tmp_path):
    class MixedResponse(ExtractionProvider):
        def complete(self, system, user, **kwargs):
            payload = json.loads(super().complete(system, user, **kwargs))
            record, = payload["records"]
            ungrounded = {**record, "value_raw": "900"}
            return json.dumps({"records": [ungrounded, record]})

    svc = service(tmp_path, MixedResponse())
    doc = svc.ingest("paper.txt", TEXT)

    run = svc.extract(doc.id)

    assert (run.status, run.record_count, run.skipped_count) == ("completed", 1, 1)
    assert svc.repo.runs(doc.id)[0].skipped_count == 1
    row, = svc.repo.records(doc.id)
    assert row.value_normalized == 0.25 and row.status == "candidate"


def test_extract_omits_unquoted_optional_fields_without_weakening_approval(tmp_path):
    class MissingOptionalEvidence(ExtractionProvider):
        def complete(self, system, user, **kwargs):
            payload = json.loads(super().complete(system, user, **kwargs))
            payload["records"][0]["anneal_temperature"] = "900 C"
            return json.dumps(payload)

    svc = service(tmp_path, MissingOptionalEvidence())
    doc = svc.ingest("paper.txt", TEXT)

    run = svc.extract(doc.id)

    row, = svc.repo.records(doc.id)
    assert run.record_count == 1
    assert row.anneal_temperature is None
    edited = row.model_copy(update={"anneal_temperature": "900 C"})
    with pytest.raises(AppError, match="anneal_temperature"):
        svc.review(row.id, edited, status="approved")


def test_extract_locates_verbatim_fields_when_the_model_only_cites_the_value(tmp_path):
    class ValueCitationOnly:
        def complete(self, system, user, **kwargs):
            block, = json.loads(user)["blocks"]
            return json.dumps({"records": [{
                "material_name": "TEST-OSC-A",
                "sample_label": "SYN-01",
                "property_name": "hole mobility in saturation",
                "value_raw": "0.42",
                "unit_raw": "cm^2 V^-1 s^-1",
                "evidence": [{
                    "field": "value_raw", "block_id": block["block_id"],
                    "quote": "Hole mobility in saturation: 0.42 cm^2 V^-1 s^-1",
                }],
            }]})

    svc = service(tmp_path, ValueCitationOnly())
    doc = svc.ingest(
        "example.txt",
        b"Material: TEST-OSC-A. Sample: SYN-01.\n"
        b"Hole mobility in saturation: 0.42 cm^2 V^-1 s^-1 (mean of 6 devices).",
    )

    run = svc.extract(doc.id)

    assert (run.status, run.record_count, run.skipped_count) == ("completed", 1, 0)
    row, = svc.repo.records(doc.id)
    assert {ref.field: ref.quote for ref in row.evidence} == {
        "value_raw": "Hole mobility in saturation: 0.42 cm^2 V^-1 s^-1",
        "material_name": "TEST-OSC-A",
        "sample_label": "SYN-01",
        "property_name": "Hole mobility in saturation",
        "unit_raw": "Hole mobility in saturation: 0.42 cm^2 V^-1 s^-1",
    }
    assert row.property_name == "Hole mobility in saturation"
    assert row.value_normalized == 0.42
    assert svc.review(row.id, row, status="approved").status == "approved"


@pytest.mark.parametrize(("source", "quote"), [
    (
        "Material: OSC-A. Sample: SYN-01.\nSYN-01 showed no mobility. "
        "For reference only, OSC-B sample SYN-02 has mobility 0.42 cm2/Vs.",
        "OSC-B sample SYN-02 has mobility 0.42 cm2/Vs.",
    ),
    (
        "Material: OSC-A. Sample: SYN-01.\nFor an unrelated calibration, "
        "voltage: 0.42 V; mobility: 0.9 cm2/Vs.",
        "For an unrelated calibration, voltage: 0.42 V; mobility: 0.9 cm2/Vs.",
    ),
    (
        "Material: OSC-A / OSC-B. Sample: SYN-01 / SYN-02.\nMobility: 0.42 cm2/Vs.",
        "Mobility: 0.42 cm2/Vs.",
    ),
])
def test_extract_does_not_reconstruct_mismatched_measurement_evidence(tmp_path, source, quote):
    class MismatchedResponse:
        def complete(self, system, user, **kwargs):
            block, = json.loads(user)["blocks"]
            return json.dumps({"records": [{
                "material_name": "OSC-A",
                "sample_label": "SYN-01",
                "property_name": "mobility",
                "value_raw": "0.42",
                "unit_raw": "cm2/Vs",
                "evidence": [{
                    "field": "value_raw", "block_id": block["block_id"], "quote": quote,
                }],
            }]})

    svc = service(tmp_path, MismatchedResponse())
    doc = svc.ingest("example.txt", source.encode())

    with pytest.raises(AppError, match="缺少可核对的证据"):
        svc.extract(doc.id)
    assert svc.repo.records(doc.id) == []


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
        "property_name": "threshold voltage", "value_raw": "1", "unit_raw": "mV",
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


def test_typographically_equivalent_evidence_from_existing_block_can_be_approved(tmp_path):
    svc = service(tmp_path)
    text = "Alpha ﬁlm: field-\neﬀect mobility was 2.3 × 10⁻⁷ cm2 V−1 s−1."
    quote = "Alpha film: field-effect mobility was 2.3 × 10^-7 cm² V⁻¹ s⁻¹."
    doc = svc.ingest("old-extract.txt", text.encode())
    block = svc.repo.blocks(doc.id)[0]
    row = MaterialRecord(
        id="old-record", document_id=doc.id, material_name="Alpha",
        property_name="field-effect mobility", value_raw="2.3 × 10^-7", unit_raw="cm² V⁻¹ s⁻¹",
        evidence=[EvidenceReference(field=field, block_id=block.id, quote=quote) for field in (
            "material_name", "property_name", "value_raw", "unit_raw",
        )],
    )
    saved = svc.add_record(row)
    approved = svc.review(row.id, saved, status="approved")
    assert approved.value_normalized == pytest.approx(2.3e-7)
    assert approved.value_raw == "2.3 × 10^-7"
    assert approved.evidence[0].quote == quote
    assert svc.repo.blocks(doc.id)[0].text == text


@pytest.mark.parametrize(("source", "value"), [
    ("mobility −0.12", "0.12"),
    ("mobility 2.3 × 10⁻⁷", "2.3"),
    ("mobility 2.3 × 10⁻⁷", "2.3 × 10^-"),
    ("mobility 2.3 × 10⁻⁷⁸", "2.3 × 10^-7"),
    ("mobility 10−23416", "10"),
])
def test_numeric_evidence_rejects_partial_signed_and_scientific_numbers(tmp_path, source, value):
    svc = service(tmp_path)
    doc = svc.ingest("old-extract.txt", source.encode())
    block = svc.repo.blocks(doc.id)[0]
    row = MaterialRecord(
        id="old-record", document_id=doc.id, material_name="Alpha",
        property_name="mobility", value_raw=value,
        evidence=[EvidenceReference(field="value_raw", block_id=block.id, quote=source)],
    )
    with pytest.raises(AppError, match="value_raw"):
        svc.add_record(row)


def test_trimming_a_quote_cannot_hide_the_exponent(tmp_path):
    svc = service(tmp_path)
    doc = svc.ingest("paper.txt", "mobility 2.3 × 10⁻⁷ cm²/Vs".encode())
    block = svc.repo.blocks(doc.id)[0]
    row = MaterialRecord(
        id="record", document_id=doc.id, material_name="Alpha",
        property_name="mobility", value_raw="2.3",
        evidence=[EvidenceReference(field="value_raw", block_id=block.id, quote="mobility 2.3")],
    )
    with pytest.raises(AppError, match="value_raw"):
        svc.add_record(row)


def test_incompatible_unit_is_not_saved_for_a_recognized_metric(tmp_path):
    svc = service(tmp_path)
    doc = svc.ingest("paper.txt", b"Alpha mobility 1 mV")
    row = MaterialRecord(
        id="record", document_id=doc.id, material_name="Alpha",
        property_name="mobility", value_raw="1", unit_raw="mV",
    )
    saved = svc.add_record(row)
    assert saved.value_normalized is None and saved.unit_normalized is None


def test_unit_evidence_keeps_exponent_separate_from_plain_digits(tmp_path):
    svc = service(tmp_path)
    source = "mobility 20 in 104 cm2/Vs"
    doc = svc.ingest("paper.txt", source.encode())
    block = svc.repo.blocks(doc.id)[0]
    row = MaterialRecord(
        id="record", document_id=doc.id, material_name="Alpha", property_name="mobility",
        value_raw="20", unit_raw="10⁴ cm²/Vs",
        evidence=[EvidenceReference(field="unit_raw", block_id=block.id, quote=source)],
    )
    with pytest.raises(AppError, match="unit_raw"):
        svc.add_record(row)


def test_numeric_evidence_accepts_a_unit_printed_without_a_space(tmp_path):
    svc = service(tmp_path)
    source = "threshold voltage −90V"
    doc = svc.ingest("paper.txt", source.encode())
    block = svc.repo.blocks(doc.id)[0]
    row = MaterialRecord(
        id="record", document_id=doc.id, material_name="Alpha",
        property_name="threshold voltage", value_raw="−90", unit_raw="V",
        evidence=[EvidenceReference(field="value_raw", block_id=block.id, quote=source)],
    )
    assert svc.add_record(row).value_normalized == -90.0


def test_numeric_evidence_keeps_adjacent_signed_table_cells_separate(tmp_path):
    svc = service(tmp_path)
    source = "PY3-OTS 25 2.3 × 10⁻⁷ 4 −90"
    doc = svc.ingest("paper.txt", source.encode())
    block = svc.repo.blocks(doc.id)[0]
    row = MaterialRecord(
        id="record", document_id=doc.id, material_name="PY3-OTS",
        property_name="on_off_ratio", value_raw="4",
        evidence=[EvidenceReference(field="value_raw", block_id=block.id, quote=source)],
    )
    assert svc.add_record(row).value_normalized == 4.0


@pytest.mark.parametrize(("reported", "fragment", "kind"), [
    (">= 2.0", "2.0", "bound"),
    ("≤ 2.0", "2.0", "bound"),
    ("2.0 ± 0.4", "2.0", "uncertainty"),
    ("2.0 ± 0.4", "0.4", "uncertainty"),
    ("2.0 ± 0.4", "± 0.4", "uncertainty"),
    ("(2.0 ± 0.4) × 10⁻³", "2.0 ± 0.4", "uncertainty"),
    ("[2.0, 2.8]", "2.0", "range"),
    ("[2.0, 2.8]", "2.8", "range"),
    ("−24–−20", "−20", "range"),
    ("2.0 to 2.8", "2.0", "range"),
    ("2.0 - 2.8", "2.0", "range"),
    ("2.0 - 2.8", "2.8", "range"),
    ("≈ 2.0", "2.0", None),
    ("approximately 2.0", "2.0", None),
    ("约2.0", "2.0", None),
    ("约为2.0", "2.0", None),
    ("≈ (2.0 ± 0.4) × 10⁻³", "(2.0 ± 0.4) × 10⁻³", None),
])
def test_numeric_evidence_preserves_the_complete_reported_quantity(
    tmp_path, reported, fragment, kind,
):
    svc = service(tmp_path)
    doc = svc.ingest("paper.txt", f"Alpha threshold voltage {reported} V.".encode())
    block, = svc.repo.blocks(doc.id)
    values = dict(material_name="Alpha", property_name="threshold voltage",
                  value_raw=reported, unit_raw="V")
    row = MaterialRecord(
        id="record", document_id=doc.id, **values,
        evidence=[
            EvidenceReference(field=k, block_id=block.id, quote=v) for k, v in values.items()
        ],
    )
    cropped = row.model_copy(update={
        "value_raw": fragment,
        "evidence": [
            ref.model_copy(update={"quote": fragment}) if ref.field == "value_raw" else ref
            for ref in row.evidence
        ],
    })
    with pytest.raises(AppError, match="value_raw"):
        svc.add_record(cropped)
    candidate = svc.add_record(row)
    for status in ("candidate", "approved"):
        with pytest.raises(AppError, match="value_raw"):
            svc.review(row.id, cropped, status=status)
    assert svc.repo.record(row.id) == candidate
    approved = svc.review(row.id, candidate, status="approved")
    assert approved.revision == 2
    exported, = csv.DictReader(io.StringIO(svc.export_csv().decode("utf-8-sig")))
    assert exported["value_raw"] == reported and exported["value_normalized"] == ""
    quantity = load_observations(svc.repo)[0].value
    assert (quantity.kind if quantity else None) == kind


@pytest.mark.parametrize(("reported", "fragment"), [
    ("at least 2.0", "2.0"),
    ("no more than 2.0", "2.0"),
    ("不低于2.0", "2.0"),
    ("no more than 2.0", "more than 2.0"),
    ("no less than 2.0", "less than 2.0"),
    ("no less than 2.0", "than 2.0"),
    ("no higher than 2.0", "higher than 2.0"),
    ("no more than approximately 2.0", "approximately 2.0"),
    ("not exceeding 2.0", "exceeding 2.0"),
    ("not to exceed 2.0", "exceed 2.0"),
    ("does not exceed 2.0", "exceed 2.0"),
    ("不高于2.0", "高于2.0"),
    ("不超过2.0", "超过2.0"),
    ("不高于约2.0", "约2.0"),
    ("不得超过2.0", "超过2.0"),
    ("不低于2.0", "低于2.0"),
])
def test_textual_bound_must_keep_its_complete_qualifier(tmp_path, reported, fragment):
    svc = service(tmp_path)
    source = f"Alpha threshold voltage {reported} V."
    doc = svc.ingest("paper.txt", source.encode())
    block, = svc.repo.blocks(doc.id)
    values = dict(
        material_name="Alpha", property_name="threshold voltage",
        value_raw=reported, unit_raw="V",
    )
    full = MaterialRecord(
        id="record", document_id=doc.id, **values,
        evidence=[
            EvidenceReference(field=k, block_id=block.id, quote=v) for k, v in values.items()
        ],
    )
    cropped = full.model_copy(update={
        "value_raw": fragment,
        "evidence": [
            ref.model_copy(update={"quote": fragment}) if ref.field == "value_raw" else ref
            for ref in full.evidence
        ],
    })
    with pytest.raises(AppError, match="value_raw"):
        svc.add_record(cropped)
    saved = svc.add_record(full)
    for status in ("candidate", "approved"):
        with pytest.raises(AppError, match="value_raw"):
            svc.review(full.id, cropped, status=status)
    assert svc.repo.record(full.id) == saved
    approved = svc.review(full.id, saved, status="approved")
    assert approved.value_normalized is None
    exported, = csv.DictReader(io.StringIO(svc.export_csv().decode("utf-8-sig")))
    assert exported["value_raw"] == reported and exported["value_normalized"] == ""


@pytest.mark.parametrize(("raw", "expected"), [("2.0", 2.0), ("-2.8", -2.8)])
def test_numeric_evidence_keeps_space_separated_negative_cell(raw, expected, tmp_path):
    svc = service(tmp_path)
    doc = svc.ingest("paper.txt", b"Alpha threshold voltage 2.0 -2.8 V.")
    block, = svc.repo.blocks(doc.id)
    values = dict(material_name="Alpha", property_name="threshold voltage", value_raw=raw,
                  unit_raw="V")
    candidate = svc.add_record(MaterialRecord(
        id="record", document_id=doc.id, **values,
        evidence=[
            EvidenceReference(field=k, block_id=block.id, quote=v) for k, v in values.items()
        ],
    ))
    assert svc.review(candidate.id, candidate, status="approved").value_normalized == expected


@pytest.mark.parametrize("unit", [
    "× 10⁻⁴ cm2/Vs", "10^-4 cm2/Vs", "(cm2/Vs) × 10⁻⁴",
])
def test_unit_evidence_cannot_drop_the_reported_scale(tmp_path, unit):
    svc = service(tmp_path)
    doc = svc.ingest("paper.txt", f"Alpha mobility: 20.\nMobility unit: {unit}.".encode())
    block, = svc.repo.blocks(doc.id)
    values = dict(material_name="Alpha", property_name="mobility", value_raw="20", unit_raw=unit)
    row = MaterialRecord(
        id="record", document_id=doc.id, **values,
        evidence=[
            EvidenceReference(field=k, block_id=block.id, quote=v) for k, v in values.items()
        ],
    )
    cropped = row.model_copy(update={
        "unit_raw": "cm2/Vs",
        "evidence": [
            ref.model_copy(update={"quote": "cm2/Vs"}) if ref.field == "unit_raw" else ref
            for ref in row.evidence
        ],
    })
    with pytest.raises(AppError, match="unit_raw"):
        svc.add_record(cropped)
    saved = svc.add_record(row)
    with pytest.raises(AppError, match="unit_raw"):
        svc.review(row.id, cropped, status="approved")
    approved = svc.review(row.id, saved, status="approved")
    assert approved.value_normalized == pytest.approx(0.002)
    exported, = csv.DictReader(io.StringIO(svc.export_csv().decode("utf-8-sig")))
    assert exported["unit_raw"] == unit and float(exported["value_normalized"]) == 0.002
    observation, = load_observations(svc.repo)
    assert observation.value is not None and observation.value.value == pytest.approx(0.002)
