"""Serialize local pack review/evaluation writes and replace JSON atomically."""

import json
import os
import tempfile
from contextlib import contextmanager
from functools import wraps
from pathlib import Path

from filelock import FileLock, Timeout

from rag_workbench.domain.errors import AppError


@contextmanager
def pack_operation_lock(pack_dir: Path):
    root = Path(pack_dir)
    if not root.is_dir():
        raise AppError("pack_missing", "知识包目录不存在。")
    try:
        with FileLock(root / ".review.lock", timeout=0):
            yield
    except Timeout as exc:
        raise AppError("pack_busy", "该知识包正在审核、冻结或评测，请稍后重试。") from exc


def locked_pack_operation(position: int = 0):
    """position identifies pack_dir in static functions or instance methods."""

    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            pack_dir = kwargs["pack_dir"] if "pack_dir" in kwargs else args[position]
            with pack_operation_lock(pack_dir):
                return function(*args, **kwargs)

        return wrapped

    return decorate


def write_json_atomic(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
