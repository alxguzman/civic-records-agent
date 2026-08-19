from pathlib import Path

from civic.ingest.extract import (
    PageText,
    extract_document,
    normalize_whitespace,
    read_processed,
    summarize_pages,
    write_processed,
)


def _minimal_pdf(text: str) -> bytes:
    """Assemble a valid single-page PDF containing ``text`` (Helvetica 12pt)."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (i, body)
    xref_at = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += (
        b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
        % (len(objects) + 1, xref_at)
    )
    return bytes(out)


def test_normalize_whitespace() -> None:
    raw = "Roll  Call:\t Present \n\n\n\nItem   1.  Budget \n"
    assert normalize_whitespace(raw) == "Roll Call: Present\n\nItem 1. Budget"


def test_extract_document_reads_text_without_ocr(tmp_path: Path) -> None:
    pdf = tmp_path / "mini.pdf"
    pdf.write_bytes(_minimal_pdf("City Council Meeting Agenda for the record"))
    pages = extract_document(pdf, use_ocr=False)
    assert len(pages) == 1
    assert pages[0].page_number == 1
    assert "City Council Meeting Agenda" in pages[0].text
    assert pages[0].ocr is False


def test_processed_round_trip(tmp_path: Path) -> None:
    pages = [PageText(page_number=1, text="hello", ocr=False),
             PageText(page_number=2, text="", ocr=True)]
    write_processed(pages, tmp_path, "abc123")
    loaded = read_processed(tmp_path, "abc123")
    assert loaded == pages
    assert read_processed(tmp_path, "missing") is None


def test_summarize_pages() -> None:
    pages = [PageText(page_number=1, text="x" * 100, ocr=False),
             PageText(page_number=2, text="scanned", ocr=True),
             PageText(page_number=3, text="", ocr=False)]
    assert summarize_pages(pages) == {"pages": 3, "ocr_pages": 1, "empty_pages": 1}
