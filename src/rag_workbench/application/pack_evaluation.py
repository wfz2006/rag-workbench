"""Run frozen pack evaluations and produce a rule-based failure diagnosis."""

import hashlib
import json
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from rag_workbench.application.evaluation import EvalCase, evaluate
from rag_workbench.application.knowledge import KnowledgeService
from rag_workbench.application.pack_review import PackReview
from rag_workbench.application.packs import read_manifest
from rag_workbench.application.pipeline import RagPipeline
from rag_workbench.application.teaching import load_domain_profile, load_teaching_profile
from rag_workbench.config import Settings
from rag_workbench.domain.errors import AppError
from rag_workbench.domain.models import Contract
from rag_workbench.domain.ports import ChatProvider

DIAGNOSIS_CATEGORIES = (
    "service_error",
    "retrieval_miss",
    "threshold_miss",
    "routing_error",
    "negative_leak",
    "citation_error",
    "answer_review",
    "clean",
)
RECOMMENDATIONS = {
    "service_error": "检查 API Key、余额、网络与超时；服务错误不能当作知识库未命中。",
    "retrieval_miss": "检查分块、查询扩展与 embedding；目标文档未进入候选。",
    "threshold_miss": "目标文档已召回但未过门槛；检查阈值/词法覆盖，或用调试页查看候选。",
    "routing_error": "证据已召回但路由不符；检查证据判断提示、profile 与证据门槛。",
    "negative_leak": "负例被当成可回答；检查阈值、负例题质量或证据判断。",
    "citation_error": "回答引用为空、无效或不在本次候选中；检查引用校验与生成输出。",
    "answer_review": "路由与引用结构通过，但事实正确性必须人工逐条核对。",
    "clean": "未发现规则化失败；仍需人工核对答案事实。",
}
LIMITATIONS = (
    "该报告只做检索/路由/引用结构与规则化诊断；答案事实正确性必须人工逐条核对。"
    "离线评测不覆盖路由和生成；holdout 运行后会消耗一次性留出资格。"
)


class EvalRun(Contract):
    run_id: str
    pack_id: str
    freeze_version: str
    split: Literal["dev", "holdout"]
    online: bool
    created_at: str
    duration_seconds: float
    metrics: dict
    diagnosis_counts: dict[str, int]
    report_path: Path


