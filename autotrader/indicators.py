"""テクニカル指標。

ブラウザ版の indicators と計算順序まで揃えてある。
どの関数も入力と同じ長さのリストを返し、計算に必要な期間に満たない位置は None。
"""
from __future__ import annotations
import math
from typing import List, Optional, Sequence

Series = List[Optional[float]]


def sma(arr: Sequence[float], p: int) -> Series:
    out: Series = [None] * len(arr)
    if p < 1:
        return out
    s = 0.0
    for i, v in enumerate(arr):
        s += v
        if i >= p:
            s -= arr[i - p]
        if i >= p - 1:
            out[i] = s / p
    return out


def ema(arr: Sequence[float], p: int) -> Series:
    out: Series = [None] * len(arr)
    if p < 1 or len(arr) < p:
        return out
    k = 2 / (p + 1)
    prev = 0.0
    for i in range(p):
        prev += arr[i]
    prev /= p
    out[p - 1] = prev
    for i in range(p, len(arr)):
        prev = arr[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def rsi(closes: Sequence[float], p: int) -> Series:
    out: Series = [None] * len(closes)
    if len(closes) <= p:
        return out
    gain = 0.0
    loss = 0.0
    for i in range(1, p + 1):
        d = closes[i] - closes[i - 1]
        if d >= 0:
            gain += d
        else:
            loss -= d
    gain /= p
    loss /= p
    out[p] = 100.0 if loss == 0 else 100 - 100 / (1 + gain / loss)
    for i in range(p + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        gain = (gain * (p - 1) + (d if d > 0 else 0)) / p
        loss = (loss * (p - 1) + (-d if d < 0 else 0)) / p
        out[i] = 100.0 if loss == 0 else 100 - 100 / (1 + gain / loss)
    return out


def macd(closes: Sequence[float], f: int, s: int, sg: int):
    ef = ema(closes, f)
    es = ema(closes, s)
    line: Series = [
        (ef[i] - es[i]) if (ef[i] is not None and es[i] is not None) else None
        for i in range(len(closes))
    ]
    valid = [v for v in line if v is not None]
    sig_valid = ema(valid, sg)
    offset = len(line) - len(valid)
    signal: Series = [None] * len(line)
    for i, v in enumerate(sig_valid):
        signal[i + offset] = v
    hist: Series = [
        (line[i] - signal[i]) if (line[i] is not None and signal[i] is not None) else None
        for i in range(len(line))
    ]
    return {"line": line, "signal": signal, "hist": hist}


def bollinger(closes: Sequence[float], p: int, k: float):
    mid = sma(closes, p)
    upper: Series = [None] * len(closes)
    lower: Series = [None] * len(closes)
    for i in range(p - 1, len(closes)):
        if mid[i] is None:
            continue
        v = 0.0
        for j in range(i - p + 1, i + 1):
            v += (closes[j] - mid[i]) ** 2
        sd = math.sqrt(v / p)
        upper[i] = mid[i] + k * sd
        lower[i] = mid[i] - k * sd
    return {"mid": mid, "upper": upper, "lower": lower}


def atr(bars, p: int) -> Series:
    out: Series = [None] * len(bars)
    if len(bars) <= p:
        return out
    tr = []
    for i, b in enumerate(bars):
        if i == 0:
            tr.append(b.h - b.l)
        else:
            pc = bars[i - 1].c
            tr.append(max(b.h - b.l, abs(b.h - pc), abs(b.l - pc)))
    s = 0.0
    for i in range(1, p + 1):
        s += tr[i]
    prev = s / p
    out[p] = prev
    for i in range(p + 1, len(bars)):
        prev = (prev * (p - 1) + tr[i]) / p
        out[i] = prev
    return out


def highest_prev(arr: Sequence[float], p: int) -> Series:
    """当日を含まない直近N本の最高値（ブレイクアウト判定用）"""
    out: Series = [None] * len(arr)
    for i in range(p, len(arr)):
        m = -math.inf
        for j in range(i - p, i):
            if arr[j] > m:
                m = arr[j]
        out[i] = m
    return out


def lowest_prev(arr: Sequence[float], p: int) -> Series:
    out: Series = [None] * len(arr)
    for i in range(p, len(arr)):
        m = math.inf
        for j in range(i - p, i):
            if arr[j] < m:
                m = arr[j]
        out[i] = m
    return out
