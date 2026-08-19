# Civic Records Research Agent

A local-first Python application that ingests public city council agendas, minutes, and
adopted budgets from **Downey, CA** and **El Segundo, CA**, indexes them for hybrid
retrieval, and exposes a hand-written agent that answers multi-step research questions
with page-level citations.

> **Status: Phase 2 (extraction).** `civic ingest` (polite fetcher: UA
> escalation, robots.txt, 2.5 s/host rate limit, cache-by-URL-hash, backoff,
> structured JSON logging; config-driven city adapters) and `civic extract`
> (per-page text via pdfplumber, Tesseract OCR fallback, quality report) both
> work. Indexing, retrieval, and evals land in later phases. No eval numbers
> exist yet — none are reported.

## Document-quality findings (Phase 2)

From the current bounded corpus (19 documents, 2,110 pages — a capped
demonstration set; the full 2023+ backfill runs the same commands without
`--limit`):

| city | docs | pages | OCR pages | empty after OCR |
|---|---:|---:|---:|---:|
| Downey | 4 | 783 | 35 (4.5%) | 0 |
| El Segundo | 15 | 1,327 | 123 (9.3%) | 6 |

Downey's 4 documents are 2 adopted budgets plus the English and Spanish council
agendas for the 2023-01-10 meeting (via WebLink); El Segundo's 15 are 3 budgets
plus 12 recent agendas (via CivicEngage).

- A page is sent to OCR (300 dpi render → Tesseract) when pdfplumber yields
  under 50 characters. 171 pages hit that threshold; OCR recovered readable
  text on 158 of them and 6 stayed empty (genuine blanks or pure images).
- Both cities' **adopted budget books mix born-digital and scanned pages** —
  cover sheets, org charts, and signature pages are scans even in otherwise
  digital PDFs, so an OCR fallback is mandatory for budget documents.
- One El Segundo Planning Commission packet was **57 pages of scanned
  attachments** — commission packets, not council agendas, are where scan
  quality problems concentrate.

## Data-access findings (Phase 1)

Reality diverged from the original spec in ways that shaped the design:

- **Both city domains sit behind Akamai** and 403 a plain self-identifying
  crawler UA — including for `robots.txt`. The fetcher therefore escalates per
  host: self-identifying UA first, then a browser-compatible UA that still
  carries the project name, with the contact email always in a `From` header.
  The working robots.txt files disallow only `/scripts`, `/admin`, and
  `*.asmx` — the document paths crawled here are allowed.
- **El Segundo** publishes agendas/minutes through a server-rendered
  CivicEngage calendar with inline PDF links — cleanly crawlable (the WAF
  rejects `?query` pagination but accepts the site's own `/-toggle-allpast`
  form, which the adapter uses).
- **Downey's council agendas live in AgendaLink**, a JS app whose PDF/minutes
  endpoints are gated behind a token embedded in its client bundle. Extracting
  that token is out of scope by policy. Downey agendas and minutes instead come
  from the city's **public Laserfiche WebLink** archive (`lf.downeyca.org`),
  which needs no credentials: the adapter walks its `Agendas & Reports / {year}
  / {meeting}` folder tree and drives WebLink's on-demand PDF export. Two
  wrinkles are handled rather than worked around — the host's TLS chain needs
  the OS trust store (via `truststore`), and anonymous access draws on a small
  shared license pool that intermittently returns "session limit" 500s, which
  are retried with backoff.
- **Downey stopped publishing budget PDFs after FY 2023-24** — FY 2024-25
  onward exists only as a ClearGov digital budget book, so fiscal-year
  comparisons against Downey currently end at FY2024.

## Setup

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/) (fallback: `python -m venv` + `pip install -e .`).

```sh
uv sync
cp .env.example .env   # fill in values; not needed until Phase 4
uv run civic --help
uv run pytest
```

For the OCR fallback in `civic extract`, install
[Tesseract](https://github.com/UB-Mannheim/tesseract/wiki) and
[Poppler](https://github.com/oschwartz10612/poppler-windows) and ensure both
are on PATH (on Windows: `winget install UB-Mannheim.TesseractOCR
oschwartz10612.Poppler`; the default `C:\Program Files\Tesseract-OCR` install
location is auto-detected). Without them, extraction still runs — scanned
pages are simply reported instead of OCR'd.

## Layout

- `cities/` — one YAML per city; adding a city is a config file plus one adapter class.
- `src/civic/` — application code (`config`, `models`, `db`, CLI; subpackages fill in by phase).
- `data/` — gitignored local cache: raw PDFs, extracted text, SQLite, Chroma.
- `evals/` — committed golden set and eval results (from Phase 4).
