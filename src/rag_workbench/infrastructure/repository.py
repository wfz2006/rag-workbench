"""SQLite authority for document catalog, current chunks, settings and conversations."""

import json
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from rag_workbench.domain.errors import AppError
from rag_workbench.domain.models import AnswerResult, Chunk, Document, utc_now


class Repository:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            if db.execute("PRAGMA user_version").fetchone()[0] > 2:
                raise AppError("schema_version", "数据库来自更新版本，请使用匹配的软件。")
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS documents (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS chunks (
                    id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id),
                    position INTEGER NOT NULL UNIQUE, payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, created_at TEXT NOT NULL,
                    updated_at TEXT
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    role TEXT NOT NULL, content TEXT NOT NULL, result TEXT, created_at TEXT NOT NULL
                );
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(sessions)")}
            if "updated_at" not in columns:
                db.execute("ALTER TABLE sessions ADD COLUMN updated_at TEXT")
                db.execute("UPDATE sessions SET updated_at=created_at WHERE updated_at IS NULL")
            epoch = db.execute(
                "SELECT payload FROM state WHERE key='knowledge_epoch'"
            ).fetchone()
            if epoch is None:
                db.execute(
                    "INSERT INTO state VALUES ('knowledge_epoch',?)",
                    (json.dumps(uuid.uuid4().hex),),
                )
            else:
                try:
                    json.loads(epoch[0])
                except (TypeError, ValueError):
                    db.execute(
                        "UPDATE state SET payload=? WHERE key='knowledge_epoch'",
                        (json.dumps(uuid.uuid4().hex),),
                    )
            db.execute("PRAGMA user_version=2")

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA synchronous=FULL")
        try:
            with db:
                yield db
        finally:
            db.close()

    def documents(self) -> list[Document]:
        with self.connect() as db:
            return [
                Document.model_validate_json(r[0])
                for r in db.execute("SELECT payload FROM documents ORDER BY id")
            ]

    def save_document(self, document: Document) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO documents VALUES (?,?)",
                (document.id, document.model_dump_json()),
            )
            self._bump_knowledge_epoch(db)

    def delete_unindexed_document(self, document_id: str) -> None:
        with self.connect() as db:
            db.execute(
                "DELETE FROM documents WHERE id=? AND NOT EXISTS "
                "(SELECT 1 FROM chunks WHERE document_id=?)",
                (document_id, document_id),
            )
            self._bump_knowledge_epoch(db)

    @staticmethod
    def _bump_knowledge_epoch(db: sqlite3.Connection) -> None:
        db.execute(
            "INSERT OR REPLACE INTO state VALUES ('knowledge_epoch',?)",
            (json.dumps(uuid.uuid4().hex),),
        )

    def chunks(self) -> list[Chunk]:
        with self.connect() as db:
            return [
                Chunk.model_validate_json(r[0])
                for r in db.execute("SELECT payload FROM chunks ORDER BY position")
            ]

    def get_state(self, key: str) -> Any:
        with self.connect() as db:
            row = db.execute("SELECT payload FROM state WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def set_state(self, key: str, value: Any) -> None:
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO state VALUES (?,?)", (key, json.dumps(value)))

    def publish(self, documents: list[Document], chunks: list[Chunk], active: dict) -> None:
        """Single commit switches the entire searchable snapshot."""
        with self.connect() as db:
            db.execute("DELETE FROM chunks")
            db.execute("DELETE FROM documents")
            db.executemany(
                "INSERT INTO documents VALUES (?,?)",
                [(d.id, d.model_dump_json()) for d in documents],
            )
            db.executemany(
                "INSERT INTO chunks VALUES (?,?,?,?)",
                [(c.id, c.document_id, i, c.model_dump_json()) for i, c in enumerate(chunks)],
            )
            db.execute("INSERT OR REPLACE INTO state VALUES ('active',?)", (json.dumps(active),))
            self._bump_knowledge_epoch(db)

    def new_session(self) -> str:
        identity = uuid.uuid4().hex
        now = utc_now()
        with self.connect() as db:
            db.execute(
                "INSERT INTO sessions VALUES (?,?,?,?)", (identity, "新会话", now, now)
            )
        return identity

    def sessions(self) -> list[dict]:
        with self.connect() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT s.*, (SELECT COUNT(*) FROM messages m WHERE m.session_id=s.id) "
                    "AS message_count, EXISTS(SELECT 1 FROM state "
                    "WHERE key='session_title:' || s.id) "
                    "AS custom_title, (SELECT MIN(id) FROM messages WHERE session_id=s.id "
                    "AND role='assistant') AS first_assistant_id "
                    "FROM sessions s ORDER BY COALESCE(s.updated_at, s.created_at) DESC"
                )
            ]

    def empty_session(self, exclude: set[str] | None = None) -> str | None:
        with self.connect() as db:
            rows = db.execute(
                "SELECT id FROM sessions s WHERE NOT EXISTS "
                "(SELECT 1 FROM messages m WHERE m.session_id=s.id) "
                "AND s.title='新会话' AND NOT EXISTS "
                "(SELECT 1 FROM state WHERE key='session_title:' || s.id) "
                "ORDER BY created_at DESC"
            ).fetchall()
        return next((row[0] for row in rows if row[0] not in (exclude or set())), None)

    def rename_session(self, identity: str, title: str) -> None:
        title = title.strip()
        if not title or len(title) > 80:
            raise AppError("invalid_title", "会话名称需为 1–80 个字符。")
        with self.connect() as db:
            db.execute(
                "UPDATE sessions SET title=?, updated_at=? WHERE id=?",
                (title, utc_now(), identity),
            )
            db.execute(
                "INSERT OR REPLACE INTO state VALUES (?, 'true')", (f"session_title:{identity}",)
            )

    def delete_session(self, identity: str) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM sessions WHERE id=?", (identity,))
            db.execute("DELETE FROM state WHERE key=?", (f"session_title:{identity}",))

    def messages(self, session_id: str) -> list[dict]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM messages WHERE session_id=? ORDER BY id", (session_id,)
            )
            return [dict(row) for row in rows]

    def delete_failed_turn(self, session_id: str, assistant_id: int) -> None:
        """Remove only a failed pair; successful history is protected."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            question, _ = self._failed_pair(db, session_id, assistant_id)
            first = db.execute(
                "SELECT MIN(id) FROM messages WHERE session_id=? AND role='user'", (session_id,)
            ).fetchone()[0]
            db.execute(
                "DELETE FROM messages WHERE session_id=? AND id IN (?,?)",
                (session_id, question["id"], assistant_id),
            )
            remaining = db.execute(
                "SELECT content FROM messages WHERE session_id=? AND role='user' "
                "ORDER BY id LIMIT 1",
                (session_id,),
            ).fetchone()
            if first == question["id"]:
                self._retitle(
                    db, session_id, question["content"], remaining[0] if remaining else "新会话"
                )
            db.execute("UPDATE sessions SET updated_at=? WHERE id=?", (utc_now(), session_id))

    @staticmethod
    def _failed_pair(db, session_id: str, assistant_id: int):
        answer = db.execute(
            "SELECT * FROM messages WHERE session_id=? AND id=? AND role='assistant'",
            (session_id, assistant_id),
        ).fetchone()
        if not answer or AnswerResult.model_validate_json(answer["result"]).mode != "error":
            raise AppError("invalid_turn", "只能对失败回合执行此操作。")
        question = db.execute(
            "SELECT * FROM messages WHERE session_id=? AND id<? ORDER BY id DESC LIMIT 1",
            (session_id, assistant_id),
        ).fetchone()
        if not question or question["role"] != "user":
            raise AppError("invalid_turn", "消息配对不完整，未执行操作。")
        return question, answer

    @staticmethod
    def _retitle(db, session_id: str, old_question: str, new_question: str) -> None:
        db.execute(
            "UPDATE sessions SET title=? WHERE id=? AND title=? "
            "AND NOT EXISTS(SELECT 1 FROM state WHERE key=?)",
            (new_question[:80], session_id, old_question[:80], f"session_title:{session_id}"),
        )

    def validate_retry(self, session_id: str, assistant_id: int) -> int:
        with self.connect() as db:
            question, _ = self._failed_pair(db, session_id, assistant_id)
            return question["id"]

    def replace_failed_turn(
        self, session_id: str, assistant_id: int, question: str, result: AnswerResult
    ) -> None:
        """Commit a retry in place; retain the original pair if validation/writing fails."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            user, previous = self._failed_pair(db, session_id, assistant_id)
            updated = db.execute(
                "UPDATE messages SET content=?,result=? WHERE id=? AND session_id=? AND result=?",
                (
                    result.answer,
                    result.model_dump_json(),
                    assistant_id,
                    session_id,
                    previous["result"],
                ),
            )
            if updated.rowcount != 1:
                raise AppError("invalid_turn", "原失败回合已变化，请刷新后重试。")
            db.execute("UPDATE messages SET content=? WHERE id=?", (question, user["id"]))
            first = db.execute(
                "SELECT MIN(id) FROM messages WHERE session_id=? AND role='user'", (session_id,)
            ).fetchone()[0]
            if first == user["id"]:
                self._retitle(db, session_id, user["content"], question)
            db.execute("UPDATE sessions SET updated_at=? WHERE id=?", (utc_now(), session_id))

    def save_turn(self, session_id: str, question: str, result: AnswerResult) -> None:
        with self.connect() as db:
            now = utc_now()
            db.execute(
                "UPDATE sessions SET title=?, updated_at=? WHERE id=? AND title='新会话' "
                "AND NOT EXISTS(SELECT 1 FROM messages WHERE session_id=?) "
                "AND NOT EXISTS(SELECT 1 FROM state WHERE key=?)",
                (question[:80], now, session_id, session_id, f"session_title:{session_id}"),
            )
            db.execute("UPDATE sessions SET updated_at=? WHERE id=?", (now, session_id))
            db.executemany(
                "INSERT INTO messages (session_id,role,content,result,created_at) "
                "VALUES (?,?,?,?,?)",
                [
                    (session_id, "user", question, None, now),
                    (session_id, "assistant", result.answer, result.model_dump_json(), now),
                ],
            )
