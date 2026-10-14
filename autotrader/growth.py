"""成長株発掘 — 「これから大きく伸びるかもしれない会社」を早めに見つけるための採点器。

株価を当てる仕組みではない。過去の大化け株に共通して見られた“兆し”を4つの面から点数にし、
人が調べる候補を全銘柄から絞り込むための道具である。

  1. 業績の伸び   売上の伸び・伸びの加速・営業利益の伸び・黒字転換・利益率の改善・会社予想の上方修正
                  （W. オニール『オニールの成長株発掘法』の C・A に相当）
  2. 株価の勢い   他の銘柄と比べた上昇率（相対力）と、上昇トレンドの形
                  （M. ミネルヴィニ『ミネルヴィニの成長株投資法』のトレンドテンプレート）
  3. 伸びしろ     時価総額が小さいほど、同じ成功で株価が何倍にもなりやすい
  4. 割安さ       成長の速さに対して株価が高すぎないか（PEG・PSR）

結果は次の段階に分ける。
  🌱 芽     業績は伸び始めたが、株価はまだ大きく動いていない（安いうちに拾える代わりに、外れも多い）
  🚀 離陸   業績も株価も上向き。統計的には一番当たりやすいが、すでに値上がりしている
  🔥 過熱   株価が先に走りすぎ。成長が続いても株価は調整しやすい
  👀 監視   兆しが一部だけ。決算を待って再確認
  📈 株価先行 業績データが無く、株価の形だけが良い（業績を自分で確認すること）

過去の時点での採点は、その時点までに開示された決算と株価だけを使う（validate で検証できる）。
"""
from __future__ import annotations
import bisect, csv, os
from collections import deque
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

from .indicators import sma
from .fundamentals import Statement, CompanyInfo, add_years
from .simulator import Symbol

DEFAULTS: Dict[str, Any] = {
    "fundamentals": "auto",            # auto / jquants / csv / none
    "fundamentals_csv": "fundamentals.csv",
    "csv_unit_yen": 1,                 # fundamentals.csv の金額の単位（百万円で書いたなら 1000000）
    "universe": "file",                # file=universe.file（JPXの上場銘柄一覧CSVも可） / listed=J-Quantsの全上場銘柄
    "themes_file": "themes.csv",       # code,theme（任意。注目テーマの印を付ける）
    "history_days": 520,               # 52週の高値安値と200日線に足りる日数
    "min_bars": 60,                    # 上場直後の銘柄も拾えるよう短めにしてある
    "min_turnover_yen": 20_000_000,    # 1日平均の売買代金（小型株も対象にするため控えめ）
    "max_market_cap_yen": 0,           # 0=上限なし
    "top_n": 30,
    "weights": {"growth": 0.40, "momentum": 0.30, "room": 0.15, "value": 0.15},
    "validate": {"step_days": 21, "horizon_days": 120, "top_n": 10},
}

STAGES = {
    "sprout":  "🌱 芽",
    "takeoff": "🚀 離陸",
    "hot":     "🔥 過熱",
    "watch":   "👀 監視",
    "pricelead": "📈 株価先行",
    "none":    "—",
}


def growth_cfg(cfg: dict) -> dict:
    g = dict(DEFAULTS)
    user = cfg.get("growth") or {}
    for k, v in user.items():
        if isinstance(v, dict) and isinstance(g.get(k), dict):
            g[k] = dict(g[k], **v)
        else:
            g[k] = v
    return g


# ---------------- 株価の特徴 ----------------
def _rolling(arr: List[float], w: int, fn) -> List[float]:
    """直近 w 本（足りなければある分）の最大/最小。単調キューで O(n)"""
    out, dq = [], deque()
    better = (lambda a, b: a >= b) if fn is max else (lambda a, b: a <= b)
    for i, v in enumerate(arr):
        while dq and better(v, arr[dq[-1]]):
            dq.pop()
        dq.append(i)
        if dq[0] <= i - w:
            dq.popleft()
        out.append(arr[dq[0]])
    return out


