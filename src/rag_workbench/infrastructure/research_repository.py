import hashlib
import json
from uuid import uuid4

from rag_workbench.domain.errors import AppError
from rag_workbench.domain.models import utc_now
from rag_workbench.domain.research import STANCE_LABELS, EvidenceCard, EvidenceSource, Stance
from rag_workbench.infrastructure.repository import Repository


class ResearchRepository:
    def __init__(self, repository: Repository):
        self.repository = repository
        with repository.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS research_questions (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS research_claims (
                    id TEXT PRIMARY KEY, question_id TEXT NOT NULL REFERENCES
                        research_questions(id), text TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS research_cards (
                    id TEXT PRIMARY KEY, claim_id TEXT NOT NULL REFERENCES research_claims(id),
                    fingerprint TEXT NOT NULL, source TEXT NOT NULL,
                    stance TEXT NOT NULL DEFAULT 'pending', note TEXT NOT NULL DEFAULT '',
                    revision INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL,
                    UNIQUE(claim_id, fingerprint)
                );
            """)

    @staticmethod
    def _required(value: str, limit: int) -> str:
        value = value.strip()
        if not value or len(value) > limit:
            raise AppError("research_text", f"请输入 1 至 {limit} 字。")
        return value

    def questions(self) -> list[tuple[str, str]]:
        with self.repository.connect() as db:
            return [tuple(row) for row in db.execute(
                "SELECT id, title FROM research_questions ORDER BY rowid DESC"
            )]

    def create_question(self, title: str) -> str:
        with self.repository.connect() as db:
            return self._new_question(db, title)

    def _new_question(self, db, title: str) -> str:
        identity = uuid4().hex
        db.execute("INSERT INTO research_questions VALUES (?,?)",
                   (identity, self._required(title, 240)))
        return identity

    def add_claim(self, question_id: str, text: str) -> str:
        with self.repository.connect() as db:
            return self._new_claim(db, question_id, text)

    def _new_claim(self, db, question_id: str, text: str) -> str:
        identity = uuid4().hex
        db.execute("INSERT INTO research_claims VALUES (?,?,?)",
                   (identity, question_id, self._required(text, 2000)))
        return identity

    def board(self, question_id: str) -> tuple[str, list[tuple[str, str]], list[EvidenceCard]]:
        with self.repository.connect() as db:
            db.execute("BEGIN")
            question = db.execute(
                "SELECT title FROM research_questions WHERE id=?", (question_id,)
            ).fetchone()
            if question is None:
                raise AppError("research_question", "研究问题已不可用。")
            claims = db.execute("SELECT id, text FROM research_claims WHERE question_id=? "
                                "ORDER BY rowid", (question_id,))
            cards = db.execute("""
                SELECT c.id, c.claim_id, c.source, c.stance, c.note, c.revision, c.created_at
                FROM research_cards c JOIN research_claims s ON s.id=c.claim_id
                WHERE s.question_id=? ORDER BY c.rowid
            """, (question_id,))
            return question[0], [tuple(row) for row in claims], [
                EvidenceCard(**(dict(row) | {
                    "source": EvidenceSource.model_validate_json(row["source"]),
                })) for row in cards
            ]

    def rename_question(self, identity: str, title: str) -> None:
        with self.repository.connect() as db:
            db.execute("UPDATE research_questions SET title=? WHERE id=?",
                       (self._required(title, 240), identity))

    def edit_claim(self, identity: str, text: str) -> None:
        with self.repository.connect() as db:
            db.execute("UPDATE research_claims SET text=? WHERE id=?",
                       (self._required(text, 2000), identity))

    def capture(self, claim_id: str, source: EvidenceSource) -> tuple[str, bool]:
        with self.repository.connect() as db:
            return self._capture(db, claim_id, source)

    def capture_to(
        self, source: EvidenceSource, question_id: str | None, claim_id: str | None,
        title: str, claim: str,
    ) -> tuple[str, bool]:
        with self.repository.connect() as db:
            question = question_id or self._new_question(db, title)
            if claim_id and not db.execute(
                "SELECT 1 FROM research_claims WHERE id=? AND question_id=?", (claim_id, question)
            ).fetchone():
                raise AppError("research_claim", "所选主张已不可用。")
            return self._capture(db, claim_id or self._new_claim(db, question, claim), source)

    @staticmethod
    def _capture(db, claim_id: str, source: EvidenceSource) -> tuple[str, bool]:
        fingerprint = hashlib.sha256(json.dumps((
            source.document_id, source.page, source.mapped_page, source.locator,
            source.quote, source.record_id, source.record_revision, source.field,
        ), ensure_ascii=False).encode()).hexdigest()
        inserted = db.execute("""
            INSERT INTO research_cards (id, claim_id, fingerprint, source, created_at)
            VALUES (?,?,?,?,?) ON CONFLICT(claim_id, fingerprint) DO NOTHING
        """, (uuid4().hex, claim_id, fingerprint, source.model_dump_json(), utc_now()))
        card = db.execute("SELECT id FROM research_cards WHERE claim_id=? AND fingerprint=?",
                          (claim_id, fingerprint)).fetchone()
        return card[0], inserted.rowcount == 1

    def annotate(self, identity: str, expected_revision: int, stance: Stance, note: str) -> None:
        if stance not in STANCE_LABELS or len(note) > 10000:
            raise AppError("research_annotation", "标记无效或笔记超过 10000 字。")
        with self.repository.connect() as db:
            self._check_revision(db.execute("""
                UPDATE research_cards SET stance=?, note=?, revision=revision+1
                WHERE id=? AND revision=?
            """, (stance, note, identity, expected_revision)))

    def remove(self, identity: str, expected_revision: int) -> None:
        with self.repository.connect() as db:
            self._check_revision(db.execute(
                "DELETE FROM research_cards WHERE id=? AND revision=?",
                (identity, expected_revision),
            ))

    @staticmethod
    def _check_revision(cursor) -> None:
        if not cursor.rowcount:
            raise AppError("research_conflict", "证据卡已在其他窗口更新，请重新打开核对。")
