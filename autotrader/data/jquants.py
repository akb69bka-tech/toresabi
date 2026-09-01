"""J-Quants API（JPX公式）から日足を取得する。リフレッシュトークンが必要。

無料プランは12週遅れのデータのため実運用には使えない。Light 以上のプランで
前営業日までの日足が取得できる。
"""
from __future__ import annotations
import time
from datetime import date, timedelta
from typing import List, Optional

import requests

from ..simulator import Bar
from .base import JsonCache


class JQuantsSource:
    name = "jquants"
    BASE = "https://api.jquants.com/v1"

    def __init__(self, refresh_token: str, cache_dir: str, interval: float = 0.6, session=None):
        self.refresh_token = refresh_token
        self.cache = JsonCache(cache_dir)
        self.interval = interval
        self.session = session or requests.Session()
        self._id_token: Optional[str] = None

    def _token(self) -> str:
        if self._id_token:
            return self._id_token
        r = self.session.post(f"{self.BASE}/token/auth_refresh",
                              params={"refreshtoken": self.refresh_token}, timeout=20)
        r.raise_for_status()
        self._id_token = r.json()["idToken"]
        return self._id_token

    def fetch(self, code: str, days: int) -> List[Bar]:
        cached = self.cache.fresh_today(code)
        if cached is not None:
            return cached[-days:] if days > 0 else cached
        code5 = code if len(code) == 5 else code + "0"
        frm = (date.today() - timedelta(days=int(days * 1.6) + 30)).isoformat()
        out: List[Bar] = []
        key = None
        try:
            while True:
                params = {"code": code5, "from": frm, "to": date.today().isoformat()}
                if key:
                    params["pagination_key"] = key
                r = self.session.get(f"{self.BASE}/prices/daily_quotes", params=params,
                                     headers={"Authorization": f"Bearer {self._token()}"}, timeout=30)
                r.raise_for_status()
                j = r.json()
                for q in j.get("daily_quotes", []):
                    c = q.get("AdjustmentClose") or q.get("Close")
                    if c is None:
                        continue
                    out.append(Bar(q["Date"], float(q.get("AdjustmentOpen") or q.get("Open") or c),
                                   float(q.get("AdjustmentHigh") or q.get("High") or c),
                                   float(q.get("AdjustmentLow") or q.get("Low") or c),
                                   float(c), float(q.get("AdjustmentVolume") or q.get("Volume") or 0)))
                key = j.get("pagination_key")
                if not key:
                    break
        except requests.RequestException:
            old = self.cache.load(code)
            return [Bar(**b) for b in old["bars"]][-days:] if old else []
        finally:
            time.sleep(self.interval)
        out.sort(key=lambda b: b.d)
        if out:
            self.cache.save(code, out)
        return out[-days:] if days > 0 else out
