"""Application settings and per-city configuration.

Settings come from the environment (prefix ``CIVIC_``) with sane local-first
defaults; each city is described by a YAML file in ``cities/`` so that adding a
new city later is a config change plus one adapter class, not a rewrite.
"""

from pathlib import Path
from datetime import date

import yaml
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict


class CalendarConfig(BaseModel):
    """Location of a CivicEngage calendar listing (server-rendered HTML) that
    enumerates meetings and links their agenda/minutes PDFs. Both Downey and
    El Segundo run CivicEngage, so one parser serves both — only these values
    differ per city."""

    base_url: str          # scheme + host, e.g. https://www.elsegundo.gov
    listing_path: str      # path of the agendas/calendar list page
    max_pages: int = 40    # safety cap on pagination while walking back in time


class WebLinkConfig(BaseModel):
    """A public Laserfiche WebLink repository (e.g. Downey's lf.downeyca.org).

    Agendas and minutes live in year → meeting-date folder trees; the adapter
    walks them. Folder ids are stable Laserfiche entry ids, verified once."""

    base_url: str            # e.g. https://lf.downeyca.org/WebLink
    repo: str                # Laserfiche repository name, e.g. Downey
    # "Agendas & Reports" folder: year subfolders → per-meeting folders whose
    # packets hold the agenda and the approved minutes of a prior meeting.
    agendas_folder_id: int


class BudgetRef(BaseModel):
    """A hand-located adopted-budget PDF. The spec calls out that budgets must
    be found separately (they live on Finance pages, not the agenda calendar),
    so they are declared explicitly in config rather than discovered by crawl."""

    url: str
    title: str
    fiscal_year: int


class CityConfig(BaseModel):
    """One city's crawl configuration, loaded from ``cities/<slug>.yaml``."""

    slug: str
    name: str
    agendas_url: str
    # Bounded ingestion window: meetings on/after this date (spec Phase 1).
    ingest_since: date
    calendar: CalendarConfig | None = None
    weblink: WebLinkConfig | None = None
    budgets: list[BudgetRef] = []


class Settings(BaseSettings):
    """Process-wide settings. Everything lives under ``data_dir`` by default."""

    model_config = SettingsConfigDict(
        env_prefix="CIVIC_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    data_dir: Path = Path("data")
    cities_dir: Path = Path("cities")

    anthropic_api_key: str | None = None
    contact_email: str = "you@example.com"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "civic.db"

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def processed_dir(self) -> Path:
        return self.data_dir / "processed"

    @property
    def chroma_dir(self) -> Path:
        return self.data_dir / "chroma"


def load_city_config(path: Path) -> CityConfig:
    """Load and validate one city YAML. Fails loudly on unknown/missing fields."""
    with path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    return CityConfig.model_validate(raw)


def load_all_city_configs(cities_dir: Path) -> list[CityConfig]:
    """Load every ``*.yaml`` in the cities directory, sorted by filename."""
    return [load_city_config(p) for p in sorted(cities_dir.glob("*.yaml"))]
