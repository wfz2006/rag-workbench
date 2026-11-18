"""Portable observation exports with a closed set of source/evidence references."""

import csv
import hashlib
import io
import json
import re
import zipfile
from dataclasses import asdict

from rag_workbench.domain.errors import AppError
from rag_workbench.domain.materials import LiteratureDocument, MaterialRecord
from rag_workbench.infrastructure.parsing import quote_in_source


def _rows(repo, document_id, approved_only, record_snapshot):
    records = repo.records(document_id) if record_snapshot is None else record_snapshot
    return [
        row for row in records
        if (document_id is None or row.document_id == document_id)
        and (not approved_only or row.status == "approved")
    ]


def _safe_cell(value):
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False)
    text = str(value)
    if text.lstrip().startswith(("=", "+", "-", "@")) and not re.fullmatch(
        r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", text,
    ):
        return "'" + text
    return text


def _csv_bytes(rows, fields):
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        writer.writerow({key: _safe_cell(row.get(key)) for key in fields})
    return output.getvalue().encode("utf-8-sig")


def export_csv(repo, document_id=None, *, approved_only=True, record_snapshot=None):
    records = _rows(repo, document_id, approved_only, record_snapshot)
    return _csv_bytes(
        [row.model_dump(mode="json") for row in records],
        list(MaterialRecord.model_fields),
    )


