import hashlib

import pytest

from rag_workbench.application.reproduction import ReproductionChecklist
from rag_workbench.domain.errors import AppError
from rag_workbench.domain.materials import EvidenceBlock, LiteratureDocument
from rag_workbench.domain.reproduction import ReproductionStep
from rag_workbench.infrastructure.material_repository import MaterialsRepository


def _document(repo, name, text, parent=None):
    raw = text.encode()
    identity = hashlib.sha256(raw).hexdigest()
    doc = LiteratureDocument(id=identity, name=name, extension=".txt", size=len(raw),
                             parser="text", role="supplement" if parent else "main",
                             parent_id=parent)
    block = EvidenceBlock(id=identity, document_id=identity, text=text)
    repo.save_document(doc, [block], raw)
    return doc, block


def test_reproduction_validates_paper_scope_and_preserves_review_status(tmp_path):
    repo = MaterialsRepository(tmp_path)
    paper, _ = _document(repo, "paper.txt", "Preparation and measurements")
    supplement, original = _document(repo, "si.txt", "The film was dried.", paper.id)
    _, unrelated = _document(repo, "other.txt", "Different procedure")
    checklist = ReproductionChecklist(repo)
    step = ReproductionStep(paper_id=paper.id, stage="processing", title="干燥温度")
    pending = checklist.save(step)
    with pytest.raises(AppError, match="核查范围"):
        checklist.save(pending.model_copy(update={"status": "not_reported"}))
    missing = checklist.save(pending.model_copy(update={
        "status": "not_reported", "note": "已查正文方法和补充材料制备段，仅报告了干燥。",
    }))
    with pytest.raises(AppError, match="其他窗口"):
        checklist.save(pending)
    verified = ReproductionStep(paper_id=paper.id, stage="processing", title="干燥步骤",
                                status="verified", block_id=original.id, quote=original.text)
    for invalid in (verified.model_copy(update={"block_id": unrelated.id}),
                    verified.model_copy(update={"quote": "invented"})):
        with pytest.raises(AppError, match="引文"):
            checklist.save(invalid)
    saved = checklist.save(verified)
    reopened = ReproductionChecklist(MaterialsRepository(tmp_path))
    assert reopened.steps(paper.id) == [missing, saved]
    assert reopened.source(saved)[0].id == supplement.id
    reopened.remove(saved)
    assert reopened.steps(paper.id) == [missing]
