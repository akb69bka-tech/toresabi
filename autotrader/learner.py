"""定期的な再学習（ウォークフォワード方式）。

「学習」と言っても値動きを予測するのではなく、直近の相場に対して
戦略のパラメータ（移動平均の期間・ブレイクアウトの期間）を検証し直す。

採用の条件は厳格にする:
  1. 期間を分割し、前の区間で最適化した設定を後の「見ていない区間」で運用した
     成績(OOS)の平均が、現在の設定を同じ区間で運用した成績を上回ること
  2. OOS の平均がプラスであること
どちらかを満たさなければ現在の設定を維持する。過去への合わせ込みを避けるため。
"""
from __future__ import annotations
import copy
from typing import Any, Dict, List, Optional

from .simulator import Symbol, backtest

GRID = [
    {"short": sh, "long": lo, "breakout": br}
    for sh in (10, 15, 20, 25, 30)
    for lo in (50, 60, 75, 100)
    for br in (30, 40, 60)
    if sh < lo
]


def apply_params(strat: dict, params: dict) -> dict:
    st = copy.deepcopy(strat)
    st["rules"]["smaCross"]["short"] = params["short"]
    st["rules"]["smaCross"]["long"] = params["long"]
    st["rules"]["breakout"]["period"] = params["breakout"]
    return st


def current_params(strat: dict) -> dict:
    return {"short": strat["rules"]["smaCross"]["short"],
            "long": strat["rules"]["smaCross"]["long"],
            "breakout": strat["rules"]["breakout"]["period"]}


def _segments(symbols: List[Symbol], folds: int):
    dates = sorted({b.d for s in symbols for b in s.bars})
    seg = len(dates) // (folds + 1)
    if seg < 60:
        return None
    out = []
    for k in range(folds):
        out.append({
            "trainFrom": dates[k * seg], "trainTo": dates[(k + 1) * seg - 1],
            "testFrom": dates[(k + 1) * seg], "testTo": dates[min((k + 2) * seg - 1, len(dates) - 1)],
        })
    return out


def _ret(res) -> Optional[float]:
    return res["metrics"]["totalRet"] if res and res.get("metrics") else None


def walk_forward(symbols: List[Symbol], strat: dict, risk: dict, folds: int = 3,
                 grid: Optional[List[dict]] = None) -> Optional[Dict[str, Any]]:
    segs = _segments(symbols, folds)
    if not segs:
        return None
    grid = grid or GRID
    rows = []
    for k, sg in enumerate(segs):
        best = None
        for p in grid:
            r = _ret(backtest(symbols, apply_params(strat, p), risk, sg["trainFrom"], sg["trainTo"]))
            if r is not None and (best is None or r > best["ret"]):
                best = {"params": p, "ret": r}
        if not best:
            continue
        test = backtest(symbols, apply_params(strat, best["params"]), risk, sg["testFrom"], sg["testTo"])
        cur = backtest(symbols, strat, risk, sg["testFrom"], sg["testTo"])
        rows.append({
            "fold": k + 1, "params": best["params"], "trainRet": best["ret"],
            "testRet": _ret(test), "currentTestRet": _ret(cur),
            "testBH": test["buyHold"]["totalRet"] if test else None,
            "testDD": test["metrics"]["maxDD"] if test else None,
            "trades": test["metrics"]["trades"] if test else 0,
            **sg,
        })
    valid = [r for r in rows if r["testRet"] is not None]
    n = len(valid) or 1
    return {
        "rows": rows,
        "avgTrain": sum(r["trainRet"] for r in valid) / n,
        "avgTest": sum(r["testRet"] for r in valid) / n,
        "avgCurrent": sum((r["currentTestRet"] or 0) for r in valid) / n,
        "avgBH": sum((r["testBH"] or 0) for r in valid) / n,
        "posCount": sum(1 for r in valid if r["testRet"] > 0),
        "folds": len(valid),
    }


def propose(symbols: List[Symbol], strat: dict, risk: dict, cfg: dict) -> Dict[str, Any]:
    """再学習を行い、採用すべきかを判定する"""
    wf = walk_forward(symbols, strat, risk, int(cfg.get("folds", 3)))
    if not wf or not wf["rows"]:
        return {"adopt": False, "reason": "検証に必要なデータが不足しています", "wf": None,
                "current": current_params(strat), "candidate": None}
    candidate = wf["rows"][-1]["params"]
    min_oos = float(cfg.get("min_oos_return", 0.0))
    margin = 0.5
    if wf["avgTest"] <= min_oos:
        reason = f"検証期間の平均 {wf['avgTest']:+.2f}% が基準 {min_oos:+.2f}% を超えないため現状維持"
        adopt = False
    elif wf["avgTest"] <= wf["avgCurrent"] + margin:
        reason = (f"再最適化の検証成績 {wf['avgTest']:+.2f}% が現在の設定 {wf['avgCurrent']:+.2f}% を"
                  f"明確に上回らないため現状維持")
        adopt = False
    elif candidate == current_params(strat):
        reason = "最適値が現在の設定と同じです"
        adopt = False
    else:
        reason = (f"検証期間の平均 {wf['avgTest']:+.2f}%（現在の設定 {wf['avgCurrent']:+.2f}%、"
                  f"買い持ち {wf['avgBH']:+.2f}%）で上回ったため採用")
        adopt = True
    return {"adopt": adopt, "reason": reason, "wf": wf,
            "current": current_params(strat), "candidate": candidate}
