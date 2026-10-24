import json

import pytest

from rag_workbench.application.knowledge import KnowledgeService
from rag_workbench.application.pack_authoring import PackAuthoring
from rag_workbench.application.pack_evaluation import PackEvaluation, diagnose_report
from rag_workbench.application.pack_review import PackReview
from rag_workbench.application.packs import (
    PackBuilder,
    PackCatalog,
    PackSpec,
    load_pack_settings,
    pack_lint,
)
from rag_workbench.config import Settings
from rag_workbench.domain.errors import AppError

from .fakes import FakeEmbedder, FakeProvider


class ScriptedProvider:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def complete(self, system, user, *, json_mode=False, history=None):
        self.calls.append((system, user, json_mode))
        return self.response

    def stream(self, system, user, *, json_mode=False, history=None, cancellation=None):
        yield self.complete(system, user, json_mode=json_mode, history=history)


def settings_for(tmp_path):
    return Settings(
        _env_file=None,
        data_dir=tmp_path / "active",
        packs_dir=tmp_path / "packs",
        packs_data_dir=tmp_path / "data",
        chunk_size=100,
        chunk_overlap=10,
    )


def build_pack(tmp_path, *, pack_id="studio-pack", text="保管期限为三年。"):
    settings = settings_for(tmp_path)
    result = PackBuilder(settings, FakeEmbedder()).build(
        PackSpec(
            pack_id=pack_id,
            title="工作室测试包",
            license_status="internal_only",
            chunk_size=100,
            chunk_overlap=10,
            similarity_threshold=0.5,
        ),
        [("a.txt", text.encode())],
    )
    return settings, result


def read_manifest(pack_dir):
    return json.loads((pack_dir / "pack.json").read_text(encoding="utf-8"))


