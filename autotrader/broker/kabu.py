"""kabuステーションAPI（auカブコム証券）への接続。

公開仕様（http://localhost:18080/kabusapi）に沿って実装している。
このコードは実口座に対しては未検証で、模擬サーバに対するテストのみ行っている。
必ず検証環境(port 18081)と live-dryrun モードで動作を確かめてから使うこと。

注意: kabuステーションAPIでは単元未満株(プチ株)の売買はできない。
実発注では単元株(通常100株)の設定が必要になる。
"""
from __future__ import annotations
from datetime import date
from typing import Dict, List, Optional

import requests

from .base import Broker, BrokerError, Order, Fill


class KabuBroker(Broker):
    name = "kabu"

    def __init__(self, cfg: dict, session=None):
        self.base = cfg.get("base_url", "http://localhost:18080/kabusapi").rstrip("/")
        self.api_password = cfg.get("api_password", "")
        self.order_password = cfg.get("order_password", "")
        self.exchange = int(cfg.get("exchange", 1))
        self.account_type = int(cfg.get("account_type", 4))
        self.fund_type = cfg.get("fund_type", "AA")
        self.front_order_type = int(cfg.get("front_order_type", 13))
        self.session = session or requests.Session()
        self.token: Optional[str] = None

    # ---- 基本 ----
    def _headers(self) -> dict:
        if not self.token:
            self.connect()
        return {"X-API-KEY": self.token, "Content-Type": "application/json"}

    def _raise(self, r: requests.Response, what: str):
        try:
            j = r.json()
        except ValueError:
            j = {}
        code = j.get("Code")
        msg = j.get("Message") or r.text[:200]
        raise BrokerError(f"{what} 失敗 HTTP {r.status_code} Code={code} {msg}")

    def _get(self, path: str, params=None) -> dict:
        r = self.session.get(self.base + path, headers=self._headers(), params=params, timeout=15)
        if r.status_code == 401:
            self.token = None
            r = self.session.get(self.base + path, headers=self._headers(), params=params, timeout=15)
        if r.status_code != 200:
            self._raise(r, "GET " + path)
        return r.json()

    def connect(self) -> None:
        if not self.api_password:
            raise BrokerError("kabu.api_password が設定されていません")
        r = self.session.post(self.base + "/token", json={"APIPassword": self.api_password}, timeout=15)
        if r.status_code != 200:
            self._raise(r, "認証")
        j = r.json()
        if j.get("ResultCode") != 0 or not j.get("Token"):
            raise BrokerError(f"認証失敗: {j}")
        self.token = j["Token"]

    # ---- 照会 ----
    def cash(self) -> float:
        j = self._get("/wallet/cash")
        return float(j.get("StockAccountWallet") or 0)

    def positions(self) -> Dict[str, Dict[str, float]]:
        items = self._get("/positions", params={"product": 1})
        out: Dict[str, Dict[str, float]] = {}
        for it in items:
            if str(it.get("Side")) != "2":       # 2=買建(現物保有)
                continue
            code = str(it.get("Symbol"))
            qty = int(it.get("LeavesQty") or 0)
            if qty <= 0:
                continue
            price = float(it.get("Price") or 0)
            cur = out.setdefault(code, {"qty": 0, "avg": 0.0})
            total = cur["qty"] + qty
            cur["avg"] = (cur["avg"] * cur["qty"] + price * qty) / total if total else 0.0
            cur["qty"] = total
        return out

    def board(self, code: str) -> dict:
        return self._get(f"/board/{code}@{self.exchange}")

    # ---- 発注 ----
    def send(self, order: Order) -> str:
        if not self.order_password:
            raise BrokerError("kabu.order_password が設定されていません")
        body = {
            "Password": self.order_password,
            "Symbol": order.code,
            "Exchange": self.exchange,
            "SecurityType": 1,
            "Side": "2" if order.side == "buy" else "1",
            "CashMargin": 1,
            "DelivType": 2 if order.side == "buy" else 0,
            "AccountType": self.account_type,
            "Qty": int(order.qty),
            "ExpireDay": 0,
        }
        if order.side == "buy":
            body["FundType"] = self.fund_type
        if order.order_type == "limit" and order.price:
            body["FrontOrderType"] = 20
            body["Price"] = float(order.price)
        elif order.order_type == "market":
            body["FrontOrderType"] = 10
            body["Price"] = 0
        else:
            body["FrontOrderType"] = self.front_order_type   # 13=寄成
            body["Price"] = 0
        r = self.session.post(self.base + "/sendorder", headers=self._headers(), json=body, timeout=20)
        if r.status_code != 200:
            self._raise(r, "発注")
        j = r.json()
        if j.get("Result") != 0 or not j.get("OrderId"):
            raise BrokerError(f"発注が受け付けられませんでした: {j}")
        return str(j["OrderId"])

    def fills(self, order_ids: List[str]) -> List[Fill]:
        wanted = set(order_ids)
        if not wanted:
            return []
        items = self._get("/orders", params={"product": 1})
        out: List[Fill] = []
        for it in items:
            oid = str(it.get("ID"))
            if oid not in wanted:
                continue
            qty_total, amt = 0, 0.0
            for d in it.get("Details") or []:
                if int(d.get("RecType") or 0) == 8 and int(d.get("Qty") or 0) > 0:   # 8=約定
                    q = int(d["Qty"]); p = float(d.get("Price") or 0)
                    qty_total += q; amt += q * p
            if qty_total <= 0:
                continue
            out.append(Fill(code=str(it.get("Symbol")), side="buy" if str(it.get("Side")) == "2" else "sell",
                            qty=qty_total, price=amt / qty_total, order_id=oid,
                            date=str(it.get("RecvTime") or date.today().isoformat())[:10]))
        return out

    def cancel(self, order_id: str) -> None:
        r = self.session.put(self.base + "/cancelorder", headers=self._headers(),
                             json={"OrderId": order_id, "Password": self.order_password}, timeout=15)
        if r.status_code != 200:
            self._raise(r, "取消")
