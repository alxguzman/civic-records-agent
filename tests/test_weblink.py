import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from civic.ingest.weblink import (
    SessionLimit,
    WebLinkClient,
    _classify,
    _entry_id_from_url,
    _meeting_date,
    discover_weblink,
)

FIX = Path(__file__).parent / "fixtures"


def test_meeting_date_parsing() -> None:
    assert _meeting_date("08-11-26") == date(2026, 8, 11)
    assert _meeting_date("00. Agenda - CC - 08-11-26") == date(2026, 8, 11)
    assert _meeting_date("01. Minutes - 05-12-26 - Regular Meeting") == date(2026, 5, 12)
    assert _meeting_date("Cancellation notice") is None


def test_classify() -> None:
    assert _classify("00. Agenda - CC - 08-11-26") == "agenda"
    assert _classify("00a. Agenda - CC - 08-11-26 - Spanish") == "agenda"
    assert _classify("01. Minutes - 05-12-26 - Regular Meeting") == "minutes"
    assert _classify("02. Warrant Register 8-11-26") is None
    assert _classify("03. Approval of Purchase of Crane Truck") is None


def test_entry_id_from_url() -> None:
    assert _entry_id_from_url(
        "https://lf.downeyca.org/WebLink/DocView.aspx?id=1079620&dbid=0&repo=Downey"
    ) == 1079620


def test_parse_folder_listing() -> None:
    payload = json.loads((FIX / "weblink_meeting.json").read_text(encoding="utf-8-sig"))
    entries = WebLinkClient._parse(payload)
    assert entries, "meeting fixture should have documents"
    names = [e.name for e in entries]
    assert any("Agenda" in n for n in names)
    assert any("Minutes" in n for n in names)
    assert all(not e.is_folder for e in entries)  # meeting packet is all documents


class _FakeClient:
    """Serves the two saved fixtures for a scripted year→meeting walk."""

    def __init__(self) -> None:
        self.year = json.loads((FIX / "weblink_year_2026.json").read_text(encoding="utf-8-sig"))
        self.meeting = json.loads((FIX / "weblink_meeting.json").read_text(encoding="utf-8-sig"))

    def list_folder(self, folder_id: int, end: int = 200):
        if folder_id == 12:  # agendas root → one year folder (2026)
            return [WebLinkClient._parse({"data": {"results": [
                {"name": "2026", "entryId": 1067253, "type": 0, "extension": ""}]}})[0]]
        if folder_id == 1067253:  # year → meeting folders
            return WebLinkClient._parse(self.year)
        return WebLinkClient._parse(self.meeting)  # meeting → documents

    def doc_url(self, entry_id: int) -> str:
        return f"https://lf.downeyca.org/WebLink/DocView.aspx?id={entry_id}&dbid=0&repo=Downey"


def test_discover_weblink_walks_tree_and_filters() -> None:
    refs = discover_weblink(_FakeClient(), agendas_folder_id=12, since=date(2023, 1, 1))
    assert refs, "walk should yield agenda/minutes refs"
    assert {r.doc_type for r in refs} <= {"agenda", "minutes"}
    assert any(r.doc_type == "agenda" for r in refs)
    assert any(r.doc_type == "minutes" for r in refs)
    # every ref carries a real meeting date and a WebLink doc URL
    assert all(r.meeting_date is not None for r in refs)
    assert all("DocView.aspx?id=" in r.url for r in refs)


def test_discover_weblink_respects_since_year() -> None:
    refs = discover_weblink(_FakeClient(), agendas_folder_id=12, since=date(2099, 1, 1))
    assert refs == []  # 2026 < 2099 → nothing in window


def test_list_folder_retries_connection_reset(monkeypatch) -> None:
    """A dropped connection (WinError 10054) is retried, not fatal."""
    monkeypatch.setattr("civic.ingest.weblink.time.sleep", lambda *_: None)
    payload = json.loads((FIX / "weblink_meeting.json").read_text(encoding="utf-8-sig"))
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ReadError("forcibly closed")
        return httpx.Response(200, json=payload)

    client = WebLinkClient("https://lf.test/WebLink", "Downey", min_interval=0,
                           client=httpx.Client(transport=httpx.MockTransport(handler)))
    entries = client.list_folder(999)
    assert calls["n"] == 2  # first reset, second succeeds
    assert entries and all(not e.is_folder for e in entries)


def test_list_folder_gives_up_after_persistent_failure(monkeypatch) -> None:
    monkeypatch.setattr("civic.ingest.weblink.time.sleep", lambda *_: None)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    client = WebLinkClient("https://lf.test/WebLink", "Downey", min_interval=0, max_retries=3,
                           client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(SessionLimit):
        client.list_folder(999)
