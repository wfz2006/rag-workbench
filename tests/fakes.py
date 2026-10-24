import json

import numpy as np

from rag_workbench.domain.errors import AppError


class FakeEmbedder:
    """Hand-designed test vectors, never used by the application runtime."""

    signature = "test-embedding-v1"
    fail = False

    def encode_documents(self, texts):
        if self.fail:
            raise AppError("embedding_unavailable", "测试模型不可用")
        return np.array([[1.0, 0.0] if "保管" in t else [0.0, 1.0] for t in texts], np.float32)

    def encode_query(self, text):
        return self.encode_documents([text])


class FakeProvider:
    def __init__(self, *, answerable=True, invalid_id=False, failure=None, malformed=False):
        self.answerable = answerable
        self.invalid_id = invalid_id
        self.failure = failure
        self.malformed = malformed
        self.calls = []

    def stream(self, system, user, *, json_mode=False, history=None, cancellation=None):
        if cancellation:
            cancellation.raise_if_cancelled()
        yield self.complete(system, user, json_mode=json_mode, history=history)

    def complete(self, system, user, *, json_mode=False, history=None):
        self.calls.append((system, user, history))
        if self.failure:
            raise AppError(self.failure, "测试服务异常")
        if self.malformed:
            return "not JSON"
        if not json_mode:
            return "我无法确认你们内部的安排，请向负责人核实。"
        payload = json.loads(user)
        chunk_id = "invented" if self.invalid_id else payload["evidence"][0]["chunk_id"]
        if "supporting_chunk_ids" in system:
            return json.dumps(
                {
                    "answerable": self.answerable,
                    "reason": "材料包含保管期限。"
                    if self.answerable
                    else "资料谈及归档，但未包含问题所问的安排。",
                    "supporting_chunk_ids": [chunk_id] if self.answerable else [],
                }
            )
        return json.dumps({"claims": [{"text": "保管期限为三年。", "chunk_ids": [chunk_id]}]})
