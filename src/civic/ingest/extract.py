"""PDF → per-page text, with OCR fallback for image-only pages.

The primary path is ``pdfplumber`` (born-digital text). A page yielding fewer
than :data:`OCR_THRESHOLD` characters is presumed scanned and retried through
``pdf2image`` + ``pytesseract`` — but only when the Tesseract and Poppler
binaries are actually present. When they are not, the page keeps whatever the
primary path produced and the quality report will show it; extraction never
hard-fails on a missing OCR toolchain.

Output contract (spec Phase 2): ``data/processed/{doc_id}.json`` holding a
list of ``{page_number, text, ocr}``.
"""

import json
import re
import shutil
from pathlib import Path

import pdfplumber
import structlog
from pydantic import BaseModel

log = structlog.get_logger()

# Below this many characters a page is presumed to be a scan, not real text.
OCR_THRESHOLD = 50


class PageText(BaseModel):
    page_number: int  # 1-based, as printed citations will reference it
    text: str
    ocr: bool


def normalize_whitespace(text: str) -> str:
    """Collapse horizontal whitespace runs and blank-line runs, strip edges.

    Line structure is kept (headings, list items) because page text feeds
    chunking later; only noise — trailing spaces, triple blank lines — goes.
    """
    lines = [re.sub(r"[ \t\xa0]+", " ", line).strip() for line in text.splitlines()]
    collapsed = re.sub(r"\n{3,}", "\n\n", "\n".join(lines))
    return collapsed.strip()


# The UB-Mannheim Windows installer does not add itself to PATH; look there too.
_TESSERACT_DEFAULT = Path("C:/Program Files/Tesseract-OCR/tesseract.exe")


def _tesseract_cmd() -> str | None:
    found = shutil.which("tesseract")
    if found:
        return found
    return str(_TESSERACT_DEFAULT) if _TESSERACT_DEFAULT.exists() else None


def ocr_available() -> bool:
    """True when both native OCR dependencies (Tesseract, Poppler) are found."""
    return _tesseract_cmd() is not None and shutil.which("pdftoppm") is not None


def _ocr_page(pdf_path: Path, page_number: int) -> str:
    """Rasterize one page via Poppler and OCR it via Tesseract."""
    import pytesseract
    from pdf2image import convert_from_path

    cmd = _tesseract_cmd()
    if cmd is not None:
        pytesseract.pytesseract.tesseract_cmd = cmd

    images = convert_from_path(
        pdf_path, dpi=300, first_page=page_number, last_page=page_number
    )
    if not images:
        return ""
    return pytesseract.image_to_string(images[0])


def extract_document(pdf_path: Path, *, use_ocr: bool | None = None) -> list[PageText]:
    """Extract every page of one PDF.

    ``use_ocr=None`` means auto-detect the toolchain once per call.
    """
    if use_ocr is None:
        use_ocr = ocr_available()

    pages: list[PageText] = []
    with pdfplumber.open(pdf_path) as pdf:
        for number, page in enumerate(pdf.pages, start=1):
            text = normalize_whitespace(page.extract_text() or "")
            used_ocr = False
            if len(text) < OCR_THRESHOLD and use_ocr:
                try:
                    ocr_text = normalize_whitespace(_ocr_page(pdf_path, number))
                except Exception as exc:  # noqa: BLE001 — OCR is best-effort by design
                    log.warning("extract.ocr_failed", path=str(pdf_path),
                                page=number, error=str(exc))
                    ocr_text = ""
                if len(ocr_text) > len(text):
                    text = ocr_text
                    used_ocr = True
            pages.append(PageText(page_number=number, text=text, ocr=used_ocr))
    return pages


def write_processed(pages: list[PageText], processed_dir: Path, doc_id: str) -> Path:
    """Persist one document's pages to ``data/processed/{doc_id}.json``."""
    processed_dir.mkdir(parents=True, exist_ok=True)
    path = processed_dir / f"{doc_id}.json"
    payload = [p.model_dump() for p in pages]
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def summarize_pages(pages: list[PageText]) -> dict[str, int]:
    """Per-document tallies for the extraction quality report."""
    return {
        "pages": len(pages),
        "ocr_pages": sum(1 for p in pages if p.ocr),
        "empty_pages": sum(1 for p in pages if not p.text),
    }


def read_processed(processed_dir: Path, doc_id: str) -> list[PageText] | None:
    """Load a processed document, or None if it has not been extracted."""
    path = processed_dir / f"{doc_id}.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return [PageText.model_validate(item) for item in data]
