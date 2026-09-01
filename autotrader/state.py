"""口座状態と実行記録の永続化。

Account はシミュレータの内部状態と同じ項目を持ち、日次の判定のたびに
シミュレータへ流し込み、結果を書き戻す。これによりデモ・ペーパー・実運用の
すべてがブラウザ版と同一の判定ロジックを通る。
"""
from __future__ import annotations
import json, os
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional

from .simulator import Sim


@dataclass
class Account:
    cash: float
    initialCash: float
    positions: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    pending: List[Dict[str, Any]] = field(default_factory=list)   # [{code, side, reason, qty, orderId}]
    lastExit: Dict[str, str] = field(default_factory=dict)
    consecLosses: int = 0
    halted: bool = False
    haltReason: str = ""
    dayTrades: int = 0
    dayDate: Optional[str] = None
    costs: float = 0.0
    trades: List[Dict[str, Any]] = field(default_factory=list)
    equity: List[Dict[str, Any]] = field(default_factory=list)
    lastCycleDate: Optional[str] = None
    paperDays: int = 0
    mode: str = "paper"
    watchlist: List[str] = field(default_factory=list)
    watchlistDate: Optional[str] = None


def _p(state_dir: str, name: str) -> str:
    os.makedirs(state_dir, exist_ok=True)
    return os.path.join(state_dir, name)


def load_json(state_dir: str, name: str, default=None):
    p = _p(state_dir, name)
    if not os.path.exists(p):
        return default
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def save_json(state_dir: str, name: str, obj):
    tmp = _p(state_dir, name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1, default=str)
    os.replace(tmp, _p(state_dir, name))


def load_account(state_dir: str, initial_cash: float, mode: str) -> Account:
    d = load_json(state_dir, "account.json")
    if d:
        acc = Account(**{k: v for k, v in d.items() if k in Account.__dataclass_fields__})
        acc.mode = mode
        return acc
    return Account(cash=float(initial_cash), initialCash=float(initial_cash), mode=mode)


def save_account(state_dir: str, acc: Account):
    save_json(state_dir, "account.json", asdict(acc))


def append_log(state_dir: str, msg: str, level: str = "info", **extra):
    entry = {"t": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "level": level, "m": msg, **extra}
    with open(_p(state_dir, "log.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    print(f"[{entry['t']}] {level.upper():5s} {msg}")


def read_log(state_dir: str, n: int = 200) -> List[dict]:
    p = _p(state_dir, "log.jsonl")
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as f:
        lines = f.readlines()[-n:]
    return [json.loads(l) for l in lines if l.strip()]


# ---- シミュレータとの相互変換 ----
def seed_sim(sim: Sim, acc: Account):
    sim.cash = float(acc.cash)
    sim.positions = {k: dict(v) for k, v in acc.positions.items()}
    idx = {c.sym.code: i for i, c in enumerate(sim.ctx)}
    sim.pending = [{"ci": idx[p["code"]], "side": p["side"], "reason": p.get("reason", "")}
                   for p in acc.pending if p["code"] in idx]
    sim.lastExit = dict(acc.lastExit)
    sim.consecLosses = acc.consecLosses
    sim.halted = acc.halted
    sim.haltReason = acc.haltReason
    sim.dayTrades = acc.dayTrades
    sim.dayDate = acc.dayDate
    sim.costs = acc.costs
    sim.trades = []
    sim.equity = []


def extract_account(sim: Sim, acc: Account):
    acc.cash = sim.cash
    acc.positions = {k: dict(v) for k, v in sim.positions.items()}
    acc.pending = [{"code": sim.ctx[p["ci"]].sym.code, "side": p["side"], "reason": p.get("reason", "")}
                   for p in sim.pending]
    acc.lastExit = dict(sim.lastExit)
    acc.consecLosses = sim.consecLosses
    acc.halted = sim.halted
    acc.haltReason = sim.haltReason
    acc.dayTrades = sim.dayTrades
    acc.dayDate = sim.dayDate
    acc.costs = sim.costs
    acc.trades.extend(sim.trades)
    for e in sim.equity:
        if acc.equity and acc.equity[-1]["d"] == e["d"]:
            acc.equity[-1] = e
        else:
            acc.equity.append(e)
