import pytest

from rag_workbench.application.knowledge import KnowledgeService
from rag_workbench.config import Settings

from .fakes import FakeEmbedder


@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, data_dir=tmp_path / "kb", chunk_size=100, chunk_overlap=10)


@pytest.fixture
def embedder():
    return FakeEmbedder()


@pytest.fixture
def knowledge(settings, embedder):
    return KnowledgeService(settings, embedder)
