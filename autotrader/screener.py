"""全銘柄スクリーニング。

監視対象の全銘柄を毎日走査し、いま買う候補として妥当な銘柄を絞り込む。
「値動きを予測する」のではなく、以下の観点で足切りと順位付けを行う。

- 長期トレンドが上向きか（終値 > 200日移動平均）
- 6か月の値動き（モメンタム）
- 直近60日のうち50日移動平均より上で推移した日数の割合（トレンドの安定性）
- 日々の変動幅(ATR%)が大きすぎないか
- 流動性（1日平均の売買代金）が十分か
- 資金で買える価格帯か
"""
from __future__ import annotations
from typing import Any, Dict, List

from .indicators import sma, atr
from .simulator import Symbol


def score_symbol(sym: Symbol, risk: dict, cfg: dict) -> Dict[str, Any]:
    bars = sym.bars
    n = len(bars)
    row: Dict[str, Any] = {"code": sym.code, "name": sym.name, "bars": n, "ok": False, "reasons": []}
    if n < cfg.get("min_bars", 250):
        row["reasons"].append(f"データ不足({n}本)")
        return row
    closes = [b.c for b in bars]
    last = bars[-1]
    row["price"] = last.c
    row["date"] = last.d

    s200 = sma(closes, 200)[-1]
    s50 = sma(closes, 50)
    row["regimeOK"] = s200 is not None and last.c >= s200
    row["mom126"] = (last.c / closes[-127] - 1) * 100 if n > 127 else 0.0
    window = list(range(max(0, n - 60), n))
    above = sum(1 for i in window if s50[i] is not None and closes[i] > s50[i])
    row["trendQ"] = above / len(window) * 100 if window else 0.0
    a = atr(bars, 14)[-1]
    row["atrPct"] = (a / last.c * 100) if (a and last.c > 0) else 0.0
    turn = [b.c * b.v for b in bars[-20:]]
    row["turnover"] = sum(turn) / len(turn) if turn else 0.0

    unit = max(1, int(risk.get("unit", 100)))
    budget = risk["initialCash"] * risk["allocPct"] / 100
    lot = last.c * unit
    row["lot"] = lot
    row["affordable"] = lot <= budget or (risk.get("allowMinUnit") and lot <= risk["initialCash"])

    if not row["regimeOK"]:
        row["reasons"].append("長期トレンドが下向き")
    if row["atrPct"] > cfg.get("max_atr_pct", 6.0):
        row["reasons"].append(f"変動が大きすぎる(ATR {row['atrPct']:.1f}%)")
    if row["turnover"] < cfg.get("min_turnover_yen", 0):
        row["reasons"].append("流動性が不足")
    if not row["affordable"]:
        row["reasons"].append(f"1単位 {lot:,.0f}円 が投入枠を超える")
    row["ok"] = not row["reasons"]
    return row


def screen(symbols: List[Symbol], risk: dict, cfg: dict) -> List[Dict[str, Any]]:
    rows = [score_symbol(s, risk, cfg) for s in symbols]
    ok = [r for r in rows if r["ok"]]
    # 順位の平均でスコア化（モメンタム高い・トレンド安定・変動は控えめ、を良しとする）
    def ranks(key, reverse):
        srt = sorted(ok, key=lambda r: r[key], reverse=reverse)
        return {r["code"]: i for i, r in enumerate(srt)}
    r_m = ranks("mom126", True)
    r_t = ranks("trendQ", True)
    r_a = ranks("atrPct", False)
    for r in ok:
        r["score"] = (r_m[r["code"]] * 0.45 + r_t[r["code"]] * 0.4 + r_a[r["code"]] * 0.15)
    ok.sort(key=lambda r: r["score"])
    for i, r in enumerate(ok):
        r["rank"] = i + 1
    rejected = [r for r in rows if not r["ok"]]
    return ok + rejected


def select_watchlist(rows: List[Dict[str, Any]], top_n: int) -> List[str]:
    return [r["code"] for r in rows if r.get("ok")][:top_n]
