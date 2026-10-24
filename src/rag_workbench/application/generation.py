"""A cancellable local turn. No Streamlit calls or paid-provider assumptions."""

from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from threading import Event, Lock, Thread
from time import monotonic
from typing import Self

from rag_workbench.domain.errors import AppError
from rag_workbench.domain.models import AnswerResult, GenerationEvent, Inspection, utc_now


class CancellationToken:
    def __init__(self) -> None:
        self._event = Event()
        self._lock = Lock()
        self._cleanups: list[Callable[[], None]] = []

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise AppError("cancelled", "已停止生成。本轮未完成的片段未作为完整回答保存。")

    def cancel(self) -> None:
        with self._lock:
            self._event.set()
            callbacks, self._cleanups = self._cleanups, []
        for callback in callbacks:
            # Closing an already-disconnected HTTP stream is harmless.
            with suppress(Exception):
                callback()

    def add_cleanup(self, callback: Callable[[], None]) -> None:
        with self._lock:
            if not self.cancelled:
                self._cleanups.append(callback)
                return
        with suppress(Exception):
            callback()
        self.raise_if_cancelled()

    def remove_cleanup(self, callback: Callable[[], None]) -> None:
        with self._lock:
            if callback in self._cleanups:
                self._cleanups.remove(callback)


def cancelled_result(question: str) -> AnswerResult:
    message = "已停止生成。本轮未完成的片段未作为完整回答保存。"
    return AnswerResult(
        answer=message,
        mode="error",
        error_code="cancelled",
        debug=Inspection(question=question, route="error", reason=message, error_code="cancelled"),
    )


@dataclass(frozen=True)
class GenerationSnapshot:
    question: str
    text: str
    phase: str
    done: bool
    result: AnswerResult | None
    elapsed: float
    created_at: str = ""


class GenerationJob:
    def __init__(
        self,
        question: str,
        work: Callable[[Callable[[GenerationEvent], None], CancellationToken], AnswerResult],
        save: Callable[[AnswerResult], None],
        *,
        replaces_assistant_id: int | None = None,
    ) -> None:
        self.question, self._work, self._save = question, work, save
        self.replaces_assistant_id = replaces_assistant_id
        self.created_at = utc_now()
        self._token = CancellationToken()
        self._lock = Lock()
        self._pieces: list[str] = []
        self._phase = "retrieving"
        self._done = False
        self._result: AnswerResult | None = None
        self._started = monotonic()
        self._finished: float | None = None
        self._thread = Thread(target=self._run, name="rag-answer", daemon=True)

    def start(self) -> Self:
        self._thread.start()
        return self

    def _publish(self, event: GenerationEvent) -> None:
        with self._lock:
            if self._token.cancelled or self._done or self._phase in {"stopping", "saving"}:
                return
            if event.kind == "phase" and event.phase:
                self._phase = event.phase
            elif event.kind == "delta":
                self._pieces.append(event.text)

    def _run(self) -> None:
        try:
            result = self._work(self._publish, self._token)
        except Exception:
            result = AnswerResult(
                answer="回答服务发生异常，请稍后重试。",
                mode="error",
                error_code="internal_error",
                debug=Inspection(
                    question=self.question, route="error", error_code="internal_error"
                ),
            )
        # Entering saving is the commit boundary. Never hold the state lock across disk I/O.
        with self._lock:
            if self._token.cancelled or self._phase == "stopping":
                result = cancelled_result(self.question)
            self._phase = "saving"
        try:
            self._save(result)
        except Exception:
            result = result.model_copy(
                update={
                    "error_code": "persistence_error",
                    "persistence_error": "本轮记录未能保存，请检查数据库与磁盘后重试。",
                }
            )
        with self._lock:
            self._result, self._done, self._phase = result, True, "done"
            self._finished = monotonic()

    def cancel(self) -> bool:
        with self._lock:
            if self._done or self._phase == "saving":
                return False
            self._phase = "stopping"
        self._token.cancel()
        return True

    def snapshot(self) -> GenerationSnapshot:
        with self._lock:
            return GenerationSnapshot(
                self.question,
                "".join(self._pieces),
                self._phase,
                self._done,
                self._result,
                (self._finished or monotonic()) - self._started,
                self.created_at,
            )

    def wait(self, timeout: float = 5) -> None:
        self._thread.join(timeout)
