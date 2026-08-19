"""Ingestion pipeline: discover → fetch (cached) → hash → persist.

Discovery is city-specific (the adapter); everything after it is shared. A
document row is written only once its PDF is actually on disk, so the table and
``data/raw/`` never disagree.
"""

import hashlib
import sqlite3
from datetime import datetime

import structlog

from civic.config import CityConfig
from civic.ingest.base import DocumentRef, DownloadError
from civic.ingest.fetcher import PoliteFetcher, RobotsDisallowed
from civic import db
from civic.models import Document

log = structlog.get_logger()


def build_adapter(config: CityConfig, fetcher: PoliteFetcher):
    """Return the adapter registered for this city slug."""
    from civic.ingest.downey import DowneyAdapter
    from civic.ingest.el_segundo import ElSegundoAdapter

    # Downey uses its public Laserfiche WebLink repo when configured; otherwise
    # the CivicEngage path (budgets only, in practice).
    if config.slug == "downey" and config.weblink is not None:
        from civic.ingest.weblink import WebLinkAdapter
        return WebLinkAdapter(config)

    registry = {"downey": DowneyAdapter, "el_segundo": ElSegundoAdapter}
    try:
        return registry[config.slug](config, fetcher)
    except KeyError:
        raise ValueError(f"no adapter registered for city '{config.slug}'") from None


def _doc_id(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def run_ingest(
    config: CityConfig,
    fetcher: PoliteFetcher,
    conn: sqlite3.Connection,
    limit: int | None = None,
) -> dict[str, int]:
    """Ingest one city. Returns a small tally for the CLI summary/logs.

    ``limit`` caps how many documents are downloaded this run (budgets are
    discovered first, then meetings newest-first, so a capped run still grabs
    the most useful documents). Already-cached files don't count against
    politeness — they are never re-requested.
    """
    adapter = build_adapter(config, fetcher)
    refs: list[DocumentRef] = adapter.discover_documents()
    log.info("ingest.discovered", city=config.slug, count=len(refs))
    if limit is not None and len(refs) > limit:
        log.info("ingest.limited", city=config.slug, limit=limit, dropped=len(refs) - limit)
        refs = refs[:limit]

    tally = {"discovered": len(refs), "fetched": 0, "skipped": 0, "failed": 0}
    for ref in refs:
        # Let the adapter download if it owns a custom flow (WebLink); otherwise
        # use the generic polite fetcher.
        try:
            path = adapter.download(ref, fetcher)
        except DownloadError as exc:
            log.warning("ingest.download_failed", url=ref.url, error=str(exc))
            tally["failed"] += 1
            continue
        if path is None:
            try:
                result = fetcher.fetch(ref.url, ref.city)
            except RobotsDisallowed:
                tally["skipped"] += 1
                continue
            if result.local_path is None:
                log.warning("ingest.fetch_failed", url=ref.url, status=result.status)
                tally["failed"] += 1
                continue
            path = result.local_path

        content = path.read_bytes()
        doc = Document(
            id=_doc_id(ref.url),
            city=ref.city,
            url=ref.url,
            local_path=str(path),
            title=ref.title,
            doc_type=ref.doc_type,
            meeting_date=ref.meeting_date,
            fiscal_year=ref.fiscal_year,
            sha256=hashlib.sha256(content).hexdigest(),
            fetched_at=datetime.now(),
        )
        db.upsert_document(conn, doc)
        tally["fetched"] += 1
    conn.commit()
    log.info("ingest.done", city=config.slug, **tally)
    return tally
