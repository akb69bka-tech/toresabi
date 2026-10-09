"""デモ相場の生成器。

実データが無くても「触って慣れる」ために、現実の株式相場に近い性質を持たせた擬似データを作る。
- 相場全体の局面（上昇・下落・横ばい）が数か月ごとに切り替わる
- 全銘柄に共通する「市場要因」と、銘柄ごとの個別要因を合成する（銘柄間に相関が生まれる）
- 値動きにある程度の持続性（トレンド）を持たせる
- 出来高は値動きが大きい日に増える
シードを固定すれば同じ相場を再現できる。
"""
from __future__ import annotations
import math, random
from datetime import date, timedelta
from typing import Dict, List, Optional

from ..simulator import Bar, Symbol

REGIMES = {
    "bull":  {"drift": 0.28, "vol": 0.16, "len": (80, 200)},
    "flat":  {"drift": 0.00, "vol": 0.18, "len": (40, 120)},
    "bear":  {"drift": -0.35, "vol": 0.30, "len": (40, 120)},
    "crash": {"drift": -1.20, "vol": 0.55, "len": (8, 25)},
}
NEXT = {"bull": ["flat", "bull", "bear", "crash"], "flat": ["bull", "bear", "flat"],
        "bear": ["flat", "bull", "bear"], "crash": ["bear", "flat", "bull"]}


def market_path(days: int, rnd: random.Random, trend: float = 0.25):
    """市場全体の日次リターン列と局面ラベルを返す"""
    rets, labels = [], []
    regime = "bull"
    prev = 0.0
    while len(rets) < days:
        r = REGIMES[regime]
        n = rnd.randint(*r["len"])
        dv = r["vol"] / math.sqrt(252)
        for _ in range(n):
            if len(rets) >= days:
                break
            shock = dv * math.sqrt(1 - trend * trend) * rnd.gauss(0, 1)
            ret = r["drift"] / 252 - 0.5 * dv * dv + trend * prev + shock
            prev = ret - r["drift"] / 252
            rets.append(ret)
            labels.append(regime)
        regime = rnd.choice(NEXT[regime])
    return rets, labels


def gen_symbol_bars(days: int, start: float, idio_vol: float, beta: float, mkt: List[float],
                    rnd: random.Random, end: Optional[date] = None, unit_lot_round: float = 0.1) -> List[Bar]:
    end = end or date.today()
    cur = end - timedelta(days=int(days * 1.45))
    bars: List[Bar] = []
    price = float(start)
    dv = idio_vol / math.sqrt(252)
    k = 0
    while len(bars) < days:
        cur += timedelta(days=1)
        if cur.weekday() >= 5:
            continue
        o = price
        ret = beta * mkt[k] + dv * rnd.gauss(0, 1)
        k += 1
        price = max(1.0, price * math.exp(ret))
        c = price
        amp = abs(c - o) + c * dv * (0.4 + rnd.random())
        h = max(o, c) + amp * rnd.random() * 0.6
        l = max(1.0, min(o, c) - amp * rnd.random() * 0.6)
        base_vol = 400000 * (0.6 + rnd.random())
        v = base_vol * (1 + 6 * abs(ret))
        r = lambda x: round(x / unit_lot_round) * unit_lot_round
        bars.append(Bar(cur.isoformat(), r(o), r(h), r(l), r(c), float(round(v))))
    return bars


def gen_market(symbols: List[Symbol], days: int = 700, seed: int = 42, trend: float = 0.25,
               end: Optional[date] = None) -> Dict[str, List[Bar]]:
    """銘柄リストに対して相関のある擬似日足を生成する"""
    rnd = random.Random(seed)
    mkt, _ = market_path(days, rnd, trend)
    out: Dict[str, List[Bar]] = {}
    for i, s in enumerate(symbols):
        srnd = random.Random(seed * 1000 + i)
        # 銘柄ごとに価格帯・ベータ・個別ボラをばらつかせる
        start = srnd.choice([150, 300, 600, 900, 1400, 2200, 3500, 5000, 8000, 12000]) * (0.8 + 0.4 * srnd.random())
        beta = 0.6 + 0.9 * srnd.random()
        idio = 0.12 + 0.25 * srnd.random()
        out[s.code] = gen_symbol_bars(days, start, idio, beta, mkt, srnd, end)
    return out


class DemoSource:
    """data.source: demo。銘柄リストに対して擬似日足を返す（毎回同じシードなら同じ相場）"""
    name = "demo"

    def __init__(self, days: int = 700, seed: int = 42, trend: float = 0.25, universe=None):
        self.days, self.seed, self.trend = int(days), int(seed), float(trend)
        self._cache: Dict[str, List[Bar]] = {}
        self._universe = universe or []

    def prime(self, symbols: List[Symbol]):
        self._cache = gen_market(symbols, self.days, self.seed, self.trend)

    def fetch(self, code: str, days: int) -> List[Bar]:
        if code not in self._cache:
            # 単独で呼ばれた場合も同じ相場に属するよう、ユニバース全体を生成する
            syms = self._universe or [Symbol(code=code)]
            if all(s.code != code for s in syms):
                syms = syms + [Symbol(code=code)]
            self.prime(syms)
        bars = self._cache.get(code, [])
        return bars[-days:] if days > 0 else bars
