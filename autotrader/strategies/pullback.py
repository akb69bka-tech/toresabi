"""上昇トレンド中の押し目買い（Larry Connors の RSI2 を参考）。

200日線より上（長期上昇）で、短期 RSI が極端に低い＝一時的に売られた日に買い、
5日線を回復したら売る。保有は数日で、勝率が高く1回の利益は小さい。
"""
from __future__ import annotations
from .base import Strategy, register
from ..indicators import sma, rsi


@register
class PullbackStrategy(Strategy):
    type = "pullback"
    label = "押し目買い（RSI2）"
    description = "200日線より上の銘柄が短期的に売られた日に買い、5日線を回復したら売る。短期・高勝率型。"
    param_grid = [{"rsi_buy": rb, "rsi_period": rp} for rb in (5, 10, 15, 20) for rp in (2, 3, 4)]

    @classmethod
    def defaults(cls):
        return {"regime": 200, "exit_ma": 5, "rsi_period": 2, "rsi_buy": 10, "rsi_exit": 70}

    def build(self, bars, risk):
        closes = [b.c for b in bars]
        return {"regime": sma(closes, int(self.params["regime"])),
                "exit": sma(closes, int(self.params["exit_ma"])),
                "rsi": rsi(closes, int(self.params["rsi_period"]))}

    def eval(self, bars, ind, i):
        p = self.params
        b = bars[i]
        if ind["regime"][i] is None or ind["rsi"][i] is None or ind["exit"][i] is None:
            return {"action": "hold", "score": 0.0, "votes": [], "blocked": "助走期間"}
        above = b.c >= ind["regime"][i]
        r = ind["rsi"][i]
        votes = [{"text": f"RSI{p['rsi_period']}={r:.0f}"}, {"text": "200日線上" if above else "200日線下"}]
        if above and r <= p["rsi_buy"]:
            return {"action": "buy", "score": p["rsi_buy"] - r, "votes": votes, "blocked": ""}
        if b.c > ind["exit"][i] or r >= p["rsi_exit"] or not above:
            return {"action": "sell", "score": -(r - 50), "votes": votes, "blocked": ""}
        return {"action": "hold", "score": 0.0, "votes": votes, "blocked": ""}
