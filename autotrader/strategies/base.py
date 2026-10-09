from __future__ import annotations
from typing import Any, Dict, List, Optional


class Strategy:
    """戦略の共通インターフェース"""
    type = "base"
    label = "基底"
    description = ""
    # 再学習で探索するパラメータ候補（無ければ学習対象外）
    param_grid: List[Dict[str, Any]] = []

    def __init__(self, params: Optional[dict] = None):
        self.params = dict(self.defaults(), **(params or {}))

    @classmethod
    def defaults(cls) -> dict:
        return {}

    def build(self, bars, risk: dict) -> dict:
        """銘柄ごとの指標。sim の Ctx.ind に入る"""
        return {}

    def eval(self, bars, ind: dict, i: int) -> dict:
        """i 本目の終値時点の判定。{action: buy/sell/hold, score, votes:[{text}], blocked}"""
        return {"action": "hold", "score": 0.0, "votes": [], "blocked": ""}

    def portfolio(self, sim, date: str) -> Optional[List[dict]]:
        """銘柄横断の判定。None を返せば eval() による銘柄ごとの判定が使われる。
        返す場合は [{ci, side, reason}] の翌日注文リスト"""
        return None

    def apply_params(self, params: dict) -> "Strategy":
        return type(self)(dict(self.params, **params))

    def to_config(self) -> dict:
        return {"type": self.type, "params": dict(self.params)}

    def current_params(self) -> dict:
        return dict(self.params)


STRATEGY_TYPES: Dict[str, type] = {}


def register(cls):
    STRATEGY_TYPES[cls.type] = cls
    return cls


def get_strategy(cfg) -> Strategy:
    """設定(dict) または Strategy をそのまま受け付ける"""
    if isinstance(cfg, Strategy):
        return cfg
    from . import score  # noqa: F401  登録のため
    from . import momentum, pullback, donchian  # noqa: F401
    t = (cfg or {}).get("type", "score")
    if t not in STRATEGY_TYPES:
        raise ValueError(f"未知の戦略 type={t}。使用可能: {list(STRATEGY_TYPES)}")
    cls = STRATEGY_TYPES[t]
    if t == "score":
        return cls(cfg)                 # 旧形式(rules を含む dict 全体)をそのまま渡す
    return cls(cfg.get("params") or {})


def describe_all() -> List[dict]:
    from . import score, momentum, pullback, donchian  # noqa: F401
    return [{"type": c.type, "label": c.label, "description": c.description, "defaults": c.defaults()}
            for c in STRATEGY_TYPES.values()]