def export_analysis(repo, rows, settings, plot_rows, *, chart_spec=None, chart_files=None):
    """Export the exact approved revisions presented in a filtered analysis view."""
    revisions = {row.record.id: row.record.revision for row in rows}
    if any(row.record.status != "approved" for row in rows) or any(
        revisions.get(point["record_id"]) != point["revision"] for point in plot_rows
    ):
        raise AppError("material_analysis_snapshot", "图表与已批准的筛选记录版本不一致。")

    papers = {row.paper.id for row in rows}
    documents = {
        doc.id: doc for doc in repo.documents() if doc.id in papers or doc.parent_id in papers
    }
    refs = {ref.block_id for row in rows for ref in row.record.evidence}
    blocks = {
        block.id: block for identity in documents for block in repo.blocks(identity)
        if block.id in refs
    }
    record_fields = [
        field for field in MaterialRecord.model_fields
        if field not in {"value_normalized", "unit_normalized"}
    ]
    quantity_fields = ("kind", "value", "unit", "low", "high", "error", "bound")
    observations, csv_rows, evidence_rows = [], [], []
    for row in rows:
        evidence = []
        for ref in row.record.evidence:
            block = blocks.get(ref.block_id)
            if block is None or not quote_in_source(ref.quote, block.text):
                raise AppError("material_export_reference", "部分字段证据已无法定位，已停止导出。")
            source = documents[block.document_id]
            if source.id != row.paper.id and source.parent_id != row.paper.id:
                raise AppError("material_export_reference", "字段证据的论文归属不一致。")
            evidence.append({
                **ref.model_dump(), "source_id": source.id, "source_name": source.name,
                "page": block.page, "bbox": block.bbox,
            })
        payload = row.record.model_dump(mode="json")
        source_info = {"paper_id": row.paper.id, "paper_name": row.paper.name,
                       "source_name": row.document.name}
        analysis = {
            "metric": row.metric.key if row.metric else None,
            "quantity": asdict(row.value) if row.value else None,
            "contexts": {field: asdict(value) for field, value in row.contexts.items()},
            "comparison_key": row.comparison_key,
            "comparison_label": row.comparison_label,
        }
        observations.append({
            **source_info, "record": payload, "analysis": analysis, "evidence": evidence,
        })
        csv_rows.append({
            **source_info, **payload, "analysis_metric": analysis["metric"],
            **{f"analysis_{key}": (analysis["quantity"] or {}).get(key)
               for key in quantity_fields},
            "comparison_label": row.comparison_label,
            "analysis_contexts": analysis["contexts"],
        })
        evidence_rows.extend({"record_id": row.record.id, **ref} for ref in evidence)

    snapshot = {
        "schema_version": 1, "settings": settings, "observations": observations,
        "plot_rows": plot_rows,
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("analysis.json", json.dumps(snapshot, ensure_ascii=False, indent=2))
        archive.writestr("observations.csv", _csv_bytes(
            csv_rows, ["paper_id", "paper_name", "source_name", *record_fields, "analysis_metric",
                       *(f"analysis_{key}" for key in quantity_fields),
                       "comparison_label", "analysis_contexts"],
        ))
        archive.writestr("evidence.csv", _csv_bytes(evidence_rows, [
            "record_id", "field", "block_id", "quote", "source_id", "source_name", "page", "bbox",
        ]))
        if plot_rows:
            archive.writestr("chart-data.csv", _csv_bytes(plot_rows, list(plot_rows[0])))
        if chart_spec is not None:
            archive.writestr("chart.vl.json", json.dumps(chart_spec, ensure_ascii=False))
        for name, data in (chart_files or {}).items():
            archive.writestr(name, data)
    return output.getvalue()


def export_bundle(repo, document_id=None, *, approved_only=True, record_snapshot=None):
    records = _rows(repo, document_id, approved_only, record_snapshot)
    revisions = {
        row.id: [revision for revision in repo.revisions(row.id)
                 if revision["revision"] <= row.revision]
        for row in records
    }
    all_docs = {doc.id: doc for doc in repo.documents()}
    all_blocks = {block.id: block for doc in all_docs.values() for block in repo.blocks(doc.id)}
    needed = {row.document_id for row in records}
    block_ids = {ref.block_id for row in records for ref in row.evidence}
    block_ids.update(
        evidence["block_id"] for history in revisions.values()
        for revision in history for evidence in revision["evidence"]
    )
    if document_id:
        if document_id not in all_docs:
            raise AppError("material_document_missing", "导出的文献不存在。")
        needed.add(document_id)
    for block_id in block_ids:
        if block_id not in all_blocks:
            raise AppError("material_export_reference", "记录存在无法定位的证据，已停止导出。")
        needed.add(all_blocks[block_id].document_id)
    for doc_id in list(needed):
        if all_docs[doc_id].parent_id:
            needed.add(all_docs[doc_id].parent_id)
    documents = [all_docs[identity] for identity in sorted(needed)]
    blocks = [b for b in all_blocks.values() if b.document_id in needed]
    dataset = {
        "schema_version": 1,
        "representation": "observation-with-embedded-material-sample-device-and-conditions",
        "approved_only": approved_only,
        "documents": [doc.model_dump(mode="json") for doc in documents],
        "blocks": [block.model_dump(mode="json") for block in blocks],
        "records": [row.model_dump(mode="json") for row in records],
        "revisions": revisions,
    }
    output = io.BytesIO()
    manifest = {}

    def add(archive, name, data):
        archive.writestr(name, data)
        manifest[name] = hashlib.sha256(data).hexdigest()

    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        add(archive, "dataset.json", json.dumps(dataset, ensure_ascii=False, indent=2).encode())
        add(archive, "measurements.csv", _csv_bytes(
            dataset["records"], list(MaterialRecord.model_fields),
        ))
        add(archive, "documents.csv", _csv_bytes(
            dataset["documents"], list(LiteratureDocument.model_fields),
        ))
        evidence = [
            {"record_id": row.id, **ref.model_dump()}
            for row in records for ref in row.evidence
        ]
        add(archive, "evidence.csv", _csv_bytes(
            evidence, ["record_id", "field", "block_id", "quote"],
        ))
        for doc in documents:
            try:
                data = (repo.originals / f"{doc.id}{doc.extension}").read_bytes()
                if hashlib.sha256(data).hexdigest() != doc.id:
                    raise ValueError("original hash mismatch")
            except (OSError, ValueError) as exc:
                raise AppError(
                    "material_original_missing", "原件缺失或损坏，已停止完整导出。",
                ) from exc
            add(archive, f"originals/{doc.id}{doc.extension}", data)
            parsed = repo.parse_artifact(doc.id)
            if parsed:
                add(archive, f"parsed/{doc.id}.json", parsed.encode())
        add(archive, "README.txt", (
            b"Materials export schema v1. dataset.json is authoritative; CSV is a view.\n"
            b"Material, sample, device, process and conditions are embedded in each observation.\n"
            b"IDs join observations, field evidence, original source files and source blocks.\n"
            b"Raw text, values and units are preserved. Empty means unreported or unconfirmed.\n"
            b"Candidates must not be used as approved experimental training labels.\n"
        ))
        archive.writestr("manifest.json", json.dumps({"sha256": manifest}, indent=2))
    return output.getvalue()
