"""Durable local editor drafts, separate from approved records and revision history."""

import json
import math
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from functools import wraps
from pathlib import Path
from time import time_ns

from rag_workbench.domain.errors import AppError
from rag_workbench.domain.models import utc_now

MAX_VALUES_BYTES = 1024 * 1024


@dataclass(frozen=True)
class DraftSnapshot:
    key: str
    values: dict
    document_id: str
    kind: str
    record_id: str | None
    base_revision: int | None
    version: int
    updated_at: str


def _safe_io(method):
    @wraps(method)
    def wrapped(*args, **kwargs):
        try:
            return method(*args, **kwargs)
        except AppError:
            raise
        except (OSError, sqlite3.Error) as exc:
            raise AppError(
                "material_draft_storage_error",
                "本地草稿未能读写，请检查磁盘、权限或数据库占用；当前编辑内容未主动丢弃。",
            ) from exc
        except (ValueError, TypeError, OverflowError, RecursionError) as exc:
            raise AppError(
                "material_draft_invalid", "草稿内容或元数据无效，请检查后重试。",
            ) from exc
    return wrapped


def _text(value, maximum: int) -> None:
    if (
        not isinstance(value, str) or not value.strip() or len(value) > maximum
        or any(ord(char) < 32 for char in value)
    ):
        raise ValueError("invalid text")


def _number(value, minimum: int) -> None:
    if type(value) is not int or value < minimum:
        raise ValueError("invalid version")


def _json_value(value, depth: int = 0) -> None:
    if depth > 64:
        raise ValueError("nested too deeply")
    if value is None or type(value) in {str, bool, int}:
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("non-finite number")
        return
    if type(value) is list:
        for child in value:
            _json_value(child, depth + 1)
        return
    if type(value) is dict:
        for key, child in value.items():
            if not isinstance(key, str):
                raise ValueError("JSON keys must be strings")
            _json_value(child, depth + 1)
        return
    raise ValueError("not a JSON value")


