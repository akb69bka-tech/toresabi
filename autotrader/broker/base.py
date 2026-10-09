"""証券会社接続の共通インターフェース。"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Optional


class BrokerError(Exception):
    pass


@dataclass
class Order:
    code: str
    side: str                 # 'buy' / 'sell'
    qty: int
    order_type: str = "open"  # 'open'=寄成(翌日の始値) / 'market'=成行 / 'limit'=指値
    price: Optional[float] = None
    reason: str = ""
    stop: Optional[float] = None
    take: Optional[float] = None
    est_price: float = 0.0    # 発注時点の参考価格（終値）。金額上限の判定に使う
    client_id: str = ""


@dataclass
class Fill:
    code: str
    side: str
    qty: int
    price: float
    order_id: str
    date: str


class Broker(ABC):
    name = "base"

    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def cash(self) -> float: ...

    @abstractmethod
    def positions(self) -> Dict[str, Dict[str, float]]:
        """{code: {"qty": int, "avg": float}}"""

    @abstractmethod
    def send(self, order: Order) -> str:
        """発注して注文IDを返す"""

    @abstractmethod
    def fills(self, order_ids: List[str]) -> List[Fill]: ...

    @abstractmethod
    def cancel(self, order_id: str) -> None: ...