def write_manifest(pack_dir, manifest):
    (pack_dir / "pack.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def test_new_pack_tracks_draft_profile_and_empty_eval_state(tmp_path):
    settings, result = build_pack(tmp_path)
    manifest = read_manifest(result.pack_dir)
    assert manifest["profile_status"] == "draft"
    assert manifest["eval_status"] == "none"
    assert manifest["holdout_status"] == "unused"
    assert manifest["eval_freeze_version"] is None

    summary = PackCatalog(settings).get("studio-pack")
    assert summary.profile_status == "draft"
    assert summary.eval_status == "none"
    assert summary.holdout_status == "unused"


PROFILE_DRAFT = (
    "你是本知识包的问答助手。\n\n"
    "1. 只依据 evidence 中直接支持的证据回答，不补写资料外的常识。\n"
    "2. 证据不足时明确说明不足并拒答，不猜测。\n"
    "3. 引用由系统根据真实 chunk 追加，不自行编造来源。\n"
    "4. 事实题先给直接结论，再给必要依据。\n"
) * 10


def test_profile_draft_requires_cost_confirmation_and_does_not_replace_current(tmp_path):
    settings, result = build_pack(tmp_path)
    pack_settings = load_pack_settings("studio-pack", base=settings)
    knowledge = KnowledgeService(pack_settings, FakeEmbedder())
    authoring = PackAuthoring(pack_settings, knowledge, ScriptedProvider(PROFILE_DRAFT))
    before = (result.pack_dir / "profile" / "profile.md").read_bytes()

    with pytest.raises(AppError) as caught:
        authoring.draft_profile(result.pack_dir, confirm_cost=False)
    assert caught.value.code == "cost_confirmation_required"
    assert not (result.pack_dir / "profile" / "profile.draft.md").exists()

    draft = authoring.draft_profile(result.pack_dir, confirm_cost=True)
    assert "证据" in draft.content and "不足" in draft.content
    draft_path = result.pack_dir / "profile" / "profile.draft.md"
    assert draft_path.read_text(encoding="utf-8") == draft.content
    meta = json.loads(
        (result.pack_dir / "profile" / "profile.draft.meta.json").read_text(encoding="utf-8")
    )
    assert meta["status"] == "draft"
    assert meta["prompt_version"]
    assert len(meta["input_sha256"]) == 64
    assert meta["source_chunk_ids"]
    assert (result.pack_dir / "profile" / "profile.md").read_bytes() == before
    assert read_manifest(result.pack_dir)["profile_status"] == "draft"


def test_generate_candidates_keeps_only_valid_anchors_and_checks_reachability(tmp_path):
    settings, result = build_pack(tmp_path)
    pack_settings = load_pack_settings("studio-pack", base=settings)
    knowledge = KnowledgeService(pack_settings, FakeEmbedder())
    chunk_id = knowledge.repo.chunks()[0].id
    response = json.dumps(
        {
            "cases": [
                {
                    "question": "保管期限是多久？",
                    "expected_mode": "knowledge_base",
                    "relevant_chunk_ids": [chunk_id],
                    "reference_answer": "三年。",
                    "category": "fact",
                },
                {
                    "question": "火星有几颗卫星？",
                    "expected_mode": "insufficient_evidence",
                    "relevant_chunk_ids": [],
                    "reference_answer": "",
                    "category": "negative",
                },
                {
                    "question": "错误锚点？",
                    "expected_mode": "knowledge_base",
                    "relevant_chunk_ids": ["not-a-real-chunk"],
                    "reference_answer": "x",
                    "category": "fact",
                },
            ]
        },
        ensure_ascii=False,
    )
    authoring = PackAuthoring(pack_settings, knowledge, ScriptedProvider(response))
    with pytest.raises(AppError) as caught:
        authoring.generate_candidates(result.pack_dir, count=3, confirm_cost=False)
    assert caught.value.code == "cost_confirmation_required"

    candidates = authoring.generate_candidates(
        result.pack_dir, count=3, confirm_cost=True
    )
    assert [candidate.id for candidate in candidates] == ["cand_001", "cand_002"]
    positive, negative = candidates
    assert positive.reachable is True
    assert positive.relevant_document_ids
    assert negative.reachable is True
    assert negative.negative_risk is False
    stored = [
        json.loads(line)
        for line in (result.pack_dir / "eval" / "candidates.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    assert len(stored) == 2
    assert read_manifest(result.pack_dir)["eval_status"] == "candidate"


def test_invalid_llm_json_is_reported_without_writing_candidates(tmp_path):
    settings, result = build_pack(tmp_path)
    pack_settings = load_pack_settings("studio-pack", base=settings)
    authoring = PackAuthoring(
        pack_settings,
        KnowledgeService(pack_settings, FakeEmbedder()),
        ScriptedProvider("not json"),
    )
    with pytest.raises(AppError) as caught:
        authoring.generate_candidates(result.pack_dir, confirm_cost=True)
    assert caught.value.code == "invalid_llm_output"
    assert not (result.pack_dir / "eval" / "candidates.jsonl").exists()


def candidate_response(chunk_id, *, negative_question="火星有几颗卫星？"):
    return json.dumps(
        {
            "cases": [
                {
                    "question": "保管期限是多久？",
                    "expected_mode": "knowledge_base",
                    "relevant_chunk_ids": [chunk_id],
                    "reference_answer": "三年。",
                    "category": "fact",
                },
                {
                    "question": negative_question,
                    "expected_mode": "insufficient_evidence",
                    "relevant_chunk_ids": [],
                    "reference_answer": "",
                    "category": "negative",
                },
            ]
        },
        ensure_ascii=False,
    )


def prepare_reviewable_candidates(tmp_path, *, negative_question="火星有几颗卫星？"):
    settings, result = build_pack(tmp_path)
    pack_settings = load_pack_settings("studio-pack", base=settings)
    knowledge = KnowledgeService(pack_settings, FakeEmbedder())
    chunk_id = knowledge.repo.chunks()[0].id
    authoring = PackAuthoring(
        pack_settings,
        knowledge,
        ScriptedProvider(candidate_response(chunk_id, negative_question=negative_question)),
    )
    candidates = authoring.generate_candidates(result.pack_dir, count=2, confirm_cost=True)
    return settings, result, pack_settings, knowledge, candidates


def test_adopt_profile_freeze_eval_and_lint_frozen_pack(tmp_path):
    _settings, result, _pack_settings, knowledge, candidates = prepare_reviewable_candidates(
        tmp_path
    )
    profile = PackReview.adopt_profile(
        result.pack_dir, content=PROFILE_DRAFT, source="manual"
    )
    assert profile["status"] == "accepted"
    assert (result.pack_dir / "profile" / "profile.meta.json").is_file()
    assert list((result.pack_dir / "profile" / "history").glob("profile-*.md"))
    manifest = read_manifest(result.pack_dir)
    assert manifest["profile_status"] == "reviewed"
    assert manifest["status"] == "reviewed"

    PackReview.save_review(
        result.pack_dir,
        {
            candidates[0].id: {"status": "approved", "split": "dev"},
            candidates[1].id: {"status": "approved", "split": "holdout"},
        },
    )
    freeze = PackReview.freeze_eval(result.pack_dir, knowledge)
    assert freeze.version == "f001"
    assert freeze.holdout_status == "unused"
    assert freeze.labels["dev"].cases == 1
    assert freeze.labels["holdout"].cases == 1
    assert freeze.labels["dev"].sha256

    manifest = read_manifest(result.pack_dir)
    assert manifest["eval_status"] == "frozen"
    assert manifest["status"] == "frozen"
    assert manifest["eval_freeze_version"] == "f001"
    assert manifest["holdout_status"] == "unused"

    dev_rows = [
        json.loads(line)
        for line in (result.pack_dir / freeze.labels["dev"].path)
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    assert dev_rows == [
        {
            "id": "q001",
            "question": candidates[0].question,
            "relevant_document_ids": candidates[0].relevant_document_ids,
            "expected_mode": "knowledge_base",
        }
    ]
    holdout_rows = [
        json.loads(line)
        for line in (result.pack_dir / freeze.labels["holdout"].path)
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    assert holdout_rows[0]["expected_mode"] == "insufficient_evidence"
    assert holdout_rows[0]["relevant_document_ids"] == []

    lint = pack_lint(result.pack_dir, result.data_dir)
    assert not lint.errors


def test_freeze_requires_reviewed_profile_and_blocks_negative_risk(tmp_path):
    _settings, result, _pack_settings, knowledge, candidates = prepare_reviewable_candidates(
        tmp_path
    )
    PackReview.save_review(
        result.pack_dir,
        {
            candidates[0].id: {"status": "approved", "split": "dev"},
            candidates[1].id: {"status": "approved", "split": "holdout"},
        },
    )
    with pytest.raises(AppError) as caught:
        PackReview.freeze_eval(result.pack_dir, knowledge)
    assert caught.value.code == "profile_not_reviewed"

    PackReview.adopt_profile(result.pack_dir, content=PROFILE_DRAFT, source="manual")
    _settings2, result2, _pack_settings2, knowledge2, candidates2 = prepare_reviewable_candidates(
        tmp_path / "risky", negative_question="保管期限是三十年吗？"
    )
    PackReview.adopt_profile(result2.pack_dir, content=PROFILE_DRAFT, source="manual")
    assert candidates2[1].negative_risk is True
    PackReview.save_review(
        result2.pack_dir,
        {
            candidates2[0].id: {"status": "approved", "split": "dev"},
            candidates2[1].id: {"status": "approved", "split": "holdout"},
        },
    )
    with pytest.raises(AppError) as caught:
        PackReview.freeze_eval(result2.pack_dir, knowledge2)
    assert caught.value.code == "negative_risk"


def test_adopt_profile_rejects_missing_required_terms(tmp_path):
    _settings, result = build_pack(tmp_path)
    with pytest.raises(AppError) as caught:
        PackReview.adopt_profile(result.pack_dir, content="太短，也没有关键约束。", source="manual")
    assert caught.value.code == "invalid_profile"


def test_corpus_change_after_freeze_blocks_lint_and_evaluation(tmp_path):
    _settings, result, pack_settings, knowledge, _candidates, _freeze = prepare_frozen(
        tmp_path, positive_split="dev", negative_split="holdout"
    )
    with (result.pack_dir / "generated_manifest.jsonl").open("a", encoding="utf-8") as stream:
        stream.write("\n")
    lint = pack_lint(result.pack_dir, result.data_dir)
    assert any("需要重新冻结" in check.message for check in lint.checks if check.status == "fail")
    evaluator = PackEvaluation(pack_settings, knowledge, FakeProvider(), result.pack_dir)
    with pytest.raises(AppError) as caught:
        evaluator.run(split="dev", online=False)
    assert caught.value.code == "stale_freeze"


def test_profile_change_after_freeze_blocks_lint_and_evaluation(tmp_path):
    _settings, result, pack_settings, knowledge, _candidates, _freeze = prepare_frozen(
        tmp_path, positive_split="dev", negative_split="holdout"
    )
    PackReview.adopt_profile(
        result.pack_dir,
        content=PROFILE_DRAFT + "\n5. 这是冻结后新增的规则。\n",
        source="manual",
    )
    lint = pack_lint(result.pack_dir, result.data_dir)
    assert any("重新冻结" in check.message for check in lint.checks if check.status == "fail")
    evaluator = PackEvaluation(pack_settings, knowledge, FakeProvider(), result.pack_dir)
    with pytest.raises(AppError) as caught:
        evaluator.run(split="dev", online=False)
    assert caught.value.code == "stale_freeze"


def test_mark_holdout_used_is_recorded_and_linted(tmp_path):
    _settings, result, _pack_settings, knowledge, candidates = prepare_reviewable_candidates(
        tmp_path
    )
    PackReview.adopt_profile(result.pack_dir, content=PROFILE_DRAFT, source="manual")
    PackReview.save_review(
        result.pack_dir,
        {
            candidates[0].id: {"status": "approved", "split": "dev"},
            candidates[1].id: {"status": "approved", "split": "holdout"},
        },
    )
    freeze = PackReview.freeze_eval(result.pack_dir, knowledge)
    PackReview.mark_holdout_used(result.pack_dir, freeze.version, run_id="run-001")
    current = PackReview.read_current_freeze(result.pack_dir)
    assert current.holdout_status == "used"
    assert current.holdout_used_at
    assert read_manifest(result.pack_dir)["holdout_status"] == "used"
    assert not pack_lint(result.pack_dir, result.data_dir).errors


def prepare_frozen(
    tmp_path,
    *,
    positive_split="dev",
    negative_split="holdout",
):
    settings, result, pack_settings, knowledge, candidates = prepare_reviewable_candidates(
        tmp_path
    )
    PackReview.adopt_profile(result.pack_dir, content=PROFILE_DRAFT, source="manual")
    PackReview.save_review(
        result.pack_dir,
        {
            candidates[0].id: {"status": "approved", "split": positive_split},
            candidates[1].id: {"status": "approved", "split": negative_split},
        },
    )
    freeze = PackReview.freeze_eval(result.pack_dir, knowledge)
    return settings, result, pack_settings, knowledge, candidates, freeze


def test_offline_dev_eval_writes_report_and_holdout_lock(tmp_path):
    _settings, result, pack_settings, knowledge, _candidates, freeze = prepare_frozen(tmp_path)
    evaluator = PackEvaluation(pack_settings, knowledge, FakeProvider(), result.pack_dir)
    with pytest.raises(AppError) as caught:
        evaluator.run(split="holdout", online=False)
    assert caught.value.code == "holdout_confirmation_required"

    run = evaluator.run(split="dev", online=False)
    assert run.split == "dev"
    assert run.metrics["retrieval_hit_rate"] == 1.0
    assert run.report_path.is_file()
    assert (run.report_path.parent / "diagnosis.json").is_file()
    assert read_manifest(result.pack_dir)["holdout_status"] == "unused"

    holdout = evaluator.run(split="holdout", online=False, confirm_holdout=True)
    assert holdout.split == "holdout"
    assert read_manifest(result.pack_dir)["holdout_status"] == "used"
    with pytest.raises(AppError) as caught:
        evaluator.run(split="holdout", online=False, confirm_holdout=True)
    assert caught.value.code == "holdout_used"


def test_online_holdout_requires_cost_confirmation_and_marks_used(tmp_path):
    _settings, result, pack_settings, knowledge, _candidates, _freeze = prepare_frozen(
        tmp_path, positive_split="holdout", negative_split="dev"
    )
    evaluator = PackEvaluation(pack_settings, knowledge, FakeProvider(), result.pack_dir)
    with pytest.raises(AppError) as caught:
        evaluator.run(split="holdout", online=True, confirm_holdout=True)
    assert caught.value.code == "cost_confirmation_required"

    run = evaluator.run(
        split="holdout",
        online=True,
        confirm_cost=True,
        confirm_holdout=True,
    )
    assert run.metrics["routing_accuracy"] == 1.0
    assert run.metrics["citation_validity"] == 1.0
    assert run.diagnosis_counts.get("answer_review") == 1
    assert read_manifest(result.pack_dir)["holdout_status"] == "used"
    with pytest.raises(AppError) as caught:
        evaluator.run(
            split="holdout",
            online=True,
            confirm_cost=True,
            confirm_holdout=True,
        )
    assert caught.value.code == "holdout_used"


def test_diagnose_report_classifies_failures_without_fake_answer_verdicts():
    report = {
        "results": [
            {
                "id": "q001",
                "expected_mode": "knowledge_base",
                "mode": None,
                "error_code": None,
                "relevant_document_ids": ["doc"],
                "retrieved_document_ids": [],
                "eligible_document_ids": [],
                "citation_valid": None,
            },
            {
                "id": "q002",
                "expected_mode": "knowledge_base",
                "mode": None,
                "error_code": None,
                "relevant_document_ids": ["doc"],
                "retrieved_document_ids": ["doc"],
                "eligible_document_ids": [],
                "citation_valid": None,
            },
            {
                "id": "q003",
                "expected_mode": "knowledge_base",
                "mode": "insufficient_evidence",
                "error_code": None,
                "relevant_document_ids": ["doc"],
                "retrieved_document_ids": ["doc"],
                "eligible_document_ids": ["doc"],
                "citation_valid": None,
            },
            {
                "id": "q004",
                "expected_mode": "knowledge_base",
                "mode": "knowledge_base",
                "error_code": None,
                "relevant_document_ids": ["doc"],
                "retrieved_document_ids": ["doc"],
                "eligible_document_ids": ["doc"],
                "citation_valid": False,
            },
            {
                "id": "q005",
                "expected_mode": "insufficient_evidence",
                "mode": "knowledge_base",
                "error_code": None,
                "relevant_document_ids": [],
                "retrieved_document_ids": ["doc"],
                "eligible_document_ids": ["doc"],
                "citation_valid": True,
            },
            {
                "id": "q006",
                "expected_mode": "knowledge_base",
                "mode": "error",
                "error_code": "timeout",
                "relevant_document_ids": ["doc"],
                "retrieved_document_ids": [],
                "eligible_document_ids": [],
                "citation_valid": None,
            },
            {
                "id": "q007",
                "expected_mode": "knowledge_base",
                "mode": "knowledge_base",
                "error_code": None,
                "relevant_document_ids": ["doc"],
                "retrieved_document_ids": ["doc"],
                "eligible_document_ids": ["doc"],
                "citation_valid": True,
            },
        ]
    }
    diagnosis = diagnose_report(report, online=True)
    counts = diagnosis["counts"]
    assert counts["retrieval_miss"] == 1
    assert counts["threshold_miss"] == 1
    assert counts["routing_error"] == 1
    assert counts["citation_error"] == 1
    assert counts["negative_leak"] == 1
    assert counts["service_error"] == 1
    assert counts["answer_review"] == 1
    assert diagnosis["recommendations"]


def test_lint_rejects_frozen_manifest_without_reviewed_artifacts(tmp_path):
    _settings, result = build_pack(tmp_path)
    manifest = read_manifest(result.pack_dir)
    manifest["status"] = "frozen"
    write_manifest(result.pack_dir, manifest)
    lint = pack_lint(result.pack_dir, result.data_dir)
    assert lint.errors
    assert any("冻结" in check.message or "frozen" in check.id for check in lint.checks
               if check.status == "fail")
