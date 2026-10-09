"""ドンチャン・ブレイクアウト（タートルズの手法を参考）。

過去 entry 日の高値を終値で更新したら買い、過去 exit 日の安値を割ったら売る。
損切りは ATR 基準。大きなトレンドを取りに行く代わりに勝率は低い。
"""
from __future__ import annotations
from .base import Strategy, register
from ..indicators import sma, highest_prev, lowest_prev


@register
class DonchianStrategy(Strategy):
    type = "donchian"
    label = "ドンチャン・ブレイクアウト（タートル）"
    description = "55日高値の更新で買い、20日安値割れで売る。200日線の下では買わない。大きなトレンド狙い。"
    param_grid = [{"entry": e, "exit": x} for e in (20, 40, 55, 80) for x in (10, 20, 30) if x < e]

    @classmethod
    def defaults(cls):
        return {"entry": 55, "exit": 20, "regime": 200}

    def build(self, bars, risk):
        closes = [b.c for b in bars]
        return {"hh": highest_prev([b.h for b in bars], int(self.params["entry"])),
                "ll": lowest_prev([b.l for b in bars], int(self.params["exit"])),
                "regime": sma(closes, int(self.params["regime"]))}

    def eval(self, bars, ind, i):
        p = self.params
        b = bars[i]
        if ind["hh"][i] is None or ind["ll"][i] is None:
            return {"action": "hold", "score": 0.0, "votes": [], "blocked": "助走期間"}
        above = ind["regime"][i] is None or b.c >= ind["regime"][i]
        if b.c > ind["hh"][i]:
            if not above:
                return {"action": "hold", "score": 1.0, "votes": [{"text": f"{p['entry']}日高値更新"}],
                        "blocked": "200日線の下"}
            return {"action": "buy", "score": (b.c / ind["hh"][i] - 1) * 100 + 1,
                    "votes": [{"text": f"{p['entry']}日高値更新"}], "blocked": ""}
        if b.c < ind["ll"][i]:
            return {"action": "sell", "score": -1.0, "votes": [{"text": f"{p['exit']}日安値割れ"}], "blocked": ""}
        return {"action": "hold", "score": 0.0, "votes": [], "blocked": ""}
