"""Immutable, checksummed FAISS generations. No pickle or implicit positional business IDs."""

import hashlib
import json
import os
import re
import uuid
from pathlib import Path

import faiss
import numpy as np

from rag_workbench.domain.errors import AppError
from rag_workbench.domain.models import Chunk
from rag_workbench.domain.ports import VectorArray


def digest_chunks(chunks: list[Chunk]) -> str:
    return hashlib.sha256("\n".join(c.model_dump_json() for c in chunks).encode()).hexdigest()


def normalized(vectors: VectorArray, rows: int | None = None) -> VectorArray:
    values = np.array(vectors, dtype=np.float32, order="C", copy=True)
    if (
        values.ndim != 2
        or values.shape[1] < 1
        or not np.isfinite(values).all()
        or (rows is not None and values.shape[0] != rows)
    ):
        raise AppError("invalid_vectors", "向量形状或数值无效，请检查 Embedding Provider。")
    if values.shape[0] and np.any(np.linalg.norm(values, axis=1) < 1e-12):
        raise AppError("invalid_vectors", "Embedding 返回零向量，无法构建余弦索引。")
    faiss.normalize_L2(values)
    return values


class FaissStore:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self._cache = None

    def prepare(self, chunks: list[Chunk], vectors: VectorArray, signature: str) -> dict:
        values = normalized(vectors, len(chunks))
        generation = uuid.uuid4().hex
        directory = self.root / generation
        directory.mkdir()
        index = faiss.IndexFlatIP(values.shape[1])
        if len(values):
            index.add(values)
        # serialize + Python file handles avoid Windows non-ASCII path limitations in FAISS.
        index_bytes = faiss.serialize_index(index).tobytes()
        manifest = {
            "version": 1,
            "signature": signature,
            "dimension": index.d,
            "count": index.ntotal,
            "chunk_ids": [c.id for c in chunks],
            "chunks_sha256": digest_chunks(chunks),
            "index_sha256": hashlib.sha256(index_bytes).hexdigest(),
        }
        self._durable_write(directory / "index.faiss", index_bytes)
        payload = json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode()
        self._durable_write(directory / "manifest.json", payload)
        return {
            "generation": generation,
            "signature": signature,
            "count": len(chunks),
            "manifest_sha256": hashlib.sha256(payload).hexdigest(),
        }

    @staticmethod
    def _durable_write(path: Path, data: bytes) -> None:
        temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("xb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def load(self, active: dict, chunks: list[Chunk], signature: str) -> faiss.IndexFlatIP:
        try:
            generation = active["generation"]
            if not re.fullmatch(r"[0-9a-f]{32}", generation):
                raise ValueError("invalid generation")
            directory = self.root / generation
            stamp = tuple(
                (p.stat().st_mtime_ns, p.stat().st_size, p.stat().st_ctime_ns)
                for p in [directory / "manifest.json", directory / "index.faiss"]
            )
            key = (json.dumps(active, sort_keys=True), signature, stamp)
            if self._cache is not None and self._cache[0] == key and self._cache[1] is chunks:
                return self._cache[2]
            payload = (directory / "manifest.json").read_bytes()
            if hashlib.sha256(payload).hexdigest() != active["manifest_sha256"]:
                raise ValueError("manifest checksum mismatch")
            manifest = json.loads(payload)
            if active["signature"] != signature or manifest["signature"] != signature:
                raise AppError("index_incompatible", "索引与模型或分块设置不兼容，请重新索引。")
            if (
                manifest["version"] != 1
                or manifest["chunk_ids"] != [c.id for c in chunks]
                or manifest["chunks_sha256"] != digest_chunks(chunks)
                or active["count"] != len(chunks)
            ):
                raise ValueError("metadata mismatch")
            data = (directory / "index.faiss").read_bytes()
            if hashlib.sha256(data).hexdigest() != manifest["index_sha256"]:
                raise ValueError("index checksum mismatch")
            index = faiss.deserialize_index(np.frombuffer(data, dtype=np.uint8).copy())
            if (
                index.d != manifest["dimension"]
                or index.ntotal != len(chunks)
                or index.ntotal != manifest["count"]
                or not isinstance(index, faiss.IndexFlatIP)
                or index.metric_type != faiss.METRIC_INNER_PRODUCT
            ):
                raise ValueError("index dimension or type mismatch")
            self._cache = (key, chunks, index)
            return index
        except AppError:
            raise
        except Exception as exc:
            raise AppError(
                "index_corrupt", "索引缺失、损坏或与元数据不一致，请重新建立索引。"
            ) from exc

    def search(
        self,
        active: dict,
        chunks: list[Chunk],
        signature: str,
        query: VectorArray,
        top_k: int,
    ) -> list[tuple[Chunk, float]]:
        index = self.load(active, chunks, signature)
        return self.search_loaded(index, chunks, query, top_k)

    def without_document(
        self, active: dict, chunks: list[Chunk], document_id: str
    ) -> tuple[list[Chunk], dict]:
        """Rebuild a filtered snapshot from validated vectors, without raw files/model loading."""
        signature = active.get("signature", "")
        index = self.load(active, chunks, signature)
        positions = [i for i, chunk in enumerate(chunks) if chunk.document_id != document_id]
        kept = [chunks[i] for i in positions]
        vectors = index.reconstruct_n(0, index.ntotal)[positions]
        return kept, self.prepare(kept, vectors, signature)

    @staticmethod
    def search_loaded(
        index: faiss.IndexFlatIP, chunks: list[Chunk], query: VectorArray, top_k: int
    ) -> list[tuple[Chunk, float]]:
        """Search a snapshot already validated by load, without validating it a second time."""
        values = normalized(query, 1)
        if values.shape[1] != index.d:
            raise AppError("dimension_mismatch", "查询向量维度与索引不一致，请检查模型并重新索引。")
        scores, positions = index.search(values, min(top_k, len(chunks)))
        return [
            (chunks[int(i)], float(np.clip(score, -1, 1)))
            for i, score in zip(positions[0], scores[0], strict=True)
            if i >= 0
        ]
