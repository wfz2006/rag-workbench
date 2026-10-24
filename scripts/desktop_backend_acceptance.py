"""Run with the shipped runtime's `python.exe -I`, not the development interpreter."""

import argparse
import json
import os
import sys
from pathlib import Path

import rag_workbench
from rag_workbench.application.knowledge import KnowledgeService
from rag_workbench.config import Settings
from rag_workbench.infrastructure.embedding import BgeEmbedder


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--profile", type=Path, required=True)
    args = parser.parse_args()
    bundle, profile = args.bundle.resolve(), args.profile.resolve()
    assert Path(sys.executable).is_relative_to(bundle / "runtime")
    assert Path(rag_workbench.__file__).is_relative_to(bundle / "runtime")
    os.environ["HF_HUB_OFFLINE"] = "1"
    for name in list(os.environ):
        if name.startswith("RAG_") or name == "DEEPSEEK_API_KEY":
            del os.environ[name]
    settings = Settings(
        _env_file=None,
        api_key="",
        data_dir=profile / "acceptance-kb",
        model_cache=profile / "models",
    )
    service = KnowledgeService(settings, BgeEmbedder(settings))
    assert service.retrieve("空知识库") == []
    expected = service.ingest("desktop.txt", "虚构验收资料：记录保管三年，到期后复核。".encode())
    duplicate = service.ingest("renamed.txt", "虚构验收资料：记录保管三年，到期后复核。".encode())
    assert duplicate.id == expected.id
    service.ingest("appearance.md", "# 界面\n开发示例：界面可以切换浅色与深色主题。".encode())
    result = service.retrieve("记录的保管期限是多久？")
    assert result[0].chunk.document_id == expected.id
    assert result[0].score > result[1].score
    restarted = KnowledgeService(settings, BgeEmbedder(settings))
    assert restarted.retrieve("记录保管多久")[0].chunk.document_id == expected.id
    restarted.rebuild()
    restarted.delete(expected.id)
    assert all(r.chunk.document_id != expected.id for r in restarted.retrieve("保管期限"))
    # Review regression: restore only the target, even when another indexed raw file is absent.
    other = restarted.list_documents()[0]
    other_raw = restarted._raw_path(other)
    backup = profile / "acceptance-original.backup"
    other_raw.replace(backup)
    try:
        restored = restarted.restore(expected.id)
        assert restored.created_at == expected.created_at
        assert restarted.retrieve("保管期限")[0].chunk.document_id == expected.id
    finally:
        backup.replace(other_raw)
    restarted.delete(expected.id)
    remaining = restarted.list_documents()
    for doc in remaining:
        restarted.delete(doc.id)
    assert restarted.retrieve("保管期限") == []
    report = {
        "passed": True,
        "offline": True,
        "paid_api_calls": 0,
        "python": sys.version.split()[0],
        "module": str(rag_workbench.__file__),
        "top_score": result[0].score,
        "checks": [
            "isolated_runtime",
            "empty_kb",
            "real_bge",
            "txt",
            "markdown",
            "dedup",
            "faiss",
            "restart",
            "rebuild",
            "delete",
            "restore_without_other_originals",
            "restore_preserves_upload_time",
        ],
    }
    (profile / "backend-result.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