class Prepared:
    """1銘柄ぶんの指標をまとめて先に計算しておく（過去の各時点での採点を速くするため）。
    どの値も「その日までのデータ」だけで計算されるので、途中の日付で切って使っても未来は混ざらない"""

    def __init__(self, sym: Symbol):
        self.sym = sym
        b = sym.bars
        self.dates = [x.d for x in b]
        self.c = [x.c for x in b]
        self.v = [x.v for x in b]
        turn = [x.c * x.v for x in b]
        self.s50, self.s150, self.s200 = sma(self.c, 50), sma(self.c, 150), sma(self.c, 200)
        self.v50, self.v200 = sma(self.v, 50), sma(self.v, 200)
        self.t20 = sma(turn, 20)
        self.hi = _rolling([x.h for x in b], 252, max)
        self.lo = _rolling([x.l for x in b], 252, min)

    def index_at(self, asof: Optional[str]) -> int:
        if asof is None:
            return len(self.dates) - 1
        return bisect.bisect_right(self.dates, asof) - 1


def price_features(p: Prepared, i: int) -> Dict[str, Any]:
    c = p.c[i]

    def ret(k):
        return c / p.c[i - k] - 1 if i >= k and p.c[i - k] > 0 else None
    rets = [(ret(63), 0.4), (ret(126), 0.2), (ret(189), 0.2), (ret(252), 0.2)]
    avail = [(r, w) for r, w in rets if r is not None]
    rs_raw = sum(r * w for r, w in avail) / sum(w for _, w in avail) if avail else None

    s50, s150, s200 = p.s50[i], p.s150[i], p.s200[i]
    s200_prev = p.s200[i - 21] if i >= 21 else None

    def both(a, b):
        return None if a is None or b is None else (a and b)
    checks = [
        ("終値が150日線・200日線の上", both(None if s150 is None else c > s150, None if s200 is None else c > s200)),
        ("150日線が200日線の上", None if s150 is None or s200 is None else s150 > s200),
        ("200日線が上向き", None if s200 is None or s200_prev is None else s200 > s200_prev),
        ("50日線が長期線の上", None if s50 is None or s150 is None or s200 is None else (s50 > s150 and s50 > s200)),
        ("終値が50日線の上", None if s50 is None else c > s50),
        ("52週安値から+30%以上", c >= p.lo[i] * 1.3),
        ("52週高値から-25%以内", c >= p.hi[i] * 0.75),
    ]
    known = [ok for _, ok in checks if ok is not None]
    v_long = p.v200[i] or (sum(p.v[: i + 1]) / (i + 1))
    v_short = p.v50[i] or v_long
    return {
        "price": c, "date": p.dates[i], "bars": i + 1,
        "ret63": ret(63), "ret126": ret(126), "ret252": ret(252), "rsRaw": rs_raw,
        "ttPass": sum(1 for ok in known if ok), "ttAvail": len(known),
        "ttFailed": [name for name, ok in checks if ok is False],
        "volRatio": v_short / v_long if v_long else 1.0,
        "turnover": p.t20[i] if p.t20[i] is not None else p.c[i] * p.v[i],
        "fromHigh": c / p.hi[i] - 1 if p.hi[i] else 0.0,
        "above200": c / s200 - 1 if s200 else None,
        "downtrend": s200 is not None and s200_prev is not None and c < s200 and s200 < s200_prev,
    }


# ---------------- 決算の特徴 ----------------
def _yoy(cur: Optional[float], prev: Optional[float]) -> Optional[float]:
    if cur is None or prev is None or prev <= 0:
        return None
    return cur / prev - 1


