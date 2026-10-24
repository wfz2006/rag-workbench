"""Map Santi author labels to the SHA-256 IDs of the imported Markdown files."""

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from rag_workbench.application.evaluation import EvalCase


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_manifest(path: Path) -> dict[str, dict]:
    rows = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row["path"] in rows:
            raise ValueError(f"Duplicate generated path: {row['path']}")
        rows[row["path"]] = row
    return rows


def materialize(labels: Path, manifest: Path, corpus: Path, output_dir: Path) -> dict:
    labels, manifest, corpus = labels.resolve(), manifest.resolve(), corpus.resolve()
    output_dir = output_dir.resolve()
    if output_dir == corpus or corpus in output_dir.parents:
        raise ValueError("Runtime labels must not be written inside corpus")
    rows = load_manifest(manifest)
    splits: dict[str, list[str]] = {"dev": [], "holdout": []}
    seen = set()
    categories: dict[str, int] = {}
    for line in labels.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip():
            continue
        raw = json.loads(line)
        if raw["id"] in seen:
            raise ValueError(f"Duplicate question ID: {raw['id']}")
        seen.add(raw["id"])
        split = raw["split"]
        if split not in splits:
            raise ValueError(f"Unknown split: {split}")
        documents = []
        for relative in raw["relevant_paths"]:
            if relative not in rows:
                raise ValueError(f"Unknown corpus document: {relative}")
            document_id = rows[relative]["sha256"]
            if sha256(corpus / relative) != document_id:
                raise ValueError(f"Corpus changed: {relative}")
            documents.append(document_id)
        if raw["expected_mode"] == "knowledge_base" and not documents:
            raise ValueError(f"Positive case lacks evidence: {raw['id']}")
        if raw["expected_mode"] != "knowledge_base" and documents:
            raise ValueError(f"Negative case has evidence: {raw['id']}")
        case = EvalCase(
            id=raw["id"],
            question=raw["question"],
            relevant_document_ids=sorted(set(documents)),
            expected_mode=raw["expected_mode"],
        )
        splits[split].append(case.model_dump_json())
        categories[raw["category"]] = categories.get(raw["category"], 0) + 1
    if not splits["dev"] or not splits["holdout"]:
        raise ValueError("Both dev and holdout must be non-empty")
    output_dir.mkdir(parents=True, exist_ok=True)
    files = {}
    for split, lines in splits.items():
        target = output_dir / f"labels.{split}.jsonl"
        target.write_text("\n".join(lines) + "\n", encoding="utf-8")
        files[split] = {
            "path": target.name,
            "cases": len(lines),
            "sha256": sha256(target),
        }
    freeze = {
        "author_labels": labels.name,
        "author_labels_sha256": sha256(labels),
        "generated_manifest_sha256": sha256(manifest),
        "splits": files,
        "categories": categories,
        "frozen_at": datetime.now(UTC).isoformat(),
    }
    freeze_path = output_dir / "labels_freeze.json"
    freeze_path.write_text(json.dumps(freeze, ensure_ascii=False, indent=2), encoding="utf-8")
    return freeze


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    freeze = materialize(args.labels, args.manifest, args.corpus, args.output_dir)
    print(json.dumps(freeze["splits"], ensure_ascii=False))


if __name__ == "__main__":
    main()
