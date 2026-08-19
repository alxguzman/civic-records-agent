"""Parser for CivicEngage calendar listing pages.

Both Downey and El Segundo publish meetings through CivicEngage's calendar
component, which renders one HTML table row per meeting. Each row carries the
meeting title, its date, and zero or more agenda/minutes PDF links of the form
``/home/showpublisheddocument/{id}``. Parsing the listing directly means one
request per page of results instead of one per meeting.

This module is pure parsing — no network — so it can be unit-tested against a
saved fixture, as the coding standards require.
"""

from datetime import date, datetime
from urllib.parse import urljoin

import structlog
from bs4 import BeautifulSoup, Tag

from civic.config import CityConfig
from civic.ingest.base import CityAdapter, DocumentRef
from civic.ingest.fetcher import PoliteFetcher
from civic.models import DocType

log = structlog.get_logger()


def _row_meeting_date(row: Tag) -> date | None:
    """Read the local meeting date from an event_datetime cell.

    We use the visible ``MM/DD/YYYY`` text rather than the ``<time>`` element's
    ISO value: the ISO value is UTC and, for evening meetings, rolls over to the
    next calendar day.
    """
    cell = row.find("td", class_="event_datetime")
    if cell is None:
        return None
    text = cell.get_text(" ", strip=True)
    token = text.split(" ", 1)[0]
    try:
        return datetime.strptime(token, "%m/%d/%Y").date()
    except ValueError:
        return None


def _row_title(row: Tag) -> str | None:
    summary = row.find("span", attrs={"itemprop": "summary"})
    if summary is not None:
        return summary.get_text(strip=True)
    link = row.find("a", attrs={"itemprop": "url"})
    return link.get_text(strip=True) if link else None


def _doc_type_from_label(label: str) -> DocType:
    """Map a CivicEngage 'Agenda:' / 'Minutes:' label to a doc_type."""
    return "minutes" if "minute" in label.lower() else "agenda"


def parse_listing(html: str, *, base_url: str, city: str) -> list[DocumentRef]:
    """Extract every agenda/minutes ``DocumentRef`` on one listing page.

    Date-window filtering and pagination stop-logic live in the adapter so that
    this function stays a pure, easily-tested HTML→refs transform. Rows with no
    document links (a scheduled meeting with nothing published) yield nothing.
    """
    soup = BeautifulSoup(html, "lxml")
    refs: list[DocumentRef] = []
    for link in soup.select("a.agenda_minutes_link"):
        href = link.get("href")
        if not href:
            continue
        row = link.find_parent("tr")
        if row is None:
            continue
        label_el = link.find_previous("span", class_="agenda-minutes-label")
        doc_type = _doc_type_from_label(label_el.get_text() if label_el else "agenda")
        refs.append(
            DocumentRef(
                city=city,
                url=urljoin(base_url, href),
                title=_row_title(row) or link.get_text(strip=True),
                doc_type=doc_type,
                meeting_date=_row_meeting_date(row),
            )
        )
    return refs


def listing_page_url(base_url: str, listing_path: str, page: int) -> str:
    """Build a listing URL for a given page of *past* meetings.

    CivicEngage accepts both ``?toggle=allpast&npage=N`` query params and
    "friendly" dash segments (``/-toggle-allpast/-npage-N``). Only the friendly
    form is used: the query form trips these cities' WAF (verified 2026-08-18 —
    identical requests returned 403 with query params and 200 with segments).
    """
    url = f"{base_url}{listing_path}/-toggle-allpast"
    return url if page <= 1 else f"{url}/-npage-{page}"


class CivicEngageAdapter(CityAdapter):
    """Discovery for a CivicEngage city: budgets from config, agendas/minutes by
    walking the calendar listing back to the ``ingest_since`` cutoff."""

    def __init__(self, config: CityConfig, fetcher: PoliteFetcher) -> None:
        super().__init__(config)
        self.fetcher = fetcher

    def discover_documents(self) -> list[DocumentRef]:
        refs: list[DocumentRef] = list(self._budget_refs())
        if self.config.calendar is not None:
            refs.extend(self._calendar_refs())
        else:
            log.info("discover.no_calendar", city=self.slug)
        return refs

    def _budget_refs(self) -> list[DocumentRef]:
        return [
            DocumentRef(
                city=self.slug, url=b.url, title=b.title,
                doc_type="budget", fiscal_year=b.fiscal_year,
            )
            for b in self.config.budgets
        ]

    def _calendar_refs(self) -> list[DocumentRef]:
        cal = self.config.calendar
        assert cal is not None
        since = self.config.ingest_since
        seen: set[str] = set()
        collected: list[DocumentRef] = []
        for page in range(1, cal.max_pages + 1):
            url = listing_page_url(cal.base_url, cal.listing_path, page)
            status, html = self.fetcher.fetch_text(url)
            if status != 200 or not html:
                log.warning("discover.listing_unavailable", city=self.slug,
                            page=page, status=status)
                break
            page_refs = parse_listing(html, base_url=cal.base_url, city=self.slug)
            if not page_refs:
                log.info("discover.listing_end", city=self.slug, page=page)
                break
            in_window = [r for r in page_refs if r.meeting_date and r.meeting_date >= since]
            for ref in in_window:
                if ref.url not in seen:
                    seen.add(ref.url)
                    collected.append(ref)
            # Stop once a page has walked entirely past the window into older meetings.
            if page_refs and not in_window:
                log.info("discover.reached_window_edge", city=self.slug, page=page)
                break
        log.info("discover.calendar_done", city=self.slug, found=len(collected))
        return collected