def fund_metrics(stmts: List[Statement], asof: Optional[str], unit: float = 1.0) -> Optional[Dict[str, Any]]:
    """asof までに開示された決算から、成長の度合いを測る。決算が無ければ None"""
    st = [s for s in stmts if asof is None or s.disclosed <= asof]
    res = [s for s in st if s.kind == "results" and s.sales is not None and s.period]
    if not res:
        return None
    # 同じ決算の訂正が後から出た場合は新しい方を使う
    latest_by_key: Dict[Tuple[str, str], Statement] = {}
    for s in res:
        latest_by_key[(s.fy_end, s.period)] = s
    res = sorted(latest_by_key.values(), key=lambda s: (s.fy_end, ["1Q", "2Q", "3Q", "FY"].index(s.period)
                                                          if s.period in ("1Q", "2Q", "3Q", "FY") else 9))
    find = lambda fy, per: latest_by_key.get((fy, per))
    L = res[-1]
    P = find(add_years(L.fy_end, -1), L.period)
    m: Dict[str, Any] = {"period": f"{L.fy_end[:7]}期 {L.period}", "disclosed": L.disclosed}
    m["salesYoY"] = _yoy(L.sales, P.sales if P else None)
    m["opYoY"] = _yoy(L.op, P.op if P else None)
    m["turnaround"] = bool(P and P.op is not None and L.op is not None and P.op <= 0 < L.op)
    m["opLoss"] = L.op is not None and L.op < 0
    m["margin"] = L.op / L.sales if L.op is not None and L.sales else None
    pm = P.op / P.sales if P and P.op is not None and P.sales else None
    m["marginChg"] = m["margin"] - pm if m["margin"] is not None and pm is not None else None
    # 伸びの加速: ひとつ前の決算での前年同期比と比べる
    m["accel"] = None
    if len(res) >= 2:
        L2 = res[-2]
        P2 = find(add_years(L2.fy_end, -1), L2.period)
        y2 = _yoy(L2.sales, P2.sales if P2 else None)
        if m["salesYoY"] is not None and y2 is not None:
            m["accel"] = m["salesYoY"] - y2
    # 直近12か月の売上・純利益（PSR・PER 用）
    fy_prev = find(add_years(L.fy_end, -1), "FY")
    def ttm(attr):
        a = getattr(L, attr)
        if a is None:
            return None
        if L.period == "FY":
            return a
        if fy_prev and P and getattr(fy_prev, attr) is not None and getattr(P, attr) is not None:
            return getattr(fy_prev, attr) + a - getattr(P, attr)
        return None
    m["salesTTM"] = (ttm("sales") or 0) * unit or None
    m["profitTTM"] = ttm("profit")
    m["profitTTM"] = m["profitTTM"] * unit if m["profitTTM"] is not None else None

    # 会社予想: 今期（直近が通期決算なら翌期）の予想と、その修正履歴
    target = add_years(L.fy_end, 1) if L.period == "FY" else L.fy_end
    obs = []
    for s in st:
        if s.fy_end == target and (s.f_sales is not None or s.f_op is not None):
            obs.append((s.disclosed, s.f_sales, s.f_op, s.f_eps))
        if s.period == "FY" and add_years(s.fy_end, 1) == target and (s.nf_sales is not None or s.nf_op is not None):
            obs.append((s.disclosed, s.nf_sales, s.nf_op, s.nf_eps))
    obs.sort(key=lambda o: o[0])
    base = find(add_years(target, -1), "FY")
    m["fcSalesG"] = m["fcOpG"] = m["revision"] = m["fcEps"] = None
    if obs:
        cur = obs[-1]
        m["fcEps"] = cur[3]
        if base:
            m["fcSalesG"] = _yoy(cur[1], base.sales)
            m["fcOpG"] = _yoy(cur[2], base.op)
        ops = [(o[2], o[1]) for o in obs if o[2] is not None]
        if len(ops) >= 2:
            (first, fs), (last, _) = ops[0], ops[-1]
            # 当初予想がごく小さいと率が極端になるので、分母は売上予想の2%を下限にする
            floor = abs(fs) * 0.02 if fs else 0.0
            denom = max(first, floor)
            if denom > 0:
                m["revision"] = (last - first) / denom
            elif first <= 0 < last:
                m["revision"] = 1.0               # 赤字予想から黒字予想へ
    shares = [s.shares for s in st if s.shares]
    m["shares"] = shares[-1] if shares else None
    eps = [s.eps for s in res if s.period == "FY" and s.eps is not None]
    m["epsFY"] = eps[-1] if eps else None
    return m