class PackEvaluation:
    def __init__(
        self,
        settings: Settings,
        knowledge: KnowledgeService,
        provider: ChatProvider,
        pack_dir: Path,
    ):
        self.settings = settings
        self.knowledge = knowledge
        self.provider = provider
        self.pack_dir = Path(pack_dir)

    def run(
        self,
        *,
        split: Literal["dev", "holdout"],
        online: bool,
        confirm_cost: bool = False,
        confirm_holdout: bool = False,
    ) -> EvalRun:
        if split not in {"dev", "holdout"}:
            raise AppError("invalid_split", "评测分流只能是 dev 或 holdout。")
        if split == "holdout" and not confirm_holdout:
            raise AppError(
                "holdout_confirmation_required",
                "运行 holdout 会消耗其一次性留出资格；请先确认。",
            )
        if online and not confirm_cost:
            raise AppError(
                "cost_confirmation_required",
                "在线评测会调用远程模型并可能产生费用；请先确认。",
            )
        if online:
            self._preflight_online()
        manifest = read_manifest(self.pack_dir)
        freeze = PackReview.read_current_freeze(self.pack_dir)
        if manifest.eval_freeze_version != freeze.version:
            raise AppError("stale_freeze", "pack.json 与当前冻结版本不一致，请重新冻结。")
        if (
            _sha256((self.pack_dir / "profile" / "profile.md").read_bytes())
            != freeze.profile_sha256
            or _sha256((self.pack_dir / "settings.json").read_bytes())
            != freeze.settings_sha256
            or not freeze.corpus_sha256
            or _sha256((self.pack_dir / "generated_manifest.jsonl").read_bytes())
            != freeze.corpus_sha256
        ):
            raise AppError(
                "stale_freeze",
                "profile、settings 或正文清单在冻结后发生变化；需要重新冻结评测集后才能评测。",
            )
        if split == "holdout" and freeze.holdout_status == "used":
            raise AppError("holdout_used", "该 holdout 已经使用过，不能重复运行。")
        label_info = freeze.labels.get(split)
        if label_info is None:
            raise AppError("invalid_freeze", "冻结记录缺少该分流。")
        label_path = self._label_path(label_info.path)
        if _sha256(label_path.read_bytes()) != label_info.sha256:
            raise AppError("stale_labels", "labels 文件与冻结哈希不一致，拒绝运行。")
        try:
            cases = [
                EvalCase.model_validate_json(line)
                for line in label_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        except (ValueError, TypeError) as exc:
            raise AppError("stale_labels", "labels 文件格式无效，拒绝运行。") from exc
        if len(cases) != label_info.cases:
            raise AppError("stale_labels", "labels 题目数量与冻结记录不一致。")
        pipeline = RagPipeline(
            self.knowledge,
            self.provider,
            teaching_profile=load_teaching_profile(self.settings.teaching_profile_path),
            domain_profile=load_domain_profile(self.settings.domain_profile_path),
        )
        started = time.monotonic()
        report = evaluate(pipeline, cases, with_routing=online)
        duration = time.monotonic() - started
        diagnosis = diagnose_report(report, online=online)
        run_id = (
            f"{split}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-"
            f"{uuid.uuid4().hex[:8]}"
        )
        run_dir = self.pack_dir / "eval" / "runs" / freeze.version / run_id
        run_dir.mkdir(parents=True)
        summary = {key: value for key, value in report.items() if key != "results"}
        payload = {
            "schema_version": 1,
            "run_id": run_id,
            "pack_id": manifest.pack_id,
            "freeze_version": freeze.version,
            "split": split,
            "online": online,
            "created_at": _utc_now(),
            "duration_seconds": round(duration, 3),
            "model": self.settings.llm_model,
            "profile_sha256": freeze.profile_sha256,
            "settings_sha256": freeze.settings_sha256,
            "labels_sha256": label_info.sha256,
            "metrics": summary,
            "diagnosis": diagnosis,
            "holdout_consumed": False,
            "limitations": LIMITATIONS,
        }
        _write_json(run_dir / "report.json", payload)
        _write_jsonl(run_dir / "cases.jsonl", report["results"])
        _write_json(run_dir / "diagnosis.json", diagnosis)
        _write_text(run_dir / "diagnosis.md", _diagnosis_markdown(payload, diagnosis))
        _write_text(run_dir / "report.md", _report_markdown(payload))
        if split == "holdout":
            # Do not burn the one-shot holdout when every case failed at the service layer.
            if online and report["service_errors"] == report["cases"]:
                payload["holdout_consumed"] = False
                payload["diagnosis"]["recommendations"].append(
                    "全部用例发生服务错误；本次未消耗 holdout，可修复服务后重试。"
                )
            else:
                PackReview.mark_holdout_used(
                    self.pack_dir, freeze.version, run_id=run_id
                )
                payload["holdout_consumed"] = True
            _write_json(run_dir / "report.json", payload)
            _write_text(run_dir / "report.md", _report_markdown(payload))
        return EvalRun(
            run_id=run_id,
            pack_id=manifest.pack_id,
            freeze_version=freeze.version,
            split=split,
            online=online,
            created_at=payload["created_at"],
            duration_seconds=duration,
            metrics=summary,
            diagnosis_counts=diagnosis["counts"],
            report_path=run_dir / "report.json",
        )

    def _preflight_online(self) -> None:
        from rag_workbench.infrastructure.llm import OpenAIChatProvider

        if isinstance(self.provider, OpenAIChatProvider) and not (
            self.settings.api_key.get_secret_value().strip()
        ):
            raise AppError("missing_api_key", "在线评测需要配置 DEEPSEEK_API_KEY。")

    def _label_path(self, relative: str) -> Path:
        candidate = (self.pack_dir / relative).resolve()
        if (
            not candidate.is_relative_to(self.pack_dir.resolve())
            or not candidate.is_file()
        ):
            raise AppError("stale_labels", "labels 文件缺失或路径越界。")
        return candidate


def diagnose_report(report: dict, *, online: bool) -> dict:
    counts = {category: 0 for category in DIAGNOSIS_CATEGORIES}
    cases = []
    for row in report.get("results", []):
        category = _classify(row, online=online)
        counts[category] += 1
        cases.append(
            {
                "id": row.get("id"),
                "category": category,
                "expected_mode": row.get("expected_mode"),
                "mode": row.get("mode"),
                "error_code": row.get("error_code"),
                "note": RECOMMENDATIONS[category],
            }
        )
    recommendations = [
        RECOMMENDATIONS[category]
        for category in DIAGNOSIS_CATEGORIES
        if counts[category] and category not in {"clean"} and category != "answer_review"
    ]
    if counts["answer_review"]:
        recommendations.append(RECOMMENDATIONS["answer_review"])
    if not online:
        recommendations.append("离线评测不覆盖证据判断、路由和答案生成。")
    if not recommendations:
        recommendations.append(RECOMMENDATIONS["clean"])
    return {"counts": counts, "cases": cases, "recommendations": recommendations}


def _classify(row: dict, *, online: bool) -> str:
    if row.get("error_code"):
        return "service_error"
    expected = row.get("expected_mode")
    mode = row.get("mode")
    relevant = set(row.get("relevant_document_ids") or [])
    retrieved = set(row.get("retrieved_document_ids") or [])
    eligible = set(row.get("eligible_document_ids") or [])
    if expected == "knowledge_base" and relevant:
        if not (relevant & retrieved):
            return "retrieval_miss"
        if not (relevant & eligible):
            return "threshold_miss"
        if online and mode != expected:
            return "routing_error"
        if online:
            if row.get("citation_valid") is False:
                return "citation_error"
            return "answer_review"
        return "clean"
    if online and mode != expected:
        if expected != "knowledge_base" and mode == "knowledge_base":
            return "negative_leak"
        return "routing_error"
    if expected != "knowledge_base":
        if eligible:
            return "negative_leak"
        return "clean"
    return "clean"


def _report_markdown(payload: dict) -> str:
    metrics = payload["metrics"]
    diagnosis = payload["diagnosis"]
    lines = [
        "# 知识包评测报告",
        "",
        f"> {payload['limitations']}",
        "",
        "## 运行",
        "",
        f"- 知识包：{payload['pack_id']}",
        f"- 冻结版本：{payload['freeze_version']}",
        f"- 分流：{payload['split']}",
        f"- 在线：{'是' if payload['online'] else '否'}",
        f"- 模型：{payload['model']}",
        f"- 耗时：{payload['duration_seconds']} 秒",
        f"- holdout 是否消耗：{'是' if payload['holdout_consumed'] else '否'}",
        "",
        "## 指标",
        "",
        "| 指标 | 数值 |",
        "|---|---:|",
    ]
    for key in (
        "cases",
        "retrieval_hit_rate",
        "recall_at_k",
        "eligible_hit_rate",
        "eligible_recall_at_k",
        "irrelevant_threshold_hit_rate",
        "routing_accuracy",
        "citation_validity",
        "service_errors",
    ):
        value = metrics.get(key)
        lines.append(f"| {key} | {'未测' if value is None else value} |")
    lines.extend(["", "## 诊断计数", "", "| 类别 | 数量 |", "|---|---:|"])
    for category, count in diagnosis["counts"].items():
        lines.append(f"| {category} | {count} |")
    lines.extend(["", "## 建议", ""])
    for item in diagnosis["recommendations"]:
        lines.append(f"- {item}")
    failures = [case for case in diagnosis["cases"] if case["category"] != "clean"]
    if failures:
        lines.extend(
            [
                "",
                "## 非 clean 用例",
                "",
                "| 题号 | 类别 | 期望模式 | 实际模式 | 错误码 |",
                "|---|---|---|---|---|",
            ]
        )
        for case in failures:
            lines.append(
                f"| {case['id']} | {case['category']} | {case['expected_mode']} | "
                f"{case['mode'] or '-'} | {case['error_code'] or '-'} |"
            )
    return "\n".join(lines) + "\n"


def _diagnosis_markdown(payload: dict, diagnosis: dict) -> str:
    lines = [
        f"# 失败诊断（{payload['split']}）",
        "",
        f"> {LIMITATIONS}",
        "",
        "## 计数",
        "",
        "| 类别 | 数量 |",
        "|---|---:|",
    ]
    for category, count in diagnosis["counts"].items():
        lines.append(f"| {category} | {count} |")
    lines.extend(["", "## 建议", ""])
    for item in diagnosis["recommendations"]:
        lines.append(f"- {item}")
    return "\n".join(lines) + "\n"


def _write_json(path: Path, payload) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _write_text(path: Path, value: str) -> None:
    path.write_text(value, encoding="utf-8", newline="\n")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
