"""複数戦略を同じデータ・同じ資金管理で比較する（backtrader の optstrategy / Lean の比較を参考）。

「どの戦略が本当に機能しているか」を、買い持ちを基準に横並びで見るための機能。
"""
from __future__ import annotations
import copy
from typing import Any, Dict, List

from .simulator import Symbol, backtest
from .strategies import get_strategy, STRATEGY_TYPES, describe_all


def strategy_config(type_: str, base_cfg: dict) -> dict:
    if type_ == "score":
        c = copy.deepcopy(base_cfg["strategy"]) if base_cfg["strategy"].get("type", "score") == "score" \
            else copy.deepcopy(__import__("autotrader.config", fromlist=["STRATEGY_500K"]).STRATEGY_500K)
        c["type"] = "score"
        return c
    return {"type": type_, "params": {}}


def risk_for(type_: str, risk: dict) -> dict:
    """戦略の性質に合わせた資金配分（ローテーションは均等配分）"""
    r = copy.deepcopy(risk)
    if type_ == "momentum":
        k = 3
        r["maxPositions"] = k
        r["allocPct"] = max(5, int((100 - r.get("reserveCashPct", 0)) / k) - 1)
        r["minHoldDays"] = 0
        r["cooldownDays"] = 0
    if type_ == "pullback":
        r["minHoldDays"] = 0          # 数日で回転させる戦略なので制限を外す
        r["cooldownDays"] = 2
    return r


def compare(symbols: List[Symbol], cfg: dict, types: List[str] = None, frm=None, to=None) -> List[Dict[str, Any]]:
    types = types or cfg.get("compare", {}).get("strategies") or list(STRATEGY_TYPES)
    rows = []
    bh_done = False
    for t in types:
        sc = strategy_config(t, cfg)
        rk = risk_for(t, cfg["risk"])
        res = backtest(symbols, sc, rk, frm, to)
        if not res:
            continue
        m, st = res["metrics"], res["steady"]
        if not bh_done:
            bh = res["buyHold"]
            rows.append({"type": "buyhold", "label": "買って持ち続ける（基準）", "totalRet": bh["totalRet"],
                         "maxDD": bh["maxDD"], "trades": 0, "costs": 0, "winMonthRate": None,
                         "maxLoseStreak": 0, "finalEquity": bh["finalEquity"], "halted": False,
                         "equity": res["buyHoldCurve"]})
            bh_done = True
        rows.append({"type": t, "label": get_strategy(sc).label, "totalRet": m["totalRet"], "maxDD": m["maxDD"],
                     "trades": m["trades"], "costs": res["costs"], "winMonthRate": st["winMonthRate"],
                     "maxLoseStreak": st["maxLoseStreak"], "finalEquity": m["finalEquity"],
                     "winRate": m["winRate"], "sharpe": m["sharpe"], "halted": res["halted"],
                     "equity": res["equity"]})
    return rows


def verdict(rows: List[dict]) -> str:
    bh = next((r for r in rows if r["type"] == "buyhold"), None)
    strat = [r for r in rows if r["type"] != "buyhold"]
    if not strat:
        return "比較できる結果がありません"
    best = max(strat, key=lambda r: r["totalRet"])
    if bh and best["totalRet"] <= bh["totalRet"]:
        safer = [r for r in strat if r["maxDD"] > bh["maxDD"] + 3]
        msg = (f"この期間はどの戦略も買い持ち（{bh['totalRet']:+.1f}%）を上回れませんでした。")
        if safer:
            s = max(safer, key=lambda r: r["totalRet"])
            msg += f" ただし「{s['label']}」は最大下落が {s['maxDD']:.1f}%（買い持ち {bh['maxDD']:.1f}%）と浅く、守りには働いています。"
        return msg
    return (f"最も良かったのは「{best['label']}」で {best['totalRet']:+.1f}%"
            + (f"（買い持ち {bh['totalRet']:+.1f}%）" if bh else "")
            + f"、最大下落 {best['maxDD']:.1f}%、売買 {best['trades']}回。ただし1つの期間の結果なので、別の期間やウォークフォワードでも確かめてください。")
