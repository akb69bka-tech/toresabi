from .base import DataSource, parse_csv_bars
from .csvdir import CsvDirSource
from .stooq import StooqSource
from .jquants import JQuantsSource
from .universe import load_universe, SAMPLE_UNIVERSE


def make_source(cfg) -> DataSource:
    d = cfg["data"]
    kind = d.get("source", "stooq")
    if kind == "csv":
        return CsvDirSource(d.get("csv_dir", "data_csv"))
    if kind == "jquants":
        return JQuantsSource(d.get("jquants_refresh_token", ""), d.get("cache_dir", "data_cache"),
                             interval=d.get("request_interval_sec", 0.6))
    return StooqSource(d.get("cache_dir", "data_cache"), interval=d.get("request_interval_sec", 0.6))
