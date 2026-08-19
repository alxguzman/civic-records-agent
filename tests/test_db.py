from pathlib import Path

from civic import db


def _table_names(conn) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {r["name"] for r in rows}


def test_connect_creates_schema(tmp_path: Path) -> None:
    conn = db.connect(tmp_path / "civic.db")
    assert {"schema_migrations", "documents", "agent_steps"} <= _table_names(conn)
    assert db.schema_version(conn) == len(db.MIGRATIONS)
    conn.close()


def test_migrations_are_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "civic.db"
    db.connect(path).close()
    conn = db.connect(path)  # would raise if migrations re-ran
    assert db.schema_version(conn) == len(db.MIGRATIONS)
    conn.close()
