from datetime import date
from pathlib import Path

from civic.config import Settings, load_all_city_configs, load_city_config

CITIES_DIR = Path(__file__).parent.parent / "cities"


def test_load_downey_config() -> None:
    cfg = load_city_config(CITIES_DIR / "downey.yaml")
    assert cfg.slug == "downey"
    assert cfg.name == "Downey, CA"
    assert cfg.agendas_url.startswith("https://www.downeyca.org/")
    assert cfg.ingest_since == date(2023, 1, 1)


def test_load_all_city_configs() -> None:
    configs = load_all_city_configs(CITIES_DIR)
    assert [c.slug for c in configs] == ["downey", "el_segundo"]


def test_settings_paths_derive_from_data_dir(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "d")
    assert settings.db_path == tmp_path / "d" / "civic.db"
    assert settings.raw_dir == tmp_path / "d" / "raw"
    assert settings.processed_dir == tmp_path / "d" / "processed"
    assert settings.chroma_dir == tmp_path / "d" / "chroma"
