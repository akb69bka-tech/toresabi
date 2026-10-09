"""デュアルモメンタム・ローテーション（Gary Antonacci の手法を参考）。

月初に、監視銘柄を「過去 lookback 日の騰落率」で順位付けし、
200日線より上にある銘柄の上位 top_k を保有、それ以外は売る。
相場全体が下向きなら現金で待つ。売買は月に数回で、コストが少ない。
学術的に最も再現性が報告されている手法のひとつ。
"""
from __future__ import annotations
from typing import List, Optional
from .base import Strategy, register
from ..indicators import sma


@register
class MomentumRotation(Strategy):
    type = "momentum"
    label = "デュアルモメンタム（月次ローテーション）"
    description = ("月初に過去6か月の騰落率で順位付けし、200日線より上の上位銘柄だけを保有。"
                   "下向きなら現金で待機。売買回数が少なくコストに強い。")
    param_grid = [{"lookback": lb, "top_k": k} for lb in (63, 126, 189, 252) for k in (2, 3, 5)]

    @classmethod
    def defaults(cls):
        return {"lookback": 126, "top_k": 3, "regime": 200, "skip_recent": 0}

    def build(self, bars, risk):
        closes = [b.c for b in bars]
        return {"regime": sma(closes, int(self.params["regime"])), "closes": closes}

    def eval(self, bars, ind, i):
        # 銘柄ごとの表示用。実際の判定は portfolio() で行う
        p = self.params
        lb, skip = int(p["lookback"]), int(p["skip_recent"])
        if i < lb or ind["regime"][i] is None:
            return {"action": "hold", "score": 0.0, "votes": [], "blocked": "助走期間"}
        mom = (ind["closes"][i - skip] / ind["closes"][i - lb] - 1) * 100
        above = bars[i].c >= ind["regime"][i]
        return {"action": "hold", "score": mom, "blocked": "" if above else "200日線の下",
                "votes": [{"text": f"{lb}日騰落 {mom:+.1f}%"}, {"text": "200日線上" if above else "200日線下"}]}

    def _is_rebalance_day(self, sim, date: str) -> bool:
        t = sim.dates.index(date)
        if t == sim.startT:
            return True
        return sim.dates[t - 1][:7] != date[:7]       # 月が変わった最初の営業日

    def portfolio(self, sim, date: str) -> Optional[List[dict]]:
        p = self.params
        lb, skip, k = int(p["lookback"]), int(p["skip_recent"]), int(p["top_k"])
        orders: List[dict] = []
        if not self._is_rebalance_day(sim, date):
            # 月の途中: 200日線を割った保有銘柄だけ手仕舞う（安全弁）
            for ci, c in enumerate(sim.ctx):
                if c.sym.code not in sim.positions:
                    continue
                i = c.idx.get(date)
                if i is None or c.ind["regime"][i] is None:
                    continue
                if c.bars[i].c < c.ind["regime"][i]:
                    orders.append({"ci": ci, "side": "sell", "reason": "200日線割れ"})
            return orders
        ranked = []
        for ci, c in enumerate(sim.ctx):
            i = c.idx.get(date)
            if i is None or i < lb or c.ind["regime"][i] is None:
                continue
            if c.bars[i].c < c.ind["regime"][i]:
                continue
            mom = c.ind["closes"][i - skip] / c.ind["closes"][i - lb] - 1
            if mom <= 0:
                continue
            ranked.append((mom, ci))
        ranked.sort(reverse=True)
        target = {ci for _, ci in ranked[:k]}
        held = {ci for ci, c in enumerate(sim.ctx) if c.sym.code in sim.positions}
        for ci in held - target:
            orders.append({"ci": ci, "side": "sell", "reason": "月次入替"})
        for _, ci in ranked[:k]:
            if ci not in held:
                orders.append({"ci": ci, "side": "buy", "reason": "月次入替"})
        return orders
