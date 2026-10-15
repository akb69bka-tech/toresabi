import copy
from datetime import date

from helpers import gen_bars
from autotrader.config import DEFAULT_CONFIG
from autotrader.simulator import Symbol
from autotrader.fundamentals import Statement, CsvFundamentals, parse_jquants_statement, write_csv
from autotrader import growth
from autotrader.dashboard import DashboardApp
from autotrader.runner import Runner


def demo_cfg(tmp_path):
    c = copy.deepcopy(DEFAULT_CONFIG)
    c["mode"] = "demo"
    c["data"]["source"] = "demo"
    c["state_dir"] = str(tmp_path / "state")
    c["guard"]["stop_file"] = str(tmp_path / "STOP")
    return c


def results(code_fy, period, disclosed, sales, op, **kw):
    return Statement(disclosed=disclosed, period=period, fy_end=code_fy, sales=sales, op=op, **kw)


def test_fund_metrics_yoy_accel_turnaround_and_revision():
    st = [
        results("2025-03-31", "2Q", "2024-11-10", 100, -5),
        results("2025-03-31", "3Q", "2025-02-10", 160, -4),
        results("2025-03-31", "FY", "2025-05-12", 220, -2, nf_sales=300, nf_op=10),
        results("2026-03-31", "2Q", "2025-11-10", 120, 6, f_sales=300, f_op=10, shares=1000),
        Statement(disclosed="2026-01-15", kind="revision", fy_end="2026-03-31", f_sales=330, f_op=25),
        results("2026-03-31", "3Q", "2026-02-10", 220, 15, f_sales=330, f_op=25),
    ]
    m = growth.fund_metrics(st, None)
    assert abs(m["salesYoY"] - (220 / 160 - 1)) < 1e-9
    assert abs(m["accel"] - ((220 / 160 - 1) - (120 / 100 - 1))) < 1e-9
    assert m["turnaround"] and not m["opLoss"]
    assert abs(m["revision"] - 1.5) < 1e-9          # 当初 10 → 25
    assert abs(m["fcSalesG"] - (330 / 220 - 1)) < 1e-9
    assert m["shares"] == 1000
    # 過去の時点では、その後に出た決算や修正を使わない
    m0 = growth.fund_metrics(st, "2025-12-31")
    assert abs(m0["salesYoY"] - 0.2) < 1e-9 and m0["revision"] == 0     # 1月の上方修正はまだ知らない
    assert growth.fund_metrics(st, "2024-01-01") is None
    G, why = growth.score_growth(m)
    assert G >= 70 and any("黒字に転換" in w for w in why) and any("上方修正" in w for w in why)


def test_price_features_trend_template_and_new_listing():
    up = Symbol("1111", "上昇", bars=gen_bars(400, 1000, 25, seed=3, drift_pct=60, trend_pct=20))
    f = growth.price_features(growth.Prepared(up), 399)
    assert f["ttAvail"] == 7 and f["ttPass"] >= 5 and f["ret252"] > 0
    ipo = Symbol("2222", "新規上場", bars=gen_bars(90, 1000, 25, seed=4))
    f2 = growth.price_features(growth.Prepared(ipo), 89)
    assert f2["ttAvail"] == 3                   # 移動平均が作れない条件は数えない
    assert f2["ret252"] is None and f2["rsRaw"] is not None


def test_demo_discovery_finds_story_stocks_and_saves(tmp_path):
    cfg = demo_cfg(tmp_path)
    res = growth.discover(cfg)
    assert res["fundamentals"] and res["passed"] > 50
    top = res["rows"][:8]
    assert sum(1 for r in top if "★" in r["name"]) >= 4           # 成長ストーリーを持つ銘柄が上位に来る
    assert all(r["stage"] in growth.STAGES for r in res["rows"])
    assert (tmp_path / "state" / "growth.json").exists()


