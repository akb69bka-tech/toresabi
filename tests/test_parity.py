"""ブラウザ版(JavaScript)と自走エンジン(Python)の判定が完全一致することを検証する。

parity_cases.json はブラウザ版のエンジンで生成した売買結果。
同じ入力を Python エンジンに与えて、取引履歴・資産曲線・コストが一致することを確かめる。
これが保証されるので、ブラウザで検証した設定をそのまま自走させられる。
"""
import json, os, math
import pytest
from autotrader.simulator import Symbol, Bar, backtest

HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(HERE, "parity_cases.json"), encoding="utf-8") as f:
    CASES = json.load(f)


def to_symbols(raw):
    return [Symbol(code=s["code"], name=s["name"], unit=s["unit"],
                   bars=[Bar(b["d"], b["o"], b["h"], b["l"], b["c"], b["v"]) for b in s["bars"]])
            for s in raw]


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_trades_match_browser(case):
    res = backtest(to_symbols(case["symbols"]), case["strategy"], case["risk"], case["from"], case["to"])
    assert res is not None
    js, py = case["trades"], res["trades"]
    assert len(py) == len(js), f"取引数が不一致: JS {len(js)} / Python {len(py)}"
    for a, b in zip(js, py):
        assert (a["code"], a["entryDate"], a["exitDate"], a["qty"], a["reason"]) == \
               (b["code"], b["entryDate"], b["exitDate"], b["qty"], b["reason"])
        assert math.isclose(a["entryPrice"], b["entryPrice"], rel_tol=1e-9)
        assert math.isclose(a["exitPrice"], b["exitPrice"], rel_tol=1e-9)
        assert math.isclose(a["pnl"], b["pnl"], rel_tol=1e-9, abs_tol=1e-6)


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_equity_and_costs_match_browser(case):
    res = backtest(to_symbols(case["symbols"]), case["strategy"], case["risk"], case["from"], case["to"])
    assert len(res["equity"]) == len(case["equity"])
    for a, b in zip(case["equity"], res["equity"]):
        assert a["d"] == b["d"]
        assert math.isclose(a["e"], b["e"], rel_tol=1e-9, abs_tol=1e-6)
    assert math.isclose(case["costs"], res["costs"], rel_tol=1e-9, abs_tol=1e-6)
    assert case["halted"] == res["halted"]


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_metrics_match_browser(case):
    res = backtest(to_symbols(case["symbols"]), case["strategy"], case["risk"], case["from"], case["to"])
    m, jm = res["metrics"], case["metrics"]
    for k in ("totalRet", "maxDD", "sharpe", "cagr", "winRate", "expectancy"):
        assert math.isclose(m[k], jm[k], rel_tol=1e-9, abs_tol=1e-9), k
    assert math.isclose(res["buyHold"]["totalRet"], case["buyHold"]["totalRet"], rel_tol=1e-9)
    assert math.isclose(res["steady"]["winMonthRate"], case["steady"]["winMonthRate"], rel_tol=1e-9)
    assert res["steady"]["maxLoseStreak"] == case["steady"]["maxLoseStreak"]