# ---------------- 点数 ----------------
def _pct(x):
    return f"{x + 1:.0f}倍" if x >= 9 else f"{x * 100:+.0f}%"


def score_growth(m: Dict[str, Any]) -> Tuple[float, List[str]]:
    pts, why = 0.0, []
    g = m.get("salesYoY")
    if g is not None:
        pts += 35 if g >= 0.40 else 28 if g >= 0.25 else 20 if g >= 0.15 else 10 if g >= 0.08 else 3 if g >= 0 else 0
        if g >= 0.15:
            why.append(f"売上 {_pct(g)}（前年同期比）")
        elif g < 0:
            why.append(f"売上が減少 {_pct(g)}")
    a = m.get("accel")
    if a is not None and a >= 0.05 and (g or 0) > 0:
        pts += 15; why.append(f"売上の伸びが加速（{a * 100:+.0f}pt）")
    elif a is not None and a <= -0.10:
        pts -= 10; why.append("売上の伸びが鈍化")
    if m.get("turnaround"):
        pts += 22; why.append("営業黒字に転換")
    else:
        o = m.get("opYoY")
        if o is not None:
            pts += 20 if o >= 0.5 else 14 if o >= 0.25 else 8 if o >= 0.1 else 0
            if o >= 0.25:
                why.append(f"営業利益 {_pct(o)}")
        if m.get("opLoss"):
            pts -= 12; why.append("営業赤字")
    mc = m.get("marginChg")
    if mc is not None and mc >= 0.03:
        pts += 10; why.append(f"利益率が改善（{mc * 100:+.1f}pt）")
    fs = m.get("fcSalesG")
    if fs is not None and fs >= 0.15:
        pts += 8; why.append(f"会社予想 売上 {_pct(fs)}")
    fo = m.get("fcOpG")
    if fo is not None and fo >= 0.30:
        pts += 5
    r = m.get("revision")
    if r is not None and r >= 0.10:
        pts += 15; why.append(f"会社予想を上方修正（営業利益 {_pct(r)}）")
    elif r is not None and r <= -0.10:
        pts -= 15; why.append(f"会社予想を下方修正（{_pct(r)}）")
    return max(0.0, min(100.0, pts)), why


def score_momentum(f: Dict[str, Any]) -> Tuple[float, List[str]]:
    why = []
    rs = f.get("rsPct")
    tt = f["ttPass"] / f["ttAvail"] * 100 if f["ttAvail"] else 0.0
    vol = min(100.0, max(0.0, (f["volRatio"] - 0.8) / 0.8 * 100))
    s = 0.5 * (rs if rs is not None else 50) + 0.4 * tt + 0.1 * vol
    if rs is not None and rs >= 80:
        why.append(f"上昇率が全銘柄の上位{100 - rs:.0f}%")
    if f["ttAvail"] and f["ttPass"] == f["ttAvail"]:
        why.append("上昇トレンドの形がそろった")
    if f["volRatio"] >= 1.5:
        why.append(f"出来高が増加（{f['volRatio']:.1f}倍）")
    if f["fromHigh"] >= -0.02:
        why.append("52週高値の更新圏")
    return s, why


def score_room(mcap: Optional[float]) -> Tuple[float, List[str]]:
    if mcap is None:
        return 50.0, []
    oku = mcap / 1e8
    s = 100 if oku <= 300 else 85 if oku <= 1000 else 60 if oku <= 3000 else 35 if oku <= 10000 else 10
    return float(s), ([f"時価総額 {oku:,.0f}億円（伸びしろ大）"] if oku <= 1000 else [])


