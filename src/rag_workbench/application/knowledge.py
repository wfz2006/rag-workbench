import hashlib
import json
import logging
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import numpy as np
from filelock import FileLock, Timeout
from pydantic import ValidationError

from rag_workbench.application.query_features import contains_quote, question_type
from rag_workbench.application.retrieval_expansion import (
    coverage_focus,
    coverage_queries,
    expand_query,
    load_retrieval_expansions,
)
from rag_workbench.config import Settings
from rag_workbench.domain.errors import AppError
from rag_workbench.domain.models import Chunk, Document, ImportFailure, RetrievalResult
from rag_workbench.domain.ports import Embedder, VectorArray
from rag_workbench.infrastructure.chunking import split_sections
from rag_workbench.infrastructure.lexical import BM25Index, reciprocal_rank_fusion
from rag_workbench.infrastructure.parsing import (
    ALLOWED_EXTENSIONS,
    parse_document,
    safe_display_name,
)
from rag_workbench.infrastructure.repository import Repository
from rag_workbench.infrastructure.vector_store import FaissStore, normalized

QUOTE_CONTEXT_LIMIT = 4

logger = logging.getLogger(__name__)


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
        }

    def _raw_path(self, document: Document) -> Path:
        if not re.fullmatch(r"[0-9a-f]{64}", document.id) or document.extension not in {
            ".txt",
            ".md",
            ".pdf",
        }:
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
                            raise AppError("unsupported_type", "仅支持 TXT、Markdown 和 PDF。")
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

    def delete(self, document_id: str) -> None:
        try:
            with self._locked():
                documents = self.repo.documents()
                document = next((d for d in documents if d.id == document_id), None)
                if not document:
                    raise AppError("not_found", "文档不存在或已删除。")
                chunks = self.repo.chunks()
                indexed = any(c.document_id == document_id for c in chunks)
                active = self.repo.get_state("active") or {}
                if indexed:
                    kept, updated = self.store.without_document(active, chunks, document_id)
                    updated["limits"] = active.get("limits")
                    self.repo.publish([d for d in documents if d.id != document_id], kept, updated)
                else:
                    self.repo.delete_unindexed_document(document_id)
                self._snapshot_cache = None
                self._lexical_cache = None
                try:
                    self._archive_original(document)
                except Exception as exc:
                    self._rollback_delete(document, documents, chunks, active, indexed)
                    raise AppError(
                        "delete_failed",
                        "删除未完成，文档已保留；请确认回收目录可写且原件未被占用后重试。",
                    ) from exc
        except AppError:
            raise
        except Exception as exc:
            raise AppError(
                "delete_failed", "删除未能完整完成，请刷新列表检查；原件仍保留。"
            ) from exc

    def _rollback_delete(
        self,
        document: Document,
        documents: list[Document],
        chunks: list[Chunk],
        active: dict,
        indexed: bool,
    ) -> None:
        try:
            if indexed:
                self.repo.publish(documents, chunks, active)
            else:
                self.repo.save_document(document)
        except Exception as exc:
            raise AppError(
                "delete_failed",
                "删除未完成且状态回滚失败；请刷新列表并检查数据库、索引与原件。",
            ) from exc
        finally:
            # Metadata is written before the move; a failed move leaves it behind.
            (self.root / "trash" / f"{document.id}.json").unlink(missing_ok=True)
            self._snapshot_cache = None
            self._lexical_cache = None

    def _archive_original(self, document: Document) -> None:
        target = self.root / "trash" / f"{document.id}{document.extension}"
        metadata = self.root / "trash" / f"{document.id}.json"
        self.store._durable_write(metadata, document.model_dump_json().encode())
        path = self._raw_path(document)
        if path.exists():
            path.replace(target)

    def restore(self, document_id: str) -> Document:
        try:
            return self._restore(document_id)
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

    def _restore(self, document_id: str) -> Document:
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
                self.store.load(active, chunks, active.get("signature", ""))
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
            coverage = lexical.coverage_any(
                coverage_queries(
                    coverage_focus(coverage_question or question, question), self.expansions
                )
            )
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
            ]
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
                    lexical_passed=(
                        lexical_scores[index] > 0
                        and coverage[index] >= self.settings.lexical_min_coverage
                    ),
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
                        lexical_passed=lexical_scores[index] > 0
                        and coverage[index] >= self.settings.lexical_min_coverage,
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
                            lexical_passed=(
                                lexical_scores[chunk_indices[chunk.id]] > 0
                                and coverage[chunk_indices[chunk.id]]
                                >= self.settings.lexical_min_coverage
                            ),
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
