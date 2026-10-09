"""テスト用の擬似日足生成（ブラウザ版 genBars と同じ考え方。乱数列は別物）"""
import math, random
from datetime import date, timedelta
from autotrader.simulator import Bar, Symbol


def gen_bars(days, start, vol_pct, seed, drift_pct=6.0, trend_pct=0.0, end=None):
    rnd = random.Random(seed)
    daily_vol = (vol_pct / 100) / math.sqrt(252)
    drift = drift_pct / 100 / 252
    phi = max(0.0, min(0.6, trend_pct / 100))
    end = end or date(2026, 7, 31)
    cur = end - timedelta(days=int(days * 1.45))
    bars, price, prev = [], float(start), 0.0
    while len(bars) < days:
        cur += timedelta(days=1)
        if cur.weekday() >= 5:
            continue
        o = price
        shock = daily_vol * math.sqrt(1 - phi * phi) * rnd.gauss(0, 1)
        ret = drift - 0.5 * daily_vol ** 2 + phi * prev + shock
        prev = ret - drift
        price = price * math.exp(ret)
        c = max(1.0, price)
        rng = abs(c - o) + c * daily_vol * (0.4 + rnd.random())
        h = max(o, c) + rng * rnd.random() * 0.6
        l = max(1.0, min(o, c) - rng * rnd.random() * 0.6)
        bars.append(Bar(cur.isoformat(), round(o, 1), round(h, 1), round(l, 1), round(c, 1),
                        round(500000 * (0.5 + rnd.random() * 1.6))))
    return bars


def market(seed=1, n=5, days=700, drift=6.0, trend=20.0, unit=1):
    base = [("7203", "A", 2800, 24), ("6758", "B", 1350, 30), ("9432", "C", 155, 18),
            ("8306", "D", 1450, 26), ("4063", "E", 520, 32), ("6861", "F", 6000, 28),
            ("9984", "G", 900, 40), ("8035", "H", 2400, 35)]
    out = []
    for i, (code, name, px, vol) in enumerate(base[:n]):
        out.append(Symbol(code=code, name=name, unit=unit, bars=gen_bars(days, px, vol, seed * 10 + i, drift, trend)))
    return out


def write_csv_dir(dirpath, symbols):
    import os
    os.makedirs(dirpath, exist_ok=True)
    for s in symbols:
        with open(os.path.join(dirpath, f"{s.code}.csv"), "w", encoding="utf-8") as f:
            f.write("日付,始値,高値,安値,終値,出来高\n")
            for b in s.bars:
                f.write(f"{b.d},{b.o},{b.h},{b.l},{b.c},{int(b.v)}\n")
