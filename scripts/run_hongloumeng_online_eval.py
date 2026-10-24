"""Run the real DeepSeek path for Hongloumeng and keep answers for review."""

import argparse
import json
import sys
import time
from pathlib import Path

from rag_workbench.application.evaluation import EvalCase
from rag_workbench.bootstrap import build_services
from rag_workbench.config import load_file_settings

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = PROJECT_ROOT / "knowledge_packs" / "hongloumeng" / "settings.json"


def mean(values):
    return sum(values) / len(values) if values else None


def load_author_labels(path: Path | None) -> dict[str, dict]:
    if path is None:
        return {}
    rows = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if line.strip():
            row = json.loads(line)
            rows[row["id"]] = row
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--author-labels", type=Path)
    parser.add_argument("--data-dir", type=Path, default=Path("data/hongloumeng"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    cases = [
        EvalCase.model_validate_json(line)
        for line in args.labels.read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    ]
    authors = load_author_labels(args.author_labels)
    settings = load_file_settings(CONFIG_PATH, data_dir=args.data_dir)
    services = build_services(settings)
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    hits, recalls, eligible_hits, eligible_recalls = [], [], [], []
    irrelevant_hits, routes, citations, expected_citations = [], [], [], []
    cited_expected, cited_other = [], []
    errors = 0
    with output.open("w", encoding="utf-8") as stream:
        for index, case in enumerate(cases, 1):
            started = time.perf_counter()
            error_code = None
            try:
                result = services.pipeline.answer(case.question)
            except Exception as exc:  # keep the run alive for per-case reporting
                result = None
                error_code = f"unexpected_{type(exc).__name__}"
            elapsed = time.perf_counter() - started
            relevant = set(case.relevant_document_ids)
            if result is None:
                candidates = []
                mode = "error"
                answer = ""
                sources = []
                evidence_reason = ""
            else:
                candidates = result.debug.candidates
                mode = result.mode
                error_code = result.error_code
                answer = result.answer
                sources = result.sources
                evidence_reason = result.debug.reason
            found = {r.chunk.document_id for r in candidates}
            eligible_found = {r.chunk.document_id for r in candidates if r.eligible}
            cited_document_ids = {source.chunk.document_id for source in sources}
            citation_support = None
            if relevant:
                citation_support = bool(cited_document_ids & relevant)
            elif sources:
                citation_support = False
            if error_code is None:
                if relevant:
                    hits.append(bool(found & relevant))
                    recalls.append(len(found & relevant) / len(relevant))
                    eligible_hits.append(bool(eligible_found & relevant))
                    eligible_recalls.append(len(eligible_found & relevant) / len(relevant))
                    expected_citations.append(citation_support)
                    cited_expected.append(bool(cited_document_ids & relevant))
                    cited_other.append(bool(cited_document_ids - relevant))
                else:
                    irrelevant_hits.append(any(r.eligible for r in candidates))
            if result is not None:
                routes.append(mode == case.expected_mode)
                if mode == "knowledge_base":
                    actual = {r.chunk.id for r in candidates if r.eligible}
                    citations.append(
                        bool(sources)
                        and all(
                            r.chunk.id in actual and f"[{r.chunk.id}]" in answer for r in sources
                        )
                    )
            errors += int(error_code is not None)
            author = authors.get(case.id, {})
            row = {
                "id": case.id,
                "question": case.question,
                "category": author.get("category"),
                "expected_mode": case.expected_mode,
                "mode": mode,
                "error_code": error_code,
                "answer": answer,
                "evidence_reason": evidence_reason,
                "reference_facts": author.get("reference_facts", []),
                "author_notes": author.get("notes", ""),
                "relevant_paths": author.get("relevant_paths", []),
                "sources": [
                    {
                        "chunk_id": r.chunk.id,
                        "document": r.chunk.document_name,
                        "document_id": r.chunk.document_id,
                        "score": r.score,
                        "eligible": r.eligible,
                    }
                    for r in sources
                ],
                "retrieved_document_ids": sorted(found),
                "eligible_document_ids": sorted(eligible_found),
                "cited_document_ids": sorted(cited_document_ids),
                "citation_support": citation_support,
                "seconds": round(elapsed, 2),
            }
            rows.append(row)
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
            stream.flush()
            print(
                f"[{index}/{len(cases)}] {case.id} {mode} {elapsed:.1f}s",
                file=sys.stderr,
                flush=True,
            )
    summary = {
        "cases": len(cases),
        "top_k": services.knowledge.settings.top_k,
        "threshold": services.knowledge.settings.similarity_threshold,
        "retrieval_hit_rate": mean(hits),
        "recall_at_k": mean(recalls),
        "eligible_hit_rate": mean(eligible_hits),
        "eligible_recall_at_k": mean(eligible_recalls),
        "irrelevant_threshold_hit_rate": mean(irrelevant_hits),
        "routing_accuracy": mean(routes),
        "knowledge_base_cases": len(citations),
        "citation_validity": mean(citations),
        "positive_cases": len(expected_citations),
        "expected_citation_rate": mean(expected_citations),
        "cited_expected_rate": mean(cited_expected),
        "cited_other_rate": mean(cited_other),
        "negative_cases": len(irrelevant_hits),
        "service_errors": errors,
        "successful_retrieval_cases": len(cases) - errors,
        "with_routing": True,
    }
    summary_path = output.with_name(output.stem + "-summary.json")
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
