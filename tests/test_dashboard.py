import copy, os
from helpers import market, write_csv_dir
from autotrader.config import DEFAULT_CONFIG
from autotrader.runner import Runner
from autotrader.dashboard import DashboardApp


def make_cfg(tmp_path):
    syms = market(seed=61, n=6, days=500, drift=8, trend=25)
    write_csv_dir(str(tmp_path / "csv"), syms)
    (tmp_path / "universe.csv").write_text("code,name\n" + "".join(f"{s.code},{s.name}\n" for s in syms), encoding="utf-8")
    c = copy.deepcopy(DEFAULT_CONFIG)
    c["mode"] = "demo"
    c["data"].update({"source": "csv", "csv_dir": str(tmp_path / "csv")})
    c["universe"]["file"] = str(tmp_path / "universe.csv")
    c["state_dir"] = str(tmp_path / "state")
    c["guard"]["stop_file"] = str(tmp_path / "STOP")
    c["screener"].update({"min_turnover_yen": 0, "max_atr_pct": 99, "top_n": 6})
    return c


def test_demo_playback_controls(tmp_path):
    app = DashboardApp(make_cfg(tmp_path), Runner)
    assert app.state()["demo"] is None
    r = app.api("/api/demo/start", {"days": 120})
    assert r["ok"] and r["days"] == 120
    d = app.state()["demo"]; assert d["done"] == 0 and d["total"] == 120 and d["equity"] == d["initial"]
    app.api("/api/demo/step", {"n": 10})
    d = app.state()["demo"]; assert d["done"] == 10 and not d["finished"] and len(d["equityCurve"]) == 10
    app.api("/api/demo/run", {})
    d = app.state()["demo"]; assert d["finished"] and d["result"] is not None and d["buyHold"] is not None
    assert d["done"] == d["total"]
    app.api("/api/demo/reset", {})
    assert app.state()["demo"] is None


def test_step_without_start_auto_starts(tmp_path):
    app = DashboardApp(make_cfg(tmp_path), Runner)
    app.api("/api/demo/step", {"n": 3})
    assert app.state()["demo"]["done"] == 3


def test_strategy_switch_affects_demo_and_runner(tmp_path):
    cfg = make_cfg(tmp_path); app = DashboardApp(cfg, Runner)
    assert app.state()["strategy"] == "score"
    assert app.api("/api/strategy", {"type": "momentum"})["ok"]
    assert app.state()["strategy"] == "momentum"
    assert Runner(cfg).strat["type"] == "momentum"
    app.api("/api/demo/start", {"days": 100})
    assert "モメンタム" in app.state()["demo"]["strategy"]
    bad = app.api("/api/strategy", {"type": "unknown"})
    assert bad["ok"] is False and "未知" in bad["error"]
    assert app.state()["strategy"] == "momentum"          # 失敗時は変更されない


def test_stop_resume_cycle_compare(tmp_path):
    cfg = make_cfg(tmp_path); app = DashboardApp(cfg, Runner)
    app.api("/api/stop", {}); assert app.state()["stopFile"]
    app.api("/api/resume", {}); assert app.state()["stopFile"] is None
    r = app.api("/api/cycle", {}); assert r["ok"] and r["date"]
    assert app.state()["signals"]["date"] == r["date"]
    r = app.api("/api/compare", {"days": 200}); assert r["ok"]
    c = app.state()["compare"]; assert len(c["rows"]) == 5 and c["verdict"]
