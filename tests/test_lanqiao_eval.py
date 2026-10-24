import csv
import hashlib
import json

import pytest

from rag_workbench.application.evaluation import EvalCase
from scripts.materialize_lanqiao_eval import materialize_cases


def make_inputs(tmp_path):
    corpus = tmp_path / "corpus"
    (corpus / "lessons").mkdir(parents=True)
    document = corpus / "lessons/stack_栈_p002.md"
    document.write_text("栈的第二页", encoding="utf-8")
    manifest = tmp_path / "manifest.csv"
    with manifest.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["source_id", "status", "prepared_path", "title"])
        writer.writeheader()
        writer.writerow(
            dict(source_id="stack", status="selected", prepared_path="lessons", title="栈")
        )
    labels, output = tmp_path / "labels.jsonl", tmp_path / "runtime.jsonl"
    return corpus, manifest, labels, output, document


def test_maps_exact_uploaded_bytes_and_strict_eval_schema(tmp_path):
    corpus, manifest, labels, output, doc = make_inputs(tmp_path)
    labels.write_text(
        json.dumps(
            dict(
                id="q1",
                question="什么是栈？",
                relevant_source_ids=["stack@p002"],
                expected_mode="knowledge_base",
                reference_facts="只用于人工评分",
            )
        ),
        encoding="utf-8",
    )
    assert materialize_cases(labels, manifest, corpus, output) == 1
    case = EvalCase.model_validate_json(output.read_text(encoding="utf-8"))
    assert case.relevant_document_ids == [hashlib.sha256(doc.read_bytes()).hexdigest()]
    assert "reference_facts" not in output.read_text(encoding="utf-8")


@pytest.mark.parametrize("ids", [["unknown"], ["stack@p999"]])
def test_unknown_or_missing_document_is_not_silently_ignored(tmp_path, ids):
    corpus, manifest, labels, output, _ = make_inputs(tmp_path)
    labels.write_text(
        json.dumps(
            dict(id="q", question="问题", relevant_source_ids=ids, expected_mode="knowledge_base")
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        materialize_cases(labels, manifest, corpus, output)
    assert not output.exists()


def test_duplicate_ids_and_empty_dataset_rejected(tmp_path):
    corpus, manifest, labels, output, _ = make_inputs(tmp_path)
    row = json.dumps(
        dict(
            id="q", question="未覆盖", relevant_source_ids=[], expected_mode="insufficient_evidence"
        )
    )
    for content in ("", row + "\n" + row):
        labels.write_text(content, encoding="utf-8")
        with pytest.raises(ValueError):
            materialize_cases(labels, manifest, corpus, output)


def test_knowledge_case_requires_labelled_evidence(tmp_path):
    corpus, manifest, labels, output, _ = make_inputs(tmp_path)
    labels.write_text(
        json.dumps(
            dict(id="q", question="问题", relevant_source_ids=[], expected_mode="knowledge_base")
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        materialize_cases(labels, manifest, corpus, output)


@pytest.mark.parametrize("target", ["labels", "manifest", "document"])
def test_runtime_output_cannot_overwrite_inputs(tmp_path, target):
    corpus, manifest, labels, _, doc = make_inputs(tmp_path)
    labels.write_text(
        json.dumps(
            dict(
                id="q",
                question="栈？",
                relevant_source_ids=["stack"],
                expected_mode="knowledge_base",
            )
        ),
        encoding="utf-8",
    )
    output = {"labels": labels, "manifest": manifest, "document": doc}[target]
    before = output.read_bytes()
    with pytest.raises(ValueError, match="output"):
        materialize_cases(labels, manifest, corpus, output)
    assert output.read_bytes() == before
