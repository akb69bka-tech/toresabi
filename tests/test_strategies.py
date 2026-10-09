import copy, random
import pytest
from autotrader.config import DEFAULT_CONFIG, STRATEGY_500K, RISK_500K
from autotrader.simulator import Symbol, backtest, create_sim, sim_step
from autotrader.strategies import get_strategy, STRATEGY_TYPES, describe_all
from autotrader.data.demo import gen_market, market_path, DemoSource
from autotrader.data.universe import SAMPLE_UNIVERSE
from autotrader import compare as cmp, learner
from autotrader.notify import Notifier


def demo_syms(n=12, days=600, seed=42):
    syms = [Symbol(code=c, name=nm, unit=1) for c, nm in SAMPLE_UNIVERSE[:n]]
    bars = gen_market(syms, days=days, seed=seed)
    for s in syms:
        s.bars = bars[s.code]
    return syms


# ---------- デモ相場 ----------
def test_demo_market_reproducible_and_consistent():
    a = gen_market([Symbol("7203"), Symbol("6758")], days=300, seed=5)
    b = gen_market([Symbol("7203"), Symbol("6758")], days=300, seed=5)
    c = gen_market([Symbol("7203"), Symbol("6758")], days=300, seed=6)
    assert [x.c for x in a["7203"]] == [x.c for x in b["7203"]] and [x.c for x in a["7203"]] != [x.c for x in c["7203"]]
    for bars in a.values():
        assert len(bars) == 300
        assert all(bb.h >= max(bb.o, bb.c) - 1e-9 and bb.l <= min(bb.o, bb.c) + 1e-9 and bb.l > 0 for bb in bars)
        assert all(bars[i].d > bars[i - 1].d for i in range(1, len(bars)))

def test_demo_market_has_regimes_and_correlation():
    rets, labels = market_path(700, random.Random(42))
    assert {"bull", "bear"} <= set(labels) and len(rets) == 700
    m = gen_market([Symbol("A"), Symbol("B")], days=500, seed=3)
    ra = [m["A"][i].c / m["A"][i - 1].c - 1 for i in range(1, 500)]
    rb = [m["B"][i].c / m["B"][i - 1].c - 1 for i in range(1, 500)]
    ma, mb = sum(ra) / len(ra), sum(rb) / len(rb)
    cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    va = sum((x - ma) ** 2 for x in ra) ** .5; vb = sum((y - mb) ** 2 for y in rb) ** .5
    assert cov / (va * vb) > 0.2          # 市場要因による正の相関がある

def test_demo_source_serves_consistent_universe():
    u = [Symbol("7203"), Symbol("6758")]
    src = DemoSource(days=200, seed=9, universe=u)
    a = src.fetch("7203", 100); b = src.fetch("6758", 0)
    assert len(a) == 100 and len(b) == 200
    assert src.fetch("7203", 100)[0].d == a[0].d


# ---------- 戦略プラグイン ----------
def test_registry_and_descriptions():
    assert set(STRATEGY_TYPES) == {"score", "momentum", "pullback", "donchian"}
    assert all(d["label"] and d["description"] for d in describe_all())
    with pytest.raises(ValueError):
        get_strategy({"type": "nope"})

@pytest.mark.parametrize("t", ["score", "momentum", "pullback", "donchian"])
def test_each_strategy_runs_and_respects_regime(t):
    syms = demo_syms()
    sc = dict(STRATEGY_500K, type="score") if t == "score" else {"type": t, "params": {}}
    rk = cmp.risk_for(t, RISK_500K)
    res = backtest(syms, sc, rk)
    assert res and res["metrics"] and res["buyHold"]
    assert all(p["e"] > 0 for p in res["equity"])
    # 不変条件: 買った前日の終値は 200日線以上（全戦略が長期トレンドフィルタを持つ）
    from autotrader.indicators import sma
    for tr in res["trades"]:
        s = next(x for x in syms if x.code == tr["code"])
        i = next(k for k, b in enumerate(s.bars) if b.d == tr["entryDate"]) - 1
        r200 = sma([b.c for b in s.bars], 200)[i]
        if r200 is not None:
            assert s.bars[i].c >= r200, f"{t}: {tr['code']} {tr['entryDate']} 200日線の下で買っている"

