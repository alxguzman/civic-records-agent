from datetime import date
from pathlib import Path

from civic.ingest.civicengage import listing_page_url, parse_listing

FIXTURE = Path(__file__).parent / "fixtures" / "el_segundo_listing.html"
BASE = "https://www.elsegundo.gov"


def _refs():
    html = FIXTURE.read_text(encoding="utf-8")
    return parse_listing(html, base_url=BASE, city="el_segundo")


def test_parse_listing_finds_documents() -> None:
    refs = _refs()
    assert refs, "fixture should yield at least one document"
    for ref in refs:
        assert ref.city == "el_segundo"
        assert ref.url.startswith(f"{BASE}/home/showpublisheddocument/")
        assert ref.doc_type in ("agenda", "minutes")
        assert ref.title


def test_parse_listing_dates_and_titles() -> None:
    refs = _refs()
    # The fixture was captured 2026-08-18; the council meeting that day links
    # an English and a Spanish agenda.
    council = [r for r in refs if r.meeting_date == date(2026, 8, 18)]
    assert len(council) >= 2
    assert all(r.doc_type == "agenda" for r in council)
    assert all("City Council" in (r.title or "") for r in council)


def test_listing_page_url_uses_friendly_segments() -> None:
    url1 = listing_page_url(BASE, "/gov/agendas", 1)
    assert url1 == f"{BASE}/gov/agendas/-toggle-allpast"
    url3 = listing_page_url(BASE, "/gov/agendas", 3)
    assert url3 == f"{BASE}/gov/agendas/-toggle-allpast/-npage-3"
    assert "?" not in url3  # query form trips the cities' WAF
