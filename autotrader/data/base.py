"""株価データ取得の共通部分。"""
from __future__ import annotations
import json, os, re
from datetime import date, datetime
from typing import Dict, List, Optional, Protocol

from ..simulator import Bar


class DataSource(Protocol):
    name: str

    def fetch(self, code: str, days: int) -> List[Bar]:
        """日足を古い順で返す。取得できなければ空リスト"""
        ...


def _num(v) -> float:
    if v is None:
        return float("nan")
    s = str(v).strip().replace(",", "").replace("¥", "").replace("円", "").strip('"')
    try:
        return float(s)
    except ValueError:
        return float("nan")


def _norm_date(s: str) -> Optional[str]:
    t = str(s).strip().strip('"\'')
    m = re.match(r"^(\d{4})[-/年.](\d{1,2})[-/月.](\d{1,2})", t)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    for fmt in ("%Y%m%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(t, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return None


def _split(line: str, delim: str) -> List[str]:
    out, cur, q = [], "", False
    i = 0
    while i < len(line):
        ch = line[i]
        if q:
            if ch == '"':
                if i + 1 < len(line) and line[i + 1] == '"':
                    cur += '"'; i += 1
                else:
                    q = False
            else:
                cur += ch
        else:
            if ch == '"':
                q = True
            elif ch == delim:
                out.append(cur); cur = ""
            else:
                cur += ch
        i += 1
    out.append(cur)
    return [c.strip() for c in out]


def parse_csv_bars(text: str) -> List[Bar]:
    """ブラウザ版と同じ規則で CSV を日足に変換する（日本語/英語ヘッダ、ヘッダ無し、タブ区切り対応）"""
    lines = [l for l in text.splitlines() if l.strip()]
    if not lines:
        return []
    delim = "\t" if lines[0].count("\t") > lines[0].count(",") else ","
    cells = [_split(l, delim) for l in lines]
    col = {"d": 0, "o": 1, "h": 2, "l": 3, "c": 4, "v": 5}
    start = 0
    first = [c.lower() for c in cells[0]]
    if len(cells[0]) > 1 and _num(cells[0][1]) != _num(cells[0][1]):  # NaN → ヘッダ行
        def pick(keys, default):
            for i, h in enumerate(first):
                if any(k in h for k in keys):
                    return i
            return default
        col = {"d": pick(["date", "日付", "年月日"], 0), "o": pick(["open", "始値", "始"], 1),
               "h": pick(["high", "高値", "高"], 2), "l": pick(["low", "安値", "安"], 3),
               "c": pick(["close", "終値", "終"], 4), "v": pick(["volume", "出来高", "売買高"], 5)}
        start = 1
    by_date: Dict[str, Bar] = {}
    for row in cells[start:]:
        if len(row) <= col["c"]:
            continue
        d = _norm_date(row[col["d"]])
        if not d:
            continue
        c = _num(row[col["c"]])
        if not (c == c) or c <= 0:
            continue
        o = _num(row[col["o"]]) if len(row) > col["o"] else float("nan")
        h = _num(row[col["h"]]) if len(row) > col["h"] else float("nan")
        l = _num(row[col["l"]]) if len(row) > col["l"] else float("nan")
        v = _num(row[col["v"]]) if len(row) > col["v"] else float("nan")
        o = o if (o == o and o > 0) else c
        h = h if (h == h and h > 0) else max(o, c)
        l = l if (l == l and l > 0) else min(o, c)
        v = v if v == v else 0.0
        by_date[d] = Bar(d, o, h, l, c, v)
    return [by_date[k] for k in sorted(by_date)]


class JsonCache:
    """銘柄ごとの日足キャッシュ。同じ日に二度取りに行かない"""

    def __init__(self, cache_dir: str):
        self.dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)

    def path(self, code: str) -> str:
        return os.path.join(self.dir, f"{code}.json")

    def load(self, code: str):
        p = self.path(code)
        if not os.path.exists(p):
            return None
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        return d

    def fresh_today(self, code: str) -> Optional[List[Bar]]:
        d = self.load(code)
        if d and d.get("fetched") == date.today().isoformat():
            return [Bar(**b) for b in d["bars"]]
        return None

    def save(self, code: str, bars: List[Bar]):
        with open(self.path(code), "w", encoding="utf-8") as f:
            json.dump({"fetched": date.today().isoformat(), "bars": [b.__dict__ for b in bars]}, f)
