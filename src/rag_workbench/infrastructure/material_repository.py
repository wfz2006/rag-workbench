"""Independent, transactional authority for the materials workspace.

Originals and parsed evidence are immutable. Candidate ingestion is idempotent;
only a revision-checked review may change an existing record.
"""

import hashlib
import json
import os
import sqlite3
import tempfile
from collections.abc import Iterator
from contextlib import closing, contextmanager
from functools import wraps
from pathlib import Path
from typing import NamedTuple

from filelock import FileLock, Timeout
from pydantic import ValidationError

from rag_workbench.domain.errors import AppError
from rag_workbench.domain.materials import (
    EvidenceBlock,
    ExtractionRun,
    LiteratureDocument,
    MaterialRecord,
)
from rag_workbench.domain.models import utc_now


def _safe_io(method):
    @wraps(method)
    def wrapped(*args, **kwargs):
        try:
            return method(*args, **kwargs)
        except AppError:
            raise
        except Timeout as exc:
            raise AppError("materials_busy", "材料工作区正在写入，请稍后重试。") from exc
        except (OSError, sqlite3.Error) as exc:
            raise AppError(
                "materials_storage_error", "材料数据未能读写，请检查磁盘、权限或数据库占用。"
            ) from exc
        except (ValidationError, ValueError, TypeError) as exc:
            raise AppError(
                "invalid_material_data", "材料记录格式无效或数据已损坏，请检查后重试。"
            ) from exc
    return wrapped


class MaterialNameEvidence(NamedTuple):
    document_id: str
    material_name: str
    quote: str
    block: EvidenceBlock


class ApprovedConceptEvidence(NamedTuple):
    record_id: str
    document_id: str
    field: str
    value: str
    quote: str
    block: EvidenceBlock
    revision: int