def _values_json(values: dict) -> str:
    if type(values) is not dict:
        raise ValueError("draft values must be an object")
    _json_value(values)
    payload = json.dumps(values, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    if len(payload.encode("utf-8")) > MAX_VALUES_BYTES:
        raise ValueError("draft is too large")
    return payload


def _metadata(key, document_id, kind, record_id, base_revision) -> None:
    _text(key, 512)
    _text(document_id, 128)
    _text(kind, 40)
    if record_id is not None:
        _text(record_id, 128)
    if base_revision is not None:
        _number(base_revision, 1)


def _snapshot(row: sqlite3.Row) -> DraftSnapshot:
    _metadata(row["key"], row["document_id"], row["kind"], row["record_id"], row["base_revision"])
    _number(row["version"], 1)
    _text(row["updated_at"], 80)
    values = json.loads(row["values_json"])
    _values_json(values)
    return DraftSnapshot(
        key=row["key"], values=values, document_id=row["document_id"], kind=row["kind"],
        record_id=row["record_id"], base_revision=row["base_revision"],
        version=row["version"], updated_at=row["updated_at"],
    )


def _conflict() -> AppError:
    return AppError(
        "material_draft_conflict",
        "本地草稿已被其他页面修改或移除，未覆盖较新内容；请先核对已有草稿。",
    )


class MaterialDraftStore:
    SCHEMA_VERSION = 2

    @_safe_io
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "drafts.sqlite3"
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("BEGIN IMMEDIATE")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version > self.SCHEMA_VERSION:
                raise AppError(
                    "material_draft_schema_version", "草稿数据库来自更新版本，请使用匹配的软件。",
                )
            db.execute("""
                CREATE TABLE IF NOT EXISTS drafts (
                    key TEXT PRIMARY KEY, values_json TEXT NOT NULL,
                    document_id TEXT NOT NULL, kind TEXT NOT NULL, record_id TEXT,
                    base_revision INTEGER, version INTEGER NOT NULL CHECK(version >= 1),
                    updated_at TEXT NOT NULL
                )
            """)
            db.execute("CREATE INDEX IF NOT EXISTS drafts_document ON drafts(document_id)")
            clock_exists = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='draft_version_clock'",
            ).fetchone()
            missing_clock = clock_exists is None
            if not missing_clock:
                missing_clock = db.execute(
                    "SELECT 1 FROM draft_version_clock WHERE id=1",
                ).fetchone() is None
            if version == self.SCHEMA_VERSION and missing_clock:
                raise AppError(
                    "material_draft_storage_error",
                    "草稿版本记录已损坏，未重置；请先保留数据库副本。",
                )
            db.execute("""
                CREATE TABLE IF NOT EXISTS draft_version_clock (
                    id INTEGER PRIMARY KEY CHECK(id=1), value INTEGER NOT NULL CHECK(value >= 0)
                )
            """)
            # Legacy per-key versions left no tombstones after deletion. Start a
            # new generation well above those historic counters during migration.
            floor = time_ns() if version == 1 else 0
            maximum = db.execute("SELECT COALESCE(MAX(version), 0) FROM drafts").fetchone()[0]
            floor = max(floor, maximum)
            db.execute(
                "INSERT OR IGNORE INTO draft_version_clock VALUES (1, ?)", (floor,),
            )
            db.execute(
                "UPDATE draft_version_clock SET value=MAX(value, ?) WHERE id=1", (floor,),
            )
            db.execute(f"PRAGMA user_version={self.SCHEMA_VERSION}")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA synchronous=FULL")
            with db:
                yield db
        finally:
            db.close()

    @_safe_io
    def save(
        self, key: str, values: dict, *, document_id: str, kind: str,
        record_id: str | None = None, base_revision: int | None = None,
        expected_version: int = 0,
    ) -> DraftSnapshot:
        _metadata(key, document_id, kind, record_id, base_revision)
        _number(expected_version, 0)
        payload = _values_json(values)
        now = utc_now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT * FROM drafts WHERE key=?", (key,)).fetchone()
            if (existing is None and expected_version != 0) or (
                existing is not None and existing["version"] != expected_version
            ):
                raise _conflict()
            current_version = db.execute(
                "SELECT value FROM draft_version_clock WHERE id=1",
            ).fetchone()
            if current_version is None:
                raise AppError("material_draft_storage_error", "草稿版本记录已损坏，未保存。")
            _number(current_version[0], 0)
            next_version = current_version[0] + 1
            if existing is None:
                db.execute(
                    "INSERT INTO drafts VALUES (?,?,?,?,?,?,?,?)",
                    (key, payload, document_id, kind, record_id, base_revision, next_version, now),
                )
            else:
                identity = (document_id, kind, record_id, base_revision)
                identity_fields = ("document_id", "kind", "record_id", "base_revision")
                current_identity = tuple(
                    existing[field] for field in identity_fields
                )
                if identity != current_identity:
                    raise ValueError("draft source identity changed")
                db.execute(
                    "UPDATE drafts SET values_json=?, version=?, updated_at=? WHERE key=?",
                    (payload, next_version, now, key),
                )
            db.execute("UPDATE draft_version_clock SET value=? WHERE id=1", (next_version,))
            row = db.execute("SELECT * FROM drafts WHERE key=?", (key,)).fetchone()
            return _snapshot(row)

    @_safe_io
    def get(self, key: str) -> DraftSnapshot | None:
        _text(key, 512)
        with self._connect() as db:
            row = db.execute("SELECT * FROM drafts WHERE key=?", (key,)).fetchone()
        return _snapshot(row) if row else None

    @_safe_io
    def list(self, document_id: str | None = None) -> list[DraftSnapshot]:
        if document_id is not None:
            _text(document_id, 128)
        with self._connect() as db:
            if document_id is None:
                rows = db.execute("SELECT * FROM drafts ORDER BY updated_at DESC, key").fetchall()
            else:
                rows = db.execute(
                    "SELECT * FROM drafts WHERE document_id=? ORDER BY updated_at DESC, key",
                    (document_id,),
                ).fetchall()
        return [_snapshot(row) for row in rows]

    @_safe_io
    def delete(self, key: str, *, expected_version: int) -> None:
        _text(key, 512)
        _number(expected_version, 1)
        with self._connect() as db:
            deleted = db.execute(
                "DELETE FROM drafts WHERE key=? AND version=?", (key, expected_version),
            ).rowcount
            if deleted != 1:
                raise _conflict()
