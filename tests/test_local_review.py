"""Local development audit; never updates the archived public release."""

import json

import pytest
from filelock import FileLock

from rag_workbench.application import pack_evaluation
from rag_workbench.application.pack_evaluation import PackEvaluation
from rag_workbench.application.pack_review import PackReview
from rag_workbench.domain.errors import AppError

from .fakes import FakeEmbedder, FakeProvider
from .test_pack_studio import PROFILE_DRAFT, prepare_frozen, prepare_reviewable_candidates


def approved(tmp_path):
    settings, result, config, knowledge, candidates = prepare_reviewable_candidates(tmp_path)
    PackReview.adopt_profile(result.pack_dir, content=PROFILE_DRAFT)
    PackReview.save_review(
        result.pack_dir,
        {
            candidates[0].id: {"status": "approved", "split": "dev"},
            candidates[1].id: {"status": "approved", "split": "holdout"},
        },
    )
    return settings, result, config, knowledge, candidates


@pytest.mark.parametrize(
    "index,question,code",
    [
        (0, "火星是什么颜色？", "invalid_approved_cases"),
        (1, "保管期限是多少？", "negative_risk"),
    ],
)
def test_freeze_rechecks_manually_edited_questions(tmp_path, index, question, code):
    _, result, _, knowledge, candidates = approved(tmp_path)
    PackReview.save_review(result.pack_dir, {candidates[index].id: {"question": question}})
    with pytest.raises(AppError) as caught:
        PackReview.freeze_eval(result.pack_dir, knowledge)
    assert caught.value.code == code
    assert not (result.pack_dir / "eval/current.json").exists()


def test_freeze_requires_profile_to_match_human_review_hash(tmp_path):
    _, result, _, knowledge, _ = approved(tmp_path)
    profile = result.pack_dir / "profile/profile.md"
    profile.write_text(PROFILE_DRAFT + "未经审核的外部修改。", encoding="utf-8")
    with pytest.raises(AppError) as caught:
        PackReview.freeze_eval(result.pack_dir, knowledge)
    assert caught.value.code == "stale_profile"


def test_holdout_stays_consumed_if_report_write_fails(tmp_path, monkeypatch):
    _, result, config, knowledge, _, _ = prepare_frozen(tmp_path)

    def fail(*args):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(pack_evaluation, "_write_json", fail)
    with pytest.raises(AppError) as caught:
        PackEvaluation(config, knowledge, FakeProvider(), result.pack_dir).run(
            split="holdout", online=False, confirm_holdout=True
        )
    assert caught.value.code == "evaluation_io_error"
    assert PackReview.read_current_freeze(result.pack_dir).holdout_status == "used"


def test_holdout_and_freeze_respect_pack_operation_lock(tmp_path):
    _, result, config, knowledge, _, _ = prepare_frozen(tmp_path)
    with FileLock(result.pack_dir / ".review.lock"):
        with pytest.raises(AppError) as caught:
            PackEvaluation(config, knowledge, FakeProvider(), result.pack_dir).run(
                split="holdout", online=False, confirm_holdout=True
            )
        assert caught.value.code == "pack_busy"
        with pytest.raises(AppError) as caught:
            PackReview.freeze_eval(result.pack_dir, knowledge)
        assert caught.value.code == "pack_busy"


def test_switching_pack_clears_unsent_question(monkeypatch):
    from rag_workbench.ui import shell

    state = {"active_pack": "old", "question": "旧包私有草稿", "answer_jobs": {}}
    monkeypatch.setattr(shell.st, "session_state", state)
    monkeypatch.setattr(shell.st, "rerun", lambda: None)
    shell.activate_pack("new")
    assert state["question"] == ""


def test_inactive_pack_knowledge_service_is_reused(tmp_path, monkeypatch):
    from rag_workbench.bootstrap import build_services
    from rag_workbench.ui import pack_studio

    settings, _, config, _, _ = prepare_reviewable_candidates(tmp_path)
    services = build_services(settings, embedder=FakeEmbedder(), provider=FakeProvider())
    monkeypatch.setattr(pack_studio, "BgeEmbedder", lambda _: FakeEmbedder())
    assert pack_studio._knowledge(services, config) is pack_studio._knowledge(services, config)


