"""シグナル判定。ブラウザ版 buildIndicators / evalSignal の移植。"""
from __future__ import annotations
from typing import Any, Dict, List

from . import indicators as ind_


def build_indicators(bars, strat: Dict[str, Any], risk: Dict[str, Any]) -> Dict[str, Any]:
    closes = [b.c for b in bars]
    r = strat["rules"]
    regime = r.get("regime") or {"period": 200}
    return {
        "smaS": ind_.sma(closes, max(2, int(r["smaCross"]["short"]))),
        "smaL": ind_.sma(closes, max(3, int(r["smaCross"]["long"]))),
        "emaT": ind_.ema(closes, max(3, int(r["emaTrend"]["period"]))),
        "rsi": ind_.rsi(closes, max(2, int(r["rsi"]["period"]))),
        "macd": ind_.macd(closes, max(2, int(r["macd"]["fast"])), max(3, int(r["macd"]["slow"])),
                          max(2, int(r["macd"]["signal"]))),
        "boll": ind_.bollinger(closes, max(3, int(r["boll"]["period"])), float(r["boll"]["k"])),
        "hh": ind_.highest_prev([b.h for b in bars], max(3, int(r["breakout"]["period"]))),
        "ll": ind_.lowest_prev([b.l for b in bars], max(3, int(r["breakout"]["period"]))),
        "volMa": ind_.sma([b.v or 0 for b in bars], max(3, int(r["volume"]["period"]))),
        "regime": ind_.sma(closes, max(20, int(regime["period"]))),
        "atr": ind_.atr(bars, max(2, int(risk["atrPeriod"]))),
    }


def eval_signal(bars, ind: Dict[str, Any], i: int, strat: Dict[str, Any]) -> Dict[str, Any]:
    """i 本目の終値時点でのシグナルを評価する"""
    r = strat["rules"]
    votes: List[Dict[str, Any]] = []
    score = 0.0
    b = bars[i]

    def add(key, d, weight, text):
        nonlocal score
        if not d:
            return
        score += d * weight
        votes.append({"key": key, "dir": d, "weight": weight, "text": text})

    if r["smaCross"]["on"] and ind["smaS"][i] is not None and ind["smaL"][i] is not None:
        now = ind["smaS"][i] - ind["smaL"][i]
        if i > 0 and ind["smaS"][i - 1] is not None and ind["smaL"][i - 1] is not None:
            prev = ind["smaS"][i - 1] - ind["smaL"][i - 1]
        else:
            prev = now
        crossed = (prev <= 0 and now > 0) or (prev >= 0 and now < 0)
        d = 1 if now > 0 else (-1 if now < 0 else 0)
        w = r["smaCross"]["w"] * (2 if crossed else 1)
        add("smaCross", d, w,
            ("ゴールデンクロス" if d > 0 else "デッドクロス") if crossed
            else ("短期>長期" if d > 0 else "短期<長期"))
    if r["emaTrend"]["on"] and ind["emaT"][i] is not None:
        d = 1 if b.c > ind["emaT"][i] else -1
        add("emaTrend", d, r["emaTrend"]["w"], "EMA上" if d > 0 else "EMA下")
    if r["rsi"]["on"] and ind["rsi"][i] is not None:
        d, txt = 0, ""
        if ind["rsi"][i] <= r["rsi"]["low"]:
            d, txt = 1, "RSI%d（売られすぎ）" % round(ind["rsi"][i])
        elif ind["rsi"][i] >= r["rsi"]["high"]:
            d, txt = -1, "RSI%d（買われすぎ）" % round(ind["rsi"][i])
        add("rsi", d, r["rsi"]["w"], txt)
    if r["macd"]["on"] and ind["macd"]["hist"][i] is not None:
        h = ind["macd"]["hist"][i]
        ph = ind["macd"]["hist"][i - 1] if (i > 0 and ind["macd"]["hist"][i - 1] is not None) else h
        crossed = (ph <= 0 and h > 0) or (ph >= 0 and h < 0)
        d = 1 if h > 0 else (-1 if h < 0 else 0)
        add("macd", d, r["macd"]["w"] * (2 if crossed else 1),
            "MACDシグナルクロス" if crossed else ("MACD陽転中" if d > 0 else "MACD陰転中"))
    if r["boll"]["on"] and ind["boll"]["upper"][i] is not None:
        d, txt = 0, ""
        if b.c < ind["boll"]["lower"][i]:
            d, txt = 1, "−σ割れ"
        elif b.c > ind["boll"]["upper"][i]:
            d, txt = -1, "+σ超え"
        add("boll", d, r["boll"]["w"], txt)
    if r["breakout"]["on"] and ind["hh"][i] is not None:
        d, txt = 0, ""
        if b.c > ind["hh"][i]:
            d, txt = 1, "%d日高値更新" % r["breakout"]["period"]
        elif b.c < ind["ll"][i]:
            d, txt = -1, "%d日安値更新" % r["breakout"]["period"]
        add("breakout", d, r["breakout"]["w"], txt)

    volume_ok = True
    if r["volume"]["on"] and ind["volMa"][i] is not None and ind["volMa"][i] > 0:
        volume_ok = (b.v or 0) >= ind["volMa"][i] * r["volume"]["mult"]
    regime_ok = True
    rg = r.get("regime")
    if rg and rg["on"] and ind.get("regime") and ind["regime"][i] is not None:
        regime_ok = b.c >= ind["regime"][i]

    action = "hold"
    if strat["mode"] == "all":
        dirs = [v["dir"] for v in votes]
        if dirs and all(d > 0 for d in dirs):
            action = "buy"
        elif dirs and all(d < 0 for d in dirs):
            action = "sell"
    else:
        if score >= strat["buyTh"]:
            action = "buy"
        elif score <= -strat["sellTh"]:
            action = "sell"

    if strat.get("exitPolicy") == "trend":
        below = ind.get("regime") and ind["regime"][i] is not None and b.c < ind["regime"][i]
        if action == "sell":
            action = "sell" if below else "hold"
        elif below:
            action = "sell"

    blocked = ""
    if action == "buy" and not regime_ok:
        action, blocked = "hold", "長期トレンドが下向きのため見送り"
    elif action == "buy" and not volume_ok:
        action, blocked = "hold", "出来高が不足しているため見送り"

    return {"action": action, "score": score, "votes": votes,
            "volumeOK": volume_ok, "regimeOK": regime_ok, "blocked": blocked}
