"""kabuステーションAPI の模擬サーバ（テスト用）"""
import json, threading
from http.server import BaseHTTPRequestHandler, HTTPServer


class MockKabu:
    def __init__(self, api_password="apipw", order_password="ordpw", cash=500000.0):
        self.api_password = api_password
        self.order_password = order_password
        self.cash = cash
        self.positions = []     # {Symbol, LeavesQty, Price, Side}
        self.orders = {}        # id -> order dict
        self.seq = 0
        self.token = "TOKEN123"
        self.reject_next = False
        self.received = []
        srv = self
        self._server = None
        self._thread = None

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a): pass

            def _json(self, code, obj):
                b = json.dumps(obj).encode()
                self.send_response(code); self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

            def _body(self):
                n = int(self.headers.get("Content-Length") or 0)
                return json.loads(self.rfile.read(n) or b"{}")

            def _auth(self):
                if self.headers.get("X-API-KEY") != srv.token:
                    self._json(401, {"Code": 4001007, "Message": "Unauthorized"}); return False
                return True

            def do_POST(self):
                srv.received.append(("POST", self.path))
                if self.path.endswith("/token"):
                    b = self._body()
                    if b.get("APIPassword") != srv.api_password:
                        return self._json(401, {"Code": 4001001, "Message": "APIパスワードが違います"})
                    return self._json(200, {"ResultCode": 0, "Token": srv.token})
                if self.path.endswith("/sendorder"):
                    if not self._auth(): return
                    b = self._body()
                    if b.get("Password") != srv.order_password:
                        return self._json(400, {"Code": 4001008, "Message": "注文パスワードが違います"})
                    if srv.reject_next:
                        srv.reject_next = False
                        return self._json(400, {"Code": 100000, "Message": "余力不足"})
                    srv.seq += 1
                    oid = f"2026MOCK{srv.seq:06d}"
                    srv.orders[oid] = {"ID": oid, "Symbol": b["Symbol"], "Side": b["Side"], "Qty": b["Qty"],
                                       "State": 1, "OrderState": 1, "CumQty": 0, "Details": [],
                                       "RecvTime": "2026-07-31T08:30:00", "body": b}
                    return self._json(200, {"Result": 0, "OrderId": oid})
                self._json(404, {"Code": 404, "Message": "not found"})

            def do_GET(self):
                srv.received.append(("GET", self.path))
                if not self._auth(): return
                if "/wallet/cash" in self.path:
                    return self._json(200, {"StockAccountWallet": srv.cash})
                if "/positions" in self.path:
                    return self._json(200, srv.positions)
                if "/orders" in self.path:
                    return self._json(200, [{k: v for k, v in o.items() if k != "body"} for o in srv.orders.values()])
                if "/board/" in self.path:
                    return self._json(200, {"Symbol": self.path.split("/board/")[1].split("@")[0], "CurrentPrice": 1000.0})
                self._json(404, {"Code": 404, "Message": "not found"})

            def do_PUT(self):
                srv.received.append(("PUT", self.path))
                if not self._auth(): return
                if self.path.endswith("/cancelorder"):
                    b = self._body(); o = srv.orders.get(b.get("OrderId"))
                    if not o: return self._json(400, {"Code": 1, "Message": "注文がありません"})
                    o["State"] = 5; return self._json(200, {"Result": 0, "OrderId": b["OrderId"]})
                self._json(404, {"Code": 404, "Message": "not found"})
        self._handler = H

    def start(self):
        self._server = HTTPServer(("127.0.0.1", 0), self._handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return f"http://127.0.0.1:{self._server.server_port}/kabusapi"

    def stop(self):
        if self._server:
            self._server.shutdown()

    def execute_all(self, price_by_code):
        """寄付で全注文を約定させる"""
        for o in self.orders.values():
            if o["State"] != 1: continue
            px = price_by_code[o["Symbol"]]
            o["CumQty"] = o["Qty"]; o["State"] = 5; o["OrderState"] = 5
            o["Details"] = [{"RecType": 1, "Qty": o["Qty"], "Price": 0},
                            {"RecType": 8, "Qty": o["Qty"], "Price": px}]
            if o["Side"] == "2":
                self.positions.append({"Symbol": o["Symbol"], "LeavesQty": o["Qty"], "HoldQty": 0, "Price": px, "Side": "2"})
                self.cash -= px * o["Qty"]
            else:
                self.positions = [p for p in self.positions if p["Symbol"] != o["Symbol"]]
                self.cash += px * o["Qty"]
