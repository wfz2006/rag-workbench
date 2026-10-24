"""Reproducible local corpus import/retrieval evaluation; LLM calls require --with-routing."""

import argparse
import hashlib
import json
import os
import time
from pathlib import Path

from rag_workbench.application.evaluation import EvalCase, evaluate
from rag_workbench.application.pipeline import RagPipeline
from rag_workbench.bootstrap import build_services
from rag_workbench.config import Settings
from scripts.materialize_lanqiao_eval import materialize_cases

ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "knowledge_packs/lanqiao_ds"
REVISION = "7999e1d3359715c523056ef9478215996d62a620"


class RecordedPipeline(RagPipeline):
    """Keep real response snapshots for subsequent evidence and factuality review."""

    def __init__(self, pipeline: RagPipeline):
        super().__init__(
            pipeline.knowledge, pipeline.provider, teaching_profile=pipeline.teaching_profile
        )
        self.answers = []

    def answer(self, question, history=None, **kwargs):
        result = super().answer(question, history, **kwargs)
        self.answers.append(result.model_dump())
        print(f"Answered {len(self.answers)}: {result.mode}", flush=True)
        return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", choices=["dev", "test", "holdout", "newcontent"], default="dev")
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data/lanqiao_ds_trial")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--threshold", type=float, default=0.55)
    parser.add_argument("--chunk-size", type=int, default=400)
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--context-window", type=int, default=0)
    parser.add_argument("--with-routing", action="store_true")
    parser.add_argument("--run-label", default="", help="Optional suffix for report filenames")
    args = parser.parse_args()
    if args.data_dir.resolve() in {ROOT / "data/production", ROOT / "data/sandbox"}:
        raise ValueError("Use a dedicated course workspace")
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    import torch

    torch.set_num_threads(4)
    settings = Settings(
        data_dir=args.data_dir.resolve(),
        model_cache=ROOT / ".cache/models",
        embedding_revision=REVISION,
        chunk_size=args.chunk_size,
        chunk_overlap=60,
        top_k=args.top_k,
        similarity_threshold=args.threshold,
        allow_general=False,
        retrieval_context_window=args.context_window,
        teaching_profile_path=PACK / "profile/teaching.md",
    )
    if args.with_routing and not settings.api_key.get_secret_value().strip():
        raise ValueError("Configure DEEPSEEK_API_KEY locally before --with-routing")
    services = build_services(settings)
    report_root = ROOT / "artifacts/lanqiao_ds"
    report_root.mkdir(parents=True, exist_ok=True)
    build = json.loads((report_root / "build.json").read_text(encoding="utf-8"))
    known = {doc.id for doc in services.repo.documents() if doc.status == "ready"}
    expected = {item["sha256"] for item in build["documents"]}
    if any(doc.id not in expected for doc in services.repo.documents()):
        raise ValueError("Unexpected documents in destination; preserve and inspect manually")
    started = time.monotonic()
    pending = []
    for i, item in enumerate(build["documents"], 1):
        path = PACK / "corpus" / item["path"]
        if hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
            raise ValueError("Frozen content hash changed")
        if item["sha256"] not in known:
            pending.append((path.name, path.read_bytes()))
        if i % 10 == 0 or i == len(build["documents"]):
            elapsed = time.monotonic() - started
            print(f"Imported {i}/{len(build['documents'])}; elapsed {elapsed:.1f}s", flush=True)
    if pending:
        imported = services.knowledge.ingest_many(pending)
        if any(doc.status == "failed" for doc in imported):
            raise ValueError("Some imported documents failed; inspect the isolated workspace")
    services.knowledge.assign_sources(
        {
            item["sha256"]: (item["source_id"], item["page"])
            for item in build["documents"]
            if item["page"] is not None
        }
    )
    if args.rebuild:
        services.knowledge.rebuild()
    persisted = {
        k: getattr(settings, k)
        for k in (
            "embedding_revision",
            "chunk_size",
            "chunk_overlap",
            "top_k",
            "similarity_threshold",
            "allow_general",
        )
    }
    runtime = report_root / f"{args.split}.runtime.jsonl"
    labels_path = PACK / f"eval/labels.{args.split}.jsonl"
    materialize_cases(labels_path, PACK / "source_manifest.csv", PACK / "corpus", runtime)
    cases = [
        EvalCase.model_validate_json(line)
        for line in runtime.read_text(encoding="utf-8").splitlines()
    ]
    pipeline = RecordedPipeline(services.pipeline)
    result = evaluate(pipeline, cases, with_routing=args.with_routing)
    result["configuration"] = {
        **persisted,
        "embedding_model": settings.embedding_model,
        "data_dir": str(settings.data_dir),
        "documents": len(expected),
        "dataset_sha256": hashlib.sha256(labels_path.read_bytes()).hexdigest(),
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "llm_model": settings.llm_model if args.with_routing else None,
        "context_window": args.context_window,
        "retrieval_candidate_pool": settings.retrieval_candidate_pool,
    }
    suffix = f"{args.split}-k{args.top_k}-t{args.threshold}"
    if args.chunk_size != 400:
        suffix += f"-c{args.chunk_size}"
    if args.context_window:
        suffix += f"-w{args.context_window}"
    if args.with_routing:
        suffix += "-routing"
    if args.run_label:
        suffix += f"-{args.run_label}"
    (report_root / f"{suffix}.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if args.with_routing:
        (report_root / f"{suffix}-answers.json").write_text(
            json.dumps(pipeline.answers, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    candidates = [
        {
            "id": case.id,
            "question": case.question,
            "chunks": row["candidates"],
        }
        for case, row in zip(cases, result["results"], strict=True)
    ]
    (report_root / f"{suffix}-evidence.json").write_text(
        json.dumps(candidates, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps({k: v for k, v in result.items() if k not in {"results", "configuration"}}),
        flush=True,
    )


if __name__ == "__main__":
    main()
