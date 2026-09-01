"""売買シミュレータ。ブラウザ版 createSim / simStep / simFinish / simResult の移植。

1営業日ずつ進められる構造で、バックテスト・デモ再生・実運用の日次判定が
同じコードを通る。ブラウザで検証した結果と実運用の判定が一致することが目的。
"""
from __future__ import annotations
import math
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional

from .strategy import build_indicators, eval_signal


@dataclass
class Bar:
    d: str
    o: float
    h: float
    l: float
    c: float
    v: float = 0.0


@dataclass
class Symbol:
    code: str
    name: str = ""
    unit: int = 100
    bars: List[Bar] = field(default_factory=list)


def days_between(a: str, b: str) -> int:
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def yen(n: float) -> str:
    return f"{round(n):,}円"


class Ctx:
    __slots__ = ("sym", "bars", "idx", "ind")

    def __init__(self, sym: Symbol, bars: List[Bar], strat, risk):
        self.sym = sym
        self.bars = bars
        self.idx = {b.d: i for i, b in enumerate(bars)}
        self.ind = build_indicators(bars, strat, risk)


class Sim:
    def __init__(self, ctx: List[Ctx], dates: List[str], strat, risk, start_t: int):
        self.ctx = ctx
        self.dates = dates
        self.strat = strat
        self.risk = risk
        self.t = start_t
        self.startT = start_t
        self.tradeDates = dates[start_t:]
        self.cash = float(risk["initialCash"])
        self.positions: Dict[str, Dict[str, Any]] = {}
        self.trades: List[Dict[str, Any]] = []
        self.equity: List[Dict[str, Any]] = []
        self.marks: Dict[str, List[Dict[str, Any]]] = {}
        self.pending: List[Dict[str, Any]] = []
        self.halted = False
        self.haltReason = ""
        self.consecLosses = 0
        self.dayTrades = 0
        self.dayDate: Optional[str] = None
        self.costs = 0.0
        self.lastExit: Dict[str, str] = {}
        self.events: List[Dict[str, str]] = []
        self.finished = False


def create_sim(symbols: List[Symbol], strat, risk, frm: Optional[str] = None,
               to: Optional[str] = None) -> Optional[Sim]:
    ctx: List[Ctx] = []
    for s in symbols:
        bars = [b for b in s.bars if b.d <= to] if to else list(s.bars)
        if len(bars) > 30:
            ctx.append(Ctx(s, bars, strat, risk))
    if not ctx:
        return None
    date_set = set()
    for c in ctx:
        for b in c.bars:
            date_set.add(b.d)
    dates = sorted(date_set)
    start_t = 0
    if frm:
        k = next((i for i, d in enumerate(dates) if d >= frm), -1)
        if k < 0:
            return None
        start_t = k
    if start_t >= len(dates):
        return None
    return Sim(ctx, dates, strat, risk, start_t)


def sim_event(sim: Sim, msg: str):
    sim.events.append({"d": sim.dates[min(sim.t, len(sim.dates) - 1)], "m": msg})
    if len(sim.events) > 500:
        sim.events.pop(0)


def usable_cash(sim: Sim) -> float:
    reserve = sim.risk["initialCash"] * (sim.risk.get("reserveCashPct") or 0) / 100
    return max(0.0, sim.cash - reserve)


def mtm(ctx: List[Ctx], positions, d: str) -> float:
    v = 0.0
    for c in ctx:
        pos = positions.get(c.sym.code)
        if not pos:
            continue
        i = c.idx.get(d)
        price = c.bars[i].c if i is not None else pos["avg"]
        v += price * pos["qty"]
    return v


def calc_stop(c: Ctx, d: str, price: float, risk) -> Optional[float]:
    if risk.get("useAtrStop"):
        i = c.idx.get(d)
        a = c.ind["atr"][i] if i is not None else None
        if a is not None and a > 0:
            return price - a * risk["atrMult"]
    return price * (1 - risk["stopPct"] / 100) if risk["stopPct"] > 0 else None


def calc_qty(c: Ctx, i: int, price: float, equity: float, cash: float, risk) -> int:
    unit = max(1, int(risk["unit"]))
    if risk["sizing"] == "risk":
        a = c.ind["atr"][i]
        stop_dist = a * risk["atrMult"] if (a is not None and a > 0) else price * (risk["stopPct"] / 100)
        if stop_dist <= 0:
            return 0
        qty = math.floor((equity * risk["riskPct"] / 100) / stop_dist / unit) * unit
    else:
        qty = math.floor((equity * risk["allocPct"] / 100) / price / unit) * unit
    if qty < unit and risk.get("allowMinUnit"):
        qty = unit
    affordable = math.floor(cash / (price * (1 + risk["feePct"] / 100)) / unit) * unit
    return max(0, min(qty, affordable))