def test_momentum_rebalances_only_at_month_start():
    syms = demo_syms(n=10)
    res = backtest(syms, {"type": "momentum", "params": {"top_k": 3}}, cmp.risk_for("momentum", RISK_500K))
    entries = [t["entryDate"] for t in res["trades"]]
    assert entries
    dates = sorted({b.d for s in syms for b in s.bars})
    first_days = {d for i, d in enumerate(dates) if i == 0 or dates[i - 1][:7] != d[:7]}
    # 執行は翌営業日なので、シグナル日(前営業日)が月初であること
    for e in entries:
        k = dates.index(e)
        assert dates[k - 1] in first_days, e
    held_max = 0
    sim = create_sim(syms, {"type": "momentum", "params": {"top_k": 3}}, cmp.risk_for("momentum", RISK_500K))
    while not sim_step(sim):
        held_max = max(held_max, len(sim.positions))
    assert held_max <= 3

def test_pullback_exits_quickly_and_donchian_holds_long():
    syms = demo_syms(n=10)
    pb = backtest(syms, {"type": "pullback", "params": {}}, cmp.risk_for("pullback", RISK_500K))
    dc = backtest(syms, {"type": "donchian", "params": {}}, cmp.risk_for("donchian", RISK_500K))
    if pb["trades"] and dc["trades"]:
        assert pb["metrics"]["avgDays"] < dc["metrics"]["avgDays"]

def test_strategy_params_roundtrip():
    s = get_strategy({"type": "donchian", "params": {"entry": 40}})
    assert s.params["entry"] == 40 and s.params["exit"] == 20
    s2 = s.apply_params({"exit": 10})
    assert s.params["exit"] == 20 and s2.params["exit"] == 10 and s2.to_config()["params"]["entry"] == 40
    assert learner.current_params({"type": "momentum", "params": {}}) == get_strategy({"type": "momentum"}).params
    assert learner.param_grid({"type": "pullback"}) and learner.param_grid({"type": "momentum"})

def test_learner_works_for_plugin_strategy():
    syms = demo_syms(n=6, days=500)
    wf = learner.walk_forward(syms, {"type": "donchian", "params": {}}, RISK_500K, folds=2,
                              grid=[{"entry": 55, "exit": 20}, {"entry": 20, "exit": 10}])
    assert wf and wf["folds"] == 2
    res = learner.propose(syms, {"type": "momentum", "params": {}}, RISK_500K, {"folds": 2, "min_oos_return": 1e9})
    assert res["adopt"] is False


# ---------- 比較 ----------
def test_compare_rows_and_verdict():
    syms = demo_syms(n=10)
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    rows = cmp.compare(syms, cfg)
    assert rows[0]["type"] == "buyhold" and [r["type"] for r in rows[1:]] == ["score", "momentum", "pullback", "donchian"]
    assert all("equity" in r for r in rows) and len(rows[0]["equity"]) == len(rows[1]["equity"])
    v = cmp.verdict(rows)
    assert "買い持ち" in v


# ---------- 通知 ----------
class FakeSession:
    def __init__(self, status=204): self.status = status; self.calls = []
    def post(self, url, data=None, headers=None, timeout=None):
        self.calls.append((url, data))
        class R: status_code = self.status
        return R()

def test_notifier_disabled_without_url():
    n = Notifier({"notify": {"webhook_url": "", "events": ["signal"]}}, session=FakeSession())
    assert not n.send("signal", "x")

def test_notifier_posts_json_for_enabled_events():
    fs = FakeSession()
    n = Notifier({"notify": {"webhook_url": "https://hook.example/x", "events": ["signal", "halt"]}}, session=fs)
    assert n.send("signal", "こんにちは") and not n.send("order", "x")
    import json
    assert json.loads(fs.calls[0][1])["content"] == "こんにちは" and len(fs.calls) == 1
    bad = Notifier({"notify": {"webhook_url": "https://hook.example/x", "events": ["halt"]}}, session=FakeSession(500))
    assert not bad.send("halt", "x") and bad.last_error == "HTTP 500"
