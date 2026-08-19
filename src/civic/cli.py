"""Typer CLI. ``ingest`` is real (Phase 1); the rest are stubs that boot the
app (load settings, apply DB migrations) and report which phase implements
them."""

import typer

from civic import db
from civic.config import Settings, load_all_city_configs, load_city_config
from civic.logconfig import configure_logging

app = typer.Typer(
    name="civic",
    help="Civic Records Research Agent — research over Downey and El Segundo city records.",
    no_args_is_help=True,
)


def _startup() -> Settings:
    """Load settings and ensure the SQLite schema is current."""
    settings = Settings()
    conn = db.connect(settings.db_path)
    version = db.schema_version(conn)
    conn.close()
    typer.echo(f"db ready at {settings.db_path} (schema v{version})")
    return settings


def _stub(command: str, phase: int) -> None:
    typer.echo(f"'{command}' is not implemented yet — it arrives in Phase {phase}.")


@app.command()
def ingest(
    city: str = typer.Option(None, help="City slug (e.g. downey, el_segundo). Omit for all."),
    limit: int = typer.Option(
        None, help="Max documents to download this run (budgets first, then newest meetings)."
    ),
    max_pages: int = typer.Option(
        None, help="Max calendar listing pages to walk (overrides city config)."
    ),
) -> None:
    """Discover and download source documents into data/raw/ and SQLite."""
    from civic.ingest.fetcher import PoliteFetcher
    from civic.ingest.pipeline import run_ingest

    settings = _startup()
    configure_logging(settings.data_dir / "logs" / "civic.jsonl")
    configs = (
        [load_city_config(settings.cities_dir / f"{city}.yaml")]
        if city
        else load_all_city_configs(settings.cities_dir)
    )
    conn = db.connect(settings.db_path)
    fetcher = PoliteFetcher(settings.raw_dir, settings.contact_email)
    try:
        for config in configs:
            if max_pages is not None and config.calendar is not None:
                config.calendar.max_pages = max_pages
            typer.echo(f"ingesting {config.slug} …")
            tally = run_ingest(config, fetcher, conn, limit=limit)
            typer.echo(
                f"  discovered={tally['discovered']} fetched={tally['fetched']} "
                f"skipped={tally['skipped']} failed={tally['failed']}"
            )
    finally:
        fetcher.close()

    typer.echo("\ndocuments by city and type:")
    typer.echo(f"  {'city':<12} {'doc_type':<10} {'count':>5}")
    for city_name, doc_type, n in db.count_by_city_and_type(conn):
        typer.echo(f"  {city_name:<12} {doc_type:<10} {n:>5}")
    conn.close()


@app.command()
def index() -> None:
    """Chunk, embed, and index extracted documents for retrieval."""
    _startup()
    _stub("index", 3)


@app.command()
def ask(
    question: str = typer.Argument(..., help="A research question about either city."),
    agent: bool = typer.Option(False, "--agent", help="Use the multi-step agent loop."),
) -> None:
    """Answer a question with citations back to source documents."""
    _startup()
    _stub("ask", 5 if agent else 3)


@app.command("eval")
def eval_cmd() -> None:
    """Run the golden-set evaluation suite and write a report."""
    _startup()
    _stub("eval", 4)


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="Bind address."),
    port: int = typer.Option(8000, help="Port."),
) -> None:
    """Start the FastAPI server."""
    _startup()
    _stub("serve", 6)


if __name__ == "__main__":
    app()
