"""Portable observation exports with a closed set of source/evidence references."""

import csv
import hashlib
import io
import json
import re
import zipfile

from rag_workbench.domain.errors import AppError
from rag_workbench.domain.materials import LiteratureDocument, MaterialRecord


def _rows(repo, document_id, approved_only):
    return [
        row for row in repo.records(document_id)
        if not approved_only or row.status == "approved"
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


def export_csv(repo, document_id=None, *, approved_only=True):
    return _csv_bytes(
        [row.model_dump(mode="json") for row in _rows(repo, document_id, approved_only)],
        list(MaterialRecord.model_fields),
    )


def export_bundle(repo, document_id=None, *, approved_only=True):
    records = _rows(repo, document_id, approved_only)
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
