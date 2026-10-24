import hashlib
import json
import threading
from functools import lru_cache
from typing import Any

import numpy as np

from rag_workbench.config import Settings
from rag_workbench.domain.errors import AppError
from rag_workbench.domain.models import Chunk
from rag_workbench.domain.ports import VectorArray

_MODEL_LOCK = threading.RLock()


@lru_cache(maxsize=2)
def _load_model(name: str, revision: str, cache: str, device: str) -> Any:
    # This import and any download happen only on the first real embedding request.
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(
        name, revision=revision or None, cache_folder=cache, device=device, trust_remote_code=False
    )


class BgeEmbedder:
    def __init__(self, settings: Settings):
        self.settings = settings

    @property
    def signature(self) -> str:
        configuration = {
            "model": self.settings.embedding_model,
            "revision": self.settings.embedding_revision,
            "instruction": self.settings.query_instruction,
            "adapter": 2,
        }
        return hashlib.sha256(json.dumps(configuration, sort_keys=True).encode()).hexdigest()

    def _encode(self, texts: list[str]) -> VectorArray:
        try:
            with _MODEL_LOCK:
                model = _load_model(
                    self.settings.embedding_model,
                    self.settings.embedding_revision,
                    str(self.settings.model_cache.resolve()),
                    self.settings.embedding_device,
                )
                # Fail explicitly instead of indexing only a silently truncated prefix.
                for text in texts:
                    token_ids = model.tokenizer(text, truncation=False)["input_ids"]
                    if len(token_ids) > model.max_seq_length:
                        raise AppError(
                            "embedding_input_too_long",
                            f"文本超过模型 {model.max_seq_length} token 上限。请缩小分块后重建，"
                            "或缩短问题；系统未静默截断。",
                        )
                result = model.encode(
                    texts,
                    normalize_embeddings=True,
                    convert_to_numpy=True,
                    batch_size=32,
                    show_progress_bar=False,
                )
                return np.asarray(result, dtype=np.float32)
        except AppError:
            raise
        except Exception as exc:
            raise AppError(
                "embedding_unavailable",
                f"Embedding 加载或计算失败（{type(exc).__name__}）。"
                "请检查 Hugging Face 网络、缓存路径、模型名称和磁盘空间后重试。",
            ) from exc

    def encode_documents(self, texts: list[str]) -> VectorArray:
        return self._encode(texts)

    def encode_query(self, text: str) -> VectorArray:
        # Cover the whole query with token-safe windows; never silently drop its suffix.
        spans = self._token_spans(text, self.settings.query_instruction)
        vectors = self._encode([self.settings.query_instruction + text[a:b] for a, b in spans])
        return np.mean(vectors, axis=0, keepdims=True, dtype=np.float32)

    def _token_spans(self, text: str, prefix: str = "") -> list[tuple[int, int]]:
        try:
            with _MODEL_LOCK:
                model = _load_model(
                    self.settings.embedding_model,
                    self.settings.embedding_revision,
                    str(self.settings.model_cache.resolve()),
                    self.settings.embedding_device,
                )

                def fits(end: int, start: int) -> bool:
                    return (
                        len(
                            model.tokenizer(prefix + text[start:end], truncation=False)["input_ids"]
                        )
                        <= model.max_seq_length
                    )

                spans, start = [], 0
                while start < len(text):
                    low, high = start + 1, len(text)
                    if not fits(low, start):
                        raise AppError(
                            "embedding_input_too_long",
                            "检索指令或单个字符超过模型上限，请检查模型配置。",
                        )
                    while low < high:
                        middle = (low + high + 1) // 2
                        if fits(middle, start):
                            low = middle
                        else:
                            high = middle - 1
                    spans.append((start, low))
                    if low == len(text):
                        break
                    start = low - min(self.settings.chunk_overlap, (low - start) // 4)
                return spans
        except AppError:
            raise
        except Exception as exc:
            raise AppError(
                "embedding_unavailable",
                f"Embedding tokenizer 加载失败（{type(exc).__name__}），请检查模型缓存与网络。",
            ) from exc

    def fit_chunks(self, chunks: list[Chunk]) -> list[Chunk]:
        fitted = []
        for chunk in chunks:
            spans = self._token_spans(chunk.text)
            for start, end in spans:
                update = {"ordinal": len(fitted)}
                if start != 0 or end != len(chunk.text):
                    update.update(
                        text=chunk.text[start:end],
                        start=chunk.start + start,
                        end=chunk.start + end,
                        id=hashlib.sha256(
                            f"{chunk.id}:{start}:{end}:token-v2".encode()
                        ).hexdigest(),
                    )
                fitted.append(chunk.model_copy(update=update))
        return fitted
