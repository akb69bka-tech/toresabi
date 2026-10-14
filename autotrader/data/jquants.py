"""J-Quants API（JPX公式）から日足を取得する。

V1（リフレッシュトークン認証）は 2026-06-01 に提供終了し、現在は V2（ダッシュボードで発行する
API キーを x-api-key ヘッダーで送る）のみ。設定に data.jquants_api_key があれば V2 を使い、
無ければ従来の jquants_refresh_token で V1 を試す（互換のため残してある）。
V2 は項目名が短縮形（AdjC, AdjVo など）で、一覧は data 配列で返る。

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


def pick(q: dict, *names):
    """V2 の短縮名と V1 の名前のどちらでも値を取る。空文字は無いものとして扱う"""
    for n in names:
        v = q.get(n)
        if v not in (None, ""):
            return v
    return None


class JQuantsClient:
    BASE_V1 = "https://api.jquants.com/v1"
    BASE_V2 = "https://api.jquants.com/v2"

    def __init__(self, api_key: str = "", refresh_token: str = "", interval: float = 0.6, session=None):
        self.api_key = api_key
        self.refresh_token = refresh_token
        self.interval = interval
        self.session = session or requests.Session()
        self.version = "v2" if api_key else "v1"
        self._id_token: Optional[str] = None

    def _headers(self) -> dict:
        if self.version == "v2":
            return {"x-api-key": self.api_key}
        if not self._id_token:
            r = self.session.post(f"{self.BASE_V1}/token/auth_refresh",
                                  params={"refreshtoken": self.refresh_token}, timeout=20)
            r.raise_for_status()
            self._id_token = r.json()["idToken"]
        return {"Authorization": f"Bearer {self._id_token}"}

    def get(self, v1_path: str, v2_path: str, params: dict, v1_key: str) -> List[dict]:
        """全ページを取得して1つのリストで返す"""
        base, path, key = ((self.BASE_V2, v2_path, "data") if self.version == "v2"
                           else (self.BASE_V1, v1_path, v1_key))
        out, pkey = [], None
        while True:
            p = dict(params)
            if pkey:
                p["pagination_key"] = pkey
            r = self.session.get(f"{base}{path}", params=p, headers=self._headers(), timeout=30)
            r.raise_for_status()
            j = r.json()
            out.extend(j.get(key) or [])
            pkey = j.get("pagination_key")
            time.sleep(self.interval)
            if not pkey:
                return out


def client_from_cfg(d: dict, session=None) -> JQuantsClient:
    return JQuantsClient(d.get("jquants_api_key", ""), d.get("jquants_refresh_token", ""),
                         interval=d.get("request_interval_sec", 0.6), session=session)


def parse_bar(q: dict) -> Optional[Bar]:
    c = pick(q, "AdjC", "AdjustmentClose", "C", "Close")
    if c is None:
        return None                              # 売買が成立しなかった日
    c = float(c)
    return Bar(q["Date"][:10] if len(q["Date"]) >= 10 else f"{q['Date'][:4]}-{q['Date'][4:6]}-{q['Date'][6:8]}",
               float(pick(q, "AdjO", "AdjustmentOpen", "O", "Open") or c),
               float(pick(q, "AdjH", "AdjustmentHigh", "H", "High") or c),
               float(pick(q, "AdjL", "AdjustmentLow", "L", "Low") or c),
               c, float(pick(q, "AdjVo", "AdjustmentVolume", "Vo", "Volume") or 0))


class JQuantsSource:
    name = "jquants"

    def __init__(self, refresh_token: str, cache_dir: str, interval: float = 0.6, session=None,
                 api_key: str = ""):
        self.client = JQuantsClient(api_key, refresh_token, interval, session)
        self.cache = JsonCache(cache_dir)

    def fetch(self, code: str, days: int) -> List[Bar]:
        cached = self.cache.fresh_today(code)
        if cached is not None:
            return cached[-days:] if days > 0 else cached
        code5 = code if len(code) == 5 else code + "0"
        frm = (date.today() - timedelta(days=int(days * 1.6) + 30)).isoformat()
        try:
            rows = self.client.get("/prices/daily_quotes", "/equities/bars/daily",
                                   {"code": code5, "from": frm, "to": date.today().isoformat()}, "daily_quotes")
        except requests.RequestException:
            old = self.cache.load(code)
            return [Bar(**b) for b in old["bars"]][-days:] if old else []
        out = [b for b in (parse_bar(q) for q in rows) if b]
        out.sort(key=lambda b: b.d)
        if out:
            self.cache.save(code, out)
        return out[-days:] if days > 0 else out
