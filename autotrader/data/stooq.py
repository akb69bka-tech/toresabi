"""Stooq から日足を取得する（無料・キー不要。日本株は 7203.jp の形式）。

終値は当日の取引終了後、夕方以降に更新される。本システムは終値で判定して
翌朝に発注する設計なので、日次の更新で足りる。
"""
from __future__ import annotations
import time
from typing import List

import requests

from ..simulator import Bar
from .base import JsonCache, parse_csv_bars


class StooqSource:
    name = "stooq"
    URL = "https://stooq.com/q/d/l/?s={code}.jp&i=d"

    def __init__(self, cache_dir: str, interval: float = 0.6, session=None):
        self.cache = JsonCache(cache_dir)
        self.interval = interval
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = "autotrader/0.1 (+personal use)"

    def fetch(self, code: str, days: int) -> List[Bar]:
        cached = self.cache.fresh_today(code)
        if cached is not None:
            return cached[-days:] if days > 0 else cached
        try:
            r = self.session.get(self.URL.format(code=code), timeout=20)
            r.raise_for_status()
            text = r.text
        except requests.RequestException:
            old = self.cache.load(code)
            return [Bar(**b) for b in old["bars"]][-days:] if old else []
        finally:
            time.sleep(self.interval)
        if "No data" in text or "Exceeded" in text:
            old = self.cache.load(code)
            return [Bar(**b) for b in old["bars"]][-days:] if old else []
        bars = parse_csv_bars(text)
        if bars:
            self.cache.save(code, bars)
        return bars[-days:] if days > 0 else bars
