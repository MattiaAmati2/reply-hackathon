from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Optional

from backend.schemas import (
    AlignedInterval,
    EvaluationReport,
    SessionMode,
    SessionRecord,
    SlideMetadata,
)


class DatabaseService:
    """SQLite persistence layer for Sparring sessions and Socratic Contraddittorio turns."""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    mode TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    pdf_path TEXT NOT NULL,
                    key_messages_json TEXT NOT NULL,
                    slides_json TEXT NOT NULL,
                    intervals_json TEXT NOT NULL DEFAULT '[]',
                    report_json TEXT,
                    annotated_pdf_path TEXT,
                    digest_path TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS contraddittorio_turns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    turn INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    refused INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.commit()

    def create_session(
        self,
        session_id: str,
        mode: SessionMode,
        filename: str,
        pdf_path: str,
        key_messages: list[str],
        slides: list[SlideMetadata],
    ) -> SessionRecord:
        created_at = datetime.now(timezone.utc).isoformat()
        slides_json = json.dumps([s.model_dump() for s in slides])
        key_messages_json = json.dumps(key_messages)

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO sessions (
                    session_id, mode, filename, pdf_path,
                    key_messages_json, slides_json, intervals_json,
                    report_json, annotated_pdf_path, digest_path, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, '[]', NULL, NULL, NULL, ?)
                """,
                (
                    session_id,
                    mode,
                    filename,
                    pdf_path,
                    key_messages_json,
                    slides_json,
                    created_at,
                ),
            )
            conn.commit()

        return SessionRecord(
            session_id=session_id,
            mode=mode,
            filename=filename,
            pdf_path=pdf_path,
            key_messages=key_messages,
            slides=slides,
            aligned_intervals=[],
            report=None,
            annotated_pdf_path=None,
            digest_path=None,
            created_at=created_at,
        )

    def get_session(self, session_id: str) -> Optional[SessionRecord]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()

        if row is None:
            return None

        slides = [SlideMetadata(**item) for item in json.loads(row["slides_json"])]
        intervals = [
            AlignedInterval(**item) for item in json.loads(row["intervals_json"])
        ]
        report = (
            EvaluationReport(**json.loads(row["report_json"]))
            if row["report_json"]
            else None
        )
        return SessionRecord(
            session_id=row["session_id"],
            mode=row["mode"],
            filename=row["filename"],
            pdf_path=row["pdf_path"],
            key_messages=json.loads(row["key_messages_json"]),
            slides=slides,
            aligned_intervals=intervals,
            report=report,
            annotated_pdf_path=row["annotated_pdf_path"],
            digest_path=row["digest_path"],
            created_at=row["created_at"],
        )

    def save_analysis(
        self,
        session_id: str,
        intervals: list[AlignedInterval],
        report: EvaluationReport,
        annotated_pdf_path: Optional[str],
        digest_path: Optional[str],
    ) -> None:
        intervals_json = json.dumps([iv.model_dump() for iv in intervals])
        report_json = json.dumps(report.model_dump())

        with self._connect() as conn:
            conn.execute(
                """
                UPDATE sessions
                SET intervals_json = ?,
                    report_json = ?,
                    annotated_pdf_path = ?,
                    digest_path = ?
                WHERE session_id = ?
                """,
                (
                    intervals_json,
                    report_json,
                    annotated_pdf_path,
                    digest_path,
                    session_id,
                ),
            )
            conn.commit()

    def get_chat_history(self, session_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, session_id, turn, role, content, refused, created_at
                FROM contraddittorio_turns
                WHERE session_id = ?
                ORDER BY id ASC
                """,
                (session_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_user_turn_count(self, session_id: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS cnt
                FROM contraddittorio_turns
                WHERE session_id = ? AND role = 'user' AND refused = 0
                """,
                (session_id,),
            ).fetchone()
        return int(row["cnt"]) if row else 0

    def append_chat_turn(
        self,
        session_id: str,
        turn: int,
        role: str,
        content: str,
        refused: bool = False,
    ) -> None:
        created_at = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO contraddittorio_turns (
                    session_id, turn, role, content, refused, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (session_id, turn, role, content, int(refused), created_at),
            )
            conn.commit()
