import hashlib
import json
import logging
import re
import uuid
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
from filelock import FileLock, Timeout
from pydantic import ValidationError

from rag_workbench.application.literature_retrieval import ranked_literature_matches
from rag_workbench.application.paper_queries import paper_question, resolve_documents
from rag_workbench.application.paper_sources import (
    PAPER_BUDGET,
    PaperEvidence,
    page_source,
    paper_passages,
)
from rag_workbench.application.pdf_table_lookup import (
    TableLookupAmbiguity,
    inspect_pdf_table,
    table_electrode_conditions,
    table_labels,
    table_lookup_intended,
    table_lookup_requested,
    table_samples,
    without_table_temperatures,
)
from rag_workbench.application.preparation_retrieval import ranked_preparation_matches
from rag_workbench.application.query_features import (
    MEASUREMENT_PHRASE,
    contains_quote,
    material_followup,
    question_type,
)
from rag_workbench.application.retrieval_expansion import (
    coverage_focus,
    coverage_queries,
    expand_query,
    load_retrieval_expansions,
)
from rag_workbench.config import Settings
from rag_workbench.domain.errors import AppError
from rag_workbench.domain.models import (
    ChatMessage,
    Chunk,
    Document,
    ImportFailure,
    RetrievalResult,
)
from rag_workbench.domain.ports import Embedder, VectorArray
from rag_workbench.infrastructure.chunking import split_sections
from rag_workbench.infrastructure.lexical import BM25Index, identifier_text, reciprocal_rank_fusion
from rag_workbench.infrastructure.parsing import (
    ALLOWED_EXTENSIONS,
    SUPPORTED_FORMATS_LABEL,
    parse_document,
    safe_display_name,
)
from rag_workbench.infrastructure.repository import Repository
from rag_workbench.infrastructure.vector_store import FaissStore, normalized

if TYPE_CHECKING:
    from faiss import IndexFlatIP

QUOTE_CONTEXT_LIMIT = 4
_TABLE_POINTER = re.compile(r"(?:该表|上表)(?![现明示达述情演单征面观皮层])")
_TableTurn = tuple[str, list[Document], bool]

logger = logging.getLogger(__name__)


def _table_identifier(sample: str) -> re.Pattern[str]:
    parts = identifier_text(sample).split("-")
    # Some PDFs extract a printed compound identifier with spaces instead of hyphens.
    separator = r"(?:\s*-\s*|\s+)" if len(parts) >= 3 else r"\s*-\s*"
    form = separator.join(re.escape(part) for part in parts)
    return re.compile(rf"(?<![a-z0-9_-]){form}(?![a-z0-9_(-])")


def _rewrite_table_followup(question: str, previous: str) -> str | None:
    followup = material_followup(question)
    if not followup or not table_lookup_requested(previous):
        return None
    samples = table_samples(previous)
    if not (samples or table_electrode_conditions(previous) or table_labels(previous)):
        return None
    kind, value = followup
    if kind == "metric" and MEASUREMENT_PHRASE.search(previous):
        return f"{MEASUREMENT_PHRASE.sub(' ', previous)} {value}"
    if kind == "temperature" and not table_electrode_conditions(previous):
        return f"{without_table_temperatures(previous)} {value}"
    if kind == "comparison" and len(samples) == 1 and len(table_samples(value)) == 1:
        # Write both names together so an existing unpaired condition still
        # applies to both. All other predicates go through the normal guard.
        source = re.compile(re.escape(samples[0]), re.I)
        if len(source.findall(previous)) == 1:
            return source.sub(lambda match: f"{match[0]} 和 {value}", previous, count=1)
    return None


def _resolve_table_turn(
    question: str, catalog: list[Document], prior: _TableTurn,
) -> _TableTurn | None:
    named, ambiguous, lookup = resolve_documents(question, catalog)
    previous, previous_docs, previous_ambiguity = prior
    labels = table_labels(lookup)
    if _TABLE_POINTER.search(lookup) and not (named and labels):
        prior_labels = table_labels(previous)
        if (
            (not labels and len(prior_labels) != 1) or ambiguous or previous_ambiguity
            or (named and previous_docs and named != previous_docs)
        ):
            return None
        named = named or previous_docs
        if len(named) != 1:
            return None
        if not labels:
            lookup += "\n" + prior_labels[0]
    elif not named and material_followup(lookup):
        if rewritten := _rewrite_table_followup(lookup, previous):
            if not previous_docs or previous_ambiguity:
                return None
            return rewritten, previous_docs, False
        if table_labels(previous) and (
            _TABLE_POINTER.search(previous) or table_lookup_intended(previous)
        ):
            return None
        return lookup, previous_docs, previous_ambiguity
    elif not (labels and table_lookup_intended(lookup)):
        return lookup, named, ambiguous

    if not (table_samples(lookup) or table_electrode_conditions(lookup)):
        if not named:
            named, ambiguous = previous_docs, previous_ambiguity
        if (not previous_ambiguity and (not previous_docs or named == previous_docs)
                and (samples := table_samples(previous))):
            lookup += "\n样品：" + "、".join(samples)
    return lookup, named, ambiguous