def test_validate_uses_only_past_data(tmp_path):
    cfg = demo_cfg(tmp_path)
    inputs = growth.load_inputs(cfg)
    prepared, stmts, info, _, _ = inputs
    v = growth.validate(prepared, stmts, growth.growth_cfg(cfg), info)
    assert v["ok"] and len(v["periods"]) >= 5
    # 未来の株価を消しても、その時点の採点結果は変わらない
    asof = v["periods"][0]["asof"]
    cut = {c: growth.Prepared(Symbol(c, p.sym.name, bars=[b for b in p.sym.bars if b.d <= asof]))
           for c, p in prepared.items()}
    g = growth.growth_cfg(cfg)
    a = [(r["code"], round(r.get("total") or 0, 6)) for r in growth.score_universe(prepared, stmts, g, asof, info)]
    b = [(r["code"], round(r.get("total") or 0, 6)) for r in growth.score_universe(cut, stmts, g, asof, info)]
    assert a == b


def test_price_only_mode_without_fundamentals():
    syms = [Symbol(f"{1000 + i}", bars=gen_bars(300, 500 + 100 * i, 30, seed=i, drift_pct=-20 + 15 * i))
            for i in range(6)]
    prepared = {s.code: growth.Prepared(s) for s in syms}
    g = dict(growth.growth_cfg({}), min_turnover_yen=0)
    rows = growth.score_universe(prepared, {}, g)
    assert all(r["ok"] for r in rows)
    assert all(r["G"] is None and r["stage"] in ("pricelead", "none") for r in rows)


def test_csv_roundtrip_and_jquants_parse(tmp_path):
    st = [results("2026-03-31", "1Q", "2025-08-10", 50, 5, shares=100)]
    p = tmp_path / "f.csv"
    write_csv(str(p), {"7203": st})
    back = CsvFundamentals(str(p)).statements("7203")
    assert back[0].sales == 50 and back[0].period == "1Q" and back[0].shares == 100
    q = {"DisclosedDate": "2025-08-08", "TypeOfDocument": "1QFinancialStatements_Consolidated_JP",
         "TypeOfCurrentPeriod": "1Q", "CurrentFiscalYearEndDate": "2026-03-31", "NetSales": "1000",
         "OperatingProfit": "100", "ForecastNetSales": "4500", "ForecastOperatingProfit": "",
         "NumberOfIssuedAndOutstandingSharesAtTheEndOfFiscalYearIncludingTreasuryStock": "1000",
         "NumberOfTreasuryStockAtTheEndOfFiscalYear": "50"}
    s = parse_jquants_statement(q)
    assert s.sales == 1000 and s.f_sales == 4500 and s.f_op is None and s.shares == 950
    assert parse_jquants_statement({"DisclosedDate": "2025-08-08", "TypeOfDocument": "DividendForecastRevision",
                                    "CurrentFiscalYearEndDate": "2026-03-31"}) is None


def test_jpx_list_loader(tmp_path):
    p = tmp_path / "data_j.csv"
    p.write_text("日付,コード,銘柄名,市場・商品区分,33業種コード,33業種区分\n"
                 "20261001,285A,キオクシアホールディングス,プライム（内国株式）,3650,電気機器\n"
                 "20261001,1306,ＴＯＰＩＸ連動型上場投信,ETF・ETN,-,-\n", encoding="utf-8")
    syms, info = growth.load_jpx_list(str(p))
    assert [s.code for s in syms] == ["285A"]
    assert info["285A"].sector == "電気機器" and info["285A"].market == "プライム"


def test_dashboard_growth_tab(tmp_path):
    app = DashboardApp(demo_cfg(tmp_path), Runner)
    assert app.state()["growth"] is None
    assert app.api("/api/growth", {})["ok"]
    assert app.state()["growth"]["rows"]
    assert app.api("/api/growth/validate", {})["ok"]
    assert app.state()["growthValidate"]["summary"]["all"] is not None


class FakeResp:
    def __init__(self, j):
        self._j = j

    def raise_for_status(self):
        pass

    def json(self):
        return self._j


class FakeSession:
    """J-Quants V2 の応答を真似る。2ページに分けて返す"""
    def __init__(self, pages):
        self.pages, self.calls = pages, []
        self.headers = {}

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append((url, dict(params or {}), dict(headers or {})))
        return FakeResp(self.pages[len(self.calls) - 1])