def score_value(f: Dict[str, Any], m: Optional[Dict[str, Any]], mcap: Optional[float]) -> Tuple[float, List[str], Dict]:
    out = {"per": None, "psr": None, "peg": None}
    if not m:
        return 50.0, [], out
    c = f["price"]
    eps = m.get("fcEps") or m.get("epsFY")
    if eps and eps > 0:
        out["per"] = c / eps
    if mcap and m.get("salesTTM"):
        out["psr"] = mcap / m["salesTTM"]
    g = m.get("fcOpG") if m.get("fcOpG") is not None else m.get("opYoY")
    if out["per"] and g and g > 0:
        out["peg"] = out["per"] / (g * 100)
    if out["peg"] is not None:
        p = out["peg"]
        s = 100 if p <= 0.7 else 85 if p <= 1.0 else 65 if p <= 1.5 else 45 if p <= 2.0 else 25 if p <= 3.0 else 10
        why = [f"PEG {p:.1f}（成長の割に割安）"] if p <= 1.0 else []
        return float(s), why, out
    if out["psr"] is not None:
        p = out["psr"]
        s = 80 if p <= 1 else 60 if p <= 3 else 40 if p <= 6 else 20 if p <= 12 else 5
        return float(s), [], out
    return 50.0, [], out


def classify(G: Optional[float], f: Dict[str, Any], V: float, total: float) -> str:
    rs = f.get("rsPct") or 0
    tt_full = f["ttAvail"] > 0 and f["ttPass"] >= max(1, f["ttAvail"] - 1)
    stretched = (f.get("above200") or 0) >= 0.9 or (rs >= 95 and V <= 15)
    if G is None:
        return "pricelead" if (tt_full and rs >= 80 and f["volRatio"] >= 1.1) else "none"
    if G >= 55:
        if stretched:
            return "hot"
        if tt_full and rs >= 70:
            return "takeoff"
        if not f["downtrend"]:
            return "sprout"
    if total >= 50 or G >= 40:
        return "watch"
    return "none"


