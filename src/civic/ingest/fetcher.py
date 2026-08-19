"""Polite HTTP fetcher: caching, rate limiting, robots, and UA escalation.

Implements the Phase 1 crawling policy (see PROJECT_SPEC.md §5, 2026-08-18
amendment):

* Try a self-identifying User-Agent first; if a host blocks it, escalate once
  to a browser-compatible hybrid UA that still names the project, and remember
  that choice per host. The contact email always rides in a ``From`` header.
* Read ``robots.txt`` with whatever UA succeeds and refuse disallowed paths.
* One request per 2.5 s per host, with jitter.
* Cache every file under ``data/raw/{city}/`` keyed by a hash of the URL and
  never re-download a cached file.
* Exponential backoff on 5xx; never retry 4xx.
* Log every fetch (URL, status, bytes, elapsed, UA tier, cache hit).
"""

import hashlib
import random
import ssl
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx
import structlog
import truststore

log = structlog.get_logger()


def make_client(**kwargs) -> httpx.Client:
    """An httpx client that trusts the OS certificate store.

    Some municipal document hosts (e.g. Downey's Laserfiche server) present an
    incomplete chain that certifi rejects but Windows/macOS trust; truststore
    bridges to the OS store so those hosts verify normally.
    """
    ctx = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    kwargs.setdefault("follow_redirects", True)
    kwargs.setdefault("timeout", 60.0)
    return httpx.Client(verify=ctx, **kwargs)

# A plain project UA is tried first; if blocked, a browser-shaped UA that still
# carries the project token. The email is sent separately in a From header so
# it never has to sit inside the UA string (which is what trips some WAFs).
SELF_ID_UA = "CivicRecordsResearchAgent/0.1"
HYBRID_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 CivicRecordsResearchAgent/0.1"
)
# Browser-shaped requests need a browser-shaped header set to pass a WAF —
# the UA string alone is not enough (verified against both cities' Akamai).
_BROWSER_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
}


class RobotsDisallowed(Exception):
    """Raised when robots.txt forbids a URL we were asked to fetch."""


@dataclass
class FetchResult:
    url: str
    status: int
    local_path: Path | None
    from_cache: bool
    bytes: int
    elapsed_ms: int
    ua_tier: str  # "self_id" | "hybrid" | "cache"


