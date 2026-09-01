"""ペーパートレード用。実際の発注は行わず、記録だけ残す。

約定はシミュレータ（ブラウザ版と同一ロジック）が翌営業日の始値で行うため、
このクラスは注文の受付だけを担当する。
"""
from __future__ import annotations
import uuid
from typing import Dict, List

from .base import Broker, Order, Fill


class PaperBroker(Broker):
    name = "paper"

    def __init__(self, account=None):
        self.account = account
        self.sent: List[dict] = []

    def connect(self) -> None:
        return None

    def cash(self) -> float:
        return float(self.account.cash) if self.account else 0.0

    def positions(self) -> Dict[str, Dict[str, float]]:
        if not self.account:
            return {}
        return {c: {"qty": p["qty"], "avg": p["avg"]} for c, p in self.account.positions.items()}

    def send(self, order: Order) -> str:
        oid = "PAPER-" + uuid.uuid4().hex[:10]
        self.sent.append({"id": oid, "order": order.__dict__.copy()})
        return oid

    def fills(self, order_ids: List[str]) -> List[Fill]:
        return []

    def cancel(self, order_id: str) -> None:
        return None
