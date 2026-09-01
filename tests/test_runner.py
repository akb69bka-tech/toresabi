import copy, json, math, os
from datetime import datetime
import pytest
from helpers import market, write_csv_dir
from mock_kabu import MockKabu
from autotrader.config import DEFAULT_CONFIG, LIVE_CONFIRM_PHRASE
from autotrader.runner import Runner
from autotrader.simulator import Symbol, backtest
from autotrader.state import load_json


def make_cfg(tmp_path, syms, **over):
    write_csv_dir(str(tmp_path / "csv"), syms)
    (tmp_path / "universe.csv").write_text("code,name\n" + "".join(f"{s.code},{s.name}\n" for s in syms), encoding="utf-8")
    c = copy.deepcopy(DEFAULT_CONFIG)
    c["data"].update({"source": "csv", "csv_dir": str(tmp_path / "csv")})
    c["universe"]["file"] = str(tmp_path / "universe.csv")
    c["state_dir"] = str(tmp_path / "state")
    c["guard"]["stop_file"] = str(tmp_path / "STOP")
    c["screener"]["enabled"] = False
    c["learner"]["enabled"] = False
    for k, v in over.items():
        if isinstance(v, dict): c[k].update(v)
        else: c[k] = v
    return c


def truncated(syms, d):
    return [Symbol(s.code, s.name, s.unit, [b for b in s.bars if b.d <= d]) for s in syms]


# ---------- ペーパー運用: 日次サイクルの積み重ね == 一括バックテスト ----------
def test_paper_daily_cycles_match_backtest(tmp_path):
    syms = market(seed=21, n=4, days=400, drift=8, trend=25)
    cfg = make_cfg(tmp_path, syms, mode="paper")
    dates = sorted({b.d for s in syms for b in s.bars})
    for d in dates[30:]:
        r = Runner(cfg)                                   # 毎回プロセスを起動し直す想定
        r.signal_cycle(today=d, symbols=truncated(syms, d))
    r = Runner(cfg)
    full = [t for t in backtest(syms, cfg["strategy"], cfg["risk"])["trades"] if t["reason"] != "期末清算"]
    assert len(r.acc.trades) == len(full) > 0
    for a, b in zip(r.acc.trades, full):
        assert (a["code"], a["entryDate"], a["exitDate"], a["qty"]) == (b["code"], b["entryDate"], b["exitDate"], b["qty"])
        assert math.isclose(a["pnl"], b["pnl"], rel_tol=1e-9, abs_tol=1e-6)
    assert r.acc.paperDays == len(dates) - 30
    sig = load_json(cfg["state_dir"], "signals.json")
    assert sig["date"] == dates[-1] and "signals" in sig


def test_cycle_is_idempotent_per_day(tmp_path):
    syms = market(seed=22, n=3, days=300, drift=8, trend=25)
    cfg = make_cfg(tmp_path, syms, mode="paper")
    r = Runner(cfg); d = syms[0].bars[-1].d
    a = r.signal_cycle(today=d); b = r.signal_cycle(today=d)
    assert a["ok"] and b.get("skipped") is True
    assert Runner(cfg).acc.paperDays == 1


def test_screener_limits_tradable_set(tmp_path):
    syms = market(seed=23, n=6, days=400, drift=8, trend=25)
    cfg = make_cfg(tmp_path, syms, mode="paper", screener={"enabled": True, "top_n": 2, "min_turnover_yen": 0, "max_atr_pct": 99})
    r = Runner(cfg); res = r.signal_cycle()
    assert len(r.acc.watchlist) <= 2
    assert all(s["code"] in r.acc.watchlist for s in res["signals"])
    assert os.path.exists(os.path.join(cfg["state_dir"], "screener.json"))


# ---------- ライブ試運転: 接続はするが発注しない ----------
def test_live_dryrun_never_sends(tmp_path):
    syms = market(seed=24, n=4, days=400, drift=12, trend=30, unit=100)
    m = MockKabu(); url = m.start()
    cfg = make_cfg(tmp_path, syms, mode="live-dryrun",
                   broker={"type": "kabu", "kabu": {"base_url": url, "api_password": "apipw", "order_password": "ordpw"}},
                   risk={"unit": 100, "allowMinUnit": True, "initialCash": 2000000, "allocPct": 20})
    cfg["guard"].update({"send_window": ["00:00", "23:59"], "max_order_value_yen": 10**9, "max_daily_orders": 10})
    # 買い注文が出るまで日を進める
    dates = sorted({b.d for s in syms for b in s.bars})
    sent = None
    for d in dates[200:]:
        r = Runner(cfg); res = r.signal_cycle(today=d, symbols=truncated(syms, d))
        if res.get("orders"):
            o = Runner(cfg).order_cycle(); sent = o; break
    assert sent and sent["ok"] and sent["sent"]
    assert all(p["orderId"] == "DRYRUN" for p in sent["sent"])
    assert not any(path.endswith("/sendorder") for _, path in m.received)      # 実発注は一切していない
    assert any(path.endswith("/token") for _, path in m.received)             # 接続確認はしている
    r = Runner(cfg); r.reconcile(symbols=truncated(syms, d)); assert r.acc.pending == []
    m.stop()


