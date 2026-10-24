"""Human review, evaluation-set freezing and the one-shot holdout lock."""

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import Field

from rag_workbench.application.knowledge import KnowledgeService
from rag_workbench.application.pack_authoring import EvalCandidate
from rag_workbench.application.packs import (
    PackManifest,
    read_manifest,
    update_manifest,
)
from rag_workbench.domain.errors import AppError
from rag_workbench.domain.models import Contract

FREEZE_VERSION_RE = re.compile(r"f\d{3}")
PROFILE_REQUIRED_GROUPS = (
    ("证据", "evidence"),
    ("不足", "insufficient", "无法回答"),
    ("引用", "citation", "chunk"),
)


class LabelInfo(Contract):
    path: str
    sha256: str
    cases: int


class FreezeInfo(Contract):
    schema_version: int = 1
    version: str
    created_at: str
    profile_sha256: str
    settings_sha256: str
    labels: dict[str, LabelInfo]
    counts: dict[str, int]
    corpus_sha256: str = ""
    warnings: list[str] = Field(default_factory=list)
    holdout_status: Literal["unused", "used"] = "unused"
    holdout_used_at: str | None = None
    holdout_run_id: str | None = None
    generator: dict = Field(default_factory=dict)

    @property
    def dev_labels(self) -> LabelInfo:
        return self.labels["dev"]

    @property
    def holdout_labels(self) -> LabelInfo:
        return self.labels["holdout"]


class ProfileMeta(Contract):
    schema_version: int = 1
    status: Literal["accepted"] = "accepted"
    content_sha256: str
    accepted_at: str
    source: Literal["draft", "manual"]
    model: str | None = None
    prompt_version: str | None = None
    input_sha256: str | None = None
    warnings: list[str] = Field(default_factory=list)


