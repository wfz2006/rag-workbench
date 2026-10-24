from collections.abc import Callable, Iterator
from typing import Protocol

import numpy as np
from numpy.typing import NDArray

from rag_workbench.domain.models import ChatMessage

VectorArray = NDArray[np.float32]


class Embedder(Protocol):
    @property
    def signature(self) -> str: ...

    def encode_documents(self, texts: list[str]) -> VectorArray: ...

    def encode_query(self, text: str) -> VectorArray: ...


class ChatProvider(Protocol):
    def complete(
        self,
        system: str,
        user: str,
        *,
        json_mode: bool = False,
        history: list[ChatMessage] | None = None,
    ) -> str: ...

    def stream(
        self,
        system: str,
        user: str,
        *,
        json_mode: bool = False,
        history: list[ChatMessage] | None = None,
        cancellation: "Cancellation | None" = None,
    ) -> Iterator[str]: ...


class Cancellation(Protocol):
    def raise_if_cancelled(self) -> None: ...

    def add_cleanup(self, callback: Callable[[], None]) -> None: ...

    def remove_cleanup(self, callback: Callable[[], None]) -> None: ...
