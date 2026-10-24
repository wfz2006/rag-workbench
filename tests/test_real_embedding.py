import os

import pytest

from rag_workbench.application.knowledge import KnowledgeService
from rag_workbench.config import Settings
from rag_workbench.infrastructure.embedding import BgeEmbedder


@pytest.mark.integration
@pytest.mark.skipif(os.getenv("RAG_RUN_MODEL_TEST") != "1", reason="requires real BGE download")
def test_real_bge_retrieval_restart_and_delete(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path / "real-kb")
    service = KnowledgeService(settings, BgeEmbedder(settings))
    expected = service.ingest("sample.txt", "虚构示例：记录保管三年，到期后复核。".encode())
    service.ingest("interface.md", "开发样例：界面支持浅色和深色主题。".encode())
    results = service.retrieve("记录的保管期限是多久？")
    assert results[0].chunk.document_id == expected.id
    assert results[0].score > results[1].score
    restarted = KnowledgeService(settings, BgeEmbedder(settings))
    assert restarted.retrieve("保管期限")[0].chunk.document_id == expected.id
    restarted.delete(expected.id)
    assert all(r.chunk.document_id != expected.id for r in restarted.retrieve("保管期限"))


@pytest.mark.integration
@pytest.mark.skipif(os.getenv("RAG_RUN_MODEL_TEST") != "1", reason="requires real BGE download")
def test_real_bge_large_character_chunks_and_long_question(tmp_path):
    from rag_workbench.infrastructure.embedding import _load_model

    settings = Settings(_env_file=None, data_dir=tmp_path / "long-kb", chunk_size=4000)
    adapter = BgeEmbedder(settings)
    service = KnowledgeService(settings, adapter)
    text = "虚构验收资料：记录保管三年，到期后复核。" * 100 + "末尾保留全部内容。"
    service.ingest("long.txt", text.encode())
    chunks = service.repo.chunks()
    assert len(chunks) > 1 and chunks[-1].text.endswith("末尾保留全部内容。")
    model = _load_model(
        settings.embedding_model,
        settings.embedding_revision,
        str(settings.model_cache.resolve()),
        settings.embedding_device,
    )
    assert all(
        len(model.tokenizer(c.text, truncation=False)["input_ids"]) <= model.max_seq_length
        for c in chunks
    )
    query = "这些记录到期以后应如何处理？" * 100
    assert 512 < len(query) < 2000
    assert service.retrieve(query)