def sim_open(sim: Sim, c: Ctx, price: float, qty: int, d: str) -> bool:
    risk = sim.risk
    p = price * (1 + risk["slipPct"] / 100)
    cost = p * qty * (1 + risk["feePct"] / 100)
    if cost > usable_cash(sim):
        return False
    sim.cash -= cost
    sim.costs += p * qty * (risk["feePct"] / 100) + (p - price) * qty
    sim.positions[c.sym.code] = {
        "qty": qty, "avg": p, "entryDate": d, "peak": p,
        "stop": calc_stop(c, d, p, risk),
        "take": p * (1 + risk["takePct"] / 100) if risk["takePct"] > 0 else None,
    }
    sim.marks.setdefault(c.sym.code, []).append({"d": d, "type": "buy", "price": p})
    sim.dayTrades += 1
    sim_event(sim, f"買い {c.sym.code} {qty:,}株 @{p:,.1f}（{yen(cost)}）")
    return True


def sim_close(sim: Sim, c: Ctx, price: float, d: str, reason: str):
    risk = sim.risk
    code = c.sym.code
    pos = sim.positions.get(code)
    if not pos:
        return
    p = price * (1 - risk["slipPct"] / 100)
    proceeds = p * pos["qty"] * (1 - risk["feePct"] / 100)
    cost = pos["avg"] * pos["qty"] * (1 + risk["feePct"] / 100)
    sim.cash += proceeds
    sim.costs += p * pos["qty"] * (risk["feePct"] / 100) + (price - p) * pos["qty"]
    pnl = proceeds - cost
    sim.trades.append({
        "code": code, "name": c.sym.name, "qty": pos["qty"],
        "entryDate": pos["entryDate"], "entryPrice": pos["avg"],
        "exitDate": d, "exitPrice": p,
        "pnl": pnl, "pnlPct": (pnl / cost * 100) if cost > 0 else 0, "reason": reason,
        "days": days_between(pos["entryDate"], d),
    })
    sim.marks.setdefault(code, []).append({"d": d, "type": "sell", "price": p})
    del sim.positions[code]
    sim.lastExit[code] = d
    sim.consecLosses = 0 if pnl > 0 else sim.consecLosses + 1
    sign = "+" if pnl >= 0 else ""
    sim_event(sim, f"売り {code} {pos['qty']:,}株 @{p:,.1f}（{reason} {sign}{yen(pnl)}）")


def sim_safety(sim: Sim, equity_now: float):
    if sim.halted:
        return
    risk = sim.risk
    if risk.get("haltDrawdownPct", 0) > 0:
        floor_ = risk["initialCash"] * (1 - risk["haltDrawdownPct"] / 100)
        if equity_now <= floor_:
            sim.halted = True
            sim.haltReason = (f"資産が下限 {yen(floor_)}（初期資金の-{risk['haltDrawdownPct']}%）"
                              "に到達したため新規買いを停止しました")
    if not sim.halted and risk.get("maxConsecLosses", 0) > 0 and sim.consecLosses >= risk["maxConsecLosses"]:
        sim.halted = True
        sim.haltReason = f"{risk['maxConsecLosses']}連敗に到達したため新規買いを停止しました"
    if sim.halted:
        sim_event(sim, "⛔ " + sim.haltReason)


