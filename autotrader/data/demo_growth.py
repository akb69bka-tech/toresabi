"""成長株発掘のデモ用に、株価と決算をセットで作る擬似相場。

本物の相場ではないので、ここでの成績は何の参考にもならない。画面と採点の流れを
お金もトークンも無しで確かめるためのもの。
- 1割ほどの銘柄に「成長ストーリー」を持たせる: ある時点から売上が急に伸び、利益率が上がり、
  会社予想が上方修正され、少し遅れて株価が評価し直される
- 業績の裏付けなしに株価だけが急騰し、のちに崩れる銘柄も混ぜる（だまし）
- 決算は四半期ごとに、期末から約40日後に開示される（3月決算）
"""
from __future__ import annotations
import math, random
from datetime import date, timedelta
from typing import Dict, List, Tuple

from ..simulator import Bar, Symbol
from ..fundamentals import Statement, CompanyInfo
from .demo import market_path

THEMES = ["AI・半導体", "電力・データセンター", "防衛", "宇宙", "創薬", "ロボット", "", "", "", ""]
SECTORS = ["電気機器", "情報・通信業", "機械", "化学", "医薬品", "サービス業"]


def _quarter_ends(start: date, end: date) -> List[Tuple[date, str, date]]:
    """(四半期末, 区分, 会計年度末) を古い順に。3月決算"""
    out = []
    for y in range(start.year - 1, end.year + 1):
        for m, d, per in ((6, 30, "1Q"), (9, 30, "2Q"), (12, 31, "3Q")):
            q = date(y, m, d)
            out.append((q, per, date(y + 1, 3, 31)))
        out.append((date(y + 1, 3, 31), "FY", date(y + 1, 3, 31)))
    return sorted([o for o in out if start <= o[0] <= end], key=lambda o: o[0])


def gen_growth_demo(n: int = 120, days: int = 900, seed: int = 42, end: date = None
                    ) -> Tuple[List[Symbol], Dict[str, List[Statement]], Dict[str, CompanyInfo], Dict[str, str]]:
    rnd = random.Random(seed + 7)
    end = end or date.today()
    cal: List[date] = []
    cur = end
    while len(cal) < days:
        if cur.weekday() < 5:
            cal.append(cur)
        cur -= timedelta(days=1)
    cal.reverse()
    mkt, _ = market_path(days, rnd, 0.2)
    qs = _quarter_ends(cal[0] - timedelta(days=400), cal[-1])
    syms, stmts, info, themes = [], {}, {}, {}
    for k in range(n):
        code = f"9{k:03d}"
        r = random.Random(seed * 7919 + k)
        kind = "story" if r.random() < 0.12 else ("fake" if r.random() < 0.06 else "plain")
        story_q = r.randint(len(qs) // 3, len(qs) - 3)        # 成長が始まる四半期
        q_sales = 10 ** r.uniform(9.0, 11.3) / 4              # 四半期売上（円）
        g0 = r.uniform(-0.04, 0.10)
        m0 = r.uniform(-0.04, 0.12)
        shares = 10 ** r.uniform(7.0, 8.6)
        # ---- 決算 ----
        quarterly = []
        for qi, (qd, per, fy) in enumerate(qs):
            g, mg = g0, m0
            if kind == "story" and qi >= story_q:
                ramp = min(1.0, (qi - story_q + 1) / 3)
                g, mg = g0 + 0.6 * ramp, m0 + 0.14 * ramp
            q_sales *= math.exp(g / 4 + r.gauss(0, 0.03))
            quarterly.append((qd, per, fy, q_sales, q_sales * (mg + r.gauss(0, 0.015))))
        out: List[Statement] = []
        for qi, (qd, per, fy, s, o) in enumerate(quarterly):
            same_fy = [x for x in quarterly[: qi + 1] if x[2] == fy]
            cs, co = sum(x[3] for x in same_fy), sum(x[4] for x in same_fy)
            disc = qd + timedelta(days=45 if per == "FY" else 40)
            st = Statement(disclosed=disc.isoformat(), period=per, fy_end=fy.isoformat(), sales=cs, op=co,
                           profit=co * 0.65, eps=co * 0.65 / shares, shares=shares)
            run_rate_g = g0 + (0.6 if kind == "story" and qi >= story_q else 0)
            if per == "FY":
                st.nf_sales = cs * (1 + max(-0.05, run_rate_g * 0.6))
                st.nf_op = max(co, 1) * (1 + max(-0.05, run_rate_g))
                st.nf_eps = st.nf_op * 0.65 / shares
            else:
                left = {"1Q": 3, "2Q": 2, "3Q": 1}[per]
                st.f_sales = cs + s * left * (1 + g0 / 4)
                st.f_op = co + o * left
                st.f_eps = st.f_op * 0.65 / shares
            if disc <= cal[-1]:
                out.append(st)
        stmts[code] = out
        # ---- 株価: 決算が開示された後、ゆっくり評価し直される ----
        story_disc = date.fromisoformat(out[min(story_q, len(out) - 1)].disclosed) if out else cal[-1]
        fake_t = r.randint(days // 3, days - 120)
        price = (q_sales * 4 * r.uniform(0.6, 2.5)) / shares * (0.5 if kind == "story" else 1.0)
        beta, idio = r.uniform(0.7, 1.4), r.uniform(0.15, 0.40) / math.sqrt(252)
        bars = []
        for t, d in enumerate(cal):
            alpha = 0.0
            if kind == "story" and d >= story_disc + timedelta(days=20):
                age = (d - story_disc).days
                alpha = (1.1 if age < 420 else 0.1) / 252
            if kind == "fake":
                if fake_t <= t < fake_t + 60:
                    alpha = 2.5 / 252
                elif fake_t + 60 <= t < fake_t + 140:
                    alpha = -2.2 / 252
            o = price
            price = max(1.0, price * math.exp(beta * mkt[t] + alpha + idio * r.gauss(0, 1)))
            amp = abs(price - o) + price * idio * r.random()
            vol = 10 ** r.uniform(4.6, 5.6) * (1 + 8 * abs(alpha * 252) * 0.1 + 4 * abs(price / o - 1) * 10)
            bars.append(Bar(d.isoformat(), round(o, 1), round(max(o, price) + amp * 0.3, 1),
                            round(max(1.0, min(o, price) - amp * 0.3), 1), round(price, 1), float(round(vol))))
        name = f"デモ{k:03d}" + ("★" if kind == "story" else "")
        syms.append(Symbol(code=code, name=name, unit=100, bars=bars))
        info[code] = CompanyInfo(code, name, r.choice(SECTORS), r.choice(["グロース", "スタンダード", "プライム"]))
        th = r.choice(THEMES)
        if th:
            themes[code] = th
    return syms, stmts, info, themes
