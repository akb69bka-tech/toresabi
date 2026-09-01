import os, json, math
from datetime import datetime
import pytest
from helpers import gen_bars, market
from autotrader import screener, learner
from autotrader.simulator import Symbol, Bar, backtest, create_sim, sim_step
from autotrader.config import STRATEGY_500K, RISK_500K, load_config, deep_merge, DEFAULT_CONFIG, LIVE_CONFIRM_PHRASE
from autotrader.data.base import parse_csv_bars
from autotrader.data.universe import load_universe
from autotrader.guard import Guard
from autotrader.broker.base import Order
from autotrader.broker.paper import PaperBroker
from autotrader.state import Account, load_account, save_account, seed_sim, extract_account


def cfg500():
    import copy
    return copy.deepcopy(STRATEGY_500K), copy.deepcopy(RISK_500K)


# ---------- CSV 取込 ----------
def test_parse_csv_japanese_header_and_quoted_numbers():
    bars = parse_csv_bars('日付,始値,高値,安値,終値,出来高\n2024/01/04,"2,500",2560,2490,2545,1200000\n2024/01/05,2545,2580,2530,2570,980000')
    assert len(bars) == 2 and bars[0].d == "2024-01-04" and bars[0].o == 2500 and bars[1].c == 2570

def test_parse_csv_english_and_reordered_columns():
    bars = parse_csv_bars("Date,Close,Open,High,Low,Volume\n2024-05-01,55,50,60,45,10")
    b = bars[0]; assert (b.c, b.o, b.h, b.l, b.v) == (55, 50, 60, 45, 10)

def test_parse_csv_no_header_and_tab():
    assert parse_csv_bars("2024-03-01,10,12,9,11,100\n2024-03-02,11,13,10,12,120")[1].c == 12
    assert parse_csv_bars("Date\tOpen\tHigh\tLow\tClose\tVolume\n2024-04-01\t10\t12\t9\t11\t100")[0].c == 11

def test_parse_csv_dedup_and_sort():
    bars = parse_csv_bars("2024-03-02,11,13,10,12,1\n2024-03-01,10,12,9,11,1\n2024-03-01,1,1,1,99,1")
    assert [b.d for b in bars] == ["2024-03-01", "2024-03-02"] and bars[0].c == 99


# ---------- 銘柄リスト ----------
def test_universe_from_csv_and_fallback(tmp_path):
    p = tmp_path / "u.csv"
    p.write_text("code,name,unit\n7203,トヨタ,100\n9432,NTT\n", encoding="utf-8")
    u = load_universe(str(p), default_unit=1)
    assert [(s.code, s.unit) for s in u] == [("7203", 100), ("9432", 1)]
    assert len(load_universe(str(tmp_path / "none.csv"))) > 50     # サンプルにフォールバック
    assert len(load_universe(str(tmp_path / "none.csv"), max_symbols=5)) == 5