class PoliteFetcher:
    def __init__(
        self,
        raw_dir: Path,
        contact_email: str,
        *,
        min_interval: float = 2.5,
        max_retries: int = 3,
        client: httpx.Client | None = None,
    ) -> None:
        self.raw_dir = raw_dir
        self.contact_email = contact_email
        self.min_interval = min_interval
        self.max_retries = max_retries
        self._client = client or make_client()
        self._last_request: dict[str, float] = {}       # host -> monotonic time
        self._ua_for_host: dict[str, str] = {}           # host -> working UA
        self._robots: dict[str, RobotFileParser | None] = {}

    # -- public API ---------------------------------------------------------

    def fetch(self, url: str, city: str) -> FetchResult:
        """Fetch ``url`` into the city's cache, or return the cached copy.

        Raises :class:`RobotsDisallowed` if robots.txt forbids the path.
        """
        cached = self._cached_path(url, city)
        if cached is not None:
            log.info("fetch.cache_hit", url=url, city=city, path=str(cached))
            return FetchResult(url, 200, cached, True, cached.stat().st_size, 0, "cache")

        if not self._robots_allows(url):
            log.warning("fetch.robots_disallowed", url=url, city=city)
            raise RobotsDisallowed(url)

        return self._download(url, city)

    def fetch_text(self, url: str) -> tuple[int, str]:
        """Fetch an HTML listing page and return ``(status, text)``.

        Discovery pages are transient — they are not written to the document
        cache — but they still go through robots, rate limiting, UA policy, and
        5xx backoff like any other request.
        """
        if not self._robots_allows(url):
            raise RobotsDisallowed(url)
        host = urlsplit(url).netloc
        backoff = 1.0
        last_status = 0
        for attempt in range(1, self.max_retries + 1):
            self._respect_rate_limit(host)
            try:
                resp, tier = self._get_with_ua_policy(url, host)
            except httpx.HTTPError as exc:
                log.warning("fetch_text.error", url=url, attempt=attempt, error=str(exc))
                time.sleep(backoff)
                backoff *= 2
                continue
            last_status = resp.status_code
            log.info("fetch_text.done", url=url, status=resp.status_code, ua_tier=tier,
                     bytes=len(resp.content))
            if resp.status_code < 400:
                return resp.status_code, resp.text
            if 400 <= resp.status_code < 500:
                break  # never retry 4xx
            time.sleep(backoff)
            backoff *= 2
        return last_status, ""

    def cached(self, url: str, city: str) -> Path | None:
        """Public cache lookup for adapters that download documents themselves
        (e.g. the Laserfiche WebLink export flow)."""
        return self._cached_path(url, city)

    def store(self, url: str, city: str, content: bytes, suffix: str = ".pdf") -> Path:
        """Write adapter-fetched bytes into the same cache the fetcher uses, so
        re-runs skip the download exactly like ``fetch`` does."""
        path = self._cache_path(url, city, suffix)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def close(self) -> None:
        self._client.close()

    # -- caching ------------------------------------------------------------

    def _cache_key(self, url: str) -> str:
        return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]

    def _cache_path(self, url: str, city: str, suffix: str = ".bin") -> Path:
        return self.raw_dir / city / f"{self._cache_key(url)}{suffix}"

    def _cached_path(self, url: str, city: str) -> Path | None:
        """Return an existing cached file for this URL regardless of suffix."""
        directory = self.raw_dir / city
        if not directory.exists():
            return None
        key = self._cache_key(url)
        for path in directory.glob(f"{key}.*"):
            return path
        return None

    # -- rate limiting ------------------------------------------------------

    def _respect_rate_limit(self, host: str) -> None:
        last = self._last_request.get(host)
        if last is not None:
            wait = self.min_interval - (time.monotonic() - last)
            if wait > 0:
                time.sleep(wait + random.uniform(0.0, 0.75))  # jitter
        self._last_request[host] = time.monotonic()

    # -- UA escalation ------------------------------------------------------

    def _headers(self, ua: str) -> dict[str, str]:
        headers = {"User-Agent": ua, "From": self.contact_email}
        if ua == HYBRID_UA:
            headers.update(_BROWSER_HEADERS)
        return headers

    def _get(self, url: str, ua: str) -> httpx.Response:
        return self._client.get(url, headers=self._headers(ua))

    def _get_with_ua_policy(self, url: str, host: str) -> tuple[httpx.Response, str]:
        """GET, honoring the per-host UA choice and escalating on a block."""
        preferred = self._ua_for_host.get(host, SELF_ID_UA)
        order = [preferred] + [ua for ua in (SELF_ID_UA, HYBRID_UA) if ua != preferred]
        resp = self._get(url, order[0])
        tier = "self_id" if order[0] == SELF_ID_UA else "hybrid"
        if resp.status_code in (401, 403, 429) and len(order) > 1:
            log.info("fetch.ua_escalate", host=host, from_status=resp.status_code)
            resp = self._get(url, order[1])
            tier = "self_id" if order[1] == SELF_ID_UA else "hybrid"
        if resp.status_code < 400:
            self._ua_for_host[host] = HYBRID_UA if tier == "hybrid" else SELF_ID_UA
        return resp, tier

    # -- robots -------------------------------------------------------------

    def _robots_allows(self, url: str) -> bool:
        parts = urlsplit(url)
        host = parts.netloc
        if host not in self._robots:
            self._robots[host] = self._load_robots(f"{parts.scheme}://{host}")
        parser = self._robots[host]
        if parser is None:
            return True  # robots unreadable (e.g. blocked); do not hard-fail
        # Check against whichever UA we would actually send.
        ua = self._ua_for_host.get(host, SELF_ID_UA)
        return parser.can_fetch(ua, url)

    def _load_robots(self, origin: str) -> RobotFileParser | None:
        robots_url = f"{origin}/robots.txt"
        try:
            resp, _ = self._get_with_ua_policy(robots_url, urlsplit(origin).netloc)
        except httpx.HTTPError as exc:
            log.warning("robots.fetch_error", url=robots_url, error=str(exc))
            return None
        if resp.status_code != 200:
            log.warning("robots.unavailable", url=robots_url, status=resp.status_code)
            return None
        parser = RobotFileParser()
        parser.parse(resp.text.splitlines())
        log.info("robots.loaded", url=robots_url)
        return parser

    # -- download with retry ------------------------------------------------

    def _download(self, url: str, city: str) -> FetchResult:
        host = urlsplit(url).netloc
        start = time.monotonic()
        backoff = 1.0
        last_status = 0
        tier = "self_id"
        for attempt in range(1, self.max_retries + 1):
            self._respect_rate_limit(host)
            try:
                resp, tier = self._get_with_ua_policy(url, host)
            except httpx.HTTPError as exc:
                log.warning("fetch.error", url=url, attempt=attempt, error=str(exc))
                time.sleep(backoff)
                backoff *= 2
                continue
            last_status = resp.status_code
            if resp.status_code < 400:
                path = self._write_cache(url, city, resp)
                elapsed = int((time.monotonic() - start) * 1000)
                log.info(
                    "fetch.ok", url=url, city=city, status=resp.status_code,
                    bytes=len(resp.content), elapsed_ms=elapsed, ua_tier=tier,
                    path=str(path),
                )
                return FetchResult(
                    url, resp.status_code, path, False, len(resp.content), elapsed, tier
                )
            if 400 <= resp.status_code < 500:
                log.warning("fetch.client_error", url=url, status=resp.status_code, ua_tier=tier)
                break  # never retry 4xx
            log.warning("fetch.server_error", url=url, status=resp.status_code, attempt=attempt)
            time.sleep(backoff)
            backoff *= 2
        elapsed = int((time.monotonic() - start) * 1000)
        return FetchResult(url, last_status, None, False, 0, elapsed, tier)

    def _write_cache(self, url: str, city: str, resp: httpx.Response) -> Path:
        suffix = _suffix_for(url, resp.headers.get("content-type", ""))
        path = self._cache_path(url, city, suffix)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(resp.content)
        return path


def _suffix_for(url: str, content_type: str) -> str:
    ctype = content_type.split(";")[0].strip().lower()
    if "pdf" in ctype or url.lower().endswith(".pdf"):
        return ".pdf"
    if "html" in ctype:
        return ".html"
    if "json" in ctype:
        return ".json"
    return ".bin"