class PackReview:
    @staticmethod
    def read_candidates(pack_dir: Path) -> list[EvalCandidate]:
        path = Path(pack_dir) / "eval" / "candidates.jsonl"
        if not path.is_file():
            return []
        candidates = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                candidates.append(EvalCandidate.model_validate_json(line))
            except ValueError as exc:
                raise AppError("invalid_candidates", "评测题候选文件损坏。") from exc
        return candidates

    @staticmethod
    def save_review(pack_dir: Path, decisions: dict[str, dict]) -> list[EvalCandidate]:
        pack_dir = Path(pack_dir)
        candidates = PackReview.read_candidates(pack_dir)
        if not candidates:
            raise AppError("missing_candidates", "还没有可审核的评测题。")
        known = {candidate.id for candidate in candidates}
        unknown = set(decisions) - known
        if unknown:
            raise AppError("unknown_candidate", "审核结果包含不存在的题目 ID。")
        reviewed = []
        for candidate in candidates:
            decision = decisions.get(candidate.id, {})
            status = decision.get("status", candidate.status)
            split = decision.get("split", candidate.split)
            if status not in {"pending", "approved", "rejected"}:
                raise AppError("invalid_review", "题目状态无效。")
            if split not in {"unassigned", "dev", "holdout"}:
                raise AppError("invalid_review", "题目分流无效。")
            if status != "approved":
                split = "unassigned"
            updates = {"status": status, "split": split}
            if "question" in decision:
                question = re.sub(r"\s+", " ", str(decision["question"])).strip()
                if not question:
                    raise AppError("invalid_review", "题目文本不能为空。")
                updates["question"] = question[:500]
            if "reference_answer" in decision:
                updates["reference_answer"] = str(decision["reference_answer"])[:2000]
            reviewed.append(
                EvalCandidate.model_validate({**candidate.model_dump(), **updates})
            )
        PackReview._write_candidates(pack_dir, reviewed)
        manifest = read_manifest(pack_dir)
        if manifest.eval_status == "none":
            update_manifest(pack_dir, eval_status="candidate")
        return reviewed

    @staticmethod
    def adopt_profile(
        pack_dir: Path,
        *,
        content: str | None = None,
        accept_draft: bool = False,
        source: Literal["draft", "manual"] = "manual",
    ) -> dict:
        pack_dir = Path(pack_dir)
        manifest = read_manifest(pack_dir)
        draft_meta = {}
        if accept_draft:
            draft_path = pack_dir / "profile" / "profile.draft.md"
            draft_meta_path = pack_dir / "profile" / "profile.draft.meta.json"
            if not draft_path.is_file():
                raise AppError("draft_missing", "还没有可采用的 profile 草案。")
            content = draft_path.read_text(encoding="utf-8")
            if draft_meta_path.is_file():
                try:
                    draft_meta = json.loads(draft_meta_path.read_text(encoding="utf-8"))
                except ValueError as exc:
                    raise AppError("invalid_profile_draft", "profile 草案元数据损坏。") from exc
            source = "draft"
        value = (content or "").strip()
        if len(value) < 50:
            raise AppError("invalid_profile", "profile 内容过短，未保存。")
        if len(value) > 6000:
            raise AppError("invalid_profile", "profile 内容过长，未保存。")
        lowered = value.casefold()
        missing = [
            group[0]
            for group in PROFILE_REQUIRED_GROUPS
            if not any(term.casefold() in lowered for term in group)
        ]
        if missing:
            raise AppError(
                "invalid_profile",
                "profile 缺少必要约束：" + "、".join(missing) + "；未保存。",
            )
        profile_dir = pack_dir / "profile"
        history_dir = profile_dir / "history"
        history_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        content_hash = _sha256(value.encode("utf-8"))
        current_path = profile_dir / "profile.md"
        if current_path.is_file():
            previous = current_path.read_text(encoding="utf-8")
            previous_name = (
                f"profile-{stamp}-previous-{_sha256(previous.encode())[:8]}.md"
            )
            (history_dir / previous_name).write_text(
                previous, encoding="utf-8", newline="\n"
            )
        current_path.write_text(value, encoding="utf-8", newline="\n")
        (history_dir / f"profile-{stamp}-{content_hash[:8]}.md").write_text(
            value, encoding="utf-8", newline="\n"
        )
        meta = ProfileMeta(
            content_sha256=content_hash,
            accepted_at=_utc_now(),
            source=source,
            model=draft_meta.get("model"),
            prompt_version=draft_meta.get("prompt_version"),
            input_sha256=draft_meta.get("input_sha256"),
        )
        (profile_dir / "profile.meta.json").write_text(
            json.dumps(meta.model_dump(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        status = "frozen" if manifest.eval_status == "frozen" else "reviewed"
        updated = update_manifest(
            pack_dir, profile_status="reviewed", status=status
        )
        _update_readme_status(pack_dir, updated)
        return meta.model_dump()

    @staticmethod
    def freeze_eval(pack_dir: Path, knowledge: KnowledgeService) -> FreezeInfo:
        pack_dir = Path(pack_dir)
        manifest = read_manifest(pack_dir)
        if manifest.profile_status != "reviewed":
            raise AppError("profile_not_reviewed", "请先采用并审核 profile，再冻结评测集。")
        candidates = PackReview.read_candidates(pack_dir)
        approved = [candidate for candidate in candidates if candidate.status == "approved"]
        dev = [candidate for candidate in approved if candidate.split == "dev"]
        holdout = [candidate for candidate in approved if candidate.split == "holdout"]
        if not dev or not holdout:
            raise AppError("missing_split", "dev 和 holdout 都必须至少有一道已批准题目。")
        if not any(candidate.category != "negative" for candidate in approved):
            raise AppError("missing_positive", "评测集至少需要一道正例。")
        if not any(candidate.category == "negative" for candidate in approved):
            raise AppError("missing_negative", "评测集至少需要一道负例。")
        risky_negatives = [
            candidate.id
            for candidate in approved
            if candidate.category == "negative" and candidate.negative_risk
        ]
        if risky_negatives:
            raise AppError(
                "negative_risk",
                "以下负例检索到过门槛候选，不能冻结：" + "、".join(risky_negatives),
            )
        invalid = [
            candidate.id
            for candidate in approved
            if candidate.category != "negative"
            and (not candidate.reachable or not candidate.relevant_chunk_ids)
        ]
        if invalid:
            raise AppError(
                "invalid_approved_cases",
                "以下已批准题目未通过可达性检查，不能冻结：" + "、".join(invalid),
            )
        identities: set[str] = set()
        for candidate in approved:
            identity = re.sub(r"\s+", "", candidate.question).casefold()
            if identity in identities:
                raise AppError("duplicate_cases", "评测集中存在重复题目，不能冻结。")
            identities.add(identity)
        chunks = {chunk.id: chunk for chunk in knowledge.repo.chunks()}
        dev_rows = _runtime_rows(dev, chunks, knowledge.settings.allow_general)
        holdout_rows = _runtime_rows(
            holdout, chunks, knowledge.settings.allow_general
        )
        version = _next_freeze_version(pack_dir)
        freeze_dir = pack_dir / "eval" / "freezes" / version
        freeze_dir.mkdir(parents=True)
        dev_path = freeze_dir / "labels.dev.jsonl"
        holdout_path = freeze_dir / "labels.holdout.jsonl"
        _write_jsonl(dev_path, dev_rows)
        _write_jsonl(holdout_path, holdout_rows)
        candidates_path = pack_dir / "eval" / "candidates.jsonl"
        if candidates_path.is_file():
            (freeze_dir / "candidates.jsonl").write_bytes(candidates_path.read_bytes())
        meta_path = pack_dir / "eval" / "candidates.meta.json"
        generator = {}
        if meta_path.is_file():
            (freeze_dir / "candidates.meta.json").write_bytes(meta_path.read_bytes())
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                generator = {
                    "model": meta.get("model"),
                    "prompt_version": meta.get("prompt_version"),
                    "input_sha256": meta.get("input_sha256"),
                    "generated_at": meta.get("generated_at"),
                }
            except ValueError:
                generator = {}
        warnings = []
        if len(dev_rows) < 5:
            warnings.append(f"dev 只有 {len(dev_rows)} 题，少于建议的 5 题。")
        if len(holdout_rows) < 3:
            warnings.append(f"holdout 只有 {len(holdout_rows)} 题，少于建议的 3 题。")
        previous = PackReview._read_current_freeze(pack_dir)
        if previous and previous.holdout_status == "used":
            warnings.append(
                "上一版 holdout 已使用；如果本次复用旧题，不能视为独立验收。"
            )
        if not any(candidate.category == "negative" for candidate in holdout):
            warnings.append("holdout 没有负例，无法在该留出集上检查拒答。")
        freeze = FreezeInfo(
            version=version,
            created_at=_utc_now(),
            profile_sha256=_sha256((pack_dir / "profile" / "profile.md").read_bytes()),
            settings_sha256=_sha256((pack_dir / "settings.json").read_bytes()),
            corpus_sha256=_sha256(
                (pack_dir / "generated_manifest.jsonl").read_bytes()
            ),
            labels={
                "dev": LabelInfo(
                    path=dev_path.relative_to(pack_dir).as_posix(),
                    sha256=_sha256(dev_path.read_bytes()),
                    cases=len(dev_rows),
                ),
                "holdout": LabelInfo(
                    path=holdout_path.relative_to(pack_dir).as_posix(),
                    sha256=_sha256(holdout_path.read_bytes()),
                    cases=len(holdout_rows),
                ),
            },
            counts={"dev": len(dev_rows), "holdout": len(holdout_rows)},
            warnings=warnings,
            holdout_status="unused",
            generator=generator,
        )
        _write_json(freeze_dir / "freeze.json", freeze.model_dump())
        _write_json(
            pack_dir / "eval" / "current.json",
            {
                "version": version,
                "freeze": (freeze_dir / "freeze.json").relative_to(pack_dir).as_posix(),
            },
        )
        updated = update_manifest(
            pack_dir,
            eval_status="frozen",
            eval_freeze_version=version,
            holdout_status="unused",
            status="frozen",
        )
        _update_readme_status(pack_dir, updated)
        return freeze

    @staticmethod
    def read_current_freeze(pack_dir: Path) -> FreezeInfo:
        freeze = PackReview._read_current_freeze(pack_dir)
        if freeze is None:
            raise AppError("missing_freeze", "还没有冻结的评测集。")
        return freeze

    @staticmethod
    def mark_holdout_used(
        pack_dir: Path, version: str, *, run_id: str, when: str | None = None
    ) -> FreezeInfo:
        pack_dir = Path(pack_dir)
        if not FREEZE_VERSION_RE.fullmatch(version):
            raise AppError("invalid_freeze_version", "冻结版本无效。")
        freeze = PackReview.read_current_freeze(pack_dir)
        if freeze.version != version:
            raise AppError("freeze_mismatch", "冻结版本与当前版本不一致。")
        if freeze.holdout_status == "used":
            raise AppError("holdout_used", "该 holdout 已经使用过，不能重复使用。")
        updated = freeze.model_copy(
            update={
                "holdout_status": "used",
                "holdout_used_at": when or _utc_now(),
                "holdout_run_id": run_id,
            }
        )
        _write_json(
            pack_dir / "eval" / "freezes" / version / "freeze.json",
            updated.model_dump(),
        )
        manifest = update_manifest(pack_dir, holdout_status="used")
        _update_readme_status(pack_dir, manifest)
        return updated

    @staticmethod
    def _read_current_freeze(pack_dir: Path) -> FreezeInfo | None:
        current_path = Path(pack_dir) / "eval" / "current.json"
        if not current_path.is_file():
            return None
        try:
            current = json.loads(current_path.read_text(encoding="utf-8"))
            version = str(current.get("version", ""))
        except ValueError as exc:
            raise AppError("invalid_freeze", "eval/current.json 损坏。") from exc
        if not FREEZE_VERSION_RE.fullmatch(version):
            raise AppError("invalid_freeze", "eval/current.json 的版本无效。")
        freeze_path = Path(pack_dir) / "eval" / "freezes" / version / "freeze.json"
        if not freeze_path.is_file():
            raise AppError("invalid_freeze", "当前冻结记录缺失。")
        try:
            return FreezeInfo.model_validate_json(freeze_path.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise AppError("invalid_freeze", "当前冻结记录损坏。") from exc

    @staticmethod
    def _write_candidates(pack_dir: Path, candidates: list[EvalCandidate]) -> None:
        path = Path(pack_dir) / "eval" / "candidates.jsonl"
        path.parent.mkdir(exist_ok=True)
        path.write_text(
            "".join(
                json.dumps(candidate.model_dump(), ensure_ascii=False, sort_keys=True) + "\n"
                for candidate in candidates
            ),
            encoding="utf-8",
        )


def _runtime_rows(
    candidates: list[EvalCandidate],
    chunks: dict,
    allow_general: bool,
) -> list[dict]:
    rows = []
    for index, candidate in enumerate(candidates, 1):
        if candidate.category == "negative":
            document_ids = []
            expected_mode = (
                "general"
                if allow_general and candidate.expected_mode == "general"
                else "insufficient_evidence"
            )
        else:
            document_ids = []
            for chunk_id in candidate.relevant_chunk_ids:
                chunk = chunks.get(chunk_id)
                if chunk is None:
                    raise AppError(
                        "stale_candidate",
                        f"题目 {candidate.id} 引用的 chunk 已不在当前索引中。",
                    )
                if chunk.document_id not in document_ids:
                    document_ids.append(chunk.document_id)
            if not document_ids:
                raise AppError("stale_candidate", f"题目 {candidate.id} 没有有效来源文档。")
            expected_mode = "knowledge_base"
        rows.append(
            {
                "id": f"q{index:03d}",
                "question": candidate.question,
                "relevant_document_ids": document_ids,
                "expected_mode": expected_mode,
            }
        )
    return rows


def _next_freeze_version(pack_dir: Path) -> str:
    root = Path(pack_dir) / "eval" / "freezes"
    root.mkdir(parents=True, exist_ok=True)
    versions = [
        int(path.name[1:])
        for path in root.iterdir()
        if path.is_dir() and FREEZE_VERSION_RE.fullmatch(path.name)
    ]
    return f"f{max(versions, default=0) + 1:03d}"


def _update_readme_status(pack_dir: Path, manifest: PackManifest) -> None:
    path = Path(pack_dir) / "README.md"
    if not path.is_file():
        return
    if manifest.status == "frozen":
        banner = (
            "> profile 已人工审核，评测集已冻结；"
            f"holdout：{'已使用' if manifest.holdout_status == 'used' else '未使用'}。"
        )
    elif manifest.status == "reviewed":
        banner = "> profile 已人工审核；评测集尚未冻结，仍不是验收通过。"
    else:
        banner = (
            "> 自动草稿，未验收。本知识包由 RAG Workbench 按上传资料自动生成；"
            "profile、清单和评测结果需人工审核后才能称为验收通过。"
        )
    lines = path.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        if line.startswith("> "):
            lines[index] = banner
            break
    else:
        lines.insert(1, banner)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
