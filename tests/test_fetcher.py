from pathlib import Path

import httpx
import pytest

from civic.ingest.fetcher import HYBRID_UA, PoliteFetcher, RobotsDisallowed

ROBOTS = "User-agent: *\nDisallow: /admin\n"


class Site:
    """Fake origin: serves robots + a PDF, counting requests, optionally
    blocking the self-identifying UA the way Akamai does."""

    def __init__(self, block_self_id: bool = False) -> None:
        self.block_self_id = block_self_id
        self.requests: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(str(request.url.path))
        ua = request.headers["User-Agent"]
        if self.block_self_id and ua != HYBRID_UA:
            return httpx.Response(403, text="Access Denied")
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=ROBOTS)
        if request.url.path.endswith(".pdf"):
            return httpx.Response(200, content=b"%PDF-fake",
                                  headers={"content-type": "application/pdf"})
        return httpx.Response(404)


def _fetcher(tmp_path: Path, site: Site) -> PoliteFetcher:
    client = httpx.Client(transport=httpx.MockTransport(site.handler))
    return PoliteFetcher(tmp_path, "test@example.com", min_interval=0, client=client)


def test_fetch_caches_and_never_redownloads(tmp_path: Path) -> None:
    site = Site()
    fetcher = _fetcher(tmp_path, site)
    first = fetcher.fetch("https://city.test/doc.pdf", "downey")
    assert not first.from_cache
    assert first.local_path is not None and first.local_path.suffix == ".pdf"
    assert first.local_path.read_bytes() == b"%PDF-fake"

    requests_after_first = len(site.requests)
    second = fetcher.fetch("https://city.test/doc.pdf", "downey")
    assert second.from_cache
    assert len(site.requests) == requests_after_first  # no new network traffic


def test_robots_disallow_is_honored(tmp_path: Path) -> None:
    site = Site()
    fetcher = _fetcher(tmp_path, site)
    with pytest.raises(RobotsDisallowed):
        fetcher.fetch("https://city.test/admin/secret.pdf", "downey")


def test_ua_escalates_when_self_id_blocked(tmp_path: Path) -> None:
    site = Site(block_self_id=True)
    fetcher = _fetcher(tmp_path, site)
    result = fetcher.fetch("https://city.test/doc.pdf", "downey")
    assert result.local_path is not None
    assert result.ua_tier == "hybrid"
    # The working UA is remembered for the host.
    assert fetcher._ua_for_host["city.test"] == HYBRID_UA


def test_4xx_is_not_retried(tmp_path: Path) -> None:
    site = Site()
    fetcher = _fetcher(tmp_path, site)
    result = fetcher.fetch("https://city.test/missing.txt", "downey")
    assert result.status == 404
    assert result.local_path is None
    # robots.txt + one self-id attempt + one hybrid escalation attempt, no more.
    assert site.requests.count("/missing.txt") <= 2