# ---------- ライブ: 許可がなければ拒否、許可があれば発注→約定反映 ----------
def test_live_refused_without_confirmation(tmp_path):
    syms = market(seed=25, n=3, days=300, drift=8, trend=25, unit=100)
    m = MockKabu(); url = m.start()
    cfg = make_cfg(tmp_path, syms, mode="live",
                   broker={"type": "kabu", "kabu": {"base_url": url, "api_password": "apipw", "order_password": "ordpw"}},
                   risk={"unit": 100})
    r = Runner(cfg); r.acc.pending = [{"code": "7203", "side": "buy", "qty": 100, "estPrice": 100.0}]
    res = r.order_cycle()
    assert not res["ok"] and "live.enabled" in res["reason"]
    assert not any(p.endswith("/sendorder") for _, p in m.received)
    m.stop()


def test_live_flow_send_and_reconcile(tmp_path):
    syms = market(seed=26, n=4, days=400, drift=12, trend=30, unit=100)
    m = MockKabu(cash=2000000.0); url = m.start()
    cfg = make_cfg(tmp_path, syms, mode="live",
                   broker={"type": "kabu", "kabu": {"base_url": url, "api_password": "apipw", "order_password": "ordpw"}},
                   risk={"unit": 100, "allowMinUnit": True, "initialCash": 2000000, "allocPct": 20, "feePct": 0.1},
                   live={"enabled": True, "confirm_phrase": LIVE_CONFIRM_PHRASE, "min_paper_days": 0})
    cfg["guard"].update({"send_window": ["00:00", "23:59"], "max_order_value_yen": 10**9, "max_daily_orders": 10})
    dates = sorted({b.d for s in syms for b in s.bars})
    orders = None
    for i, d in enumerate(dates[200:], 200):
        r = Runner(cfg); res = r.signal_cycle(today=d, symbols=truncated(syms, d))
        if res.get("orders"):
            orders = res["orders"]; next_d = dates[i + 1]; break
    assert orders
    o = Runner(cfg).order_cycle()
    assert o["ok"] and o["sent"] and all(p["orderId"].startswith("2026MOCK") for p in o["sent"])
    assert any(p.endswith("/sendorder") for _, p in m.received)
    # 翌朝の寄付で約定 → 夕方に反映
    opens = {s.code: next((b.o for b in s.bars if b.d == next_d), s.bars[-1].o) for s in syms}
    m.execute_all(opens)
    r = Runner(cfg); rec = r.reconcile(symbols=truncated(syms, next_d))
    assert rec["ok"] and rec["fills"]
    for p in orders:
        if p["side"] == "buy":
            pos = r.acc.positions[p["code"]]
            assert pos["qty"] == p["qty"] and pos["avg"] == opens[p["code"]] and pos["stop"] is not None
    assert r.acc.pending == [] and r.acc.cash == m.cash      # 現金は証券会社の値に同期
    # その後の判定では約定処理を飛ばす（二重計上しない）
    res2 = Runner(cfg).signal_cycle(today=next_d, symbols=truncated(syms, next_d))
    assert res2["ok"] and all(Runner(cfg).acc.positions[c]["qty"] == p["qty"] for c, p in
                              ((p["code"], p) for p in orders if p["side"] == "buy"))
    m.stop()


def test_stop_file_blocks_live_orders(tmp_path):
    syms = market(seed=27, n=3, days=300, drift=8, trend=25, unit=100)
    m = MockKabu(); url = m.start()
    cfg = make_cfg(tmp_path, syms, mode="live",
                   broker={"type": "kabu", "kabu": {"base_url": url, "api_password": "apipw", "order_password": "ordpw"}},
                   risk={"unit": 100}, live={"enabled": True, "confirm_phrase": LIVE_CONFIRM_PHRASE, "min_paper_days": 0})
    cfg["guard"].update({"send_window": ["00:00", "23:59"]})
    (tmp_path / "STOP").write_text("")
    r = Runner(cfg); r.acc.pending = [{"code": "7203", "side": "buy", "qty": 100, "estPrice": 100.0}]
    res = r.order_cycle()
    assert res["ok"] and not res["sent"] and "緊急停止" in res["rejected"][0]["why"]
    assert not any(p.endswith("/sendorder") for _, p in m.received)
    m.stop()


def test_learn_and_demo_commands(tmp_path):
    syms = market(seed=28, n=3, days=500, drift=8, trend=25)
    cfg = make_cfg(tmp_path, syms, mode="paper", learner={"enabled": True, "folds": 2})
    r = Runner(cfg)
    d = r.demo(days=200)
    assert d["ok"] and d["trades"] >= 0 and "buyHoldRet" in d
    import autotrader.learner as L
    L_grid = L.GRID; L.GRID = L_grid[:3]
    try:
        res = r.learn()
    finally:
        L.GRID = L_grid
    assert "adopt" in res and os.path.exists(os.path.join(cfg["state_dir"], "learner.json"))
