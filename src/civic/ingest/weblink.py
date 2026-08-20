"""Downey's public Laserfiche WebLink archive.

Downey publishes council agendas through AgendaLink, whose PDF endpoints are
gated behind a token embedded in the client bundle (out of scope by policy).
Its public Laserfiche WebLink repository is the token-free alternative: an
anonymous JSON folder-listing service exposes a tree of

    Agendas & Reports / {year} / {MM-DD-YY meeting} / {documents}

where each meeting folder's packet holds the agenda and the approved minutes of
a prior meeting. This module walks that tree into ``DocumentRef``s.

Two operational realities, both handled rather than worked around:
* The listing host's TLS chain needs the OS trust store (see ``make_client``).
* Anonymous access draws on a small shared license pool; when it is exhausted
  the service returns HTTP 500 with a "number of sessions" message. Those are
  retried with backoff and are not treated as hard failures.
"""

import re
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import httpx
import structlog

from civic.config import CityConfig
from civic.ingest.base import CityAdapter, DocumentRef, DownloadError
from civic.ingest.fetcher import PoliteFetcher, make_client

log = structlog.get_logger()

_YEAR_RE = re.compile(r"^(19|20)\d{2}$")
_MEETING_DATE_RE = re.compile(r"(\d{1,2})-(\d{1,2})-(\d{2})")
_SESSION_LIMIT = "number of sessions has reached"


@dataclass
class WebLinkEntry:
    name: str
    entry_id: int
    is_folder: bool
    extension: str


class SessionLimit(Exception):
    """The WebLink anonymous license pool is momentarily exhausted."""


