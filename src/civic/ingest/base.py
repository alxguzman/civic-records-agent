"""The city-adapter contract.

Every city is ingested through a small adapter that knows *only* how to turn
that city's website into a list of ``DocumentRef`` (a URL plus whatever
metadata is knowable before download). Everything after discovery — fetching,
caching, hashing, persistence — is shared and lives outside the adapter, so
adding a new city is one subclass, not a new pipeline.
"""

from abc import ABC, abstractmethod
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel

from civic.config import CityConfig
from civic.models import DocType

if TYPE_CHECKING:
    from civic.ingest.fetcher import PoliteFetcher


class DownloadError(Exception):
    """An adapter's custom downloader tried and failed for this document."""


class DocumentRef(BaseModel):
    """A document discovered on a city site, before it has been fetched.

    ``url`` is the direct link to the file. The metadata fields are best-effort
    from the listing page; anything unknown is left ``None`` and may be filled
    in later (e.g. ``fiscal_year`` inferred during extraction).
    """

    city: str
    url: str
    title: str | None = None
    doc_type: DocType
    meeting_date: date | None = None
    fiscal_year: int | None = None


class CityAdapter(ABC):
    """Base class for per-city document discovery."""

    def __init__(self, config: CityConfig) -> None:
        self.config = config

    @property
    def slug(self) -> str:
        return self.config.slug

    @abstractmethod
    def discover_documents(self) -> list[DocumentRef]:
        """Return every document in the bounded ingestion window
        (``config.ingest_since`` onward, plus the tracked budget PDFs).

        Implementations must not download file bodies here — only enumerate
        URLs and the metadata visible on listing pages. Fetching is the
        fetcher's job. Raise rather than guess if the site structure does not
        match what the adapter expects."""
        raise NotImplementedError

    def download(self, ref: DocumentRef, fetcher: "PoliteFetcher") -> Path | None:
        """Optional custom download for sources the generic fetcher can't handle
        (e.g. Laserfiche WebLink's multi-step, session-bound PDF export).

        Return the local cache path on success, or ``None`` to defer to the
        generic fetcher. Raise :class:`DownloadError` if this adapter owns the
        download but it failed (so the pipeline records a failure instead of
        falling back to a fetch that would retrieve the wrong bytes)."""
        return None
