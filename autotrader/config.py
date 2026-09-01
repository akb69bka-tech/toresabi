"""設定の既定値と読み込み。

キー名はブラウザ版(株式自動売買システム.html)の state.strategy / state.risk と同一。
ブラウザで検証した設定をそのまま YAML に書き写せば、同じ判定で自走する。
"""
from __future__ import annotations
import copy
import os
from typing import Any, Dict

import yaml

# 50万円・堅実型（ブラウザ版 PRESETS.yen500k と同一）
STRATEGY_500K: Dict[str, Any] = {
    "mode": "score", "buyTh": 3, "sellTh": 3, "exitPolicy": "trend",
    "rules": {
        "regime":   {"on": True,  "w": 0, "period": 200},
        "smaCross": {"on": True,  "w": 2, "short": 20, "long": 60},
        "emaTrend": {"on": True,  "w": 2, "period": 100},
        "rsi":      {"on": False, "w": 1, "period": 14, "low": 30, "high": 75},
        "macd":     {"on": True,  "w": 1, "fast": 12, "slow": 26, "signal": 9},
        "boll":     {"on": False, "w": 1, "period": 20, "k": 2},
        "breakout": {"on": True,  "w": 2, "period": 40},
        "volume":   {"on": True,  "w": 1, "period": 20, "mult": 1.0},
    },
}

RISK_500K: Dict[str, Any] = {
    "initialCash": 500000, "unit": 1, "allowMinUnit": True, "sizing": "ratio", "allocPct": 18,
    "maxPositions": 5, "feePct": 0.55, "slipPct": 0.1,
    "useAtrStop": True, "atrPeriod": 14, "atrMult": 3.5, "stopPct": 10, "takePct": 0, "trailPct": 0,
    "maxHoldDays": 0, "minHoldDays": 5, "cooldownDays": 10,
    "maxDailyTrades": 2, "haltDrawdownPct": 15, "maxConsecLosses": 6, "reserveCashPct": 10,
}

# ブラウザ版の既定（短期売買型）。互換性検証用
STRATEGY_DEFAULT: Dict[str, Any] = {
    "mode": "score", "buyTh": 2, "sellTh": 2, "exitPolicy": "signal",
    "rules": {
        "regime":   {"on": True,  "w": 0, "period": 200},
        "smaCross": {"on": True,  "w": 2, "short": 5,  "long": 25},
        "emaTrend": {"on": True,  "w": 1, "period": 75},
        "rsi":      {"on": True,  "w": 1, "period": 14, "low": 30, "high": 70},
        "macd":     {"on": True,  "w": 1, "fast": 12, "slow": 26, "signal": 9},
        "boll":     {"on": False, "w": 1, "period": 20, "k": 2},
        "breakout": {"on": False, "w": 2, "period": 20},
        "volume":   {"on": False, "w": 1, "period": 20, "mult": 1.2},
    },
}
RISK_DEFAULT: Dict[str, Any] = {
    "initialCash": 1000000, "unit": 100, "allowMinUnit": False, "sizing": "ratio", "allocPct": 20,
    "maxPositions": 5, "feePct": 0.1, "slipPct": 0.05,
    "useAtrStop": False, "atrPeriod": 14, "atrMult": 2, "stopPct": 7, "takePct": 15, "trailPct": 0,
    "maxHoldDays": 0, "minHoldDays": 0, "cooldownDays": 0,
    "maxDailyTrades": 0, "haltDrawdownPct": 0, "maxConsecLosses": 0, "reserveCashPct": 0,
}

DEFAULT_CONFIG: Dict[str, Any] = {
    # 実行モード: demo / paper / live-dryrun / live
    "mode": "paper",
    "timezone": "Asia/Tokyo",
    "data": {
        # stooq / jquants / csv
        "source": "stooq",
        "cache_dir": "data_cache",
        "history_days": 600,
        "csv_dir": "data_csv",
        "jquants_refresh_token": "",
        "request_interval_sec": 0.6,
    },
    "universe": {
        # 監視対象の銘柄リスト(CSV: code,name[,unit])。無ければ同梱のサンプルを使う
        "file": "universe.csv",
        "max_symbols": 0,          # 0で無制限
    },
    "screener": {
        "enabled": True,
        "top_n": 20,               # 売買候補として残す銘柄数
        "min_bars": 250,
        "min_turnover_yen": 100_000_000,   # 1日平均売買代金の下限（流動性）
        "max_atr_pct": 6.0,        # 日々の変動が大きすぎる銘柄を除外
        "rebalance_weekday": 0,    # 候補を入れ替える曜日(0=月)
    },
    "learner": {
        "enabled": True,
        "weekday": 5,              # 再学習を行う曜日(5=土)
        "folds": 3,
        "min_oos_return": 0.0,     # 検証期間の平均リターンがこれを超えない設定は採用しない
    },
    "strategy": STRATEGY_500K,
    "risk": RISK_500K,
    "broker": {
        # paper / kabu
        "type": "paper",
        "kabu": {
            "base_url": "http://localhost:18080/kabusapi",
            "api_password": "",
            "order_password": "",
            "exchange": 1,          # 1=東証
            "account_type": 4,      # 2=一般 4=特定 12=NISA
            "fund_type": "AA",
            "front_order_type": 13, # 13=寄成（翌日の始値で約定）
        },
    },
    "guard": {
        "max_order_value_yen": 150000,   # 1注文あたりの上限金額
        "max_daily_orders": 4,
        "max_total_exposure_pct": 95,    # 建玉評価額の上限（資産比）
        "send_window": ["08:00", "08:55"],  # 発注を送る時刻の範囲(JST)
        "stop_file": "STOP",             # このファイルが存在したら一切発注しない
        "max_api_failures": 3,
    },
    "live": {
        "enabled": False,
        "confirm_phrase": "",             # "私は自己責任で実発注を許可します" と書かないと発注しない
        "min_paper_days": 20,             # ペーパー運用の実績日数がこれ未満なら live を拒否
    },
    "schedule": {
        "signal_time": "18:00",          # 終値確定後にシグナル判定
        "order_time": "08:30",           # 翌朝に発注
    },
    "state_dir": "state",
    "dashboard": {"host": "127.0.0.1", "port": 8765},
}

LIVE_CONFIRM_PHRASE = "私は自己責任で実発注を許可します"


def deep_merge(base: Dict[str, Any], over: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def load_config(path: str = "config.yaml") -> Dict[str, Any]:
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    if path and os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            user = yaml.safe_load(f) or {}
        cfg = deep_merge(cfg, user)
    return cfg
