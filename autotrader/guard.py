"""安全装置。発注の前に必ずここを通す。

- 緊急停止ファイル(STOP)があれば一切発注しない
- 発注できる時刻の範囲、1注文の上限金額、1日の発注回数、建玉総額の上限
- 監視対象外の銘柄には発注しない
- 実発注は明示的な許可フレーズと、ペーパー運用の実績日数がなければ拒否
"""
from __future__ import annotations
import os
from datetime import datetime, time as dtime
from typing import Dict, Optional, Tuple

from .config import LIVE_CONFIRM_PHRASE
from .broker.base import Order


def _parse_hm(s: str) -> dtime:
    h, m = s.split(":")
    return dtime(int(h), int(m))


class Guard:
    def __init__(self, cfg: dict, state_dir: str):
        self.cfg = cfg
        self.g = cfg["guard"]
        self.state_dir = state_dir

    def stop_file(self) -> Optional[str]:
        for p in (self.g.get("stop_file", "STOP"), os.path.join(self.state_dir, self.g.get("stop_file", "STOP"))):
            if p and os.path.exists(p):
                return p
        return None

    def in_send_window(self, now: Optional[datetime] = None) -> bool:
        w = self.g.get("send_window") or ["00:00", "23:59"]
        now = now or datetime.now()
        t = now.time()
        return _parse_hm(w[0]) <= t <= _parse_hm(w[1])

    def check_order(self, order: Order, equity: float, exposure: float, daily_count: int,
                    watchlist: Optional[set] = None, now: Optional[datetime] = None) -> Tuple[bool, str]:
        sf = self.stop_file()
        if sf:
            return False, f"緊急停止ファイル {sf} が存在するため発注しません"
        if order.qty <= 0:
            return False, "数量が0です"
        if order.side == "buy":
            value = order.est_price * order.qty
            mx = self.g.get("max_order_value_yen", 0)
            if mx and value > mx:
                return False, f"1注文の上限 {mx:,}円 を超えています（{value:,.0f}円）"
            md = self.g.get("max_daily_orders", 0)
            if md and daily_count >= md:
                return False, f"本日の発注回数が上限 {md} 回に達しています"
            mp = self.g.get("max_total_exposure_pct", 0)
            if mp and equity > 0 and (exposure + value) / equity * 100 > mp:
                return False, f"建玉総額が上限 {mp}% を超えます"
            if watchlist is not None and order.code not in watchlist:
                return False, "監視対象外の銘柄です"
        if not self.in_send_window(now):
            return False, f"発注可能な時刻 {self.g.get('send_window')} の範囲外です"
        return True, ""

    def check_live_allowed(self, paper_days: int) -> Tuple[bool, str]:
        live = self.cfg.get("live", {})
        if not live.get("enabled"):
            return False, "config の live.enabled が false です"
        if live.get("confirm_phrase", "") != LIVE_CONFIRM_PHRASE:
            return False, f"config の live.confirm_phrase に「{LIVE_CONFIRM_PHRASE}」と正確に書いてください"
        need = int(live.get("min_paper_days", 0))
        if paper_days < need:
            return False, f"ペーパー運用の実績が {paper_days} 日で、必要な {need} 日に達していません"
        if self.cfg["risk"].get("unit", 100) < 100 and self.cfg["broker"].get("type") == "kabu":
            return False, "kabuステーションAPIは単元未満株に対応していません。risk.unit を 100 にしてください"
        return True, ""