# ---------------- 全銘柄の採点 ----------------
def score_universe(prepared: Dict[str, Prepared], stmts: Dict[str, List[Statement]], g: dict,
                   asof: Optional[str] = None, info: Optional[Dict[str, CompanyInfo]] = None,
                   themes: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
    info, themes = info or {}, themes or {}
    w = g["weights"]
    unit = float(g.get("csv_unit_yen", 1) or 1)
    rows: List[Dict[str, Any]] = []
    for code, p in prepared.items():
        i = p.index_at(asof)
        ci = info.get(code)
        row: Dict[str, Any] = {"code": code, "name": (ci.name if ci and ci.name else p.sym.name),
                               "sector": ci.sector if ci else "", "market": ci.market if ci else "",
                               "theme": themes.get(code, ""), "ok": False, "reasons": []}
        if i < 0:
            row["reasons"].append("株価データなし"); rows.append(row); continue
        if asof and (date.fromisoformat(asof) - date.fromisoformat(p.dates[i])).days > 10:
            row["reasons"].append("直近の株価なし（上場廃止・売買停止の可能性）"); rows.append(row); continue
        f = price_features(p, i)
        row.update(f)
        if f["bars"] < int(g["min_bars"]):
            row["reasons"].append(f"データ不足({f['bars']}本)")
        if f["turnover"] < float(g["min_turnover_yen"]):
            row["reasons"].append(f"売買代金が少ない（1日 {f['turnover'] / 1e6:,.0f}百万円）")
        row["_m"] = fund_metrics(stmts.get(code, []), asof, unit)
        sh = row["_m"]["shares"] if row["_m"] else None
        row["mcap"] = f["price"] * sh if sh else None
        cap = float(g.get("max_market_cap_yen") or 0)
        if cap and row["mcap"] and row["mcap"] > cap:
            row["reasons"].append("時価総額が上限超え")
        row["ok"] = not row["reasons"]
        rows.append(row)

    ok = [r for r in rows if r["ok"] and r.get("rsRaw") is not None]
    ok.sort(key=lambda r: r["rsRaw"])
    for k, r in enumerate(ok):                       # 相対力: 全銘柄中の順位を 1〜99 に
        r["rsPct"] = 1 + 98 * k / max(1, len(ok) - 1)
    by_sector: Dict[str, List[float]] = {}
    for r in ok:
        if r["sector"]:
            by_sector.setdefault(r["sector"], []).append(r["rsPct"])

    for r in rows:
        if not r["ok"]:
            r.pop("_m", None); r["stage"] = "none"; continue
        m = r.pop("_m")
        why: List[str] = []
        G = None
        if m:
            G, wg = score_growth(m); why += wg
        M, wm = score_momentum(r); why += wm
        R, wr = score_room(r["mcap"]); why += wr
        V, wv, val = score_value(r, m, r["mcap"]); why += wv
        r.update(val)
        sec = by_sector.get(r["sector"]) or []
        if len(sec) >= 3 and sum(sec) / len(sec) >= 65:
            why.append(f"業種（{r['sector']}）全体に追い風")
        if r["bars"] < 250:
            why.append(f"上場から日が浅い（{r['bars']}営業日）")
        parts = [(G, w["growth"]), (M, w["momentum"]), (R, w["room"]), (V if m else None, w["value"])]
        parts = [(s, wt) for s, wt in parts if s is not None]
        total = sum(s * wt for s, wt in parts) / sum(wt for _, wt in parts)
        if G is None:
            total *= 0.8                             # 業績で裏付けが取れないものは割り引く
        r.update({"G": G, "M": M, "R": R, "V": V if m else None, "total": total, "fund": m,
                  "why": why})
        r["stage"] = classify(G, r, V, total)
        if r["stage"] == "hot":
            why.insert(0, "株価が先行しすぎ（200日線から大きく上）")
        elif G is not None and G >= 55 and r["stage"] == "watch" and r["downtrend"]:
            why.insert(0, "業績は良いが株価は下落トレンド中（底打ちを待つ）")
        if r["theme"]:
            why.insert(0, f"テーマ: {r['theme']}")
    rows.sort(key=lambda r: (not r["ok"], -(r.get("total") or 0)))
    for k, r in enumerate([r for r in rows if r["ok"]]):
        r["rank"] = k + 1
    return rows


def load_themes(path: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    if not path or not os.path.exists(path):
        return out
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.reader(f):
            if row and row[0].strip().startswith("#"):
                continue
            if len(row) >= 2 and row[0].strip() and row[0].strip().lower() not in ("code", "コード"):
                out[row[0].strip()] = row[1].strip()
    return out


def load_jpx_list(path: str) -> Tuple[List[Symbol], Dict[str, CompanyInfo]]:
    """JPX「東証上場銘柄一覧」（data_j.xls を CSV で保存したもの）を読む。ETF・REIT・PRO Market は除く。
    書式が違えば空を返す（そのときは通常の universe.csv として読む）"""
    syms, info = [], {}
    if not path or not os.path.exists(path):
        return syms, info
    for enc in ("utf-8-sig", "cp932"):
        try:
            with open(path, encoding=enc, newline="") as f:
                rows = list(csv.reader(f))
            break
        except UnicodeDecodeError:
            rows = []
    if not rows:
        return syms, info
    h = [c.strip() for c in rows[0]]
    if "コード" not in h or "銘柄名" not in h:
        return syms, info
    ic, iname = h.index("コード"), h.index("銘柄名")
    imkt = h.index("市場・商品区分") if "市場・商品区分" in h else None
    isec = h.index("33業種区分") if "33業種区分" in h else None
    for row in rows[1:]:
        if len(row) <= max(ic, iname):
            continue
        mkt = row[imkt].strip() if imkt is not None and len(row) > imkt else ""
        if any(k in mkt for k in ("ETF", "ETN", "REIT", "PRO Market", "出資証券")):
            continue
        code = row[ic].strip()
        if not code:
            continue
        syms.append(Symbol(code=code, name=row[iname].strip()))
        info[code] = CompanyInfo(code, row[iname].strip(),
                                 row[isec].strip() if isec is not None and len(row) > isec else "",
                                 mkt.replace("（内国株式）", ""))
    return syms, info


# ---------------- 検証（過去の時点で採点 → その後の値動き） ----------------
def validate(prepared: Dict[str, Prepared], stmts: Dict[str, List[Statement]], g: dict,
             info=None) -> Dict[str, Any]:
    v = g["validate"]
    step, hz, top = int(v["step_days"]), int(v["horizon_days"]), int(v["top_n"])
    dates = sorted({d for p in prepared.values() for d in p.dates})
    if len(dates) < hz + 140:
        return {"ok": False, "error": f"検証には {hz + 140} 営業日以上の株価が必要です（現在 {len(dates)} 日）。"
                                       "growth.history_days を増やしてください"}
    start = 252 if len(dates) > 252 + hz + step * 3 else 130
    periods = []
    groups = ("top", "sprout", "takeoff", "all")
    for t in range(start, len(dates) - hz, step):
        asof = dates[t]
        rows = score_universe(prepared, stmts, g, asof, info)
        fwd: Dict[str, float] = {}
        for r in rows:
            if not r["ok"]:
                continue
            p = prepared[r["code"]]
            i = p.index_at(asof)
            j = p.index_at(dates[t + hz])
            if j > i and p.c[i] > 0:
                fwd[r["code"]] = p.c[j] / p.c[i] - 1
        okrows = [r for r in rows if r["ok"] and r["code"] in fwd]
        if len(okrows) < 5:
            continue
        sel = {
            "top": [r["code"] for r in okrows[:top]],
            "sprout": [r["code"] for r in okrows if r["stage"] == "sprout"],
            "takeoff": [r["code"] for r in okrows if r["stage"] == "takeoff"],
            "all": [r["code"] for r in okrows],
        }
        row = {"asof": asof}
        for k in groups:
            xs = [fwd[c] for c in sel[k]]
            row[k] = sum(xs) / len(xs) * 100 if xs else None
            row[k + "N"] = len(xs)
            row[k + "Big"] = sum(1 for x in xs if x >= 0.5)
        row["picks"] = sel["top"][:5]
        periods.append(row)
    if not periods:
        return {"ok": False, "error": "検証できる期間がありませんでした"}

    def avg(k):
        xs = [p[k] for p in periods if p[k] is not None]
        return sum(xs) / len(xs) if xs else None
    summ = {k: avg(k) for k in groups}
    beat = [p for p in periods if p["top"] is not None and p["all"] is not None]
    summ["winRate"] = sum(1 for p in beat if p["top"] > p["all"]) / len(beat) * 100 if beat else None
    for k in ("top", "all", "sprout", "takeoff"):
        n = sum(p[k + "N"] for p in periods)
        summ[k + "BigRate"] = sum(p[k + "Big"] for p in periods) / n * 100 if n else None
    ex = (summ["top"] or 0) - (summ["all"] or 0)
    if summ["winRate"] is not None and ex > 2 and summ["winRate"] >= 60:
        verdict = (f"この期間では、上位{top}銘柄の{hz}営業日後の値動きが全体平均を {ex:+.1f}pt 上回り、"
                   f"{summ['winRate']:.0f}% の時点で勝ちました。ただし期間が短いと偶然でも起こります。")
    elif ex > 0:
        verdict = f"上位銘柄は平均を {ex:+.1f}pt 上回りましたが、勝った時点は {summ['winRate']:.0f}% で、偶然と区別できません。"
    else:
        verdict = f"この期間では上位銘柄が平均を下回りました（{ex:+.1f}pt）。この採点で選ぶ優位性は確認できません。"
    verdict += ("　※いま上場している銘柄だけで検証しているため（上場廃止になった失敗例が入らない）、"
                "実際より良く見えます。")
    return {"ok": True, "from": periods[0]["asof"], "to": periods[-1]["asof"], "horizon": hz, "top": top,
            "periods": periods, "summary": summ, "verdict": verdict}


# ---------------- 実行（コマンド・画面から呼ばれる） ----------------
def _jsonable(rows: List[Dict[str, Any]], n: int) -> List[Dict[str, Any]]:
    out = []
    for r in rows[:n]:
        r = dict(r)
        for k in ("ret63", "ret126", "ret252", "rsRaw"):
            r.pop(k, None)
        out.append(r)
    return out


def load_inputs(cfg: dict, runner=None):
    """(prepared, statements, info, themes, source_label) を返す"""
    from .fundamentals import make_fundamentals
    from .data.universe import load_universe
    g = growth_cfg(cfg)
    if cfg["data"].get("source") == "demo":
        from .data.demo_growth import gen_growth_demo
        syms, stmts, info, themes = gen_growth_demo(seed=int((cfg["data"].get("demo") or {}).get("seed", 42)))
        return {s.code: Prepared(s) for s in syms}, stmts, info, themes, "demo（擬似データ）"
    fsrc = make_fundamentals(cfg)
    info = fsrc.companies()
    ufile = cfg["universe"].get("file", "")
    syms, jpx_info = load_jpx_list(ufile)
    info = dict(jpx_info, **info)
    if g.get("universe") == "listed" and info:
        syms = [Symbol(code=c, name=i.name) for c, i in info.items()
                if not any(k in (i.market or "") for k in ("ETF", "REIT", "PRO"))]
    if not syms:
        syms = load_universe(ufile, cfg["universe"].get("max_symbols", 0))
    themes = load_themes(g.get("themes_file", ""))
    have = {s.code for s in syms}
    syms += [Symbol(code=c, name=(info[c].name if c in info else c)) for c in themes if c not in have]
    if runner is None:
        from .runner import Runner
        runner = Runner(cfg)
    hist = runner.load_history(syms, days=int(g["history_days"]))
    stmts = {s.code: fsrc.statements(s.code) for s in hist} if fsrc.name != "none" else {}
    return ({s.code: Prepared(s) for s in hist}, stmts, info, themes,
            f"{cfg['data'].get('source')} + 決算:{fsrc.name}")


def discover(cfg: dict, runner=None, inputs=None) -> Dict[str, Any]:
    from .state import save_json
    g = growth_cfg(cfg)
    prepared, stmts, info, themes, label = inputs or load_inputs(cfg, runner)
    rows = score_universe(prepared, stmts, g, None, info, themes)
    ok = [r for r in rows if r["ok"]]
    counts = {k: sum(1 for r in ok if r["stage"] == k) for k in STAGES}
    res = {"date": max((r["date"] for r in ok), default=None), "source": label,
           "fundamentals": bool(stmts and any(stmts.values())), "scanned": len(rows), "passed": len(ok),
           "counts": counts, "stages": STAGES, "rows": _jsonable(ok, max(int(g["top_n"]), 60)),
           "rejected": len(rows) - len(ok)}
    save_json(cfg.get("state_dir", "state"), "growth.json", res)
    return res


def run_validate(cfg: dict, runner=None, inputs=None) -> Dict[str, Any]:
    from .state import save_json
    g = growth_cfg(cfg)
    prepared, stmts, info, _, label = inputs or load_inputs(cfg, runner)
    res = validate(prepared, stmts, g, info)
    res["source"] = label
    if res.get("ok") and cfg["data"].get("source") == "demo":
        res["verdict"] = ("【擬似データ】デモ相場は「業績が伸びた銘柄が後から上がる」ように作ってあるため、"
                          "この成績は仕組みの確認用で、実力の証明ではありません。　" + res["verdict"])
    save_json(cfg.get("state_dir", "state"), "growth_validate.json", res)
    return res
