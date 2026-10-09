"""通知（freqtrade の Telegram 通知を参考に、Webhook 方式で実装）。

Discord / Slack の Incoming Webhook にそのまま投げられる形式。
設定: notify.webhook_url、notify.events（signal/order/halt/error/learn）
"""
from __future__ import annotations
import json
from typing import Optional

import requests


class Notifier:
    def __init__(self, cfg: dict, session=None):
        n = cfg.get("notify") or {}
        self.url: str = n.get("webhook_url") or ""
        self.events = set(n.get("events") or [])
        self.session = session or requests.Session()
        self.last_error: Optional[str] = None

    def enabled(self, event: str) -> bool:
        return bool(self.url) and event in self.events

    def send(self, event: str, text: str) -> bool:
        if not self.enabled(event):
            return False
        body = {"content": text, "text": text}     # content=Discord, text=Slack
        try:
            r = self.session.post(self.url, data=json.dumps(body).encode("utf-8"),
                                  headers={"Content-Type": "application/json"}, timeout=10)
            ok = 200 <= r.status_code < 300
            self.last_error = None if ok else f"HTTP {r.status_code}"
            return ok
        except requests.RequestException as e:
            self.last_error = str(e)
            return False
