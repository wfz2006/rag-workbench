"""Knowledge-pack builder: draft artifacts, provenance and deterministic linting."""

import contextlib
import csv
import hashlib
import io
import json
import os
import re
import shutil
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePath, PurePosixPath
from typing import Literal

from filelock import FileLock, Timeout
from pydantic import Field, field_validator, model_validator

from rag_workbench.application.knowledge import KnowledgeService
from rag_workbench.config import Settings, load_file_settings
from rag_workbench.domain.errors import AppError
from rag_workbench.domain.models import Contract, Document, ImportFailure
from rag_workbench.domain.ports import Embedder
from rag_workbench.infrastructure.parsing import (
    ALLOWED_EXTENSIONS,
    SUPPORTED_FORMATS_LABEL,
    parse_document,
    safe_display_name,
)

PACK_ID_RE = re.compile(r"^[a-z][a-z0-9-]{1,39}$")
RESERVED_PACK_IDS = {".staging", "production", "sandbox", "knowledge", "user-data"}
LICENSE_STATUSES = {"public_domain", "licensed", "internal_only", "unknown"}
DRAFT_MARKER = "自动草稿"
SMOKE_LIMIT = 12
MAX_UPLOAD_FILES = 200
MAX_BATCH_BYTES = 200 * 1024 * 1024
SECRET_PATTERN = re.compile(r"sk-[A-Za-z0-9]{16,}")
SMOKE_LIMITATIONS = "仅验证索引自洽，不代表回答准确率、事实正确性或人工验收。"


class PackSpec(Contract):
    pack_id: str
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    intended_use: str = Field(default="", max_length=800)
    authority_scope: str = Field(default="", max_length=800)
    language: str = Field(default="zh", min_length=2, max_length=20)
    license_status: Literal["public_domain", "licensed", "internal_only", "unknown"] = "unknown"
    chunk_size: int = Field(default=500, ge=20, le=4000)
    chunk_overlap: int = Field(default=60, ge=0, le=2000)
    top_k: int = Field(default=8, ge=1, le=20)
    similarity_threshold: float = Field(default=0.5, ge=-1, le=1)

    @field_validator("pack_id")
    @classmethod
    def valid_pack_id(cls, value: str) -> str:
        value = value.strip()
        if not PACK_ID_RE.fullmatch(value) or value in RESERVED_PACK_IDS:
            raise ValueError("知识包 ID 只能使用 2–40 位小写字母、数字和连字符，且不能以连字符开头")
        return value

    @field_validator("title")
    @classmethod
    def clean_title(cls, value: str) -> str:
        value = re.sub(r"\s+", " ", value).strip()
        if not value:
            raise ValueError("知识包名称不能为空")
        return value

    @model_validator(mode="after")
    def valid_chunking(self):
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("Chunk Overlap 必须小于 Chunk Size")
        return self


class PackManifest(Contract):
    schema_version: int = 1
    pack_id: str
    title: str = Field(min_length=1, max_length=120)
    description: str = ""
    intended_use: str = ""
    authority_scope: str = ""
    language: str = "zh"
    license_status: Literal["public_domain", "licensed", "internal_only", "unknown"] = "unknown"
    distribution: Literal["local_only", "internal", "public"] = "local_only"
    status: Literal["draft", "reviewed", "frozen"] = "draft"
    profile_status: Literal["draft", "reviewed"] = "draft"
    eval_status: Literal["none", "candidate", "frozen"] = "none"
    holdout_status: Literal["unused", "used"] = "unused"
    eval_freeze_version: str | None = None
    settings_file: str = "settings.json"
    profile: str = "profile/profile.md"
    source_manifest: str = "source_manifest.csv"
    generated_manifest: str = "generated_manifest.jsonl"
    review_notes: str = "review_notes.md"
    eval_dir: str = "eval"

    @field_validator("pack_id")
    @classmethod
    def valid_pack_id(cls, value: str) -> str:
        value = value.strip()
        if not PACK_ID_RE.fullmatch(value) or value in RESERVED_PACK_IDS:
            raise ValueError("invalid pack id")
        return value

    @field_validator(
        "settings_file",
        "profile",
        "source_manifest",
        "generated_manifest",
        "review_notes",
        "eval_dir",
    )
    @classmethod
    def relative_artifact(cls, value: str) -> str:
        value = value.strip()
        path = PurePosixPath(value.replace("\\", "/"))
        if (
            not value
            or path.is_absolute()
            or ".." in path.parts
            or re.match(r"^[A-Za-z]:", value)
        ):
            raise ValueError("artifact path must stay inside the pack")
        return value


class PackSummary(Contract):
    pack_id: str
    title: str
    status: Literal["draft", "reviewed", "frozen"]
    profile_status: Literal["draft", "reviewed"]
    eval_status: Literal["none", "candidate", "frozen"]
    holdout_status: Literal["unused", "used"]
    eval_freeze_version: str | None
    license_status: Literal["public_domain", "licensed", "internal_only", "unknown"]
    distribution: Literal["local_only", "internal", "public"]
    settings_file: str
    pack_dir: Path
    data_dir: Path
    has_data: bool


class LintCheck(Contract):
    id: str
    status: Literal["pass", "warn", "fail"]
    message: str


class PackLintResult(Contract):
    pack_id: str
    checks: list[LintCheck] = Field(default_factory=list)

    @property
    def errors(self) -> list[str]:
        return [check.message for check in self.checks if check.status == "fail"]

    @property
    def warnings(self) -> list[str]:
        return [check.message for check in self.checks if check.status == "warn"]

    @property
    def ok(self) -> bool:
        return not self.errors


class PackBuildResult(Contract):
    pack_id: str
    pack_dir: Path
    data_dir: Path
    documents: list[Document]
    failures: list[ImportFailure]
    stats: dict
    smoke: dict
    lint: PackLintResult


@dataclass
class _PreparedSource:
    source_id: str
    name: str
    extension: str
    sha256: str
    corpus_name: str
    corpus_text: str
    corpus_bytes: bytes
    original_bytes: bytes
    sections: int
    pages: int
    extraction: str


