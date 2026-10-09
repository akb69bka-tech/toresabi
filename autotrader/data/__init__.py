from .base import DataSource, parse_csv_bars
from .csvdir import CsvDirSource
from .stooq import StooqSource
from .jquants import JQuantsSource
from .universe import load_universe, SAMPLE_UNIVERSE
from .demo import DemoSource


def make_source(cfg, universe=None) -> DataSource:
    d = cfg["data"]
    kind = d.get("source", "stooq")
    if kind == "demo":
        dm = d.get("demo") or {}
        return DemoSource(days=dm.get("days", 700), seed=dm.get("seed", 42), trend=dm.get("trend", 0.25),
                          universe=universe)
    if kind == "csv":
        return CsvDirSource(d.get("csv_dir", "data_csv"))
    if kind == "jquants":
        return JQuantsSource(d.get("jquants_refresh_token", ""), d.get("cache_dir", "data_cache"),
                             interval=d.get("request_interval_sec", 0.6))
    return StooqSource(d.get("cache_dir", "data_cache"), interval=d.get("request_interval_sec", 0.6))