def test_corrupt_freeze_is_reported_not_presented_as_missing(tmp_path):
    from rag_workbench.ui.pack_studio import _load_freeze

    _, result, _, _, _, _ = prepare_frozen(tmp_path)
    (result.pack_dir / "eval/current.json").write_text("not-json", encoding="utf-8")
    with pytest.raises(AppError) as caught:
        _load_freeze(result.pack_dir)
    assert caught.value.code == "invalid_freeze"


def test_profile_edits_invalidate_service_cache_only_for_affected_pack(tmp_path):
    from rag_workbench.bootstrap import service_cache_key

    _, result, config, _, _ = approved(tmp_path)
    before = service_cache_key(config)
    PackReview.adopt_profile(result.pack_dir, content=PROFILE_DRAFT + "\n补充审核约束。")
    assert service_cache_key(config) != before
    # Irrelevant documents do not force a new embedding model instance.
    unchanged = service_cache_key(config)
    (result.pack_dir / "README.md").write_text("说明文本。", encoding="utf-8")
    assert service_cache_key(config) == unchanged


def test_holdout_is_reserved_before_evaluator_and_blocks_competing_writer(tmp_path, monkeypatch):
    _, result, config, knowledge, _, _ = prepare_frozen(tmp_path)
    original = pack_evaluation.evaluate
    observed = []

    def inspect_reservation(*args, **kwargs):
        freeze = PackReview.read_current_freeze(result.pack_dir)
        observed.append(freeze.holdout_status)
        with pytest.raises(AppError) as caught:
            PackReview.adopt_profile(result.pack_dir, content=PROFILE_DRAFT)
        assert caught.value.code == "pack_busy"
        return original(*args, **kwargs)

    monkeypatch.setattr(pack_evaluation, "evaluate", inspect_reservation)
    PackEvaluation(config, knowledge, FakeProvider(), result.pack_dir).run(
        split="holdout", online=False, confirm_holdout=True
    )
    assert observed == ["used"]


@pytest.mark.parametrize("damage", ["shape", "version"])
def test_corrupt_freeze_shape_is_an_app_error(tmp_path, damage):
    _, result, _, _, _, _ = prepare_frozen(tmp_path)
    current = result.pack_dir / "eval/current.json"
    if damage == "shape":
        current.write_text("[]", encoding="utf-8")
    else:
        freeze = result.pack_dir / "eval/freezes/f001/freeze.json"
        payload = json.loads(freeze.read_text(encoding="utf-8"))
        payload["version"] = "f002"
        freeze.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(AppError) as caught:
        PackReview.read_current_freeze(result.pack_dir)
    assert caught.value.code == "invalid_freeze"


def test_atomic_metadata_write_failure_preserves_previous_record(tmp_path, monkeypatch):
    from rag_workbench.application import pack_state

    record = tmp_path / "freeze.json"
    record.write_text('{"version":"f001"}', encoding="utf-8")

    def fail_replace(*args):
        raise OSError("simulated interrupted publication")

    monkeypatch.setattr(pack_state.os, "replace", fail_replace)
    with pytest.raises(OSError):
        pack_state.write_json_atomic(record, {"version": "f002"})
    assert json.loads(record.read_text(encoding="utf-8")) == {"version": "f001"}
    assert not list(tmp_path.glob("*.tmp"))


def test_pack_switch_keeps_deployment_catalog_roots(tmp_path):
    from rag_workbench.application.packs import PackCatalog, load_pack_settings

    from .test_pack_studio import build_pack

    base, _ = build_pack(tmp_path)
    selected = load_pack_settings("studio-pack", base=base)
    assert selected.packs_dir.resolve() == base.packs_dir.resolve()
    assert selected.packs_data_dir.resolve() == base.packs_data_dir.resolve()
    assert PackCatalog(selected).get("studio-pack").has_data