class PackCatalog:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.root = settings.packs_dir.resolve()
        self.data_root = settings.packs_data_dir.resolve()
        self._warnings: list[str] = []

    @property
    def warnings(self) -> list[str]:
        return list(self._warnings)

    def list(self) -> list[PackSummary]:
        self._warnings = []
        if not self.root.is_dir():
            return []
        entries: list[PackSummary] = []
        for directory in sorted(self.root.iterdir(), key=lambda item: item.name):
            if (
                directory.name.startswith(".")
                or directory.is_symlink()
                or directory.is_junction()
                or not directory.is_dir()
            ):
                continue
            manifest_path = directory / "pack.json"
            if not manifest_path.is_file():
                continue
            try:
                manifest = PackManifest.model_validate_json(
                    manifest_path.read_text(encoding="utf-8")
                )
            except (OSError, ValueError) as exc:
                self._warnings.append(f"{directory.name}：pack.json 无效（{type(exc).__name__}）。")
                continue
            if manifest.pack_id != directory.name:
                self._warnings.append(f"{directory.name}：pack_id 与目录名不一致，已忽略。")
                continue
            data_dir = (self.data_root / manifest.pack_id).resolve()
            entries.append(
                PackSummary(
                    pack_id=manifest.pack_id,
                    title=manifest.title,
                    status=manifest.status,
                    profile_status=manifest.profile_status,
                    eval_status=manifest.eval_status,
                    holdout_status=manifest.holdout_status,
                    eval_freeze_version=manifest.eval_freeze_version,
                    license_status=manifest.license_status,
                    distribution=manifest.distribution,
                    settings_file=manifest.settings_file,
                    pack_dir=directory.resolve(),
                    data_dir=data_dir,
                    has_data=(data_dir / "metadata.sqlite3").is_file(),
                )
            )
        return entries

    def get(self, pack_id: str) -> PackSummary:
        if not PACK_ID_RE.fullmatch(pack_id):
            raise AppError("invalid_pack_id", "知识包 ID 无效。")
        pack_dir = (self.root / pack_id).resolve()
        if (
            pack_dir.parent != self.root
            or not pack_dir.is_dir()
            or pack_dir.is_symlink()
            or pack_dir.is_junction()
        ):
            raise AppError("pack_not_found", "没有找到这个知识包。")
        manifest_path = pack_dir / "pack.json"
        if not manifest_path.is_file():
            raise AppError("pack_not_found", "知识包缺少 pack.json。")
        try:
            manifest = PackManifest.model_validate_json(
                manifest_path.read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as exc:
            raise AppError("invalid_pack", "知识包清单无效，请检查 pack.json。") from exc
        if manifest.pack_id != pack_id:
            raise AppError("invalid_pack", "知识包清单 ID 与目录不一致。")
        data_dir = (self.data_root / pack_id).resolve()
        return PackSummary(
            pack_id=manifest.pack_id,
            title=manifest.title,
            status=manifest.status,
            profile_status=manifest.profile_status,
            eval_status=manifest.eval_status,
            holdout_status=manifest.holdout_status,
            eval_freeze_version=manifest.eval_freeze_version,
            license_status=manifest.license_status,
            distribution=manifest.distribution,
            settings_file=manifest.settings_file,
            pack_dir=pack_dir,
            data_dir=data_dir,
            has_data=(data_dir / "metadata.sqlite3").is_file(),
        )


def load_pack_settings(
    pack_id: str,
    *,
    base: Settings | None = None,
    data_dir: Path | None = None,
) -> Settings:
    base = base or Settings()
    summary = PackCatalog(base).get(pack_id)
    target = (
        data_dir.resolve()
        if data_dir is not None
        else (base.packs_data_dir / pack_id).resolve()
    )
    return load_file_settings(
        summary.pack_dir / summary.settings_file,
        data_dir=target,
        env_file=base._deployment_env_file,
    )


class PackBuilder:
    def __init__(self, settings: Settings, embedder: Embedder):
        self.settings = settings
        self.embedder = embedder

    def build(self, spec: PackSpec, uploads: list[tuple[str, bytes]]) -> PackBuildResult:
        if not PACK_ID_RE.fullmatch(spec.pack_id) or spec.pack_id in RESERVED_PACK_IDS:
            raise AppError("invalid_pack_id", "知识包 ID 无效，已中止。")
        packs_root = _prepare_root(self.settings.packs_dir)
        data_root = _prepare_root(self.settings.packs_data_dir)
        final_pack = packs_root / spec.pack_id
        final_data = data_root / spec.pack_id
        if final_pack.exists() or final_data.exists():
            raise AppError("pack_exists", "同名知识包或数据目录已存在，请换一个 ID。")
        try:
            with FileLock(packs_root / ".pack-builder.lock", timeout=120):
                if final_pack.exists() or final_data.exists():
                    raise AppError("pack_exists", "同名知识包或数据目录已存在，请换一个 ID。")
                token = uuid.uuid4().hex
                pack_staging = _staging_dir(packs_root, spec.pack_id, token)
                try:
                    data_staging = _staging_dir(data_root, spec.pack_id, token)
                except Exception:
                    _cleanup(pack_staging, packs_root)
                    raise
                try:
                    return self._build_in_staging(
                        spec, uploads, pack_staging, data_staging, final_pack, final_data
                    )
                except Exception:
                    _cleanup(pack_staging, packs_root)
                    _cleanup(data_staging, data_root)
                    raise
        except Timeout as exc:
            raise AppError("pack_builder_busy", "另一个知识包正在构建，请稍后重试。") from exc

    def _build_in_staging(
        self,
        spec: PackSpec,
        uploads: list[tuple[str, bytes]],
        pack_staging: Path,
        data_staging: Path,
        final_pack: Path,
        final_data: Path,
    ) -> PackBuildResult:
        prepared, failures, duplicates = self._prepare_sources(spec, uploads)
        if not prepared:
            raise AppError("no_valid_sources", "没有可用的资料文件；知识包未创建。")
        self._write_sources(pack_staging, prepared)
        build_settings = _build_settings(self.settings, spec, data_staging)
        knowledge = KnowledgeService(build_settings, self.embedder)
        outcomes = knowledge.ingest_many(
            [(source.corpus_name, source.corpus_bytes) for source in prepared]
        )
        documents = [item for item in outcomes if isinstance(item, Document)]
        ingest_failures = [item for item in outcomes if isinstance(item, ImportFailure)]
        if ingest_failures:
            codes = "、".join(sorted({item.error_code for item in ingest_failures}))
            raise AppError("pack_ingest_failed", f"整理后的正文索引失败（{codes}）。")
        if len(documents) != len(prepared):
            raise AppError("pack_ingest_failed", "整理后的正文数量与来源不一致。")
        stats = knowledge.stats()
        if stats["documents"] != len(prepared) or stats["index_status"] != "ready":
            raise AppError("pack_ingest_failed", "知识包索引未就绪，已中止发布。")
        smoke = _run_smoke(knowledge, documents)
        self._write_artifacts(
            spec, pack_staging, prepared, failures, duplicates, build_settings, smoke, stats
        )
        lint = pack_lint(
            pack_staging,
            data_staging,
            secret_values=[self.settings.api_key.get_secret_value()],
            embedder=self.embedder,
        )
        if lint.errors:
            raise AppError("pack_lint_failed", "知识包体检失败：" + "；".join(lint.errors[:3]))
        _write_lint_report(pack_staging, lint)
        _write_build_report(
            pack_staging, spec, prepared, failures, duplicates, stats, smoke, lint
        )
        _publish(pack_staging, final_pack, data_staging, final_data)
        return PackBuildResult(
            pack_id=spec.pack_id,
            pack_dir=final_pack,
            data_dir=final_data,
            documents=documents,
            failures=failures,
            stats=stats,
            smoke=smoke,
            lint=lint,
        )

    def _prepare_sources(
        self, spec: PackSpec, uploads: list[tuple[str, bytes]]
    ) -> tuple[list[_PreparedSource], list[ImportFailure], list[tuple[str, str]]]:
        if not uploads:
            raise AppError("empty_upload", "请先选择资料文件。")
        if len(uploads) > MAX_UPLOAD_FILES:
            raise AppError("too_many_files", f"一次最多导入 {MAX_UPLOAD_FILES} 个文件。")
        total = sum(len(data) for _, data in uploads)
        if total > MAX_BATCH_BYTES:
            raise AppError("batch_too_large", "本次资料总量超过 200 MB，请分批导入。")
        prepared: list[_PreparedSource] = []
        failures: list[ImportFailure] = []
        duplicates: list[tuple[str, str]] = []
        seen: dict[str, str] = {}
        used_source_ids: set[str] = set()
        used_corpus_names: set[str] = set()
        for position, (raw_name, data) in enumerate(uploads, 1):
            name = f"文件 {position}"
            try:
                name = safe_display_name(raw_name)
                extension = PurePath(name).suffix.lower()
                if extension not in ALLOWED_EXTENSIONS:
                    raise AppError("unsupported_type", f"仅支持 {SUPPORTED_FORMATS_LABEL}。")
                identity = hashlib.sha256(data).hexdigest()
                if identity in seen:
                    duplicates.append((name, seen[identity]))
                    continue
                sections = parse_document(name, data, self.settings)
                corpus_text = _corpus_text(name, extension, sections)
                if not corpus_text.strip():
                    raise AppError("empty_document", "整理后没有可索引的正文。")
                seen[identity] = name
                source_id = "src_" + identity[:16]
                if source_id in used_source_ids:
                    source_id = "src_" + identity
                used_source_ids.add(source_id)
                stem = _safe_stem(name)
                corpus_name = f"{identity[:16]}_{stem}.md"
                if corpus_name in used_corpus_names:
                    corpus_name = f"{identity}_{stem}.md"
                used_corpus_names.add(corpus_name)
                prepared.append(
                    _PreparedSource(
                        source_id=source_id,
                        name=name,
                        extension=extension,
                        sha256=identity,
                        corpus_name=corpus_name,
                        corpus_text=corpus_text,
                        corpus_bytes=corpus_text.encode("utf-8"),
                        original_bytes=data,
                        sections=len(sections),
                        pages=sum(section.page is not None for section in sections),
                        extraction=_extraction_label(extension),
                    )
                )
            except AppError as exc:
                failures.append(
                    ImportFailure(name=name, error=str(exc), error_code=exc.code)
                )
        prepared.sort(key=lambda item: item.source_id)
        return prepared, failures, duplicates

    def _write_sources(self, pack_staging: Path, prepared: list[_PreparedSource]) -> None:
        for source in prepared:
            _write_bytes(
                pack_staging / "sources" / f"{source.sha256}{source.extension}",
                source.original_bytes,
            )
            _write_bytes(pack_staging / "corpus" / source.corpus_name, source.corpus_bytes)

    def _write_artifacts(
        self,
        spec: PackSpec,
        pack_staging: Path,
        prepared: list[_PreparedSource],
        failures: list[ImportFailure],
        duplicates: list[tuple[str, str]],
        build_settings: Settings,
        smoke: dict,
        stats: dict,
    ) -> None:
        source_rows = [
            {
                "source_id": source.source_id,
                "title": source.name,
                "kind": source.extension[1:],
                "original_path": f"sources/{source.sha256}{source.extension}",
                "original_sha256": source.sha256,
                "input_path": f"sources/{source.sha256}{source.extension}",
                "input_sha256": source.sha256,
                "status": "selected",
                "license_status": spec.license_status,
                "note": (
                    f"{source.extraction}；正文 {len(source.corpus_text)} 字；"
                    f"生成 corpus/{source.corpus_name}"
                ),
            }
            for source in prepared
        ]
        _write_csv(
            pack_staging / "source_manifest.csv",
            source_rows,
            [
                "source_id",
                "title",
                "kind",
                "original_path",
                "original_sha256",
                "input_path",
                "input_sha256",
                "status",
                "license_status",
                "note",
            ],
        )
        generated_rows = [
            {
                "path": f"corpus/{source.corpus_name}",
                "sha256": _sha256(source.corpus_bytes),
                "bytes": len(source.corpus_bytes),
                "chars": len(source.corpus_text),
                "source_id": source.source_id,
                "source_sha256": source.sha256,
                "kind": "prepared_text",
                "locator": (
                    f"pages 1-{source.pages}" if source.pages else "document"
                ),
                "extraction": source.extraction,
                "note": "首行标题为构建器按文件名生成的整理标记。",
            }
            for source in prepared
        ]
        _write_jsonl(pack_staging / "generated_manifest.jsonl", generated_rows)
        _write_json(
            pack_staging / "settings.json", _pack_settings_payload(build_settings, spec)
        )
        _write_manifest(pack_staging, spec)
        _write_text(
            pack_staging / "README.md",
            _readme(spec, prepared, failures, stats, build_settings),
        )
        _write_text(pack_staging / "profile" / "profile.md", _profile(spec))
        _write_text(
            pack_staging / "review_notes.md",
            _review_notes(spec, prepared, failures, duplicates),
        )
        _write_json(pack_staging / "eval" / "smoke.json", smoke)
        _write_text(pack_staging / "eval" / "smoke_report.md", _smoke_report(smoke))


def _corpus_text(name: str, extension: str, sections) -> str:
    title = re.sub(r"\s+", " ", PurePath(name).stem).strip() or "文档"
    pieces: list[str] = []
    if not sections or not sections[0].text.lstrip().startswith("#"):
        pieces.append(f"# {title}")
    if extension == ".pdf":
        for section in sections:
            text = section.text.strip()
            if not text:
                continue
            page = section.page if section.page is not None else 0
            pieces.append(f"〔p{page:04d}〕\n{text}")
    else:
        pieces.extend(section.text.strip() for section in sections if section.text.strip())
    return _clean_joined(pieces)


def _clean_joined(pieces: list[str]) -> str:
    text = "\n\n".join(pieces)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extraction_label(extension: str) -> str:
    return {
        ".pdf": "pypdf 文本层",
        ".docx": "DOCX XML 段落/表格",
        ".epub": "EPUB spine/XHTML",
        ".html": "HTML 正文提取",
        ".htm": "HTML 正文提取",
        ".md": "Markdown 文本",
        ".txt": "纯文本解码",
    }.get(extension, "文本提取")


def _safe_stem(name: str) -> str:
    stem = PurePath(name).stem
    stem = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", stem, flags=re.UNICODE)
    stem = stem.strip("._-")[:40]
    return stem or "document"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def _write_text(path: Path, text: str) -> None:
    _write_bytes(path, text.encode("utf-8"))


def _write_json(path: Path, payload) -> None:
    _write_text(path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    _write_text(
        path,
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
            for row in rows
        ),
    )


def _write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _build_settings(base: Settings, spec: PackSpec, data_dir: Path) -> Settings:
    values = base.model_dump()
    values.update(
        {
            "data_dir": data_dir,
            "chunk_size": spec.chunk_size,
            "chunk_overlap": spec.chunk_overlap,
            "top_k": spec.top_k,
            "similarity_threshold": spec.similarity_threshold,
            "allow_general": False,
            "teaching_profile_path": None,
            "domain_profile_path": None,
            "retrieval_expansions_path": None,
            "spoiler_max_page": None,
            "max_answer_claims": None,
            "max_answer_chars": None,
        }
    )
    return Settings.model_validate(values)


def _pack_settings_payload(settings: Settings, spec: PackSpec) -> dict:
    return {
        "embedding_model": settings.embedding_model,
        "embedding_revision": settings.embedding_revision,
        "query_instruction": settings.query_instruction,
        "chunk_size": spec.chunk_size,
        "chunk_overlap": spec.chunk_overlap,
        "top_k": spec.top_k,
        "similarity_threshold": spec.similarity_threshold,
        "retrieval_candidate_pool": settings.retrieval_candidate_pool,
        "retrieval_context_window": settings.retrieval_context_window,
        "lexical_min_coverage": settings.lexical_min_coverage,
        "allow_general": False,
        "teaching_profile_path": "disabled",
        "domain_profile_path": "profile/profile.md",
        "retrieval_expansions_path": "disabled",
    }


def _run_smoke(knowledge: KnowledgeService, documents: list[Document]) -> dict:
    chunks = knowledge.repo.chunks()
    by_document: dict[str, list] = {}
    for chunk in chunks:
        by_document.setdefault(chunk.document_id, []).append(chunk)
    ordered = sorted(documents, key=lambda document: document.id)
    if len(ordered) > SMOKE_LIMIT:
        step = (len(ordered) - 1) / (SMOKE_LIMIT - 1)
        ordered = [ordered[round(index * step)] for index in range(SMOKE_LIMIT)]
    cases = []
    for document in ordered:
        candidates = by_document.get(document.id, [])
        if not candidates:
            continue
        chunk = candidates[len(candidates) // 2]
        query = _smoke_query(chunk.text)
        error = None
        results = []
        try:
            results = knowledge.retrieve(query)
        except AppError as exc:
            error = exc.code
        document_rank = next(
            (
                index + 1
                for index, result in enumerate(results)
                if result.chunk.document_id == document.id
            ),
            None,
        )
        chunk_rank = next(
            (
                index + 1
                for index, result in enumerate(results)
                if result.chunk.id == chunk.id
            ),
            None,
        )
        cases.append(
            {
                "document_id": document.id,
                "document_name": document.name,
                "chunk_id": chunk.id,
                "query": query,
                "document_rank": document_rank,
                "chunk_rank": chunk_rank,
                "error_code": error,
            }
        )
    total = len(cases)
    document_hits = sum(case["document_rank"] is not None for case in cases)
    chunk_hits = sum(case["chunk_rank"] is not None for case in cases)
    errors = sum(case["error_code"] is not None for case in cases)
    return {
        "kind": "retrieval_self_check",
        "generated_at": datetime.now(UTC).isoformat(),
        "sample_strategy": "每个文档取中间一个块，最多 12 个文档，按文档 ID 排序。",
        "metrics": {
            "cases": total,
            "document_hits": document_hits,
            "chunk_hits": chunk_hits,
            "document_hit_rate": document_hits / total if total else None,
            "chunk_hit_rate": chunk_hits / total if total else None,
            "service_errors": errors,
        },
        "cases": cases,
        "limitations": SMOKE_LIMITATIONS,
    }


def _smoke_query(text: str) -> str:
    text = re.sub(r"〔p\d{4,6}〕", " ", text)
    text = re.sub(r"^#+\s*", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:120].strip() or text


def _smoke_report(smoke: dict) -> str:
    metrics = smoke["metrics"]
    lines = [
        "# 检索自检报告",
        "",
        f"> {SMOKE_LIMITATIONS}",
        "",
        "| 指标 | 数值 |",
        "|---|---:|",
        f"| 用例数 | {metrics['cases']} |",
        f"| 文档命中 | {metrics['document_hits']} |",
        f"| Chunk 命中 | {metrics['chunk_hits']} |",
        f"| 服务错误 | {metrics['service_errors']} |",
        "",
        "## 用例",
        "",
        "| 文档 | 查询片段 | 文档排名 | Chunk 排名 | 错误 |",
        "|---|---|---:|---:|---|",
    ]
    for case in smoke["cases"]:
        lines.append(
            f"| {case['document_name']} | {case['query'][:40]} | "
            f"{case['document_rank'] or '-'} | {case['chunk_rank'] or '-'} | "
            f"{case['error_code'] or '-'} |"
        )
    return "\n".join(lines) + "\n"


def _readme(spec: PackSpec, prepared: list[_PreparedSource], failures: list[ImportFailure],
            stats: dict, settings: Settings) -> str:
    failed = len(failures)
    return "\n".join(
        [
            f"# {spec.title}",
            "",
            f"> {DRAFT_MARKER}，未验收。本知识包由 RAG Workbench 按上传资料自动生成；"
            "profile、清单和评测结果需人工审核后才能称为验收通过。",
            "",
            "## 范围",
            "",
            f"- 预期用途：{spec.intended_use or '未填写'}",
            f"- 权威范围：{spec.authority_scope or '未填写'}",
            f"- 语言：{spec.language}",
            f"- 资料来源授权：{spec.license_status}",
            "- 分发：仅本机（草稿默认不发布、不分发）",
            "",
            "## 规模",
            "",
            "| 项目 | 数值 |",
            "|---|---:|",
            f"| 成功导入资料 | {len(prepared)} |",
            f"| 被拒绝文件 | {failed} |",
            f"| 文本块 | {stats['chunks']} |",
            f"| 分块 | {settings.chunk_size} 字符 / {settings.chunk_overlap} 重叠 |",
            f"| 检索 | Top-K {settings.top_k}，阈值 {settings.similarity_threshold} |",
            f"| Embedding | {settings.embedding_model} |",
            "",
            "## 目录",
            "",
            "```text",
            "pack.json                 知识包清单（status=draft）",
            "source_manifest.csv       原件、哈希与授权记录",
            "generated_manifest.jsonl  正文文件到来源的精确映射",
            "settings.json             冻结的检索与分块配置",
            "profile/profile.md        领域 profile 草案",
            "review_notes.md           构建、排除与待审核项",
            "eval/smoke.json           检索自检记录",
            "corpus/                   正文白名单（已生成）",
            "sources/                  只读原件副本",
            "```",
            "",
            "## 使用",
            "",
            f"在 RAG Workbench 左侧“知识包”选择 `{spec.pack_id}` 后即可问答。"
            "新知识包使用独立数据目录，不会混入当前工作区。",
            "",
            "## 已知限制",
            "",
            f"- P1 自动构建只支持 {SUPPORTED_FORMATS_LABEL}；扫描 PDF 和图片不做 OCR。",
            "- EPUB/DOCX/HTML 按来源合并为一个整理正文；章节标题保留，但不拆成独立文档。",
            "- PDF 页码来自文本层解析；表格、公式、代码按提取文本保真，复杂版式需回看原件。",
            "- 自动 profile 是保守模板，未经过人工确认；需要按资料范围继续修订。",
            "- `eval/smoke.json` 只验证索引自洽，不是准确率、事实正确性或独立留出验收。",
            "- 构建为同步执行；大资料包期间页面会等待，后续可改为后台任务。",
            "- 运行数据目录与知识包目录分离；复制包目录不会自动带上索引，当前未提供一键重建。",
            "- 授权未知或内部资料默认仅本机使用，不得分发或公开。",
            "",
        ]
    )


def _profile(spec: PackSpec) -> str:
    return "\n".join(
        [
            f"你是“{spec.title}”的知识库问答助手。本文件是自动生成的草案，需人工审核后生效。",
            "以下规则只调整作答范围和表达，不降低证据门槛，也不改变 JSON 结构与引用规则。",
            "",
            f"1. 只回答本知识包覆盖的资料范围：{spec.authority_scope or '上传的资料文件'}。",
            f"2. 面向的任务：{spec.intended_use or '依据资料回答用户问题'}。",
            "3. 只使用 evidence 中直接支持的事实；不得用模型记忆、常识推测或外部资料补写。",
            "4. 证据不足时明确说明不足，并给出可操作的建议；不得猜测。",
            "5. 事实题先给直接结论，再给必要依据；不要复述无关前情或扩展未被问到的内容。",
            "6. 引用由系统根据真实 chunk 追加；不要自行编造来源、页码或 chunk_id。",
            f"7. 使用与用户问题相同的语言（本包默认 {spec.language}）；"
            "代码、公式和原文术语可保留。",
            "",
            "本文件由构建器按知识包元数据生成，未调用外部模型。",
            "",
        ]
    )


def _review_notes(
    spec: PackSpec,
    prepared: list[_PreparedSource],
    failures: list[ImportFailure],
    duplicates: list[tuple[str, str]],
) -> str:
    lines = [
        "# 构建与审核记录",
        "",
        f"> {DRAFT_MARKER}。请人工审核后，再把 pack.json 的 status 改为 reviewed/frozen。",
        "",
        "## 来源",
        "",
        "| 文件 | 结果 | 提取方式 | 正文块 | 页数 |",
        "|---|---|---|---:|---:|",
    ]
    for source in prepared:
        lines.append(
            f"| {source.name} | selected | {source.extraction} | {source.sections} | "
            f"{source.pages or '-'} |"
        )
    for failure in failures:
        lines.append(f"| {failure.name} | 拒绝（{failure.error_code}） | - | - | - |")
    lines.extend(["", "## 重复与排除", ""])
    if duplicates:
        for name, kept in duplicates:
            lines.append(f"- 重复内容：{name} 与 {kept} 内容相同，只保留一份。")
    else:
        lines.append("- 未发现完全重复的内容。")
    if failures:
        lines.append("")
        lines.append("### 被拒绝文件")
        for failure in failures:
            lines.append(f"- {failure.name}：{failure.error}（{failure.error_code}）")
    lines.extend(
        [
            "",
            "## 待人工审核",
            "",
            "1. 核对每份资料的授权、版本、适用范围和是否允许分发。",
            "2. 抽看原文、提取文本和 page/locator，确认没有缺页、错序或公式/表格丢失。",
            "3. 审核 profile/profile.md，确认它符合资料边界和产品语气。",
            "4. 建立人工题库与独立 holdout；`eval/smoke.json` 不能替代验收。",
            "5. 以上完成后，将 pack.json 的 status 改为 reviewed；发布前再改为 frozen。",
            "",
        ]
    )
    return "\n".join(lines)


def _write_lint_report(pack_staging: Path, lint: PackLintResult) -> None:
    _write_json(pack_staging / "lint_report.json", lint.model_dump())
    lines = ["# 知识包体检报告", "", f"结论：{'通过' if lint.ok else '未通过'}", "",
             "| 检查 | 状态 | 说明 |", "|---|---|---|"]
    for check in lint.checks:
        lines.append(f"| {check.id} | {check.status} | {check.message} |")
    _write_text(pack_staging / "lint_report.md", "\n".join(lines) + "\n")


def _write_build_report(
    pack_staging: Path,
    spec: PackSpec,
    prepared: list[_PreparedSource],
    failures: list[ImportFailure],
    duplicates: list[tuple[str, str]],
    stats: dict,
    smoke: dict,
    lint: PackLintResult,
) -> None:
    _write_json(
        pack_staging / "build_report.json",
        {
            "generated_at": datetime.now(UTC).isoformat(),
            "builder": "rag-workbench-pack-builder/1",
            "spec": spec.model_dump(),
            "inputs": [
                {
                    "name": source.name,
                    "source_id": source.source_id,
                    "sha256": source.sha256,
                    "corpus": source.corpus_name,
                    "sections": source.sections,
                    "pages": source.pages,
                }
                for source in prepared
            ],
            "failures": [failure.model_dump() for failure in failures],
            "duplicates": [{"name": name, "kept": kept} for name, kept in duplicates],
            "stats": stats,
            "smoke": smoke["metrics"],
            "lint_ok": lint.ok,
        },
    )


def read_manifest(pack_dir: Path) -> PackManifest:
    return PackManifest.model_validate_json(
        (pack_dir / "pack.json").read_text(encoding="utf-8")
    )


def write_manifest(pack_dir: Path, manifest: PackManifest) -> None:
    _write_json(pack_dir / "pack.json", manifest.model_dump())


def update_manifest(pack_dir: Path, **updates) -> PackManifest:
    manifest = read_manifest(pack_dir)
    updated = PackManifest.model_validate({**manifest.model_dump(), **updates})
    write_manifest(pack_dir, updated)
    return updated


def _write_manifest(pack_staging: Path, spec: PackSpec) -> None:
    write_manifest(
        pack_staging,
        PackManifest(
            pack_id=spec.pack_id,
            title=spec.title,
            description=spec.description,
            intended_use=spec.intended_use,
            authority_scope=spec.authority_scope,
            language=spec.language,
            license_status=spec.license_status,
            distribution="local_only",
            status="draft",
        ),
    )


def _prepare_root(path: Path) -> Path:
    root = path.resolve()
    if root.exists():
        if root.is_symlink() or root.is_junction() or not root.is_dir():
            raise AppError("unsafe_pack_path", "知识包目录必须是普通目录。")
    else:
        root.mkdir(parents=True)
    return root


def _staging_dir(root: Path, pack_id: str, token: str) -> Path:
    staging = root / ".staging"
    if staging.exists():
        if staging.is_symlink() or staging.is_junction() or not staging.is_dir():
            raise AppError("unsafe_pack_path", "知识包暂存目录必须是普通目录。")
    else:
        staging.mkdir()
    path = staging / f"{pack_id}-{token}"
    path.mkdir()
    return path


def _cleanup(path: Path, root: Path) -> None:
    try:
        if path.resolve().parent != (root / ".staging").resolve():
            return
        if path.is_symlink() or path.is_junction():
            return
        shutil.rmtree(path)
    except OSError:
        pass


def _publish(stage_pack: Path, final_pack: Path, stage_data: Path, final_data: Path) -> None:
    if final_pack.exists() or final_data.exists():
        raise AppError("pack_exists", "发布目标已存在，已中止。")
    final_data.parent.mkdir(parents=True, exist_ok=True)
    stage_data.rename(final_data)
    try:
        stage_pack.rename(final_pack)
    except OSError:
        with contextlib.suppress(OSError):
            final_data.rename(stage_data)
        raise


def _safe_relative(base: Path, value: str) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.replace("\\", "/")
    relative = PurePosixPath(normalized)
    if relative.is_absolute() or ".." in relative.parts or re.match(r"^[A-Za-z]:", normalized):
        return None
    candidate = base / relative
    if candidate.is_symlink() or candidate.is_junction():
        return None
    target = candidate.resolve()
    if not target.is_relative_to(base.resolve()):
        return None
    return target


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        return ""


def _read_csv(path: Path) -> tuple[list[dict], list[str]]:
    text = path.read_text(encoding="utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    return list(reader), list(reader.fieldnames or [])


def _read_jsonl(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def pack_lint(
    pack_dir: Path,
    data_dir: Path | None = None,
    *,
    secret_values: list[str] | None = None,
    embedder: Embedder | None = None,
) -> PackLintResult:
    pack_dir = Path(pack_dir)
    if not pack_dir.is_dir():
        return PackLintResult(
            pack_id=pack_dir.name,
            checks=[LintCheck(id="pack_dir", status="fail", message="知识包目录不存在。")],
        )
    checks: list[LintCheck] = []

    def add(identity: str, status: str, message: str) -> None:
        checks.append(LintCheck(id=identity, status=status, message=message))

    manifest: PackManifest | None = None
    manifest_path = pack_dir / "pack.json"
    if not manifest_path.is_file():
        add("pack_json", "fail", "缺少 pack.json。")
    else:
        try:
            manifest = PackManifest.model_validate_json(
                manifest_path.read_text(encoding="utf-8")
            )
            add("pack_json", "pass", "pack.json 可解析。")
        except (OSError, ValueError) as exc:
            add("pack_json", "fail", f"pack.json 无效（{type(exc).__name__}）。")

    file_map = {
        "README.md": "readme",
        "source_manifest.csv": "source_manifest",
        "generated_manifest.jsonl": "generated_manifest",
        "settings.json": "settings",
    }
    if manifest:
        file_map[manifest.source_manifest] = "source_manifest"
        file_map[manifest.generated_manifest] = "generated_manifest"
        file_map[manifest.settings_file] = "settings"
        file_map[manifest.review_notes] = "review_notes"
        file_map[manifest.profile] = "profile"
    for relative, identity in file_map.items():
        target = _safe_relative(pack_dir, relative)
        if target is None or not target.is_file():
            add(identity, "fail", f"缺少必要文件：{relative}。")
        elif target.stat().st_size == 0:
            add(identity, "fail", f"必要文件为空：{relative}。")

    readme_path = pack_dir / "README.md"
    readme = _read_text(readme_path)
    if manifest and manifest.status == "draft":
        if DRAFT_MARKER not in readme or "未验收" not in readme:
            add("draft_marker", "fail", "草稿知识包的 README 必须标明“自动草稿/未验收”。")
        else:
            add("draft_marker", "pass", "README 已标明自动草稿与未验收。")
    elif manifest and DRAFT_MARKER in readme:
        add("draft_marker", "warn", "README 仍标注自动草稿，请同步审核状态。")

    if manifest and manifest.status == "frozen":
        if manifest.profile_status != "reviewed" or manifest.eval_status != "frozen":
            add(
                "freeze_status",
                "fail",
                "frozen 状态要求 profile 已审核且评测集已冻结；不能只改状态字段。",
            )
        else:
            add("freeze_status", "pass", "冻结状态与 profile/评测集状态一致。")
    elif manifest and manifest.status == "reviewed":
        if manifest.profile_status != "reviewed":
            add("review_status", "fail", "reviewed 状态要求 profile_status=reviewed。")
        else:
            add("review_status", "pass", "审核状态与 profile 状态一致。")

    if manifest and manifest.profile_status == "reviewed":
        meta_path = pack_dir / "profile" / "profile.meta.json"
        profile_path = _safe_relative(pack_dir, manifest.profile)
        if not meta_path.is_file():
            add("profile_meta", "fail", "profile 标记已审核但缺少 profile/profile.meta.json。")
        elif profile_path is None or not profile_path.is_file():
            add("profile_meta", "fail", "profile 标记已审核但当前 profile 文件缺失。")
        else:
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                current_hash = _sha256(profile_path.read_bytes())
                if meta.get("status") != "accepted":
                    add("profile_meta", "fail", "profile.meta.json 未记录 accepted 状态。")
                elif meta.get("content_sha256") != current_hash:
                    add("profile_meta", "fail", "profile.meta.json 与当前 profile 内容哈希不一致。")
                else:
                    add("profile_meta", "pass", "profile 审核记录与当前内容一致。")
            except (OSError, ValueError) as exc:
                add("profile_meta", "fail", f"profile.meta.json 无法读取（{type(exc).__name__}）。")

    if manifest and manifest.eval_status == "candidate":
        candidates_path = pack_dir / "eval" / "candidates.jsonl"
        if not candidates_path.is_file():
            add("eval_candidates", "fail", "eval_status=candidate 但缺少 eval/candidates.jsonl。")
        else:
            add("eval_candidates", "pass", "候选评测题文件存在。")

    if manifest and manifest.eval_status == "frozen":
        current_path = pack_dir / "eval" / "current.json"
        if not current_path.is_file():
            add("eval_freeze", "fail", "eval_status=frozen 但缺少 eval/current.json。")
        else:
            try:
                current = json.loads(current_path.read_text(encoding="utf-8"))
                version = current.get("version")
                if manifest.eval_freeze_version != version:
                    add("eval_freeze", "fail", "pack.json 的冻结版本与 eval/current.json 不一致。")
                else:
                    freeze_path = pack_dir / "eval" / "freezes" / str(version) / "freeze.json"
                    if not freeze_path.is_file():
                        add("eval_freeze", "fail", "缺少冻结记录 freeze.json。")
                    else:
                        freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
                        profile_path = pack_dir / (
                            manifest.profile if manifest else "profile/profile.md"
                        )
                        settings_path = pack_dir / (
                            manifest.settings_file if manifest else "settings.json"
                        )
                        generated_relative = (
                            manifest.generated_manifest
                            if manifest
                            else "generated_manifest.jsonl"
                        )
                        generated_path = _safe_relative(pack_dir, generated_relative)
                        if (
                            not profile_path.is_file()
                            or freeze.get("profile_sha256")
                            != _sha256(profile_path.read_bytes())
                            or not settings_path.is_file()
                            or freeze.get("settings_sha256")
                            != _sha256(settings_path.read_bytes())
                            or not freeze.get("corpus_sha256")
                            or generated_path is None
                            or _sha256(generated_path.read_bytes())
                            != freeze.get("corpus_sha256")
                        ):
                            add(
                                "eval_freeze",
                                "fail",
                                "冻结后 profile、settings 或正文清单已变化；需要重新冻结。",
                            )
                        else:
                            label_ok = True
                            for split in ("dev", "holdout"):
                                info = (freeze.get("labels") or {}).get(split) or {}
                                label_path = _safe_relative(pack_dir, info.get("path", ""))
                                if (
                                    label_path is None
                                    or not label_path.is_file()
                                    or _sha256(label_path.read_bytes()) != info.get("sha256")
                                ):
                                    label_ok = False
                                    break
                            if label_ok:
                                add("eval_freeze", "pass", "冻结记录与当前版本一致。")
                            else:
                                add(
                                    "eval_freeze",
                                    "fail",
                                    "冻结 labels 缺失或哈希不一致。",
                                )
            except (OSError, ValueError) as exc:
                add("eval_freeze", "fail", f"eval/current.json 无法读取（{type(exc).__name__}）。")

    if manifest and manifest.holdout_status == "used":
        version = manifest.eval_freeze_version
        freeze_path = (
            pack_dir / "eval" / "freezes" / str(version) / "freeze.json"
            if version
            else None
        )
        if freeze_path is None or not freeze_path.is_file():
            add("holdout_lock", "fail", "holdout 标记已使用但缺少冻结记录。")
        else:
            try:
                freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
                if freeze.get("holdout_status") != "used" or not freeze.get("holdout_used_at"):
                    add("holdout_lock", "fail", "holdout 使用状态与冻结记录不一致。")
                else:
                    add("holdout_lock", "pass", "holdout 使用记录存在。")
            except (OSError, ValueError) as exc:
                add("holdout_lock", "fail", f"冻结记录无法读取（{type(exc).__name__}）。")

    source_rows: list[dict] = []
    source_ids: set[str] = set()
    source_relative = manifest.source_manifest if manifest else "source_manifest.csv"
    source_path = _safe_relative(pack_dir, source_relative)
    if source_path and source_path.is_file():
        try:
            source_rows, fields = _read_csv(source_path)
            required = {
                "source_id",
                "title",
                "kind",
                "original_path",
                "original_sha256",
                "status",
                "license_status",
            }
            missing = required - set(fields)
            if missing:
                add(
                    "source_columns",
                    "fail",
                    "source_manifest 缺少字段：" + "、".join(sorted(missing)),
                )
            else:
                add("source_columns", "pass", "source_manifest 字段完整。")
        except (OSError, UnicodeError, csv.Error) as exc:
            add("source_manifest", "fail", f"source_manifest 无法读取（{type(exc).__name__}）。")
    for row in source_rows:
        identity = (row.get("source_id") or "").strip()
        if not identity or identity in source_ids:
            add("source_id", "fail", "source_id 为空或重复。")
            continue
        source_ids.add(identity)
        if row.get("status") != "selected":
            add(f"source_status:{identity}", "warn", "存在非 selected 的来源记录。")
        original = _safe_relative(pack_dir, row.get("original_path", ""))
        if original is None or not original.is_file():
            add(f"original:{identity}", "fail", "来源原件路径无效或文件不存在。")
        elif _sha256(original.read_bytes()) != row.get("original_sha256"):
            add(f"original:{identity}", "fail", "来源原件 sha256 与清单不一致。")
        if row.get("license_status") not in LICENSE_STATUSES:
            add(f"license:{identity}", "fail", "来源缺少合法的 license_status。")
        input_path = row.get("input_path")
        if input_path:
            target = _safe_relative(pack_dir, input_path)
            if target is None or not target.is_file():
                add(f"input:{identity}", "fail", "提取输入路径无效或文件不存在。")
            elif row.get("input_sha256") and _sha256(target.read_bytes()) != row["input_sha256"]:
                add(f"input:{identity}", "fail", "提取输入 sha256 与清单不一致。")

    generated_rows: list[dict] = []
    generated_paths: set[str] = set()
    generated_path = _safe_relative(
        pack_dir, manifest.generated_manifest if manifest else "generated_manifest.jsonl"
    )
    if generated_path and generated_path.is_file():
        try:
            generated_rows = _read_jsonl(generated_path)
            add("generated_manifest", "pass", "generated_manifest 可解析。")
        except (OSError, UnicodeError, ValueError, TypeError) as exc:
            add(
                "generated_manifest",
                "fail",
                f"generated_manifest 无法读取（{type(exc).__name__}）。",
            )
    for row in generated_rows:
        relative = row.get("path")
        target = _safe_relative(pack_dir, relative or "")
        if target is None or not target.is_file():
            add("generated_path", "fail", "生成正文路径无效或文件不存在。")
            continue
        normalized = target.relative_to(pack_dir).as_posix()
        if normalized in generated_paths:
            add("generated_path", "fail", f"生成正文路径重复：{normalized}。")
        generated_paths.add(normalized)
        if _sha256(target.read_bytes()) != row.get("sha256"):
            add("generated_hash", "fail", "生成正文 sha256 与清单不一致。")
        if row.get("source_id") not in source_ids:
            add("generated_source", "fail", "生成正文引用了不存在的 source_id。")
        if not normalized.startswith("corpus/"):
            add("generated_corpus", "warn", "生成正文不在 corpus/ 白名单内。")
    corpus_dir = pack_dir / "corpus"
    if not corpus_dir.is_dir():
        add("corpus_dir", "fail", "缺少 corpus/ 目录。")
    else:
        for path in sorted(corpus_dir.rglob("*")):
            if path.is_file():
                relative = path.relative_to(pack_dir).as_posix()
                if relative not in generated_paths:
                    add("corpus_unlisted", "fail", f"corpus 中存在未登记文件：{relative}。")

    settings: Settings | None = None
    settings_relative = manifest.settings_file if manifest else "settings.json"
    settings_path = _safe_relative(pack_dir, settings_relative)
    if settings_path and settings_path.is_file():
        try:
            settings = load_file_settings(
                settings_path,
                data_dir=data_dir if data_dir else pack_dir / ".lint-data",
                env_file=None,
            )
            add("settings", "pass", "settings.json 可解析且路径不越界。")
        except (AppError, ValueError) as exc:
            add("settings", "fail", f"settings.json 无效：{exc}。")
    if settings:
        if settings.domain_profile_path is not None:
            profile = settings.domain_profile_path.resolve()
            if not profile.is_file() or not profile.is_relative_to(pack_dir.resolve()):
                add("profile_path", "fail", "领域 profile 不存在或越出知识包目录。")
        if settings.retrieval_expansions_path is not None:
            expansions = settings.retrieval_expansions_path.resolve()
            if not expansions.is_file() or not expansions.is_relative_to(pack_dir.resolve()):
                add("expansions_path", "fail", "查询扩展表不存在或越出知识包目录。")
        if settings.allow_general:
            add("general_fallback", "warn", "允许通用兜底，知识库答案可能混入模型知识。")

    if data_dir is not None:
        data_dir = Path(data_dir)
        if not data_dir.is_dir() or not (data_dir / "metadata.sqlite3").is_file():
            add("data_dir", "fail", "运行数据目录不存在或缺少 metadata.sqlite3。")
        elif settings is None:
            add("data_stats", "fail", "设置无效，无法检查索引。")
        else:
            try:
                from rag_workbench.infrastructure.repository import Repository

                repository = Repository(data_dir / "metadata.sqlite3")
                documents_in_index = repository.documents()
                chunks = repository.chunks()
                active = repository.get_state("active")
                if not documents_in_index or not chunks or not active:
                    add("data_stats", "fail", "运行数据没有文档、文本块或活动索引。")
                else:
                    generation = active.get("generation", "")
                    index_dir = data_dir / "indexes" / generation
                    if not (
                        (index_dir / "index.faiss").is_file()
                        and (index_dir / "manifest.json").is_file()
                    ):
                        add("data_stats", "fail", "活动索引文件缺失。")
                    elif embedder is not None:
                        stats = KnowledgeService(settings, embedder).stats()
                        if stats["index_status"] != "ready":
                            add(
                                "data_stats",
                                "fail",
                                f"索引状态为 {stats['index_status']}，不是 ready。",
                            )
                        else:
                            add(
                                "data_stats",
                                "pass",
                                f"索引就绪：{stats['documents']} 文档 / {stats['chunks']} 块。",
                            )
                    else:
                        add(
                            "data_stats",
                            "warn",
                            f"索引结构存在（{len(documents_in_index)} 文档 / {len(chunks)} 块），"
                            "但未提供 embedder，未校验向量签名。",
                        )
                    document_names = {document.name for document in documents_in_index}
                    for row in generated_rows:
                        name = PurePath(row.get("path", "")).name
                        if name and name not in document_names:
                            add("data_documents", "fail", f"索引中缺少生成正文：{name}。")
            except (AppError, OSError, ValueError) as exc:
                add("data_stats", "fail", f"索引检查失败：{exc}。")
    else:
        add("data_dir", "warn", "未提供运行数据目录，跳过索引检查。")

    profile_relative = manifest.profile if manifest else "profile/profile.md"
    profile_path = _safe_relative(pack_dir, profile_relative)
    if (
        profile_path
        and profile_path.is_file()
        and manifest
        and manifest.status == "draft"
        and "自动生成" not in _read_text(profile_path)
    ):
        add("profile_draft", "warn", "草稿 profile 未标明自动生成，请人工确认。")

    eval_dir = _safe_relative(pack_dir, manifest.eval_dir if manifest else "eval")
    smoke_path = eval_dir / "smoke.json" if eval_dir else None
    if smoke_path is None or not smoke_path.is_file():
        add("smoke", "warn", "缺少 eval/smoke.json；尚未做检索自检。")
    else:
        try:
            smoke = json.loads(smoke_path.read_text(encoding="utf-8"))
            if smoke.get("kind") != "retrieval_self_check":
                add("smoke", "warn", "smoke.json 不是检索自检记录。")
            elif not smoke.get("metrics", {}).get("cases"):
                add("smoke", "warn", "检索自检没有用例。")
            else:
                metrics = smoke["metrics"]
                if metrics.get("document_hits", 0) < metrics.get("cases", 0):
                    add("smoke", "warn", "部分自检用例未召回目标文档，请查看 smoke 报告。")
                else:
                    add("smoke", "pass", "检索自检用例全部召回目标文档。")
        except (OSError, ValueError) as exc:
            add("smoke", "fail", f"smoke.json 无法读取（{type(exc).__name__}）。")

    review_path = _safe_relative(
        pack_dir, manifest.review_notes if manifest else "review_notes.md"
    )
    if review_path and review_path.is_file() and "待人工审核" not in _read_text(review_path):
        add("review_notes", "warn", "review_notes 未包含待人工审核清单。")

    if manifest and manifest.license_status in {"unknown", "internal_only"}:
        if manifest.distribution != "local_only":
            add("distribution", "fail", "授权未知/内部资料只能设为 local_only。")
        else:
            add("distribution", "pass", "授权未知/内部资料已限制为仅本机。")

    secrets = [value for value in (secret_values or []) if value and len(value) >= 8]
    leaked = False
    for path in sorted(pack_dir.rglob("*")):
        if (
            not path.is_file()
            or path.suffix.lower() not in {".md", ".json", ".jsonl", ".csv", ".txt"}
        ):
            continue
        try:
            if path.stat().st_size > 2 * 1024 * 1024:
                continue
            text = path.read_text(encoding="utf-8-sig", errors="ignore")
        except (OSError, UnicodeError):
            continue
        if any(secret in text for secret in secrets) or SECRET_PATTERN.search(text):
            leaked = True
            break
    if leaked:
        add("secrets", "fail", "知识包文本中检测到疑似密钥或凭据，已阻止发布。")
    else:
        add("secrets", "pass", "未在知识包文本中发现已知密钥。")

    return PackLintResult(pack_id=manifest.pack_id if manifest else pack_dir.name, checks=checks)