def sim_step(sim: Sim, execute_pending: bool = True, finish_at_end: bool = True) -> bool:
    """1営業日ぶん進める。最終日まで到達したら True

    execute_pending=False のときは 1) の約定処理を飛ばす。
    実発注では証券会社が約定させ、その結果を口座へ反映してから呼ぶため。
    finish_at_end=False のときは最終日でも期末清算(全建玉の強制決済)を行わない。
    日次の実運用では「今日」が常に最終日になるため必須。
    """
    if sim.finished:
        return True
    ctx, dates, risk = sim.ctx, sim.dates, sim.risk
    d = dates[sim.t]
    if sim.dayDate != d:
        sim.dayDate = d
        sim.dayTrades = 0

    # 1) 前日に生成された注文を当日始値で執行
    orders, sim.pending = sim.pending, []
    if not execute_pending:
        orders = []
    for o in orders:
        c = ctx[o["ci"]]
        i = c.idx.get(d)
        if i is None:
            continue
        bar = c.bars[i]
        if o["side"] == "buy":
            if sim.halted or c.sym.code in sim.positions:
                continue
            if len(sim.positions) >= risk["maxPositions"]:
                continue
            if risk.get("maxDailyTrades", 0) > 0 and sim.dayTrades >= risk["maxDailyTrades"]:
                continue
            equity_now = sim.cash + mtm(ctx, sim.positions, d)
            qty = calc_qty(c, i, bar.o, equity_now, usable_cash(sim), risk)
            if qty > 0:
                sim_open(sim, c, bar.o, qty, d)
        else:
            if c.sym.code in sim.positions:
                sim_close(sim, c, bar.o, d, o.get("reason") or "シグナル")

    # 2) ザラ場での損切り・利確・トレーリング判定
    for c in ctx:
        pos = sim.positions.get(c.sym.code)
        if not pos:
            continue
        i = c.idx.get(d)
        if i is None:
            continue
        bar = c.bars[i]
        if pos["stop"] is not None and bar.l <= pos["stop"]:
            sim_close(sim, c, pos["stop"], d, "損切り")
            continue
        if pos["take"] is not None and bar.h >= pos["take"]:
            sim_close(sim, c, pos["take"], d, "利確")
            continue
        if risk.get("trailPct", 0) > 0:
            pos["peak"] = max(pos["peak"], bar.h)
            trail = pos["peak"] * (1 - risk["trailPct"] / 100)
            if pos["stop"] is None or trail > pos["stop"]:
                pos["stop"] = trail
        if risk.get("maxHoldDays", 0) > 0 and days_between(pos["entryDate"], d) >= risk["maxHoldDays"]:
            sim_close(sim, c, bar.c, d, "期限到来")

    # 3) 当日終値でシグナルを評価し、翌日の注文を作成
    for ci, c in enumerate(ctx):
        i = c.idx.get(d)
        if i is None or i < 30:
            continue
        sig = eval_signal(c.bars, c.ind, i, sim.strat)
        code = c.sym.code
        pos = sim.positions.get(code)
        if sig["action"] == "buy" and not pos:
            last = sim.lastExit.get(code)
            if risk.get("cooldownDays", 0) > 0 and last and days_between(last, d) < risk["cooldownDays"]:
                continue
            sim.pending.append({"ci": ci, "side": "buy"})
        elif sig["action"] == "sell" and pos:
            if risk.get("minHoldDays", 0) > 0 and days_between(pos["entryDate"], d) < risk["minHoldDays"]:
                continue
            sim.pending.append({"ci": ci, "side": "sell", "reason": "売りシグナル"})

    # 4) 時価評価と安全装置
    eq = sim.cash + mtm(ctx, sim.positions, d)
    sim.equity.append({"d": d, "e": eq})
    sim_safety(sim, eq)

    sim.t += 1
    if sim.t >= len(dates):
        if finish_at_end:
            sim_finish(sim)
        return True
    return False


def sim_finish(sim: Sim):
    if sim.finished:
        return
    last = sim.dates[-1]
    for c in sim.ctx:
        if c.sym.code in sim.positions:
            i = c.idx.get(last)
            if i is not None:
                sim_close(sim, c, c.bars[i].c, last, "期末清算")
    if sim.equity:
        sim.equity[-1]["e"] = sim.cash
    sim.finished = True


# ---------- 成績 ----------
def calc_metrics(equity, trades, initial: float) -> Optional[Dict[str, Any]]:
    if not equity:
        return None
    last = equity[-1]["e"]
    total_pnl = last - initial
    total_ret = total_pnl / initial * 100 if initial > 0 else 0
    peak, max_dd = -math.inf, 0.0
    dd_series = []
    for p in equity:
        peak = max(peak, p["e"])
        dd = (p["e"] - peak) / peak * 100 if peak > 0 else 0
        max_dd = min(max_dd, dd)
        dd_series.append(dd)
    rets = [equity[i]["e"] / equity[i - 1]["e"] - 1 for i in range(1, len(equity)) if equity[i - 1]["e"] > 0]
    mean = sum(rets) / len(rets) if rets else 0
    sd = math.sqrt(sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)) if len(rets) > 1 else 0
    sharpe = mean / sd * math.sqrt(252) if sd > 0 else 0
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    sum_win = sum(t["pnl"] for t in wins)
    sum_loss = abs(sum(t["pnl"] for t in losses))
    years = max(days_between(equity[0]["d"], equity[-1]["d"]) / 365, 1 / 365) if len(equity) > 1 else 1
    cagr = ((last / initial) ** (1 / years) - 1) * 100 if initial > 0 and last > 0 else 0
    return {
        "finalEquity": last, "totalPnl": total_pnl, "totalRet": total_ret, "cagr": cagr,
        "maxDD": max_dd, "sharpe": sharpe, "ddSeries": dd_series,
        "trades": len(trades),
        "winRate": len(wins) / len(trades) * 100 if trades else 0,
        "avgWin": sum_win / len(wins) if wins else 0,
        "avgLoss": -sum_loss / len(losses) if losses else 0,
        "pf": (sum_win / sum_loss) if sum_loss > 0 else (math.inf if sum_win > 0 else 0),
        "expectancy": total_pnl / len(trades) if trades else 0,
        "avgDays": sum(t["days"] for t in trades) / len(trades) if trades else 0,
    }


