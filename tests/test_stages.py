import copy, os
from autotrader import stages
from autotrader.config import DEFAULT_CONFIG, LIVE_CONFIRM_PHRASE, load_config


def cfg(mode="demo", **over):
    c = copy.deepcopy(DEFAULT_CONFIG); c["mode"] = mode
    for k, v in over.items():
        if isinstance(v, dict): c[k].update(v)
        else: c[k] = v
    return c


def test_demo_stage_requires_demo_run(tmp_path):
    d = stages.describe(cfg("demo"), {}, str(tmp_path))
    adv = next(s for s in d["steps"] if s.get("action") == "advance")
    assert d["next"] == "paper" and not adv["enabled"]
    (tmp_path / "demo.json").write_text("{}")
    d = stages.describe(cfg("demo"), {}, str(tmp_path))
    assert next(s for s in d["steps"] if s.get("action") == "advance")["enabled"]


def test_paper_stage_needs_days_and_broker(tmp_path):
    c = cfg("paper")
    d = stages.describe(c, {"paperDays": 5}, str(tmp_path))
    assert not next(s for s in d["steps"] if s.get("action") == "advance")["enabled"]
    c = cfg("paper", broker={"type": "kabu", "kabu": {"api_password": "a", "order_password": "b"}}, risk={"unit": 100})
    d = stages.describe(c, {"paperDays": 25}, str(tmp_path))
    assert next(s for s in d["steps"] if s.get("action") == "advance")["enabled"]
    c["risk"]["unit"] = 1
    d = stages.describe(c, {"paperDays": 25}, str(tmp_path))
    assert not next(s for s in d["steps"] if s.get("action") == "advance")["enabled"]


def test_live_cannot_be_reached_by_button(tmp_path):
    d = stages.describe(cfg("live-dryrun"), {"paperDays": 30}, str(tmp_path))
    assert d["next"] == "live" and not any(s.get("action") == "advance" for s in d["steps"])
    assert any(LIVE_CONFIRM_PHRASE in s["text"] for s in d["steps"])


def test_advance_rewrites_config_yaml(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("mode: demo   # コメント\ndata:\n  source: demo\n  demo: {days: 700, seed: 42}\nrisk:\n  initialCash: 500000\n", encoding="utf-8")
    (tmp_path / "demo.json").write_text("{}")
    res = stages.advance(cfg("demo"), str(p), {}, str(tmp_path))
    assert res["ok"] and res["mode"] == "paper"
    c2 = load_config(str(p))
    assert c2["mode"] == "paper" and c2["data"]["source"] == "stooq" and c2["risk"]["initialCash"] == 500000
    assert c2["data"]["demo"]["seed"] == 42            # 他のキーは壊れない


def test_advance_inline_yaml_and_refusals(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("mode: demo\ndata: {source: csv, csv_dir: data_csv, history_days: 0}\n", encoding="utf-8")
    (tmp_path / "demo.json").write_text("{}")
    assert stages.advance(cfg("demo"), str(p), {}, str(tmp_path))["ok"]
    c2 = load_config(str(p)); assert c2["mode"] == "paper" and c2["data"]["source"] == "stooq" and c2["data"]["csv_dir"] == "data_csv"
    bad = stages.advance(cfg("paper"), str(p), {"paperDays": 0}, str(tmp_path))
    assert not bad["ok"]
