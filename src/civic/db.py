"""SQLite schema and repository functions. Raw SQL, no ORM.

Migrations are plain SQL scripts applied in order at startup; the applied
version is tracked in ``schema_migrations`` so re-running is a no-op. Later
phases append to ``MIGRATIONS`` — existing entries are never edited, so any
database can be brought forward from any version.
"""

import sqlite3
from pathlib import Path

MIGRATIONS: list[str] = [
    # 001 — initial schema: source documents and agent trace steps.
    """
    CREATE TABLE documents (
        id           TEXT PRIMARY KEY,
        city         TEXT NOT NULL,
        url          TEXT NOT NULL,
        local_path   TEXT,
        title        TEXT,
        doc_type     TEXT NOT NULL CHECK (doc_type IN ('agenda', 'minutes', 'budget')),
        meeting_date TEXT,
        fiscal_year  INTEGER,
        sha256       TEXT,
        fetched_at   TEXT
    );

    CREATE INDEX idx_documents_city ON documents (city);
    CREATE INDEX idx_documents_doc_type ON documents (doc_type);

    CREATE TABLE agent_steps (
        run_id              TEXT NOT NULL,
        step_index          INTEGER NOT NULL,
        tool_name           TEXT NOT NULL,
        tool_input          TEXT NOT NULL,
        tool_output_summary TEXT NOT NULL,
        latency_ms          INTEGER NOT NULL,
        input_tokens        INTEGER NOT NULL,
        output_tokens       INTEGER NOT NULL,
        PRIMARY KEY (run_id, step_index)
    );
    """,
]


def connect(db_path: Path) -> sqlite3.Connection:
    """Open the database, creating it and applying pending migrations if needed.

    This is the single entry point every command uses, which is what guarantees
    "migrations applied on startup".
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    apply_migrations(conn)
    return conn


def apply_migrations(conn: sqlite3.Connection) -> None:
    """Apply any migrations newer than the recorded schema version."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version    INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    row = conn.execute("SELECT MAX(version) AS v FROM schema_migrations").fetchone()
    current = row["v"] or 0
    for version, script in enumerate(MIGRATIONS, start=1):
        if version <= current:
            continue
        conn.executescript(script)
        conn.execute("INSERT INTO schema_migrations (version) VALUES (?)", (version,))
    conn.commit()


def schema_version(conn: sqlite3.Connection) -> int:
    """Highest applied migration version (0 for a fresh database)."""
    row = conn.execute("SELECT MAX(version) AS v FROM schema_migrations").fetchone()
    return int(row["v"] or 0)


# --- documents repository --------------------------------------------------
#
# Plain functions over a connection, so callers control the transaction. Dates
# and datetimes are stored as ISO-8601 TEXT (SQLite has no native date type).

from datetime import date, datetime  # noqa: E402  (kept next to its users)

from civic.models import Document  # noqa: E402


def _iso(value: date | datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def upsert_document(conn: sqlite3.Connection, doc: Document) -> None:
    """Insert or replace a document by primary key. Idempotent so a re-run of
    ingestion over the same cache does not create duplicates."""
    conn.execute(
        """
        INSERT INTO documents
            (id, city, url, local_path, title, doc_type, meeting_date,
             fiscal_year, sha256, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            city=excluded.city, url=excluded.url, local_path=excluded.local_path,
            title=excluded.title, doc_type=excluded.doc_type,
            meeting_date=excluded.meeting_date, fiscal_year=excluded.fiscal_year,
            sha256=excluded.sha256, fetched_at=excluded.fetched_at
        """,
        (
            doc.id, doc.city, doc.url, doc.local_path, doc.title, doc.doc_type,
            _iso(doc.meeting_date), doc.fiscal_year, doc.sha256, _iso(doc.fetched_at),
        ),
    )


def get_document(conn: sqlite3.Connection, doc_id: str) -> Document | None:
    row = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
    return Document.model_validate(dict(row)) if row else None


def list_documents(conn: sqlite3.Connection, city: str | None = None) -> list[Document]:
    if city is None:
        rows = conn.execute("SELECT * FROM documents ORDER BY city, meeting_date").fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM documents WHERE city = ? ORDER BY meeting_date", (city,)
        ).fetchall()
    return [Document.model_validate(dict(r)) for r in rows]


def count_by_city_and_type(conn: sqlite3.Connection) -> list[tuple[str, str, int]]:
    """Return ``(city, doc_type, count)`` rows for the ingest summary table."""
    rows = conn.execute(
        """
        SELECT city, doc_type, COUNT(*) AS n
        FROM documents GROUP BY city, doc_type ORDER BY city, doc_type
        """
    ).fetchall()
    return [(r["city"], r["doc_type"], r["n"]) for r in rows]
