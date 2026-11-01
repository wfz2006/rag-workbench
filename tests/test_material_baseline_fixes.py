"""Pack evaluation accounting and selected-model regressions; no real API calls."""

import json
from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from rag_workbench.application.pack_evaluation import PackEvaluation, diagnose_report
from rag_workbench.application.pack_review import PackReview
from rag_workbench.application.packs import load_pack_settings
from rag_workbench.infrastructure.llm import OpenAIChatProvider
from rag_workbench.ui import pack_studio

from .fakes import FakeProvider
from .test_pack_studio import prepare_frozen


class BadCitation(FakeProvider):
    def complete(self, system, user, *, json_mode=False, history=None):
        if json_mode and "supporting_chunk_ids" not in system:
            self.calls.append((system, user, history))
            return json.dumps({"claims": [{"text": "保管期限为三年。", "chunk_ids": ["f" * 64]}]})
        return super().complete(system, user, json_mode=json_mode, history=history)


def test_invalid_citation_stays_consumed_and_gets_citation_diagnosis(tmp_path):
    _, result, config, knowledge, _, _ = prepare_frozen(
        tmp_path, positive_split="holdout", negative_split="dev"
    )
    provider = BadCitation()
    run = PackEvaluation(config, knowledge, provider, result.pack_dir).run(
        split="holdout", online=True, confirm_cost=True, confirm_holdout=True
    )
    assert len(provider.calls) == 2
    assert run.metrics["service_errors"] == 0
    assert run.metrics["evaluation_errors"] == 1
    assert run.diagnosis_counts["citation_error"] == 1
    assert PackReview.read_current_freeze(result.pack_dir).holdout_status == "used"
    payload = json.loads(run.report_path.read_text(encoding="utf-8"))
    assert payload["holdout_consumed"] is True


@pytest.mark.parametrize(
    "code,expected_category,service_failure",
    [
        ("timeout", "service_error", True),
        ("invalid_answer", "output_error", False),
        ("invalid_evidence", "output_error", False),
        ("incomplete_response", "output_error", False),
        ("future_unknown_error", "evaluation_error", False),
    ],
)
def test_only_service_failures_release_holdout(tmp_path, code, expected_category, service_failure):
    _, result, config, knowledge, _, _ = prepare_frozen(
        tmp_path, positive_split="holdout", negative_split="dev"
    )
    run = PackEvaluation(config, knowledge, FakeProvider(failure=code), result.pack_dir).run(
        split="holdout", online=True, confirm_cost=True, confirm_holdout=True
    )
    assert run.metrics["service_errors"] == int(service_failure)
    assert run.metrics["evaluation_errors"] == 1
    assert run.diagnosis_counts[expected_category] == 1
    expected = "unused" if service_failure else "used"
    assert PackReview.read_current_freeze(result.pack_dir).holdout_status == expected


def test_citation_error_classification_does_not_depend_on_retrieved_candidates():
    report = {"results": [{"id": "case", "error_code": "invalid_citation"}]}
    assert diagnose_report(report, online=True)["counts"]["citation_error"] == 1


def test_inactive_pack_ui_requests_and_reports_selected_model(tmp_path, monkeypatch):
    base, result, _, knowledge, _, _ = prepare_frozen(tmp_path)
    path = result.pack_dir / "settings.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["llm_model"] = "selected-pack-model"
    path.write_text(json.dumps(payload), encoding="utf-8")
    config = load_pack_settings("studio-pack", base=base)
    config.api_key = SecretStr("test-selected-secret")
    PackReview.freeze_eval(result.pack_dir, knowledge)
    actual_requests = []
    fake = FakeProvider()

    def complete(**arguments):
        actual_requests.append(arguments["model"])
        system, user = arguments["messages"][-2:]
        text = fake.complete(system["content"], user["content"], json_mode=True)
        return SimpleNamespace(choices=[SimpleNamespace(
            finish_reason="stop", message=SimpleNamespace(content=text),
        )])

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=complete)))
    active_settings = config.model_copy(update={"llm_model": "active-workspace-model"})
    active = OpenAIChatProvider(active_settings, client=client)
    services = SimpleNamespace(settings=active_settings, pipeline=SimpleNamespace(provider=active))
    entry = SimpleNamespace(pack_dir=result.pack_dir, pack_id="studio-pack")
    state = {}
    monkeypatch.setattr(pack_studio.st, "session_state", state)
    monkeypatch.setattr(pack_studio.st, "spinner", lambda *a, **k: nullcontext())
    monkeypatch.setattr(pack_studio.st, "rerun", lambda: None)
    monkeypatch.setattr(
        pack_studio, "OpenAIChatProvider", lambda settings: OpenAIChatProvider(settings, client),
        raising=False,
    )
    if hasattr(pack_studio, "_cached_provider"):
        pack_studio._cached_provider.clear()
    pack_studio._run(
        services, entry, config, knowledge,
        split="dev", online=True, confirm_cost=True, confirm_holdout=False,
    )
    selected = state["pending_report_run_studio-pack"]
    report = json.loads(
        (result.pack_dir / "eval/runs" / selected / "report.json").read_text(encoding="utf-8")
    )
    assert report["model"] == "selected-pack-model"
    assert actual_requests == ["selected-pack-model", "selected-pack-model"]
    assert "test-selected-secret" not in json.dumps(report)
    if hasattr(pack_studio, "_cached_provider"):
        assert pack_studio._provider(services, config) is pack_studio._provider(services, config)
        pack_studio._cached_provider.clear()


def test_provider_reuses_matching_active_injected_provider(tmp_path):
    base, _, _, _, _, _ = prepare_frozen(tmp_path)
    provider = FakeProvider()
    services = SimpleNamespace(settings=base, pipeline=SimpleNamespace(provider=provider))
    assert pack_studio._provider(services, base) is provider
