"""スコア合議戦略（ブラウザ版と同一。テクニカルの多数決）"""
from __future__ import annotations
import copy
from .base import Strategy, register
from ..strategy import build_indicators, eval_signal


@register
class ScoreStrategy(Strategy):
    type = "score"
    label = "テクニカル合議（トレンド追随）"
    description = ("移動平均・EMA・MACD・ブレイクアウト等の重み付き合議で判定。200日線の下では買わず、"
                   "長期トレンドが崩れるまで保有。ブラウザ版と同一の判定。")
    param_grid = [
        {"short": sh, "long": lo, "breakout": br}
        for sh in (10, 15, 20, 25, 30) for lo in (50, 60, 75, 100) for br in (30, 40, 60) if sh < lo
    ]

    def __init__(self, cfg: dict):
        self.cfg = copy.deepcopy(cfg)
        self.cfg.setdefault("type", "score")
        self.params = self.current_params()

    def build(self, bars, risk):
        return build_indicators(bars, self.cfg, risk)

    def eval(self, bars, ind, i):
        return eval_signal(bars, ind, i, self.cfg)

    def current_params(self) -> dict:
        r = self.cfg["rules"]
        return {"short": r["smaCross"]["short"], "long": r["smaCross"]["long"],
                "breakout": r["breakout"]["period"]}

    def apply_params(self, params: dict) -> "ScoreStrategy":
        c = copy.deepcopy(self.cfg)
        if "short" in params: c["rules"]["smaCross"]["short"] = params["short"]
        if "long" in params: c["rules"]["smaCross"]["long"] = params["long"]
        if "breakout" in params: c["rules"]["breakout"]["period"] = params["breakout"]
        return ScoreStrategy(c)

    def to_config(self) -> dict:
        return copy.deepcopy(self.cfg)
