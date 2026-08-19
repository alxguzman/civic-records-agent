# Civic Records Research Agent

A local-first Python application that ingests public city council agendas, minutes, and
adopted budgets from **Downey, CA** and **El Segundo, CA**, indexes them for hybrid
retrieval, and exposes a hand-written agent that answers multi-step research questions
with page-level citations.

> **Status: Phase 1 (ingestion).** `civic ingest` works: polite fetcher (UA
> escalation, robots.txt, 2.5 s/host rate limit, cache-by-URL-hash, backoff,
> structured JSON logging) plus config-driven city adapters. Extraction,
> indexing, retrieval, and evals land in later phases per
> [PROJECT_SPEC.md](PROJECT_SPEC.md). No eval numbers exist yet — none are reported.

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
- **Downey's council agendas live in AgendaLink**, a JS app whose listing API
  is gated behind a token embedded in its client bundle. Extracting that token
  is out of scope by policy, so Downey Phase 1 ingests its adopted-budget PDFs
  only; the city's public Laserfiche WebLink (`lf.downeyca.org`) is the
  planned follow-up source for agendas.
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

## Layout

- `cities/` — one YAML per city; adding a city is a config file plus one adapter class.
- `src/civic/` — application code (`config`, `models`, `db`, CLI; subpackages fill in by phase).
- `data/` — gitignored local cache: raw PDFs, extracted text, SQLite, Chroma.
- `evals/` — committed golden set and eval results (from Phase 4).
