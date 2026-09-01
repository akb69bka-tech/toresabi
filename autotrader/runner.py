"""日次の運用サイクル。

夕方(終値確定後): signal_cycle
  株価取得 → 全銘柄スクリーニング → 保有/候補銘柄の判定 → 翌朝の注文を確定
翌朝(寄り前):    order_cycle   ※ live / live-dryrun のみ
  安全装置を通した注文だけを証券会社へ送る（dryrun は記録のみ）
夕方(次の判定前): reconcile     ※ live のみ
  約定結果を口座へ反映する

demo / paper では約定もシミュレータが行う（ブラウザ版と同一ロジック）。
"""
from __future__ import annotations
import math
import time
from datetime import datetime, date
from typing import Dict, List, Optional

from . import learner, screener
from .broker import make_broker, BrokerError, Order
from .config import LIVE_CONFIRM_PHRASE
from .data import make_source, load_universe
from .guard import Guard
from .simulator import (Symbol, Ctx, create_sim, sim_step, calc_qty, calc_stop, mtm, days_between,
                        backtest)
from .strategy import eval_signal
from .state import (Account, load_account, save_account, append_log, load_json, save_json,
                    seed_sim, extract_account)

MODES = ("demo", "paper", "live-dryrun", "live")


class Runner:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.mode = cfg.get("mode", "paper")
        if self.mode not in MODES:
            raise ValueError(f"mode は {MODES} のいずれかです: {self.mode}")
        self.state_dir = cfg.get("state_dir", "state")
        self.risk = cfg["risk"]
        self.strat = self._load_strategy()
        self.acc = load_account(self.state_dir, self.risk["initialCash"], self.mode)
        self.source = make_source(cfg)
        self.universe = load_universe(cfg["universe"].get("file", ""), cfg["universe"].get("max_symbols", 0),
                                      default_unit=int(self.risk.get("unit", 100)))
        self.guard = Guard(cfg, self.state_dir)
        self.broker = make_broker(cfg, self.acc)
        self._history: Dict[str, Symbol] = {}

    # ---------- 設定 ----------
    def _load_strategy(self) -> dict:
        # 再学習で採用した設定があればそちらを優先する
        learned = load_json(self.state_dir, "strategy_learned.json")
        return learned if learned else self.cfg["strategy"]

    def log(self, msg: str, level: str = "info", **extra):
        append_log(self.state_dir, msg, level, **extra)

    # ---------- データ ----------
    def load_history(self, symbols: Optional[List[Symbol]] = None, days: Optional[int] = None) -> List[Symbol]:
        symbols = symbols if symbols is not None else self.universe
        days = days or int(self.cfg["data"].get("history_days", 600))
        out: List[Symbol] = []
        t0 = time.time()
        for i, s in enumerate(symbols):
            bars = self.source.fetch(s.code, days)
            if bars:
                sym = Symbol(code=s.code, name=s.name, unit=s.unit, bars=bars)
                out.append(sym)
                self._history[s.code] = sym
        self.log(f"株価取得 {len(out)}/{len(symbols)}銘柄（{self.source.name}、{time.time()-t0:.0f}秒）")
        return out

    def latest_date(self, symbols: List[Symbol]) -> Optional[str]:
        ds = [s.bars[-1].d for s in symbols if s.bars]
        return max(ds) if ds else None

    # ---------- スクリーニング ----------
    def refresh_watchlist(self, symbols: List[Symbol], today: str, force: bool = False) -> List[str]:
        sc = self.cfg["screener"]
        if not sc.get("enabled", True):
            # スクリーニング無効時は全銘柄を対象にする
            self.acc.watchlist = [s.code for s in symbols]
            self.acc.watchlistDate = today
            return self.acc.watchlist
        wd = date.fromisoformat(today).weekday()
        due = force or not self.acc.watchlist or self.acc.watchlistDate is None \
            or wd == int(sc.get("rebalance_weekday", 0)) and self.acc.watchlistDate != today
        if not due:
            return self.acc.watchlist
        rows = screener.screen(symbols, self.risk, sc)
        wl = screener.select_watchlist(rows, int(sc.get("top_n", 20)))
        save_json(self.state_dir, "screener.json", {"date": today, "rows": rows[:200], "watchlist": wl})
        self.acc.watchlist = wl
        self.acc.watchlistDate = today
        ok = sum(1 for r in rows if r["ok"])
        self.log(f"スクリーニング {len(rows)}銘柄 → 条件通過 {ok}銘柄 → 候補 {len(wl)}銘柄: {', '.join(wl[:10])}"
                 + ("…" if len(wl) > 10 else ""))
        return wl

    # ---------- 判定 ----------
    def tradable(self, symbols: List[Symbol]) -> List[Symbol]:
        keep = set(self.acc.watchlist) | set(self.acc.positions) | {p["code"] for p in self.acc.pending}
        return [s for s in symbols if s.code in keep]

    def signal_cycle(self, today: Optional[str] = None, symbols: Optional[List[Symbol]] = None) -> dict:
        """夕方の判定。今日の足を反映し、翌朝の注文を確定する"""
        symbols = symbols if symbols is not None else self.load_history()
        if not symbols:
            self.log("株価が1銘柄も取得できませんでした", "error")
            return {"ok": False}
        today = today or self.latest_date(symbols)
        if self.acc.lastCycleDate == today:
            self.log(f"{today} の判定は実施済みです（データ未更新の可能性）")
            return {"ok": True, "skipped": True, "date": today}

        self.refresh_watchlist(symbols, today)
        tr = self.tradable(symbols)
        if not tr:
            self.log("判定対象の銘柄がありません（候補ゼロ・保有なし）", "warn")
            self.acc.lastCycleDate = today
            save_account(self.state_dir, self.acc)
            return {"ok": True, "date": today, "orders": []}

        sim = create_sim(tr, self.strat, self.risk, frm=today, to=today)
        if sim is None:
            self.log(f"{today} の足が無いため判定できません", "warn")
            return {"ok": False}
        seed_sim(sim, self.acc)
        # demo/paper はシミュレータが約定させる。live 系は証券会社が約定済み(reconcile で反映)
        sim_step(sim, execute_pending=self.mode in ("demo", "paper"), finish_at_end=False)
        extract_account(sim, self.acc)
        for e in sim.events:
            self.log(f"{e['d']} {e['m']}")

        # 画面表示用に各銘柄のシグナルを記録
        signals = []
        for c in sim.ctx:
            i = c.idx.get(today)
            if i is None:
                continue
            sg = eval_signal(c.bars, c.ind, i, self.strat)
            signals.append({"code": c.sym.code, "name": c.sym.name, "price": c.bars[i].c,
                            "action": sg["action"], "score": sg["score"], "blocked": sg["blocked"],
                            "held": c.sym.code in self.acc.positions,
                            "votes": [v["text"] for v in sg["votes"] if v["text"]]})
        signals.sort(key=lambda s: -abs(s["score"]))

        # 翌朝の注文の数量と参考価格を確定（実発注用）
        orders = self._plan_orders(sim)
        self.acc.pending = orders
        self.acc.lastCycleDate = today
        if self.mode == "paper":
            self.acc.paperDays += 1
        save_account(self.state_dir, self.acc)
        equity = self.acc.cash + self._mtm_now(sim, today)
        save_json(self.state_dir, "signals.json", {
            "date": today, "mode": self.mode, "equity": equity, "cash": self.acc.cash,
            "signals": signals, "orders": orders, "halted": self.acc.halted, "haltReason": self.acc.haltReason,
        })
        buys = sum(1 for o in orders if o["side"] == "buy")
        sells = len(orders) - buys
        self.log(f"{today} 判定完了（{self.mode}）資産 {equity:,.0f}円 / 翌朝の注文 買い{buys} 売り{sells}"
                 + (f" / ⛔ {self.acc.haltReason}" if self.acc.halted else ""))
        return {"ok": True, "date": today, "orders": orders, "equity": equity, "signals": signals}

    def _mtm_now(self, sim, today: str) -> float:
        return mtm(sim.ctx, self.acc.positions, today)

    def _plan_orders(self, sim) -> List[dict]:
        out = []
        today = sim.dates[sim.startT]
        equity = self.acc.cash + self._mtm_now(sim, today)
        for p in sim.pending:
            c = sim.ctx[p["ci"]]
            i = c.idx.get(today)
            if i is None:
                continue
            price = c.bars[i].c
            if p["side"] == "buy":
                reserve = self.risk["initialCash"] * (self.risk.get("reserveCashPct") or 0) / 100
                qty = calc_qty(c, i, price, equity, max(0.0, self.acc.cash - reserve), self.risk)
                if qty <= 0:
                    continue
                out.append({"code": c.sym.code, "name": c.sym.name, "side": "buy", "qty": qty,
                            "estPrice": price, "reason": "買いシグナル",
                            "stop": calc_stop(c, today, price, self.risk)})
            else:
                pos = self.acc.positions.get(c.sym.code)
                if not pos:
                    continue
                out.append({"code": c.sym.code, "name": c.sym.name, "side": "sell", "qty": int(pos["qty"]),
                            "estPrice": price, "reason": p.get("reason") or "売りシグナル"})
        return out

    # ---------- 発注（live 系） ----------
    def order_cycle(self, now: Optional[datetime] = None) -> dict:
        if self.mode in ("demo", "paper"):
            self.log("demo/paper では発注は行いません（シミュレータが約定させます）")
            return {"ok": True, "sent": []}
        live = self.mode == "live"
        if live:
            ok, why = self.guard.check_live_allowed(self.acc.paperDays)
            if not ok:
                self.log("実発注は許可されていません: " + why, "error")
                return {"ok": False, "reason": why}
        try:
            self.broker.connect()
        except BrokerError as e:
            self.log(f"証券会社に接続できません: {e}", "error")
            return {"ok": False, "reason": str(e)}

        sent, rejected = [], []
        daily = 0
        exposure = sum(p["qty"] * p["avg"] for p in self.acc.positions.values())
        equity = self.acc.cash + exposure
        for p in self.acc.pending:
            if p.get("orderId"):
                continue
            o = Order(code=p["code"], side=p["side"], qty=int(p["qty"]), est_price=float(p["estPrice"]),
                      reason=p.get("reason", ""), stop=p.get("stop"))
            ok, why = self.guard.check_order(o, equity, exposure, daily, set(self.acc.watchlist) | set(self.acc.positions), now)
            if not ok:
                rejected.append({**p, "why": why})
                self.log(f"発注見送り {p['side']} {p['code']} {p['qty']}株: {why}", "warn")
                continue
            if live:
                try:
                    oid = self.broker.send(o)
                except BrokerError as e:
                    self.log(f"発注失敗 {p['code']}: {e}", "error")
                    rejected.append({**p, "why": str(e)})
                    continue
                p["orderId"] = oid
                self.log(f"発注 {p['side']} {p['code']} {p['qty']}株（参考 {o.est_price:,.1f}円）→ 注文ID {oid}")
            else:
                p["orderId"] = "DRYRUN"
                self.log(f"[試運転] 発注しない {p['side']} {p['code']} {p['qty']}株（参考 {o.est_price:,.1f}円）")
            if o.side == "buy":
                daily += 1
                exposure += o.est_price * o.qty
            sent.append(p)
        save_account(self.state_dir, self.acc)
        return {"ok": True, "sent": sent, "rejected": rejected}

    def reconcile(self, symbols: Optional[List[Symbol]] = None) -> dict:
        """約定結果を口座へ反映する（live のみ）。dryrun は注文を破棄する"""
        if self.mode != "live":
            if self.mode == "live-dryrun" and self.acc.pending:
                self.log(f"[試運転] 未約定扱いで {len(self.acc.pending)} 件の注文を破棄")
            self.acc.pending = []
            save_account(self.state_dir, self.acc)
            return {"ok": True, "fills": []}
        ids = [p["orderId"] for p in self.acc.pending if p.get("orderId")]
        try:
            self.broker.connect()
            fills = self.broker.fills(ids)
        except BrokerError as e:
            self.log(f"約定照会に失敗: {e}", "error")
            return {"ok": False}
        symbols = symbols if symbols is not None else self.load_history()
        hist = {s.code: s for s in symbols}
        fee = self.risk["feePct"] / 100
        for f in fills:
            p = next((p for p in self.acc.pending if p.get("orderId") == f.order_id), None)
            if f.side == "buy":
                cost = f.price * f.qty * (1 + fee)
                self.acc.cash -= cost
                self.acc.costs += f.price * f.qty * fee
                stop = None
                s = hist.get(f.code)
                if s:
                    c = Ctx(s, s.bars, self.strat, self.risk)
                    stop = calc_stop(c, f.date if f.date in c.idx else s.bars[-1].d, f.price, self.risk)
                self.acc.positions[f.code] = {"qty": f.qty, "avg": f.price, "entryDate": f.date, "peak": f.price,
                                              "stop": stop, "take": None}
                self.acc.dayTrades += 1
                self.log(f"約定 買い {f.code} {f.qty}株 @{f.price:,.1f}（{cost:,.0f}円）")
            else:
                pos = self.acc.positions.pop(f.code, None)
                if not pos:
                    continue
                proceeds = f.price * f.qty * (1 - fee)
                cost = pos["avg"] * f.qty * (1 + fee)
                self.acc.cash += proceeds
                self.acc.costs += f.price * f.qty * fee
                pnl = proceeds - cost
                self.acc.trades.append({"code": f.code, "name": hist.get(f.code, Symbol(f.code)).name, "qty": f.qty,
                                        "entryDate": pos["entryDate"], "entryPrice": pos["avg"],
                                        "exitDate": f.date, "exitPrice": f.price, "pnl": pnl,
                                        "pnlPct": pnl / cost * 100 if cost else 0,
                                        "reason": (p or {}).get("reason", "売り"),
                                        "days": days_between(pos["entryDate"], f.date)})
                self.acc.lastExit[f.code] = f.date
                self.acc.consecLosses = 0 if pnl > 0 else self.acc.consecLosses + 1
                self.log(f"約定 売り {f.code} {f.qty}株 @{f.price:,.1f} 損益 {pnl:+,.0f}円")
        # 現金は証券会社の値を正とする
        try:
            self.acc.cash = self.broker.cash()
        except BrokerError:
            pass
        self.acc.pending = []
        save_account(self.state_dir, self.acc)
        return {"ok": True, "fills": [f.__dict__ for f in fills]}

    # ---------- 再学習 ----------
    def learn(self, symbols: Optional[List[Symbol]] = None) -> dict:
        lc = self.cfg["learner"]
        symbols = symbols if symbols is not None else self.load_history()
        tr = self.tradable(symbols) or symbols[:20]
        t0 = time.time()
        res = learner.propose(tr, self.strat, self.risk, lc)
        res["elapsed"] = time.time() - t0
        res["date"] = self.latest_date(symbols)
        hist = load_json(self.state_dir, "learner.json", default=[]) or []
        hist.append({k: v for k, v in res.items() if k != "wf"} | {"wfSummary": {
            k: v for k, v in (res.get("wf") or {}).items() if k != "rows"}})
        save_json(self.state_dir, "learner.json", hist[-50:])
        if res["adopt"]:
            self.strat = learner.apply_params(self.strat, res["candidate"])
            save_json(self.state_dir, "strategy_learned.json", self.strat)
            self.log(f"再学習: 採用 {res['current']} → {res['candidate']}。{res['reason']}")
        else:
            self.log(f"再学習: 現状維持。{res['reason']}")
        return res

    # ---------- デモ再生 ----------
    def demo(self, days: int = 250, symbols: Optional[List[Symbol]] = None) -> dict:
        symbols = symbols if symbols is not None else self.load_history()
        today = self.latest_date(symbols)
        self.refresh_watchlist(symbols, today, force=True)
        tr = self.tradable(symbols) or symbols
        dates = sorted({b.d for s in tr for b in s.bars})
        frm = dates[max(0, len(dates) - days)]
        res = backtest(tr, self.strat, self.risk, frm, None)
        if not res:
            return {"ok": False}
        m, bh, st = res["metrics"], res["buyHold"], res["steady"]
        summary = {
            "from": frm, "to": dates[-1], "symbols": len(tr),
            "finalEquity": m["finalEquity"], "totalRet": m["totalRet"], "maxDD": m["maxDD"],
            "trades": m["trades"], "winRate": m["winRate"], "costs": res["costs"],
            "buyHoldRet": bh["totalRet"], "buyHoldDD": bh["maxDD"],
            "winMonthRate": st["winMonthRate"], "maxLoseStreak": st["maxLoseStreak"],
            "halted": res["halted"], "haltReason": res["haltReason"],
        }
        save_json(self.state_dir, "demo.json", {"summary": summary, "trades": res["trades"][-100:],
                                                "equity": res["equity"]})
        return {"ok": True, **summary}

    # ---------- 状態 ----------
    def status(self) -> dict:
        sig = load_json(self.state_dir, "signals.json", default={}) or {}
        return {
            "mode": self.mode, "cash": self.acc.cash, "positions": self.acc.positions,
            "pending": self.acc.pending, "halted": self.acc.halted, "haltReason": self.acc.haltReason,
            "paperDays": self.acc.paperDays, "lastCycleDate": self.acc.lastCycleDate,
            "watchlist": self.acc.watchlist, "trades": len(self.acc.trades),
            "equity": sig.get("equity"), "stopFile": self.guard.stop_file(),
            "strategyParams": learner.current_params(self.strat),
        }


def evening_job(cfg: dict) -> dict:
    """夕方に一括で行う処理: (live) 約定反映 → 判定 → (曜日により) 再学習"""
    r = Runner(cfg)
    symbols = r.load_history()
    if r.mode in ("live", "live-dryrun"):
        r.reconcile(symbols)
    res = r.signal_cycle(symbols=symbols)
    lc = cfg["learner"]
    today = res.get("date")
    if lc.get("enabled") and today and date.fromisoformat(today).weekday() == int(lc.get("weekday", 5)):
        r.learn(symbols)
    return res


def morning_job(cfg: dict) -> dict:
    r = Runner(cfg)
    return r.order_cycle()