def buy_hold_curve(ctx: List[Ctx], dates: List[str], risk) -> List[Dict[str, Any]]:
    per = risk["initialCash"] / len(ctx)
    unit = max(1, int(risk["unit"]))
    cash = float(risk["initialCash"])
    holds = []
    d0 = dates[0]
    for c in ctx:
        i0 = c.idx.get(d0)
        if i0 is None:
            continue
        b0 = c.bars[i0]
        qty = math.floor(per / b0.o / unit) * unit
        cost = qty * b0.o * (1 + risk["feePct"] / 100)
        if qty > 0 and cost <= cash:
            cash -= cost
            holds.append({"c": c, "qty": qty, "last": b0.o})
    out = []
    for d in dates:
        v = cash
        for h in holds:
            i = h["c"].idx.get(d)
            if i is not None:
                h["last"] = h["c"].bars[i].c
            v += h["last"] * h["qty"]
        out.append({"d": d, "e": v})
    return out


def monthly_returns(equity):
    if not equity:
        return []
    by_month: Dict[str, float] = {}
    for p in equity:
        by_month[p["d"][:7]] = p["e"]
    out = []
    prev = equity[0]["e"]
    for k in sorted(by_month):
        out.append({"m": k, "r": (by_month[k] / prev - 1) * 100 if prev > 0 else 0, "e": by_month[k]})
        prev = by_month[k]
    return out


def steadiness(equity, trades, initial):
    months = monthly_returns(equity)
    win_months = sum(1 for m in months if m["r"] > 0)
    worst_month = min((m["r"] for m in months), default=0)
    streak = max_streak = 0
    for t in trades:
        streak = 0 if t["pnl"] > 0 else streak + 1
        max_streak = max(max_streak, streak)
    peak, peak_date, worst_dd, trough_date, dd_peak_date = -math.inf, None, 0.0, None, None
    for p in equity:
        if p["e"] > peak:
            peak, peak_date = p["e"], p["d"]
        dd = (p["e"] - peak) / peak * 100 if peak > 0 else 0
        if dd < worst_dd:
            worst_dd, trough_date, dd_peak_date = dd, p["d"], peak_date
    recovery = None
    if trough_date is not None:
        peak_val = next((p for p in equity if p["d"] == dd_peak_date), None)
        rec = next((p for p in equity if p["d"] > trough_date and peak_val and p["e"] >= peak_val["e"]), None)
        if rec:
            recovery = days_between(trough_date, rec["d"])
    return {
        "months": months, "winMonths": win_months, "monthCount": len(months),
        "winMonthRate": win_months / len(months) * 100 if months else 0,
        "worstMonth": worst_month, "maxLoseStreak": max_streak,
        "recoveryDays": recovery, "stillUnderwater": trough_date is not None and recovery is None,
    }


def sim_result(sim: Sim) -> Dict[str, Any]:
    return {
        "equity": sim.equity, "trades": sim.trades, "marks": sim.marks,
        "dates": sim.dates, "halted": sim.halted, "haltReason": sim.haltReason,
        "costs": sim.costs,
        "metrics": calc_metrics(sim.equity, sim.trades, sim.risk["initialCash"]),
        "buyHold": calc_metrics(buy_hold_curve(sim.ctx, sim.tradeDates, sim.risk), [], sim.risk["initialCash"]),
        "steady": steadiness(sim.equity, sim.trades, sim.risk["initialCash"]),
    }


def backtest(symbols: List[Symbol], strat, risk, frm=None, to=None) -> Optional[Dict[str, Any]]:
    sim = create_sim(symbols, strat, risk, frm, to)
    if sim is None:
        return None
    while not sim_step(sim):
        pass
    return sim_result(sim)
