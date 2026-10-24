"""Resolve stable editorial source IDs to SHA-256 of actual uploaded files."""

import argparse
import csv
import hashlib
import json
import re
from pathlib import Path

from rag_workbench.application.evaluation import EvalCase


def materialize_cases(
    labels_path: Path, manifest_path: Path, corpus_root: Path, output_path: Path
) -> int:
    corpus_root = corpus_root.resolve()
    output = output_path.resolve()
    if output in {labels_path.resolve(), manifest_path.resolve(), corpus_root} or (
        corpus_root in output.parents
    ):
        raise ValueError("Unsafe runtime output overlaps input labels, manifest or corpus")
    manifest = {
        r["source_id"]: r
        for r in csv.DictReader(manifest_path.open(encoding="utf-8-sig", newline=""))
        if r["status"] == "selected"
    }
    cases, identities = [], set()
    for line in labels_path.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip():
            continue
        raw = json.loads(line)
        if raw["id"] in identities:
            raise ValueError("Duplicate question ID")
        identities.add(raw["id"])
        documents = set()
        for locator in raw["relevant_source_ids"]:
            sid, _, page = locator.partition("@")
            if sid not in manifest or (page and not re.fullmatch(r"p\d{3}", page)):
                raise ValueError(f"Unknown source locator: {locator}")
            row = manifest[sid]
            title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", row["title"])[:70]
            folder = (corpus_root / row["prepared_path"]).resolve()
            if corpus_root not in folder.parents:
                raise ValueError("Corpus path escape")
            pattern = f"{sid}_{title}_{page}.md" if page else f"{sid}_{title}*.md"
            matches = list(folder.glob(pattern))
            if not matches:
                raise ValueError(f"Missing corpus document: {locator}")
            for path in matches:
                if corpus_root not in path.resolve().parents:
                    raise ValueError("Corpus document escapes root")
                documents.add(hashlib.sha256(path.read_bytes()).hexdigest())
        if raw["expected_mode"] == "knowledge_base" and not documents:
            raise ValueError("Knowledge-base case requires evidence labels")
        case = EvalCase(
            id=raw["id"],
            question=raw["question"],
            relevant_document_ids=sorted(documents),
            expected_mode=raw["expected_mode"],
        )
        cases.append(case.model_dump_json())
    if not cases:
        raise ValueError("Empty evaluation dataset")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(cases) + "\n", encoding="utf-8")
    return len(cases)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(materialize_cases(args.labels, args.manifest, args.corpus, args.output))