class KnowledgeService:
    def __init__(self, settings: Settings, embedder: Embedder):
        self.settings, self.embedder = settings, embedder
        self.expansions = load_retrieval_expansions(settings.retrieval_expansions_path)
        self.root = settings.data_dir.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        for folder in ("originals", "trash"):
            (self.root / folder).mkdir(exist_ok=True)
        self.lock = FileLock(self.root / "knowledge.lock", timeout=120)
        self._snapshot_cache = None
        self._lexical_cache = None
        with self._locked():
            self.repo = Repository(self.root / "metadata.sqlite3")
            self.store = FaissStore(self.root / "indexes")

    @contextmanager
    def _locked(self):
        try:
            with self.lock:
                yield
        except Timeout as exc:
            raise AppError("knowledge_busy", "知识库正在更新，等待超时，请稍后重试。") from exc

    def _snapshot(self):
        epoch = self.repo.get_state("knowledge_epoch")
        if self._snapshot_cache is None or self._snapshot_cache[0] != epoch:
            chunks = self.repo.chunks()
            active = self.repo.get_state("active")
            self._snapshot_cache = (epoch, chunks, active)
            self._source_catalog = {d.id: d for d in self.repo.documents()}
        return self._snapshot_cache[1:]

    @property
    def signature(self) -> str:
        payload = {
            "embedding": self.embedder.signature,
            "size": self.settings.chunk_size,
            "overlap": self.settings.chunk_overlap,
            "chunking_version": 3,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    def _build_limits(self) -> dict[str, int]:
        return {
            "max_upload_mb": self.settings.max_upload_mb,
            "max_text_chars": self.settings.max_text_chars,
            "max_pdf_pages": self.settings.max_pdf_pages,
            # Keep legacy indexes readable; refresh their text when rebuilding the index.
            "text_processing_version": 3,
        }

    def _raw_path(self, document: Document) -> Path:
        if (
            not re.fullmatch(r"[0-9a-f]{64}", document.id)
            or document.extension not in ALLOWED_EXTENSIONS
        ):
            raise AppError("invalid_document", "文档标识无效，请检查元数据。")
        return self.root / "originals" / f"{document.id}{document.extension}"

    def ingest(self, name: str, data: bytes) -> Document:
        document = self.ingest_many([(name, data)])[0]
        if document.status == "failed":
            raise AppError(
                document.error_code or "document_failed",
                document.error or "文档索引失败，请修复后重试。",
            )
        return document

    def ingest_many(self, uploads: list[tuple[str, bytes]]) -> list[Document | ImportFailure]:
        """Isolate rejected uploads, then publish valid files together in one snapshot."""
        try:
            with self._locked():
                catalog = {d.id: d for d in self.repo.documents()}
                selected, staged = [], {}
                for position, (raw_name, data) in enumerate(uploads, 1):
                    name = f"文件 {position}"
                    try:
                        name = safe_display_name(raw_name)
                        extension = Path(name).suffix.lower()
                        if extension not in ALLOWED_EXTENSIONS:
                            raise AppError(
                                "unsupported_type", f"仅支持 {SUPPORTED_FORMATS_LABEL}。"
                            )
                        if not data or len(data) > self.settings.max_upload_mb * 1024 * 1024:
                            raise AppError("invalid_upload", "上传文件为空或超过大小限制。")
                        identity = hashlib.sha256(data).hexdigest()
                        if identity in staged:
                            selected.append(identity)
                            continue
                        existing = catalog.get(identity)
                        if (
                            existing is not None
                            and existing.status == "ready"
                            and self._raw_matches(existing, data)
                        ):
                            selected.append(identity)
                            continue
                        parse_document(name, data, self.settings)
                    except AppError as exc:
                        selected.append(
                            ImportFailure(name=name, error=str(exc), error_code=exc.code)
                        )
                        continue
                    selected.append(identity)
                    document = catalog.get(identity) or Document(
                        id=identity, name=name, extension=extension, size=len(data)
                    )
                    staged[identity] = (
                        document.model_copy(
                            update={"status": "pending", "error": None, "error_code": None}
                        ),
                        data,
                    )
                if (
                    not staged
                    and not any(d.status == "pending" for d in catalog.values())
                    and not self._active_requires_rebuild()
                ):
                    return [
                        catalog[item] if isinstance(item, str) else item for item in selected
                    ]
                for identity, (document, data) in staged.items():
                    self.store._durable_write(self._raw_path(document), data)
                    catalog[identity] = document
                    self.repo.save_document(document)
                try:
                    self._rebuild(list(catalog.values()))
                except Exception:
                    for document, _ in staged.values():
                        self.repo.save_document(
                            document.model_copy(
                                update={
                                    "status": "pending",
                                    "error": (
                                        "本次索引发布已中止；文档保留待重试。修复索引更新错误后，"
                                        "再次导入或重建会自动处理。"
                                    ),
                                    "error_code": "index_update_aborted",
                                }
                            )
                        )
                    raise
                published = {d.id: d for d in self.repo.documents()}
                return [published[item] if isinstance(item, str) else item for item in selected]
        except AppError:
            raise
        except Exception as exc:
            raise AppError(
                "ingest_failed", "文档导入失败，原活动索引未被替换，请检查磁盘后重试。"
            ) from exc

    def _raw_matches(self, document: Document, data: bytes) -> bool:
        try:
            return self._raw_path(document).read_bytes() == data
        except OSError:
            return False

    def _active_requires_rebuild(self) -> bool:
        chunks = self.repo.chunks()
        if not chunks:
            return False
        active = self.repo.get_state("active")
        if not active:
            return True
        try:
            self.store.load(active, chunks, self.signature)
        except AppError as exc:
            # A settings-only mismatch is resolved by the explicit rebuild action.
            return exc.code != "index_incompatible"
        return False

    def _reusable_snapshot(self) -> dict[str, tuple[list[Chunk], VectorArray]]:
        chunks = self.repo.chunks()
        active = self.repo.get_state("active")
        if not chunks or not active or active.get("signature") != self.signature:
            return {}
        if active.get("limits") != self._build_limits():
            return {}
        try:
            index = self.store.load(active, chunks, self.signature)
            vectors = index.reconstruct_n(0, index.ntotal)
        except AppError:
            return {}
        positions: dict[str, list[int]] = {}
        for position, chunk in enumerate(chunks):
            positions.setdefault(chunk.document_id, []).append(position)
        return {
            document_id: ([chunks[i] for i in indexes], vectors[indexes])
            for document_id, indexes in positions.items()
        }

    def _rebuild(self, documents: list[Document], *, retry_failed: bool = False) -> None:
        chunks, arrays, catalog = [], [], []
        reusable = self._reusable_snapshot()
        for document in documents:
            if document.status == "failed" and not retry_failed:
                catalog.append(document)
                continue
            try:
                try:
                    data = self._raw_path(document).read_bytes()
                except OSError as exc:
                    raise AppError(
                        "original_missing", "原始文件无法读取，请恢复原件后重试。"
                    ) from exc
                if hashlib.sha256(data).hexdigest() != document.id:
                    raise AppError("original_corrupt", "原始文件哈希不匹配，请恢复正确原件后重建。")
                reused = reusable.get(document.id)
                if document.status == "ready" and reused and reused[0]:
                    chunks.extend(reused[0])
                    arrays.append(reused[1])
                    catalog.append(
                        document.model_copy(
                            update={"status": "ready", "error": None, "error_code": None}
                        )
                    )
                    continue
                sections = parse_document(document.name, data, self.settings)
                current = split_sections(
                    document.id,
                    document.name,
                    sections,
                    self.settings.chunk_size,
                    self.settings.chunk_overlap,
                )
                fit = getattr(self.embedder, "fit_chunks", None)
                if fit:
                    current = fit(current)
                vectors = self.embedder.encode_documents([c.text for c in current])
                chunks.extend(current)
                arrays.append(vectors)
                catalog.append(
                    document.model_copy(
                        update={"status": "ready", "error": None, "error_code": None}
                    )
                )
            except AppError as exc:
                if document.status == "ready" or exc.code in {
                    "embedding_unavailable",
                    "invalid_vectors",
                }:
                    raise
                catalog.append(
                    document.model_copy(
                        update={"status": "failed", "error": str(exc), "error_code": exc.code}
                    )
                )
        vectors = np.concatenate(arrays) if arrays else np.empty((0, 1), dtype=np.float32)
        active = self.store.prepare(chunks, vectors, self.signature)
        active["limits"] = self._build_limits()
        self.repo.publish(catalog, chunks, active)
        self._snapshot_cache = None
        self._lexical_cache = None

    def rebuild(self) -> None:
        try:
            with self._locked():
                self._rebuild(self.repo.documents(), retry_failed=True)
        except AppError:
            raise
        except Exception as exc:
            raise AppError(
                "rebuild_failed", "重建失败，原活动版本保留，请检查原件与磁盘空间。"
            ) from exc

    def delete(self, document_id: str, *, require_compatible: bool = False) -> None:
        try:
            with self._locked():
                documents = self.repo.documents()
                document = next((d for d in documents if d.id == document_id), None)
                if not document:
                    raise AppError("not_found", "文档不存在或已删除。")
                chunks = self.repo.chunks()
                indexed = any(c.document_id == document_id for c in chunks)
                active = self.repo.get_state("active") or {}
                if require_compatible:
                    if chunks and not active:
                        raise AppError("index_corrupt", "活动索引缺失，请先修复索引。")
                    if active:
                        self.store.load(active, chunks, self.signature)
                if indexed:
                    kept, updated = self.store.without_document(active, chunks, document_id)
                    updated["limits"] = active.get("limits")

                original = self._raw_path(document)
                metadata, archived = self._trash_paths(document)
                absent = [
                    path for path in (metadata, archived)
                    if not (path.exists() or path.is_symlink() or path.is_junction())
                ]
                try:
                    self._archive_original(document)
                except Exception:
                    for path in absent:
                        try:
                            path.unlink(missing_ok=True)
                        except OSError:
                            logger.warning("could not remove partial trash entry")
                    raise

                # Keep the recovery copy even if publication raises: a later retry can
                # reuse it, and an interruption around commit cannot orphan the original.
                if indexed:
                    self.repo.publish([d for d in documents if d.id != document_id], kept, updated)
                else:
                    self.repo.delete_unindexed_document(document_id)
                self._snapshot_cache = None
                self._lexical_cache = None
                try:
                    original.unlink(missing_ok=True)
                except OSError:
                    logger.warning("delete committed; redundant original could not be removed")
        except AppError:
            raise
        except Exception as exc:
            raise AppError(
                "delete_failed",
                "删除未能完整完成，请刷新列表并检查磁盘和回收目录；原件仍保留。",
            ) from exc

    def _trash_paths(self, document: Document) -> tuple[Path, Path]:
        name = self._raw_path(document).name
        trash = self.root / "trash"
        return trash / f"{document.id}.json", trash / name

    @staticmethod
    def _read_optional_trash(path: Path) -> bytes | None:
        if path.is_symlink() or path.is_junction():
            raise AppError("trash_corrupt", "回收记录不能引用链接文件，未继续删除。")
        try:
            return path.read_bytes()
        except FileNotFoundError:
            return None

    def _verified_trash_original(self, path: Path, document_id: str) -> bytes | None:
        data = self._read_optional_trash(path)
        if data is not None and hashlib.sha256(data).hexdigest() != document_id:
            raise AppError("original_corrupt", "已有回收原件与文档 ID 不符，未覆盖回收记录。")
        return data

    def _archive_original(self, document: Document) -> None:
        metadata, target = self._trash_paths(document)
        archived = self._verified_trash_original(target, document.id)
        previous_metadata = self._read_optional_trash(metadata)
        previous = None
        previous_raw = None
        previous_target = target
        if previous_metadata is not None:
            try:
                previous = Document.model_validate_json(previous_metadata)
            except ValidationError as exc:
                raise AppError("trash_corrupt", "已有回收元数据损坏，未覆盖回收记录。") from exc
            if previous.id != document.id or previous.extension not in ALLOWED_EXTENSIONS:
                raise AppError("trash_corrupt", "已有回收元数据与文档 ID 或类型不符。")
            _, previous_target = self._trash_paths(previous)
            previous_raw = (
                archived if previous_target == target
                else self._verified_trash_original(previous_target, document.id)
            )

        try:
            original = self._raw_path(document).read_bytes()
        except FileNotFoundError:
            original = None
        if original is not None and hashlib.sha256(original).hexdigest() != document.id:
            raise AppError("original_corrupt", "原始文件哈希不匹配，请恢复正确原件后删除。")

        if previous_metadata is not None and previous != document:
            # A re-upload can share the content ID but have a new name or source locator.
            backup = metadata.parent / "superseded" / uuid.uuid4().hex
            backup.mkdir(parents=True)
            if previous_raw is not None:
                self.store._durable_write(backup / previous_target.name, previous_raw)
            self.store._durable_write(backup / metadata.name, previous_metadata)
        data = original if original is not None else previous_raw
        if archived is None and data is not None:
            self.store._durable_write(target, data)
        # Missing originals are still deletable. Where bytes exist, stage and verify
        # them before publishing the metadata and leave the live original until commit.
        if previous != document:
            self.store._durable_write(metadata, document.model_dump_json().encode())

    def restore(self, document_id: str, *, require_compatible: bool = False) -> Document:
        try:
            return self._restore(document_id, require_compatible=require_compatible)
        except AppError:
            raise
        except FileNotFoundError as exc:
            raise AppError(
                "original_missing", "回收原件或元数据已丢失，无法恢复；请重新上传原件。"
            ) from exc
        except ValidationError as exc:
            raise AppError(
                "trash_corrupt", "回收元数据损坏，无法恢复；请检查备份或重新上传。"
            ) from exc
        except Exception as exc:
            raise AppError(
                "restore_failed", "恢复未能完成，请检查回收目录权限与磁盘后重试。"
            ) from exc

    def _restore(self, document_id: str, *, require_compatible: bool) -> Document:
        if not re.fullmatch(r"[0-9a-f]{64}", document_id):
            raise AppError("invalid_document", "请提供完整的文档 SHA-256 ID。")
        with self._locked():
            path = self.root / "trash" / f"{document_id}.json"
            if not path.exists():
                raise AppError("not_found", "回收目录没有此文档。")
            document = Document.model_validate_json(path.read_bytes())
            self._raw_path(document)  # Validate metadata before using its extension.
            data = (self.root / "trash" / f"{document_id}{document.extension}").read_bytes()
            if hashlib.sha256(data).hexdigest() != document_id or document.id != document_id:
                raise AppError("original_corrupt", "回收原件与文档 ID 不符。")
            documents = self.repo.documents()
            if any(d.id == document_id for d in documents):
                raise AppError("document_exists", "文档已在目录中；请先检查其状态，未重复恢复。")
            chunks = self.repo.chunks()
            active = self.repo.get_state("active")
            old_vectors = None
            if chunks:
                if not active:
                    raise AppError("index_corrupt", "活动索引缺失，请先修复索引。")
                index = self.store.load(active, chunks, self.signature)
                old_vectors = index.reconstruct_n(0, index.ntotal)
            elif active:
                signature = self.signature if require_compatible else active.get("signature", "")
                self.store.load(active, chunks, signature)
            sections = parse_document(document.name, data, self.settings)
            current = split_sections(
                document.id,
                document.name,
                sections,
                self.settings.chunk_size,
                self.settings.chunk_overlap,
            )
            if fit := getattr(self.embedder, "fit_chunks", None):
                current = fit(current)
            vectors = self.embedder.encode_documents([c.text for c in current])
            if old_vectors is not None:
                if old_vectors.shape[1] != vectors.shape[1]:
                    raise AppError("dimension_mismatch", "恢复向量与活动索引维度不兼容。")
                vectors = np.concatenate([old_vectors, vectors])
            updated = self.store.prepare(chunks + current, vectors, self.signature)
            updated["limits"] = active.get("limits") if active else self._build_limits()
            restored = document.model_copy(
                update={"status": "ready", "error": None, "error_code": None}
            )
            raw_path = self._raw_path(document)
            existed = raw_path.exists()
            if existed and raw_path.read_bytes() != data:
                raise AppError("original_corrupt", "目标原件已存在且内容不同，未覆盖。")
            if not existed:
                self.store._durable_write(raw_path, data)
            try:
                self.repo.publish(documents + [restored], chunks + current, updated)
            except Exception:
                if not existed:
                    raw_path.unlink(missing_ok=True)
                raise
            self._snapshot_cache = None
            self._lexical_cache = None
            archive = self.root / "trash" / "restored" / updated["generation"]
            raw = self.root / "trash" / f"{document_id}{document.extension}"
            try:
                archive.mkdir(parents=True, exist_ok=True)
                path.replace(archive / path.name)
                raw.replace(archive / raw.name)
            except OSError:
                # The restored document is already committed; a stale trash copy is harmless.
                logger.warning("restore cleanup failed; trash copy retained")
            return restored

    def list_documents(self) -> list[Document]:
        with self._locked():
            return self.repo.documents()

    def visible_index_snapshot(self, *, load_vectors: bool = False) -> tuple[
        tuple[Document, ...], tuple[tuple[int, Chunk], ...], "IndexFlatIP | None"
    ]:
        """Read catalog, allowed chunk positions and immutable vectors from one generation."""
        with self._locked():
            chunks, active = self._snapshot()
            if chunks and not active:
                raise AppError("index_corrupt", "文本块存在但活动索引缺失，请重新索引。")
            limit = self.settings.spoiler_max_page
            by_id = {
                doc.id: doc for doc in self._source_catalog.values()
                if doc.status == "ready" and (
                    limit is None or doc.source_page is None or doc.source_page <= limit
                )
            }
            visible = tuple(
                (position, chunk) for position, chunk in enumerate(chunks)
                if chunk.document_id in by_id and self._spoiler_allowed(chunk)
            )
            included = {chunk.document_id for _, chunk in visible}
            documents = tuple(sorted(
                (by_id[identity] for identity in included),
                key=lambda doc: (doc.name.casefold(), doc.id),
            ))
            index = None
            if load_vectors and visible:
                index = self.store.load(active, chunks, self.signature)
            return documents, visible, index

    def assign_sources(self, metadata: dict[str, tuple[str, int]]) -> None:
        """Trusted metadata connects split source pages, independently of document instructions."""
        with self._locked():
            updates = []
            for document in self.repo.documents():
                if document.id in metadata:
                    group, page = metadata[document.id]
                    updates.append(
                        Document.model_validate(
                            {**document.model_dump(), "source_group": group, "source_page": page}
                        )
                    )
            for document in updates:
                self.repo.save_document(document)
            self._snapshot_cache = None

    def prune_indexes(self, keep: int = 2, *, apply: bool = False) -> list[str]:
        import shutil

        with self._locked():
            if keep < 1:
                raise AppError("invalid_retention", "至少保留一个索引版本。")
            active = self.repo.get_state("active") or {}
            if self.store.root.is_symlink() or self.store.root.is_junction():
                raise AppError("unsafe_index_path", "不能通过链接目录清理历史索引。")
            root = self.store.root.resolve()
            directories = sorted(root.iterdir(), key=lambda p: p.stat().st_mtime_ns, reverse=True)
            old = []
            protected = {active["generation"]} if active.get("generation") else set()
            for directory in directories:
                if (
                    not re.fullmatch(r"[0-9a-f]{32}", directory.name)
                    or directory.is_symlink()
                    or directory.is_junction()
                    or not directory.is_dir()
                    or directory.resolve().parent != root
                    or {p.name for p in directory.iterdir()} != {"manifest.json", "index.faiss"}
                ):
                    continue
                if directory.name in protected:
                    continue
                if len(protected) < keep:
                    protected.add(directory.name)
                    continue
                old.append(directory.name)
                if apply:
                    # Explicit maintenance only; retain current and unknown directories.
                    shutil.rmtree(directory.resolve())
            return old

    def _spoiler_allowed(self, chunk: Chunk) -> bool:
        limit = self.settings.spoiler_max_page
        if limit is None:
            return True
        page = chunk.page
        if page is None:
            pages = [int(value) for value in re.findall(r"〔p(\d{4,6})〕", chunk.text)]
            page = max(pages) if pages else None
        return page is None or page <= limit

    def verified_original(self, document: Document) -> bytes:
        path = self._raw_path(document)
        try:
            if path.stat().st_size != document.size:
                raise ValueError("Original size mismatch")
            if document.size > self.settings.max_upload_mb * 1024 * 1024:
                raise AppError("file_too_large", "原文档超过当前文件大小上限。")
            with path.open("rb") as stream:
                data = stream.read(document.size + 1)
            if len(data) != document.size or hashlib.sha256(data).hexdigest() != document.id:
                raise ValueError("Original digest mismatch")
        except (OSError, ValueError) as exc:
            raise AppError(
                "paper_original_missing", "原文档缺失或校验失败，请重新导入。",
            ) from exc
        return data

    def paper_evidence(
        self, question: str, *, previous_questions: tuple[str, ...] = (),
        previous_turns: tuple[ChatMessage, ...] = (),
    ) -> PaperEvidence | None:
        previous_turns = previous_turns or tuple(
            ChatMessage(role="user", content=text) for text in previous_questions
        )
        profile = paper_question(question, has_reference=True)
        table = table_lookup_requested(question)
        followup = material_followup(question) if previous_turns else None
        pointer = _TABLE_POINTER.search(question)
        labels = table_labels(question)
        if self.settings.spoiler_max_page is not None or not (
            profile or table or followup or pointer or (labels and table_lookup_intended(question))
        ):
            return None
        with self._locked():
            catalog = self.repo.documents()
            prior: _TableTurn = ("", [], False)
            reference = None
            if followup or pointer or labels:
                for previous in previous_turns[-6:]:
                    resolved = _resolve_table_turn(previous.content, catalog, prior)
                    reference = previous.table_reference
                    if reference is None:
                        prior = resolved or ("", [], False)
                        continue
                    documents = [
                        doc for doc in catalog
                        if doc.id == reference.document_id and doc.status == "ready"
                    ]
                    lookup = resolved[0] if resolved else resolve_documents(
                        previous.content, catalog,
                    )[2]
                    if not table_labels(lookup):
                        lookup += "\n" + reference.label
                    prior = (lookup, documents, not documents)
            resolved = _resolve_table_turn(question, catalog, prior)
            if reference is not None and prior[2] and (
                resolved is None or (resolved[2] and not resolved[1])
            ):
                message = "上一轮引用的论文当前不可用。请指定可用论文和表号。"
                return PaperEvidence(answer=message, reason=message)
            if resolved is None:
                message = (
                    "无法确定“该表”指的是哪张表。请明确论文文件名和表号。"
                    if pointer else
                    "无法核实这次追问沿用的表格条件。请明确论文文件名、表号和实验条件。"
                )
                return PaperEvidence(answer=message, reason=message)
            lookup_question, named, ambiguous = resolved
            table = table_lookup_requested(lookup_question)
            labels = table_labels(lookup_question)
            strict_table = bool(labels) and (
                bool(_TABLE_POINTER.search(lookup_question))
                or table_lookup_intended(lookup_question)
            )
            if not (
                table or followup or strict_table
                or paper_question(lookup_question, has_reference=True)
            ):
                return None
            if ambiguous:
                message = "同一个名称匹配到多份文档。请写明完整文件名，或重命名同名文档。"
                return PaperEvidence(answer=message, reason=message)
            if strict_table and (len(labels) != 1 or len(named) > 1):
                message = "请在一次表格查找中指定一份论文和一个表号。"
                return PaperEvidence(answer=message, reason=message)
            if len(named) > 2:
                message = "请在一次问题中指定一到两份文档。"
                return PaperEvidence(answer=message, reason=message)
            if any(doc.status != "ready" for doc in named):
                message = "所问文档尚未成功导入。请在知识库管理中检查索引状态。"
                return PaperEvidence(answer=message, reason=message)
            if table and not named:
                candidates = self._table_candidates(
                    catalog,
                    table_samples(lookup_question),
                    table_electrode_conditions(lookup_question),
                )
                try:
                    originals = [(doc, self.verified_original(doc)) for doc in candidates]
                except AppError:
                    if not strict_table:
                        return None  # Inferred sources do not make the ordinary RAG path fail.
                    originals = []
            elif strict_table and not table:
                originals = []  # A qualified lookup may be blocked before PDF extraction.
            else:
                originals = [(doc, self.verified_original(doc)) for doc in named]

        if table and len(named) <= 1:
            hits = [
                (doc, hit) for doc, data in originals
                if doc.extension == ".pdf"
                and (hit := inspect_pdf_table(
                    data, lookup_question, max_pages=self.settings.max_pdf_pages
                )) is not None
            ]
            if len(hits) > 1:
                message = "多份论文有符合条件的表格行，请在问题中指定一份论文的文件名。"
                return PaperEvidence(answer=message, reason=message)
            if hits:
                doc, hit = hits[0]
                if isinstance(hit, TableLookupAmbiguity):
                    where = "、".join(f"第 {page} 页 {label}" for label, page in hit.locations[:4])
                    choices = []
                    if len({label for label, _ in hit.locations}) > 1:
                        choices.append("表号")
                    if hit.measurements:
                        choices.append("指标口径（如" + "或".join(hit.measurements[:3]) + "）")
                    message = f"{doc.name} 的{where}有多种候选数据。请明确" + (
                        "和".join(choices) if choices else "所问的样品或实验条件"
                    ) + "后再查。"
                    return PaperEvidence(answer=message, reason=message)
                if (reference is not None and doc.id == reference.document_id
                        and hit.table_label == reference.label and hit.page != reference.page):
                    message = "无法核实上一轮引用的表格页码。请明确论文和表号。"
                    return PaperEvidence(answer=message, reason=message)
                source = page_source(doc, hit.page, hit.text, method="pdf_table")
                reason = f"已在 {doc.name} 第 {hit.page} 页的 {hit.table_label} 定位所问表格行。"
                return PaperEvidence([source], answer=hit.answer, reason=reason)

        if strict_table:
            # Whole-paper passages may contain the same metric in another table.
            location = f"{named[0].name} 的 {labels[0]}" if named else labels[0]
            message = f"未能从 {location} 核实所问数据。请检查表号、指标或实验条件。"
            return PaperEvidence(answer=message, reason=message)
        if not named:
            return None
        budget = PAPER_BUDGET // len(named)
        passages = [
            passage
            for doc, data in originals
            for passage in paper_passages(
                doc, parse_document(doc.name, data, self.settings), budget=budget
            )
        ]
        return PaperEvidence(passages)

    def _table_candidates(
        self, catalog: list[Document], samples: tuple[str, ...], electrodes: tuple[str, ...],
    ) -> list[Document]:
        ready = [doc for doc in catalog if doc.status == "ready" and doc.extension == ".pdf"]
        if len(ready) < 2:
            return ready
        if not (samples or electrodes):
            return []
        chunks, _ = self._snapshot()
        ready_ids = {doc.id for doc in ready}
        patterns = [_table_identifier(sample) for sample in samples]
        for label in electrodes:
            metal, _, condition = identifier_text(label).partition("/")
            metal, condition = metal.strip(), condition.strip().removesuffix("°c").strip()
            if condition == "as-cast":
                condition_pattern = r"as[\s-]+cast\b"
            else:
                condition_pattern = re.escape(condition) + r"(?!\d|\.\d)"
            patterns.append(re.compile(
                rf"(?<![a-z0-9]){re.escape(metal)}\s*/\s*{condition_pattern}",
            ))
        matches: dict[str, set[int]] = {}
        for chunk in chunks:
            if chunk.document_id in ready_ids:
                text = identifier_text(chunk.text)
                matches.setdefault(chunk.document_id, set()).update(
                    i for i, pattern in enumerate(patterns) if pattern.search(text)
                )
        candidates = [doc for doc in ready if len(matches.get(doc.id, ())) == len(patterns)]
        return candidates if len(candidates) <= 4 else []

    def retrieve(
        self, question: str, *, coverage_question: str | None = None
    ) -> list[RetrievalResult]:
        with self._locked():
            chunks, active = self._snapshot()
            if not chunks:
                if active:
                    self.store.load(active, chunks, active.get("signature", ""))
                return []
            if not active:
                raise AppError("index_corrupt", "文本块存在但活动索引缺失，请重新索引。")
            index = self.store.load(active, chunks, self.signature)
            original_question = coverage_question or question
            scoped: set[str] = set()
            if self.settings.spoiler_max_page is None and (
                paper_question(original_question, has_reference=True)
                or table_lookup_requested(original_question)
            ):
                named, ambiguous, _ = resolve_documents(
                    original_question, list(self._source_catalog.values()),
                )
                if ambiguous or len(named) > 2 or any(doc.status != "ready" for doc in named):
                    return []
                scoped = {doc.id for doc in named}
            expanded = expand_query(question, self.expansions)
            query = normalized(self.embedder.encode_query(expanded.text), 1)
            window = self.settings.retrieval_context_window
            pool = max(self.settings.top_k, self.settings.retrieval_candidate_pool)
            results = self.store.search_loaded(index, chunks, query, min(pool, len(chunks)))
            dense_scores = {chunk.id: score for chunk, score in results}
            chunk_indices = {chunk.id: index for index, chunk in enumerate(chunks)}
            dense_ranking = [chunk_indices[chunk.id] for chunk, _ in results]
            rankings = [dense_ranking[:pool]]
            if self._lexical_cache is None or self._lexical_cache[0] is not chunks:
                self._lexical_cache = (chunks, BM25Index([chunk.text for chunk in chunks]))
            lexical = self._lexical_cache[1]
            lexical_scores = lexical.scores(expanded.text)
            focus = coverage_focus(original_question, question)
            coverage = lexical.coverage_any(coverage_queries(focus, self.expansions))
            mixed_matches = lexical.mixed_identifier_matches(focus)
            lexical_ranking = sorted(
                (index for index, score in enumerate(lexical_scores) if score > 0),
                key=lambda index: (-lexical_scores[index], index),
            )[:pool]
            if lexical_ranking:
                rankings.append(lexical_ranking)
            fused = [
                index
                for index in reciprocal_rank_fusion(rankings)
                if self._spoiler_allowed(chunks[index])
                and (not scoped or chunks[index].document_id in scoped)
            ]
            # Retain a matching original when Chinese notes fill the initial top-k.
            # This path still requires the explicit compound/DOI and numeric constraints.
            anchored_pdfs = {
                i for i, matched in enumerate(mixed_matches)
                if matched and self._source_catalog[chunks[i].document_id].extension == ".pdf"
                and self._spoiler_allowed(chunks[i])
                and (not scoped or chunks[i].document_id in scoped)
            }
            limit = self.settings.top_k
            if anchored_pdfs and not anchored_pdfs.intersection(fused[:limit]):
                best = next((i for i in fused if i in anchored_pdfs), None)
                if best is None:
                    best = max(anchored_pdfs, key=lambda i: (lexical_scores[i], -i))
                fused = [*fused[:limit - 1], best, *(i for i in fused[limit - 1:] if i != best)]
            literature = (
                [i for i in ranked_literature_matches(focus, chunks, self._source_catalog)
                 if self._spoiler_allowed(chunks[i])]
                if not scoped and self.settings.spoiler_max_page is None else []
            )
            if literature:
                # A verified material/metric match gets a place ahead of generic
                # semantic hits. Keep other sources visible when the pool allows.
                rank_order = dict.fromkeys([*literature[:2], *fused, *literature[2:]])
                counts: Counter[str] = Counter()
                first, deferred = [], []
                for i in rank_order:
                    source_id = chunks[i].document_id
                    (first if counts[source_id] < 2 else deferred).append(i)
                    counts[source_id] += 1
                fused = first + deferred
            preparation = (
                ranked_preparation_matches(focus, chunks, self._source_catalog, limit=min(limit, 4))
                if not scoped and self.settings.spoiler_max_page is None else []
            )
            if preparation:
                fused = list(dict.fromkeys(preparation + fused))
            literature_matches = set(literature) | set(preparation)
            if scoped and len(fused) < limit:
                # A named note can rank outside both global candidate pools.
                # Prefer its locally matching passages and keep their real dense scores.
                seen = set(fused)
                remaining = sorted(
                    (i for i, chunk in enumerate(chunks)
                     if chunk.document_id in scoped and i not in seen),
                    key=lambda i: (-lexical_scores[i], i),
                )
                fused.extend(remaining[:limit - len(fused)])

            def lexical_passed(index: int) -> bool:
                return index in literature_matches or (
                    lexical_scores[index] > 0 and (
                        coverage[index] >= self.settings.lexical_min_coverage
                        or index in anchored_pdfs
                    )
                )
            # Lexical-only candidates may fall outside the dense candidate pool.
            chunks_by_document: dict[str, list[Chunk]] = {}
            chunks_by_ordinal: dict[tuple[str, int], Chunk] = {}
            for chunk in chunks:
                chunks_by_document.setdefault(chunk.document_id, []).append(chunk)
                chunks_by_ordinal[(chunk.document_id, chunk.ordinal)] = chunk

            def dense_score(chunk: Chunk) -> float:
                score = dense_scores.get(chunk.id)
                if score is None:
                    vector = index.reconstruct(int(chunk_indices[chunk.id]))
                    score = float(np.clip(np.dot(vector, query[0]), -1, 1))
                    dense_scores[chunk.id] = score
                return score

            primary = [
                RetrievalResult(
                    chunk=chunks[index],
                    score=dense_score(chunks[index]),
                    passed_threshold=(
                        dense_score(chunks[index]) >= self.settings.similarity_threshold
                    ),
                    lexical_score=lexical_scores[index],
                    original_page=self._source_catalog[chunks[index].document_id].source_page,
                    lexical_passed=lexical_passed(index),
                    retrieval_method="paper" if scoped else None,
                )
                for index in fused[: self.settings.top_k]
            ]
            if not window:
                return primary
            selected = {item.chunk.id: item for item in primary}
            neighborhoods = []
            context_limit = len(primary) + 20
            if question_type(coverage_question or question) == "quote":
                seeds = {}
                for seed in primary:
                    if seed.eligible:
                        seeds.setdefault(seed.chunk.document_id, seed)
                quote_candidates = [
                    (chunk, dense_score(chunk), seeds[document_id])
                    for document_id in seeds
                    for chunk in chunks_by_document.get(document_id, [])
                    if contains_quote(chunk.text)
                    and self._spoiler_allowed(chunk)
                    and (chunk.id not in selected or not selected[chunk.id].eligible)
                ]
                quote_candidates.sort(key=lambda item: (-item[1], item[0].id))
                for chunk, score, seed in quote_candidates[:QUOTE_CONTEXT_LIMIT]:
                    if chunk.id in selected:
                        selected[chunk.id].context_for = seed.chunk.id
                        continue
                    index = chunk_indices[chunk.id]
                    selected[chunk.id] = RetrievalResult(
                        chunk=chunk,
                        score=score,
                        passed_threshold=score >= self.settings.similarity_threshold,
                        context_for=seed.chunk.id,
                        original_page=self._source_catalog[chunk.document_id].source_page,
                        lexical_score=lexical_scores[index],
                        lexical_passed=lexical_passed(index),
                    )
            for seed in [item for item in primary if item.eligible]:
                neighbors = []
                for distance in range(1, window + 1):
                    for ordinal in (seed.chunk.ordinal - distance, seed.chunk.ordinal + distance):
                        neighbor = chunks_by_ordinal.get((seed.chunk.document_id, ordinal))
                        if (
                            neighbor is not None
                            and neighbor.id != seed.chunk.id
                            and self._spoiler_allowed(neighbor)
                        ):
                            neighbors.append((neighbor, dense_score(neighbor)))
                source = self._source_catalog[seed.chunk.document_id]
                if source.source_group is not None and source.source_page is not None:
                    adjacent = sorted(
                        (
                            d
                            for d in self._source_catalog.values()
                            if d.source_group == source.source_group
                            and (not scoped or d.id in scoped)
                            and d.source_page is not None
                            and 0 < abs(d.source_page - source.source_page) <= window
                        ),
                        key=lambda d: (
                            abs(d.source_page - source.source_page),
                            d.source_page,
                            d.id,
                        ),
                    )
                    for other in adjacent:
                        page_chunks = [
                            chunk
                            for chunk in chunks_by_document.get(other.id, [])
                            if self._spoiler_allowed(chunk)
                        ]
                        if page_chunks:
                            # One adjoining boundary, never an entire adjacent page document.
                            boundary = max if other.source_page < source.source_page else min
                            chunk = boundary(page_chunks, key=lambda item: item.ordinal)
                            neighbors.append((chunk, dense_score(chunk)))
                neighbors.sort(key=lambda pair: -pair[1])
                outside = []
                for chunk, score in neighbors:
                    if chunk.id in selected:
                        if not selected[chunk.id].eligible:
                            selected[chunk.id].context_for = seed.chunk.id
                    else:
                        outside.append((chunk, score))
                neighborhoods.append((seed, iter(outside)))
            # Diagnostic primary candidates do not consume the separate 20-neighbor budget.
            while neighborhoods and len(selected) < context_limit:
                remaining = []
                for seed, neighbors in neighborhoods:
                    for chunk, score in neighbors:
                        if chunk.id in selected:
                            if not selected[chunk.id].eligible:
                                selected[chunk.id].context_for = seed.chunk.id
                            continue
                        source = self._source_catalog[chunk.document_id]
                        selected[chunk.id] = RetrievalResult(
                            chunk=chunk,
                            score=score,
                            passed_threshold=score >= self.settings.similarity_threshold,
                            context_for=seed.chunk.id,
                            original_page=source.source_page,
                            lexical_score=lexical_scores[chunk_indices[chunk.id]],
                            lexical_passed=lexical_passed(chunk_indices[chunk.id]),
                        )
                        remaining.append((seed, neighbors))
                        break
                    if len(selected) >= context_limit:
                        break
                neighborhoods = remaining
            return list(selected.values())

    def stats(self) -> dict[str, Any]:
        with self._locked():
            chunks, active = self._snapshot()
            documents = list(self._source_catalog.values())
            status = "ready" if chunks else "empty"
            error = None
            if chunks or active:
                try:
                    signature = self.signature if chunks else active.get("signature", "")
                    self.store.load(active or {}, chunks, signature)
                except AppError as exc:
                    status = "incompatible" if exc.code == "index_incompatible" else "corrupt"
                    error = str(exc)
            return {
                "documents": len(documents),
                "chunks": len(chunks),
                "index_status": status,
                "embedding_model": self.settings.embedding_model,
                "error": error,
                "failed_documents": sum(d.status == "failed" for d in documents),
            }