class WebLinkClient:
    """Thin wrapper over the anonymous FolderListingService, with polite pacing
    and retry on the shared-session-limit 500."""

    def __init__(
        self,
        base_url: str,
        repo: str,
        *,
        client: httpx.Client | None = None,
        min_interval: float = 2.5,
        max_retries: int = 6,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.repo = repo
        self._client = client or make_client()
        self.min_interval = min_interval
        self.max_retries = max_retries
        self._last = 0.0

    def _pace(self) -> None:
        wait = self.min_interval - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()

    def list_folder(self, folder_id: int, end: int = 200) -> list[WebLinkEntry]:
        """Return the child entries of a folder, retrying past session limits."""
        body = {"repoName": self.repo, "folderId": folder_id, "getNewListing": True,
                "start": 0, "end": end, "sortColumn": "", "sortAscending": True}
        url = f"{self.base_url}/FolderListingService.aspx/GetFolderListing2"
        backoff = 4.0
        for attempt in range(1, self.max_retries + 1):
            self._pace()
            try:
                resp = self._client.post(url, json=body)
            except httpx.HTTPError as exc:
                # The host resets connections under sustained load (WinError
                # 10054); treat that like a session-limit hiccup and retry.
                log.info("weblink.network_retry", folder=folder_id, attempt=attempt,
                         error=str(exc))
                time.sleep(backoff)
                backoff *= 1.7
                continue
            if resp.status_code == 200:
                return self._parse(resp.json())
            if resp.status_code == 500 and _SESSION_LIMIT in resp.text:
                log.info("weblink.session_limit", folder=folder_id, attempt=attempt)
                time.sleep(backoff)
                backoff *= 1.7
                continue
            log.warning("weblink.list_error", folder=folder_id, status=resp.status_code)
            break
        raise SessionLimit(f"folder {folder_id} unavailable after {self.max_retries} tries")

    @staticmethod
    def _parse(payload: dict) -> list[WebLinkEntry]:
        rows = [r for r in payload["data"]["results"] if isinstance(r, dict)]
        return [
            WebLinkEntry(
                name=r["name"], entry_id=r["entryId"],
                is_folder=(r["type"] == 0), extension=(r.get("extension") or ""),
            )
            for r in rows
        ]

    def doc_url(self, entry_id: int) -> str:
        """Canonical viewer/download URL for a document entry."""
        return f"{self.base_url}/DocView.aspx?id={entry_id}&dbid=0&repo={self.repo}"

    # -- PDF export ---------------------------------------------------------
    #
    # WebLink documents here are imaged (not stored PDFs), so the server builds
    # a PDF on demand. The doc-viewer app's flow (reverse-engineered from the
    # public bundle — anonymous access, no credentials involved):
    #   1. warm a public session (GET DocView until not session-limited),
    #   2. GetDocumentInfo -> pageCount,
    #   3. POST GeneratePDF10.aspx -> a job key,
    #   4. poll DocumentService.aspx/PDFTransition until finished,
    #   5. GET /PDF10/{key}/{docId} -> the PDF bytes.

    def _warm_session(self, entry_id: int, attempts: int = 15) -> None:
        for attempt in range(1, attempts + 1):
            self._pace()
            try:
                r = self._client.get(f"{self.base_url}/DocView.aspx",
                                     params={"id": entry_id, "dbid": 0, "repo": self.repo})
            except httpx.HTTPError as exc:
                log.info("weblink.network_retry", phase="warm", attempt=attempt, error=str(exc))
                time.sleep(min(4.0 * attempt, 20.0))
                continue
            if _SESSION_LIMIT not in r.text:
                return
            log.info("weblink.session_limit", phase="warm", attempt=attempt)
            time.sleep(min(4.0 * attempt, 20.0))
        raise SessionLimit("could not obtain a WebLink public session")

    def _page_count(self, entry_id: int) -> int | None:
        self._pace()
        r = self._client.post(f"{self.base_url}/FolderListingService.aspx/GetDocumentInfo",
                              json={"repoName": self.repo, "dId": entry_id})
        d = r.json().get("data") or {}
        for key in ("pageCount", "PageCount", "pages"):
            if isinstance(d, dict) and d.get(key):
                return int(d[key])
        return None

    def export_pdf(self, entry_id: int) -> bytes:
        """Return the generated PDF bytes for a document, or raise SessionLimit.

        Network resets mid-export raise SessionLimit too, so the caller records
        a single failed document rather than crashing the whole run.
        """
        try:
            return self._export_pdf(entry_id)
        except httpx.HTTPError as exc:
            raise SessionLimit(f"network error during export: {exc}") from exc

    def _export_pdf(self, entry_id: int) -> bytes:
        self._warm_session(entry_id)
        pages = self._page_count(entry_id)
        page_range = f"1 - {pages}" if pages else "1 - 9999"

        self._pace()
        gen = self._client.post(
            f"{self.base_url}/GeneratePDF10.aspx",
            params={"key": entry_id, "PageRange": page_range, "Watermark": 0, "repo": self.repo},
            content="{}",
        )
        job_key = gen.text.strip().splitlines()[0].strip().strip('"')

        for _ in range(60):
            self._pace()
            prog = self._client.post(f"{self.base_url}/DocumentService.aspx/PDFTransition",
                                     json={"Key": job_key}).json().get("data") or {}
            if isinstance(prog, dict) and prog.get("finished"):
                if not prog.get("success", True):
                    raise SessionLimit(f"PDF generation failed: {prog.get('errMsg')}")
                break
            time.sleep(1.5)

        self._pace()
        pdf = self._client.get(f"{self.base_url}/PDF10/{job_key}/{entry_id}")
        if pdf.status_code != 200 or pdf.content[:4] != b"%PDF":
            raise SessionLimit(f"PDF fetch returned {pdf.status_code} / non-PDF body")
        return pdf.content

    def close(self) -> None:
        self._client.close()


def _meeting_date(folder_name: str) -> date | None:
    """Parse ``MM-DD-YY`` out of a meeting folder or document name."""
    m = _MEETING_DATE_RE.search(folder_name)
    if not m:
        return None
    month, day, yy = (int(g) for g in m.groups())
    try:
        return date(2000 + yy, month, day)
    except ValueError:
        return None


def _classify(doc_name: str) -> str | None:
    """Map a packet document name to a doc_type, or None to skip it.

    Only the agenda and the minutes are ingested; the numbered staff-report
    attachments in the packet are left out of the corpus.
    """
    low = doc_name.lower()
    if "minute" in low:
        return "minutes"
    if "agenda" in low:
        return "agenda"
    return None


def discover_weblink(client: WebLinkClient, agendas_folder_id: int, since: date) -> list[DocumentRef]:
    """Walk the Agendas tree and return agenda/minutes refs on/after ``since``."""
    refs: list[DocumentRef] = []
    skipped = 0
    for year_folder in client.list_folder(agendas_folder_id):
        if not (year_folder.is_folder and _YEAR_RE.match(year_folder.name)):
            continue
        if int(year_folder.name) < since.year:
            continue
        try:
            meetings = client.list_folder(year_folder.entry_id)
        except SessionLimit:
            log.warning("weblink.skip_year", year=year_folder.name)
            skipped += 1
            continue
        for meeting in meetings:
            mdate = _meeting_date(meeting.name)
            if not meeting.is_folder or mdate is None or mdate < since:
                continue
            try:
                docs = client.list_folder(meeting.entry_id)
            except SessionLimit:
                # One unreachable meeting folder shouldn't abort a long backfill.
                log.warning("weblink.skip_meeting", meeting=meeting.name)
                skipped += 1
                continue
            for doc in docs:
                if doc.is_folder:
                    continue
                doc_type = _classify(doc.name)
                if doc_type is None:
                    continue
                refs.append(
                    DocumentRef(
                        city="downey",
                        url=client.doc_url(doc.entry_id),
                        title=doc.name,
                        doc_type=doc_type,
                        meeting_date=_meeting_date(doc.name) or mdate,
                    )
                )
    log.info("weblink.discovered", found=len(refs), skipped_folders=skipped)
    return refs


class WebLinkAdapter(CityAdapter):
    """Discovery for a city backed by a public Laserfiche WebLink repo."""

    def __init__(self, config: CityConfig, client: WebLinkClient | None = None) -> None:
        super().__init__(config)
        assert config.weblink is not None, "WebLinkAdapter requires a weblink config block"
        self._client = client or WebLinkClient(config.weblink.base_url, config.weblink.repo)

    def discover_documents(self) -> list[DocumentRef]:
        assert self.config.weblink is not None
        budgets = [
            DocumentRef(city=self.slug, url=b.url, title=b.title,
                        doc_type="budget", fiscal_year=b.fiscal_year)
            for b in self.config.budgets
        ]
        agendas = discover_weblink(
            self._client, self.config.weblink.agendas_folder_id, self.config.ingest_since
        )
        return budgets + agendas

    def download(self, ref: DocumentRef, fetcher: PoliteFetcher) -> Path | None:
        # Budgets are ordinary public PDFs — let the generic fetcher handle them.
        if ref.doc_type == "budget":
            return None
        hit = fetcher.cached(ref.url, ref.city)
        if hit is not None:
            return hit
        entry_id = _entry_id_from_url(ref.url)
        try:
            content = self._client.export_pdf(entry_id)
        except SessionLimit as exc:
            raise DownloadError(str(exc)) from exc
        return fetcher.store(ref.url, ref.city, content, ".pdf")


def _entry_id_from_url(url: str) -> int:
    m = re.search(r"[?&]id=(\d+)", url)
    if not m:
        raise DownloadError(f"no entry id in WebLink url: {url}")
    return int(m.group(1))
