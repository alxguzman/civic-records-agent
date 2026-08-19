from datetime import date, datetime
from pathlib import Path

from civic import db
from civic.models import Document


def _doc(doc_id: str, city: str, doc_type: str, **kw) -> Document:
    return Document(id=doc_id, city=city, url=f"https://x/{doc_id}", doc_type=doc_type, **kw)


def test_upsert_is_idempotent_and_updates(tmp_path: Path) -> None:
    conn = db.connect(tmp_path / "civic.db")
    db.upsert_document(conn, _doc("a", "downey", "agenda", title="v1"))
    db.upsert_document(conn, _doc("a", "downey", "agenda", title="v2"))  # same id
    conn.commit()

    docs = db.list_documents(conn, city="downey")
    assert len(docs) == 1
    assert docs[0].title == "v2"
    conn.close()


def test_dates_round_trip(tmp_path: Path) -> None:
    conn = db.connect(tmp_path / "civic.db")
    when = datetime(2024, 3, 1, 12, 0, 0)
    db.upsert_document(
        conn,
        _doc("b", "el_segundo", "budget", meeting_date=date(2024, 3, 1),
             fiscal_year=2024, fetched_at=when, sha256="deadbeef"),
    )
    conn.commit()

    got = db.get_document(conn, "b")
    assert got is not None
    assert got.meeting_date == date(2024, 3, 1)
    assert got.fiscal_year == 2024
    assert got.fetched_at == when
    conn.close()


def test_count_by_city_and_type(tmp_path: Path) -> None:
    conn = db.connect(tmp_path / "civic.db")
    for i, (city, dt) in enumerate(
        [("downey", "agenda"), ("downey", "agenda"), ("downey", "budget"),
         ("el_segundo", "minutes")]
    ):
        db.upsert_document(conn, _doc(f"d{i}", city, dt))
    conn.commit()

    counts = db.count_by_city_and_type(conn)
    assert ("downey", "agenda", 2) in counts
    assert ("downey", "budget", 1) in counts
    assert ("el_segundo", "minutes", 1) in counts
    conn.close()
