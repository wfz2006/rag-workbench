"""Check the trial's shipped PDF renderer, records, durable drafts and export offline."""

import argparse
import csv
import io
import json
import os
import sys
import zipfile
from pathlib import Path

import rag_workbench
from rag_workbench.application.material_export import export_bundle, export_csv
from rag_workbench.application.materials import MaterialsService
from rag_workbench.config import Settings
from rag_workbench.domain.errors import AppError
from rag_workbench.domain.materials import EvidenceReference, MaterialRecord
from rag_workbench.infrastructure.material_draft_store import MaterialDraftStore
from rag_workbench.infrastructure.material_preview import (
    load_original,
    pdf_page_count,
    render_pdf_page,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--profile", type=Path, required=True)
    args = parser.parse_args()
    bundle, profile = args.bundle.resolve(), args.profile.resolve()
    assert Path(sys.executable).is_relative_to(bundle / "runtime")
    assert Path(rag_workbench.__file__).is_relative_to(bundle / "runtime")
    assert not profile.is_relative_to(bundle), "Keep QA data out of the public release."
    profile.mkdir(parents=True, exist_ok=True)
    for key in list(os.environ):
        if key.startswith("RAG_") or key == "DEEPSEEK_API_KEY":
            del os.environ[key]
    settings = Settings(_env_file=None, api_key="", data_dir=profile / "materials-acceptance")
    service = MaterialsService(settings)
    original = (bundle / "试用示例/synthetic-ofet.pdf").read_bytes()
    document = service.ingest("synthetic-ofet.pdf", original)
    assert service.ingest("same-file.pdf", original).id == document.id
    data = load_original(service.repo, document)
    assert pdf_page_count(data) == 2
    images = [render_pdf_page(data, page) for page in (1, 2)]
    assert images[0].png != images[1].png
    assert render_pdf_page(data, 1, scale=1.5).width > images[0].width
    for page, image in enumerate(images, 1):
        assert image.png.startswith(b"\x89PNG\r\n\x1a\n")
        (profile / f"example-page-{page}.png").write_bytes(image.png)
    blocks = service.repo.blocks(document.id)
    assert {block.page for block in blocks} == {1, 2}
    fields = {
        "material_name": "TEST-OSC-A",
        "sample_label": "SYN-01",
        "property_name": "mobility",
        "value_raw": "0.42",
        "unit_raw": "cm^2 V^-1 s^-1",
        "device_structure": "Si / SiO2 / PMMA / TEST-OSC-A / Au",
        "process": "100 C for 30 min in nitrogen",
        "conditions": "300 K",
        "regime": "saturation",
        "statistic": "mean of 6 devices",
    }
    evidence = []
    for field, value in fields.items():
        block = next(block for block in blocks if value in block.text)
        evidence.append(EvidenceReference(field=field, block_id=block.id, quote=value))
    candidate = service.add_record(
        MaterialRecord(
            id="synthetic_trial_record",
            document_id=document.id,
            domain="ofet",
            **fields,
            evidence=evidence,
        )
    )
    assert list(csv.DictReader(io.StringIO(export_csv(service.repo).decode("utf-8-sig")))) == []
    drafts = MaterialDraftStore(service.repo.root)
    saved = drafts.save(
        "synthetic_trial_draft",
        {"conditions": "unsaved local notes"},
        document_id=document.id,
        kind="review",
        record_id=candidate.id,
        base_revision=1,
    )
    reopened = MaterialDraftStore(service.repo.root)
    assert reopened.get(saved.key) == saved
    newer = reopened.save(
        saved.key,
        {"conditions": "newer local notes"},
        document_id=document.id,
        kind="review",
        record_id=candidate.id,
        base_revision=1,
        expected_version=saved.version,
    )
    try:
        drafts.delete(saved.key, expected_version=saved.version)
    except AppError as exc:
        assert exc.code == "material_draft_conflict"
    else:
        raise AssertionError("Stale draft deletion unexpectedly succeeded.")
    assert reopened.get(saved.key) == newer
    approved = service.review(candidate.id, candidate, status="approved")
    restarted = MaterialsService(settings)
    assert restarted.repo.record(candidate.id) == approved
    rows = list(csv.DictReader(io.StringIO(export_csv(restarted.repo).decode("utf-8-sig"))))
    assert len(rows) == 1 and rows[0]["value_raw"] == "0.42"
    exported = export_bundle(restarted.repo)
    with zipfile.ZipFile(io.BytesIO(exported)) as archive:
        dataset = json.loads(archive.read("dataset.json"))
        assert len(dataset["records"]) == 1 and len(dataset["revisions"][candidate.id]) == 2
        assert archive.read(f"originals/{document.id}.pdf") == original
    (profile / "materials-example-export.zip").write_bytes(exported)
    report = {
        "passed": True,
        "paid_api_calls": 0,
        "python": sys.version.split()[0],
        "checks": [
            "shipped_runtime",
            "two_page_pdf_import",
            "dedup",
            "original_integrity",
            "pdfium_preview",
            "zoom",
            "field_evidence",
            "candidate_excluded_by_default",
            "durable_draft_recovery",
            "stale_draft_protection",
            "approval",
            "restart",
            "csv",
            "zip_original_and_revision_history",
        ],
    }
    (profile / "materials-result.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