class MaterialsRepository:
    SCHEMA_VERSION = 1

    @_safe_io
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.originals = self.root / "originals"
        self.originals.mkdir(exist_ok=True)
        self.path = self.root / "materials.sqlite3"
        with self._locked(), self.connect() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version > self.SCHEMA_VERSION:
                raise AppError(
                    "materials_schema_version", "材料数据库来自更新版本，请使用匹配软件。"
                )
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS literature_documents (
                    id TEXT PRIMARY KEY, payload TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS evidence_blocks (
                    id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL REFERENCES literature_documents(id),
                    ordinal INTEGER NOT NULL, payload TEXT NOT NULL,
                    UNIQUE(document_id, ordinal)
                );
                CREATE TABLE IF NOT EXISTS parse_artifacts (
                    document_id TEXT PRIMARY KEY REFERENCES literature_documents(id),
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS material_records (
                    id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL REFERENCES literature_documents(id),
                    payload TEXT NOT NULL, revision INTEGER NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS material_revisions (
                    record_id TEXT NOT NULL REFERENCES material_records(id),
                    revision INTEGER NOT NULL, payload TEXT NOT NULL, saved_at TEXT NOT NULL,
                    PRIMARY KEY(record_id, revision)
                );
                CREATE TABLE IF NOT EXISTS extraction_runs (
                    id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL REFERENCES literature_documents(id),
                    payload TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS material_records_document
                    ON material_records(document_id);
                CREATE INDEX IF NOT EXISTS extraction_runs_document
                    ON extraction_runs(document_id);
            """)
            db.execute(f"PRAGMA user_version={self.SCHEMA_VERSION}")

    def _locked(self):
        return FileLock(self.root / ".materials.lock", timeout=5)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA synchronous=FULL")
        try:
            with db:
                yield db
        finally:
            db.close()

    @_safe_io
    def save_document(
        self,
        document: LiteratureDocument,
        blocks: list[EvidenceBlock],
        data: bytes,
        *,
        parsed_json: str | None = None,
    ) -> LiteratureDocument:
        document = LiteratureDocument.model_validate(document.model_dump())
        blocks = [EvidenceBlock.model_validate(block.model_dump()) for block in blocks]
        if hashlib.sha256(data).hexdigest() != document.id or len(data) != document.size:
            raise AppError("material_source_mismatch", "原件内容与文献标识或大小不一致，未保存。")
        if not blocks or any(block.document_id != document.id for block in blocks):
            raise AppError("invalid_material_evidence", "文献没有有效证据块或证据归属不一致。")
        if len({block.id for block in blocks}) != len(blocks):
            raise AppError("invalid_material_evidence", "证据块标识重复，未保存。")
        if parsed_json is not None:
            try:
                if not isinstance(json.loads(parsed_json), dict):
                    raise ValueError("not an object")
            except (TypeError, ValueError) as exc:
                raise AppError(
                    "invalid_material_parse", "结构化解析结果不是有效 JSON 对象，未保存。"
                ) from exc
        target = self.originals / f"{document.id}{document.extension}"
        with self._locked():
            existing = self.document(document.id)
            if existing is not None:
                if (existing.role, existing.parent_id) != (document.role, document.parent_id):
                    raise AppError(
                        "material_document_conflict", "相同原件已登记为其他文献角色，未更改原记录。"
                    )
                # Existing evidence locators remain valid even if the parser changed.
                target = self.originals / f"{existing.id}{existing.extension}"
                if target.exists():
                    if hashlib.sha256(target.read_bytes()).hexdigest() != existing.id:
                        raise AppError(
                            "material_original_corrupt", "已存原件校验失败，请检查原件。"
                        )
                    return existing
                self._publish_original(target, data)
                return existing
            if document.parent_id is not None:
                parent = self.document(document.parent_id)
                if parent is None or parent.role != "main":
                    raise AppError(
                        "material_parent_missing", "请先导入对应主文献，再登记补充材料。"
                    )
            created_original = not target.exists()
            if target.exists():
                if target.read_bytes() != data:
                    raise AppError("material_original_corrupt", "原件路径已有不一致内容，未覆盖。")
            else:
                self._publish_original(target, data)
            try:
                with self.connect() as db:
                    db.execute(
                        "INSERT INTO literature_documents VALUES (?,?,?)",
                        (document.id, document.model_dump_json(), document.created_at),
                    )
                    db.executemany(
                        "INSERT INTO evidence_blocks VALUES (?,?,?,?)",
                        [
                            (b.id, b.document_id, i, b.model_dump_json())
                            for i, b in enumerate(blocks)
                        ],
                    )
                    if parsed_json is not None:
                        db.execute(
                            "INSERT INTO parse_artifacts VALUES (?,?)",
                            (document.id, parsed_json),
                        )
            except Exception:
                if created_original:
                    target.unlink(missing_ok=True)
                raise
        return document

    @staticmethod
    def _publish_original(target: Path, data: bytes) -> None:
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=target.parent, prefix=".original-", suffix=".tmp", delete=False
            ) as stream:
                temporary = Path(stream.name)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    @_safe_io
    def document(self, document_id: str) -> LiteratureDocument | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT payload FROM literature_documents WHERE id=?", (document_id,)
            ).fetchone()
        return LiteratureDocument.model_validate_json(row[0]) if row else None

    @_safe_io
    def documents(self) -> list[LiteratureDocument]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT payload FROM literature_documents ORDER BY created_at DESC, id DESC"
            ).fetchall()
        return [LiteratureDocument.model_validate_json(row[0]) for row in rows]

    @_safe_io
    def blocks(self, document_id: str) -> list[EvidenceBlock]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT payload FROM evidence_blocks WHERE document_id=? ORDER BY ordinal",
                (document_id,),
            ).fetchall()
        return [EvidenceBlock.model_validate_json(row[0]) for row in rows]

    @_safe_io
    def parse_artifact(self, document_id: str) -> str | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT payload FROM parse_artifacts WHERE document_id=?", (document_id,)
            ).fetchone()
        if row is None:
            return None
        if not isinstance(json.loads(row[0]), dict):
            raise AppError("invalid_material_parse", "结构化解析结果已损坏，请检查文献。")
        return row[0]

    @_safe_io
    def save_record(
        self, record: MaterialRecord, expected_revision: int | None = None
    ) -> MaterialRecord:
        record = MaterialRecord.model_validate(record.model_dump())
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT payload FROM material_records WHERE id=?", (record.id,)
            ).fetchone()
            old = MaterialRecord.model_validate_json(row[0]) if row else None
            if old is not None and old.document_id != record.document_id:
                raise AppError("material_document_conflict", "不能更改已有材料记录的文献归属。")
            if expected_revision is None and old is not None:
                return old
            if expected_revision is not None and (old is None or old.revision != expected_revision):
                raise AppError(
                    "material_revision_conflict", "此记录已更新，请刷新后重新审核，原修改未覆盖。"
                )
            if old is None and record.status != "candidate":
                raise AppError("invalid_material_state", "新记录必须先保存为待审核候选。")
            if not db.execute(
                "SELECT 1 FROM literature_documents WHERE id=?", (record.document_id,)
            ).fetchone():
                raise AppError("material_document_missing", "材料记录对应的文献不存在。")
            record = record.model_copy(update={
                "revision": old.revision + 1 if old else 1,
                "created_at": old.created_at if old else record.created_at,
                "updated_at": utc_now(),
            })
            db.execute(
                "INSERT INTO material_records VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET payload=excluded.payload, "
                "revision=excluded.revision, updated_at=excluded.updated_at",
                (record.id, record.document_id, record.model_dump_json(), record.revision,
                 record.created_at, record.updated_at),
            )
            db.execute(
                "INSERT INTO material_revisions VALUES (?,?,?,?)",
                (record.id, record.revision, record.model_dump_json(), utc_now()),
            )
        return record

    @_safe_io
    def record(self, record_id: str) -> MaterialRecord | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT payload FROM material_records WHERE id=?", (record_id,)
            ).fetchone()
        return MaterialRecord.model_validate_json(row[0]) if row else None

    @_safe_io
    def records(self, document_id: str | None = None) -> list[MaterialRecord]:
        query = "SELECT payload FROM material_records"
        params = ()
        if document_id is not None:
            query += " WHERE document_id=?"
            params = (document_id,)
        with self.connect() as db:
            rows = db.execute(query + " ORDER BY created_at, id", params).fetchall()
        return [MaterialRecord.model_validate_json(row[0]) for row in rows]

    @classmethod
    def approved_material_name_evidence(
        cls, root: Path, document_ids: set[str],
    ) -> tuple[MaterialNameEvidence, ...]:
        return tuple(
            MaterialNameEvidence(row.document_id, row.value, row.quote, row.block)
            for row in cls._approved_field_evidence(root, document_ids, ("material_name",))
        )

    @classmethod
    def approved_concept_evidence(
        cls, root: Path, document_ids: set[str],
    ) -> tuple[ApprovedConceptEvidence, ...]:
        return cls._approved_field_evidence(
            root, document_ids,
            (
                "material_name", "property_name", "process", "measurement_method",
                "deposition_method",
            ),
        )

    @classmethod
    @_safe_io
    def _approved_field_evidence(
        cls, root: Path, document_ids: set[str], fields: tuple[str, ...],
    ) -> tuple[ApprovedConceptEvidence, ...]:
        """Read approved fields with same-document evidence from an existing workspace."""
        path = root / "materials.sqlite3"
        if not document_ids or not path.is_file() or path.resolve() != path:
            return ()
        ids = sorted(document_ids)
        evidence = []
        field_parameters = ",".join("?" for _ in fields)
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)) as db:
            db.execute("BEGIN")
            if db.execute("PRAGMA user_version").fetchone()[0] > cls.SCHEMA_VERSION:
                raise AppError(
                    "materials_schema_version", "材料数据库来自更新版本，请使用匹配软件。"
                )
            for offset in range(0, len(ids), 400):
                batch = ids[offset:offset + 400]
                parameters = ",".join("?" for _ in batch)
                for record, identity, field, value, quote, payload, revision in db.execute(f"""
                    SELECT r.id, r.document_id, json_extract(ref.value, '$.field'),
                           json_extract(r.payload, '$.' || json_extract(ref.value, '$.field')),
                           json_extract(ref.value, '$.quote'), b.payload,
                           json_extract(r.payload, '$.revision')
                    FROM material_records r JOIN json_each(r.payload, '$.evidence') ref
                    JOIN evidence_blocks b ON b.id = json_extract(ref.value, '$.block_id')
                        AND b.document_id = r.document_id
                    WHERE r.document_id IN ({parameters})
                        AND json_extract(r.payload, '$.document_id') = r.document_id
                        AND json_extract(r.payload, '$.status') = 'approved'
                        AND json_extract(ref.value, '$.field') IN ({field_parameters})
                    ORDER BY r.document_id, r.id, ref.key
                """, (*batch, *fields)):
                    block = EvidenceBlock.model_validate_json(payload)
                    if (block.document_id == identity and isinstance(value, str) and value.strip()
                            and isinstance(quote, str) and quote.strip()):
                        evidence.append(ApprovedConceptEvidence(
                            record, identity, field, value, quote, block, revision or 1,
                        ))
        return tuple(evidence)

    @_safe_io
    def revisions(self, record_id: str) -> list[dict]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT payload FROM material_revisions WHERE record_id=? ORDER BY revision",
                (record_id,),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    @_safe_io
    def save_run(self, run: ExtractionRun) -> None:
        run = ExtractionRun.model_validate(run.model_dump())
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT payload FROM extraction_runs WHERE id=?", (run.id,)
            ).fetchone()
            if row is not None:
                old = ExtractionRun.model_validate_json(row[0])
                if old.document_id != run.document_id:
                    raise AppError("material_document_conflict", "不能更改已有任务的文献归属。")
                run = run.model_copy(update={"created_at": old.created_at})
            if not db.execute(
                "SELECT 1 FROM literature_documents WHERE id=?", (run.document_id,)
            ).fetchone():
                raise AppError("material_document_missing", "提取任务对应的文献不存在。")
            run = run.model_copy(update={"updated_at": utc_now()})
            db.execute(
                "INSERT INTO extraction_runs VALUES (?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                (run.id, run.document_id, run.model_dump_json(), run.created_at),
            )

    @_safe_io
    def runs(self, document_id: str | None = None) -> list[ExtractionRun]:
        query = "SELECT payload FROM extraction_runs"
        params = ()
        if document_id is not None:
            query += " WHERE document_id=?"
            params = (document_id,)
        with self.connect() as db:
            rows = db.execute(query + " ORDER BY created_at DESC, id DESC", params).fetchall()
        return [ExtractionRun.model_validate_json(row[0]) for row in rows]
