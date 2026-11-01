import hashlib
import json

import pytest
from pydantic import ValidationError

from rag_workbench.domain.errors import AppError
from rag_workbench.domain.materials import (
    EvidenceBlock,
    EvidenceReference,
    ExtractionRun,
    LiteratureDocument,
    MaterialRecord,
)
from rag_workbench.infrastructure.material_repository import MaterialsRepository


def source(data=b"Material A mobility 0.2 cm2/Vs", **updates):
    return LiteratureDocument(
        id=hashlib.sha256(data).hexdigest(),
        name="paper.txt",
        extension=".txt",
        size=len(data),
        parser="text",
        **updates,
    )


def block(document, block_id="block-one"):
    return EvidenceBlock(
        id=block_id,
        document_id=document.id,
        text="Material A mobility 0.2 cm2/Vs",
        page=1,
    )


def candidate(document):
    return MaterialRecord(
        id="record-one",
        document_id=document.id,
        domain="ofet",
        material_name="Material A",
        property_name="mobility",
        value_raw="0.2",
        unit_raw="cm2/Vs",
        evidence=[EvidenceReference(block_id="block-one", quote="0.2", field="value_raw")],
    )


@pytest.fixture
def catalog(tmp_path):
    repo = MaterialsRepository(tmp_path / "materials")
    document = source()
    repo.save_document(document, [block(document)], b"Material A mobility 0.2 cm2/Vs")
    return repo, document


def test_document_and_evidence_survive_reopen_and_duplicate(catalog):
    repo, document = catalog
    again = repo.save_document(document, [block(document)], b"Material A mobility 0.2 cm2/Vs")
    assert again == document
    reopened = MaterialsRepository(repo.root)
    assert reopened.documents() == [document]
    assert reopened.document(document.id) == document
    assert reopened.blocks(document.id)[0].page == 1
    assert (repo.root / "originals" / f"{document.id}.txt").read_bytes().startswith(b"Material")


def test_conflicting_duplicate_role_does_not_change_source(catalog):
    repo, document = catalog
    duplicate = document.model_copy(update={"role": "supplement", "parent_id": "a" * 64})
    with pytest.raises(AppError) as raised:
        repo.save_document(duplicate, [block(document)], b"Material A mobility 0.2 cm2/Vs")
    assert raised.value.code == "material_document_conflict"
    assert repo.document(document.id).role == "main"


def test_supplement_needs_real_main_document(catalog):
    repo, main = catalog
    data = b"Supplementary measurements"
    supplement = source(data, role="supplement", parent_id=main.id)
    repo.save_document(supplement, [block(supplement, "si-block")], data)
    assert repo.document(supplement.id).parent_id == main.id
    bad = source(b"other", role="supplement", parent_id="b" * 64)
    with pytest.raises(AppError):
        repo.save_document(bad, [block(bad, "other")], b"other")
    assert repo.document(bad.id) is None


def test_failed_database_publish_removes_new_raw_and_catalog_rows(catalog):
    repo, first = catalog
    data = b"Another document"
    second = source(data)
    # A block ID cannot be reused by another source. The database failure occurs
    # after writing the original; neither that original nor its catalog may remain.
    with pytest.raises(AppError):
        repo.save_document(second, [block(second, "block-one")], data)
    assert repo.document(second.id) is None
    assert not (repo.root / "originals" / f"{second.id}.txt").exists()
    assert repo.blocks(first.id)[0].id == "block-one"
    assert not list((repo.root / "originals").glob("*.tmp"))


def test_digest_and_size_mismatch_never_write_original(catalog):
    repo, _ = catalog
    document = source(b"legitimate")
    with pytest.raises(AppError):
        repo.save_document(document, [block(document, "new")], b"tampered")
    assert repo.document(document.id) is None


def test_structured_parse_artifact_is_persisted_and_duplicate_is_immutable(tmp_path):
    repo = MaterialsRepository(tmp_path / "materials")
    data = b"A structured document"
    document = source(data)
    parsed = json.dumps({"schema_name": "DoclingDocument", "texts": [{"text": "A"}]})
    repo.save_document(document, [block(document)], data, parsed_json=parsed)
    assert MaterialsRepository(repo.root).parse_artifact(document.id) == parsed
    repo.save_document(document, [block(document)], data, parsed_json='{"different":true}')
    assert repo.parse_artifact(document.id) == parsed


def test_invalid_parse_artifact_never_publishes_document(tmp_path):
    repo = MaterialsRepository(tmp_path / "materials")
    data = b"Original with invalid artifact"
    document = source(data)
    with pytest.raises(AppError) as raised:
        repo.save_document(document, [block(document)], data, parsed_json="not JSON")
    assert raised.value.code == "invalid_material_parse"
    assert repo.documents() == []
    assert list(repo.originals.iterdir()) == []


def test_record_review_revision_and_history(catalog):
    repo, document = catalog
    first = repo.save_record(candidate(document))
    approved = repo.save_record(
        first.model_copy(update={"status": "approved", "value_raw": "0.25"}),
        expected_revision=first.revision,
    )
    assert approved.status == "approved"
    assert approved.revision == 2
    assert [r["value_raw"] for r in repo.revisions(first.id)] == ["0.2", "0.25"]
    assert repo.records(document.id) == [approved]
    assert repo.records("a" * 64) == []


def test_review_rejects_stale_revision_without_overwrite(catalog):
    repo, document = catalog
    first = repo.save_record(candidate(document))
    repo.save_record(first.model_copy(update={"status": "approved"}), expected_revision=1)
    with pytest.raises(AppError) as raised:
        repo.save_record(first.model_copy(update={"value_raw": "999"}), expected_revision=1)
    assert raised.value.code == "material_revision_conflict"
    assert repo.record(first.id).value_raw == "0.2"
    assert len(repo.revisions(first.id)) == 2


@pytest.mark.parametrize("review_status", ["candidate", "approved", "rejected"])
def test_reextract_never_overwrites_existing_record(catalog, review_status):
    repo, document = catalog
    first = repo.save_record(candidate(document))
    if review_status != "candidate":
        first = repo.save_record(
            first.model_copy(update={"status": review_status}), expected_revision=1
        )
    repeated = repo.save_record(candidate(document).model_copy(update={"value_raw": "999"}))
    assert repeated == first
    assert repo.record(first.id).value_raw == "0.2"


def test_extraction_run_progress_persists(catalog):
    repo, document = catalog
    run = ExtractionRun(
        id="run-one", document_id=document.id, domain="ofet", model="fake-model", batch_total=2
    )
    repo.save_run(run)
    repo.save_run(run.model_copy(update={"status": "failed", "batch_completed": 1,
                                        "record_count": 1, "error_code": "invalid_extraction"}))
    actual = MaterialsRepository(repo.root).runs(document.id)[0]
    assert actual.batch_completed == 1
    assert actual.error_code == "invalid_extraction"
    assert actual.status == "failed"


def test_models_reject_infinity_unsafe_path_and_invalid_bbox(catalog):
    _, document = catalog
    with pytest.raises(ValidationError):
        candidate(document).model_validate({**candidate(document).model_dump(),
                                           "value_normalized": float("inf")})
    with pytest.raises(ValidationError):
        document.model_validate({**document.model_dump(), "extension": ".txt/../../x"})
    with pytest.raises(ValidationError):
        block(document).model_validate({**block(document).model_dump(), "bbox": (0, 0, 2, 1)})