# ---------- 設定 ----------
def test_config_merge(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("mode: live-dryrun\nrisk:\n  initialCash: 300000\nguard:\n  max_daily_orders: 1\n", encoding="utf-8")
    c = load_config(str(p))
    assert c["mode"] == "live-dryrun" and c["risk"]["initialCash"] == 300000
    assert c["risk"]["allocPct"] == RISK_500K["allocPct"]        # 未指定は既定値
    assert c["guard"]["max_daily_orders"] == 1 and c["guard"]["max_order_value_yen"] == DEFAULT_CONFIG["guard"]["max_order_value_yen"]


# ---------- スクリーナー ----------
def test_screener_filters_and_ranks():
    st, rk = cfg500()
    syms = market(seed=3, n=8, drift=8, trend=25)
    # 1銘柄を下降トレンドに、1銘柄をデータ不足にする
    syms[1].bars = gen_bars(700, 1350, 30, 99, drift_pct=-40)
    syms[2].bars = syms[2].bars[-100:]
    rows = screener.screen(syms, rk, {"min_bars": 250, "max_atr_pct": 8, "min_turnover_yen": 0})
    by = {r["code"]: r for r in rows}
    assert not by["6758"]["ok"] and "長期トレンドが下向き" in by["6758"]["reasons"]
    assert not by["9432"]["ok"] and any("データ不足" in x for x in by["9432"]["reasons"])
    ok = [r for r in rows if r["ok"]]
    assert ok and all(r["regimeOK"] for r in ok)
    assert [r["rank"] for r in ok] == list(range(1, len(ok) + 1))
    assert screener.select_watchlist(rows, 2) == [ok[0]["code"], ok[1]["code"]]

def test_screener_affordability_with_unit_100():
    st, rk = cfg500()
    rk = dict(rk, unit=100, allowMinUnit=False, allocPct=18)   # 枠 9万円
    syms = market(seed=5, n=3, drift=8, trend=25)                # 7203 は 2,800円×100 = 28万円
    rows = screener.screen(syms, rk, {"min_bars": 250, "max_atr_pct": 99, "min_turnover_yen": 0})
    r = next(r for r in rows if r["code"] == "7203")
    assert not r["affordable"] and any("投入枠" in x for x in r["reasons"])


# ---------- 学習器 ----------
def test_walk_forward_shapes_and_adoption_rule():
    st, rk = cfg500()
    syms = market(seed=7, n=3, days=700, drift=8, trend=25)
    small_grid = [{"short": 20, "long": 60, "breakout": 40}, {"short": 10, "long": 50, "breakout": 30}]
    wf = learner.walk_forward(syms, st, rk, folds=2, grid=small_grid)
    assert wf and wf["folds"] == 2 and len(wf["rows"]) == 2
    for r in wf["rows"]:
        assert r["testFrom"] > r["trainTo"]                      # 検証は学習より後
        assert r["params"] in small_grid
    res = learner.propose(syms, st, rk, {"folds": 2, "min_oos_return": 1e9})
    assert res["adopt"] is False and "基準" in res["reason"]        # 基準を超えなければ採用しない

def test_apply_params_does_not_mutate():
    st, _ = cfg500()
    new = learner.apply_params(st, {"short": 7, "long": 70, "breakout": 33})
    assert st["rules"]["smaCross"]["short"] == 20 and new["rules"]["smaCross"]["short"] == 7
    assert learner.current_params(new) == {"short": 7, "long": 70, "breakout": 33}


# ---------- 安全装置 ----------
def guard_cfg(**over):
    import copy
    c = copy.deepcopy(DEFAULT_CONFIG)
    c["guard"].update(over)
    return c

def test_guard_order_limits(tmp_path):
    g = Guard(guard_cfg(max_order_value_yen=100000, max_daily_orders=2, send_window=["08:00", "09:00"]), str(tmp_path))
    now = datetime(2026, 8, 3, 8, 30)
    ok, why = g.check_order(Order("7203", "buy", 100, est_price=2800), 500000, 0, 0, {"7203"}, now)
    assert not ok and "上限" in why
    ok, why = g.check_order(Order("7203", "buy", 30, est_price=2800), 500000, 0, 0, {"7203"}, now)
    assert ok
    ok, why = g.check_order(Order("7203", "buy", 30, est_price=2800), 500000, 0, 2, {"7203"}, now)
    assert not ok and "回数" in why
    ok, why = g.check_order(Order("9999", "buy", 30, est_price=2800), 500000, 0, 0, {"7203"}, now)
    assert not ok and "監視対象外" in why
    ok, why = g.check_order(Order("7203", "buy", 30, est_price=2800), 500000, 0, 0, {"7203"}, datetime(2026, 8, 3, 12, 0))
    assert not ok and "時刻" in why
    ok, why = g.check_order(Order("7203", "sell", 30, est_price=2800), 500000, 0, 5, None, now)   # 売りは回数制限の対象外
    assert ok

def test_guard_stop_file_blocks_everything(tmp_path):
    g = Guard(guard_cfg(stop_file=str(tmp_path / "STOP")), str(tmp_path))
    (tmp_path / "STOP").write_text("")
    ok, why = g.check_order(Order("7203", "sell", 100, est_price=100), 1e6, 0, 0, None, datetime(2026, 8, 3, 8, 30))
    assert not ok and "緊急停止" in why

def test_guard_exposure_cap(tmp_path):
    g = Guard(guard_cfg(max_total_exposure_pct=50, max_order_value_yen=10**9), str(tmp_path))
    ok, why = g.check_order(Order("7203", "buy", 100, est_price=2000), 500000, 200000, 0, None, datetime(2026, 8, 3, 8, 30))
    assert not ok and "建玉総額" in why

def test_live_gate_requires_phrase_and_paper_days(tmp_path):
    import copy
    c = copy.deepcopy(DEFAULT_CONFIG)
    g = Guard(c, str(tmp_path))
    assert not g.check_live_allowed(100)[0]
    c["live"]["enabled"] = True
    assert "confirm_phrase" in g.check_live_allowed(100)[1]
    c["live"]["confirm_phrase"] = LIVE_CONFIRM_PHRASE
    c["live"]["min_paper_days"] = 20
    ok, why = g.check_live_allowed(5); assert not ok and "実績" in why
    c["broker"]["type"] = "kabu"; c["risk"]["unit"] = 1
    ok, why = g.check_live_allowed(30); assert not ok and "単元未満株" in why
    c["risk"]["unit"] = 100
    assert g.check_live_allowed(30)[0]


# ---------- 口座状態とシミュレータの往復 ----------
def test_account_roundtrip_matches_backtest(tmp_path):
    """毎日 Account を保存→復元しながら1日ずつ進めても、一括バックテストと同じ取引になる"""
    st, rk = cfg500()
    syms = market(seed=11, n=4, days=400, drift=8, trend=25)
    full = backtest(syms, st, rk)
    dates = sorted({b.d for s in syms for b in s.bars})
    acc = Account(cash=rk["initialCash"], initialCash=rk["initialCash"])
    for d in dates[30:]:
        trunc = [Symbol(s.code, s.name, s.unit, [b for b in s.bars if b.d <= d]) for s in syms]
        sim = create_sim(trunc, st, rk, frm=d, to=d)
        seed_sim(sim, acc); sim_step(sim, finish_at_end=False); extract_account(sim, acc)
        save_account(str(tmp_path), acc)
        acc = load_account(str(tmp_path), rk["initialCash"], "paper")
    full_tr = [t for t in full["trades"] if t["reason"] != "期末清算"]
    assert len(acc.trades) == len(full_tr) > 0
    for a, b in zip(acc.trades, full_tr):
        assert (a["code"], a["entryDate"], a["exitDate"], a["qty"], a["reason"]) == (b["code"], b["entryDate"], b["exitDate"], b["qty"], b["reason"])
        assert math.isclose(a["pnl"], b["pnl"], rel_tol=1e-9, abs_tol=1e-6)


def test_paper_broker_records_only():
    acc = Account(cash=100.0, initialCash=100.0, positions={"7203": {"qty": 3, "avg": 10.0}})
    b = PaperBroker(acc)
    oid = b.send(Order("7203", "buy", 3, est_price=10))
    assert oid.startswith("PAPER-") and b.fills([oid]) == [] and b.cash() == 100.0
    assert b.positions()["7203"]["qty"] == 3