def test_jquants_v2_prices_and_summary(tmp_path):
    from autotrader.data.jquants import JQuantsSource, JQuantsClient
    from autotrader.fundamentals import JQuantsFundamentals
    sess = FakeSession([
        {"data": [{"Date": "2026-10-09", "Code": "285A0", "O": "1", "AdjO": "100", "AdjH": "110", "AdjL": "95",
                   "AdjC": "105", "AdjVo": "1000"}], "pagination_key": "k1"},
        {"data": [{"Date": "2026-10-10", "Code": "285A0", "AdjC": None, "C": None},          # 売買不成立の日
                  {"Date": "2026-10-13", "Code": "285A0", "AdjO": "105", "AdjH": "120", "AdjL": "100",
                   "AdjC": "118", "AdjVo": "2000"}]},
    ])
    src = JQuantsSource("", str(tmp_path / "c"), interval=0, session=sess, api_key="KEY")
    bars = src.fetch("285A", 100)
    assert [b.c for b in bars] == [105.0, 118.0] and bars[0].d == "2026-10-09"
    url, params, headers = sess.calls[0]
    assert url.endswith("/v2/equities/bars/daily") and headers["x-api-key"] == "KEY" and params["code"] == "285A0"
    assert sess.calls[1][1]["pagination_key"] == "k1"

    sess2 = FakeSession([{"data": [
        {"DiscDate": "2026-07-30", "DocType": "1QFinancialStatements_Consolidated_IFRS", "CurPerType": "1Q",
         "CurFYEn": "2027-03-31", "Sales": "1767100000000", "OP": "1270000000000", "FSales": "", "FOP": "",
         "ShOutFY": "1000", "TrShFY": "10"},
        {"DiscDate": "2026-08-01", "DocType": "DividendForecastRevision", "CurFYEn": "2027-03-31"},
    ]}])
    fs = JQuantsFundamentals(JQuantsClient("KEY", interval=0, session=sess2), str(tmp_path / "c"))
    st = fs.statements("285A")
    assert len(st) == 1 and st[0].sales == 1767100000000 and st[0].shares == 990 and st[0].period == "1Q"
    assert sess2.calls[0][0].endswith("/v2/fins/summary")


def test_themes_file_shipped_is_readable():
    import os
    t = growth.load_themes(os.path.join(os.path.dirname(os.path.dirname(__file__)), "themes.csv"))
    assert "285A" in t and "code" not in t and all(not c.startswith("#") for c in t)


def test_setkey_keeps_comments_and_sets_jquants(tmp_path):
    import importlib.util, os, yaml
    spec = importlib.util.spec_from_file_location("run", os.path.join(os.path.dirname(os.path.dirname(__file__)), "run.py"))
    run = importlib.util.module_from_spec(spec); spec.loader.exec_module(run)
    p = tmp_path / "config.yaml"
    p.write_text(run.CONFIG_TEMPLATE, encoding="utf-8")
    run.set_jquants_key(str(p), "ABC123")
    text = p.read_text(encoding="utf-8")
    cfg = yaml.safe_load(text)
    assert cfg["data"]["source"] == "jquants" and cfg["data"]["jquants_api_key"] == "ABC123"
    assert cfg["growth"]["universe"] == "listed" and cfg["mode"] == "paper"
    assert "# 成長株発掘" in text and cfg["risk"]["initialCash"] == 500000      # 他の設定とコメントは残る
    run.set_jquants_key(str(p), "NEW")                                          # 2回目は置き換え
    assert yaml.safe_load(p.read_text(encoding="utf-8"))["data"]["jquants_api_key"] == "NEW"
    q = tmp_path / "none.yaml"
    run.set_jquants_key(str(q), "K")                                            # 設定ファイルが無くても作れる
    assert yaml.safe_load(q.read_text(encoding="utf-8"))["data"]["jquants_api_key"] == "K"


def test_auth_error_is_reported(tmp_path):
    import requests
    from autotrader.data.jquants import JQuantsSource

    class Denied:
        headers = {}
        def get(self, *a, **k):
            r = requests.Response(); r.status_code = 403
            raise requests.HTTPError(response=r)
    src = JQuantsSource("", str(tmp_path / "c"), interval=0, session=Denied(), api_key="BAD")
    assert src.fetch("7203", 50) == [] and "API キー" in src.last_error
