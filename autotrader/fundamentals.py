"""決算データ（売上・利益・会社予想・株数）の取得。成長株発掘(growth.py)が使う。

取得元は3つ。どれも「開示日」を持たせ、過去の時点で知り得た情報だけを使えるようにしてある
（後から出た決算で過去を採点する“未来の覗き見”を防ぐため）。

- jquants: J-Quants API V2 の /fins/summary（決算短信・業績予想の修正）。/equities/master で業種と市場区分も取る
- csv    : 手元の fundamentals.csv（列は下記 CSV_COLUMNS）。四季報や証券会社の画面から書き写してもよい
- demo   : 擬似データ（data.source: demo のとき。仕組みの確認用）
"""
from __future__ import annotations
import csv, json, os, time
from dataclasses import dataclass, asdict
from datetime import date, timedelta
from typing import Dict, List, Optional

import requests

from .data.base import _norm_date
from .data.jquants import JQuantsClient, client_from_cfg, pick


@dataclass
class Statement:
    disclosed: str                    # 開示日 YYYY-MM-DD（この日より前には使わない）
    kind: str = "results"             # results=決算 / revision=業績予想の修正
    period: str = ""                  # 1Q / 2Q / 3Q / FY（決算期の区分。累計値）
    fy_end: str = ""                  # 会計年度の末日
    sales: Optional[float] = None     # 売上高（期首からの累計）
    op: Optional[float] = None        # 営業利益（累計）
    profit: Optional[float] = None    # 純利益（累計）
    eps: Optional[float] = None
    f_sales: Optional[float] = None   # fy_end 期の会社予想（通期）
    f_op: Optional[float] = None
    f_eps: Optional[float] = None
    nf_sales: Optional[float] = None  # 翌期の会社予想（通期決算の短信に載る）
    nf_op: Optional[float] = None
    nf_eps: Optional[float] = None
    shares: Optional[float] = None    # 期末発行済株式数（自己株式を除く）


@dataclass
class CompanyInfo:
    code: str
    name: str = ""
    sector: str = ""                  # 東証33業種
    market: str = ""                  # プライム / スタンダード / グロース


CSV_COLUMNS = ["code", "disclosed", "kind", "period", "fy_end", "sales", "op", "profit", "eps",
               "f_sales", "f_op", "f_eps", "nf_sales", "nf_op", "nf_eps", "shares"]
_JP_HEADERS = {"コード": "code", "銘柄コード": "code", "開示日": "disclosed", "種別": "kind", "区分": "period",
               "決算期末": "fy_end", "売上高": "sales", "営業利益": "op", "純利益": "profit",
               "予想売上高": "f_sales", "予想営業利益": "f_op", "予想EPS": "f_eps",
               "翌期予想売上高": "nf_sales", "翌期予想営業利益": "nf_op", "翌期予想EPS": "nf_eps", "株式数": "shares"}
_NUM = {"sales", "op", "profit", "eps", "f_sales", "f_op", "f_eps", "nf_sales", "nf_op", "nf_eps", "shares"}


def _f(v) -> Optional[float]:
    if v is None:
        return None
    s = str(v).strip().replace(",", "")
    if s in ("", "-", "－", "—"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def add_years(d: str, n: int) -> str:
    y, m, dd = int(d[:4]) + n, int(d[5:7]), int(d[8:10])
    if m == 2 and dd == 29:
        dd = 28
    return f"{y:04d}-{m:02d}-{dd:02d}"


# ---------------- J-Quants ----------------
# V2 /fins/summary の短縮名と V1 /fins/statements の名前の両方を受け付ける
def parse_jquants_statement(q: dict) -> Optional[Statement]:
    """決算サマリーの1件を Statement にする。売上も予想も無い開示（配当予想の修正など）は捨てる"""
    disclosed = _norm_date(pick(q, "DiscDate", "DisclosedDate") or "")
    if not disclosed:
        return None
    doc = str(pick(q, "DocType", "TypeOfDocument") or "")
    s = Statement(
        disclosed=disclosed,
        kind="revision" if "EarnForecastRevision" in doc else "results",
        period=str(pick(q, "CurPerType", "TypeOfCurrentPeriod") or "").upper(),
        fy_end=_norm_date(pick(q, "CurFYEn", "CurrentFiscalYearEndDate") or "") or "",
        sales=_f(pick(q, "Sales", "NetSales")), op=_f(pick(q, "OP", "OperatingProfit")),
        profit=_f(pick(q, "NP", "Profit")), eps=_f(pick(q, "EPS", "EarningsPerShare")),
        f_sales=_f(pick(q, "FSales", "ForecastNetSales")), f_op=_f(pick(q, "FOP", "ForecastOperatingProfit")),
        f_eps=_f(pick(q, "FEPS", "ForecastEarningsPerShare")),
        nf_sales=_f(pick(q, "NxFSales", "NextYearForecastNetSales")),
        nf_op=_f(pick(q, "NxFOP", "NextYearForecastOperatingProfit")),
        nf_eps=_f(pick(q, "NxFEPS", "NextYearForecastEarningsPerShare")),
    )
    issued = _f(pick(q, "ShOutFY", "NumberOfIssuedAndOutstandingSharesAtTheEndOfFiscalYearIncludingTreasuryStock"))
    treasury = _f(pick(q, "TrShFY", "NumberOfTreasuryStockAtTheEndOfFiscalYear")) or 0.0
    if issued:
        s.shares = issued - treasury
    if s.kind == "revision":
        s.sales = s.op = s.profit = s.eps = None
    if s.kind == "results" and s.sales is None and s.op is None:
        return None
    if s.kind == "revision" and s.f_sales is None and s.f_op is None:
        return None
    if not s.fy_end:
        return None
    return s


class JQuantsFundamentals:
    """決算は週に一度だけ取り直す（決算発表は年4回なので毎日は要らない）"""
    name = "jquants"

    def __init__(self, client: JQuantsClient, cache_dir: str, max_age_days: int = 7):
        self.client = client
        self.dir = os.path.join(cache_dir, "fins")
        os.makedirs(self.dir, exist_ok=True)
        self.max_age_days = max_age_days

    def _cached(self, name: str):
        p = os.path.join(self.dir, name)
        if not os.path.exists(p):
            return None, None
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        fresh = date.fromisoformat(d["fetched"]) >= date.today() - timedelta(days=self.max_age_days)
        return d["data"], fresh

    def _save(self, name: str, data):
        with open(os.path.join(self.dir, name), "w", encoding="utf-8") as f:
            json.dump({"fetched": date.today().isoformat(), "data": data}, f, ensure_ascii=False)

    def statements(self, code: str) -> List[Statement]:
        name = f"{code}.json"
        data, fresh = self._cached(name)
        if data is None or not fresh:
            code5 = code if len(code) == 5 else code + "0"
            try:
                data = self.client.get("/fins/statements", "/fins/summary", {"code": code5}, "statements")
                self._save(name, data)
            except requests.RequestException:
                data = data or []          # 取得に失敗したら古いキャッシュで続ける
        out = [s for s in (parse_jquants_statement(q) for q in data) if s]
        return sorted(out, key=lambda s: s.disclosed)

    def companies(self) -> Dict[str, CompanyInfo]:
        data, fresh = self._cached("_listed.json")
        if data is None or not fresh:
            try:
                data = self.client.get("/listed/info", "/equities/master", {}, "info")
                self._save("_listed.json", data)
            except requests.RequestException:
                data = data or []
        out = {}
        for q in data:
            c = str(q.get("Code", ""))
            c4 = c[:4] if len(c) == 5 and c.endswith("0") else c
            out[c4] = CompanyInfo(c4, pick(q, "CoName", "CompanyName") or "",
                                  pick(q, "S33Nm", "Sector33CodeName") or "",
                                  pick(q, "MktNm", "MarketCodeName") or "")
        return out


# ---------------- CSV ----------------
class CsvFundamentals:
    name = "csv"

    def __init__(self, path: str):
        self.path = path
        self._by_code: Dict[str, List[Statement]] = {}
        if os.path.exists(path):
            self._load()

    def _load(self):
        for enc in ("utf-8-sig", "cp932"):
            try:
                with open(self.path, encoding=enc, newline="") as f:
                    rows = list(csv.reader(f))
                break
            except UnicodeDecodeError:
                continue
        else:
            return
        if not rows:
            return
        head = [_JP_HEADERS.get(h.strip(), h.strip().lower()) for h in rows[0]]
        for row in rows[1:]:
            rec = {head[i]: v for i, v in enumerate(row) if i < len(head)}
            code = (rec.get("code") or "").strip()
            disclosed = _norm_date(rec.get("disclosed", ""))
            fy_end = _norm_date(rec.get("fy_end", ""))
            if not code or not disclosed or not fy_end:
                continue
            kw = {k: _f(rec.get(k)) for k in _NUM}
            s = Statement(disclosed=disclosed, kind=(rec.get("kind") or "results").strip() or "results",
                          period=(rec.get("period") or "").strip().upper(), fy_end=fy_end, **kw)
            self._by_code.setdefault(code, []).append(s)
        for v in self._by_code.values():
            v.sort(key=lambda s: s.disclosed)

    def statements(self, code: str) -> List[Statement]:
        return self._by_code.get(code, [])

    def companies(self) -> Dict[str, CompanyInfo]:
        return {}


def write_csv(path: str, by_code: Dict[str, List[Statement]]):
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(CSV_COLUMNS)
        for code, sts in by_code.items():
            for s in sts:
                d = asdict(s)
                w.writerow([code] + ["" if d[k] is None else d[k] for k in CSV_COLUMNS[1:]])


class NoFundamentals:
    name = "none"

    def statements(self, code: str) -> List[Statement]:
        return []

    def companies(self) -> Dict[str, CompanyInfo]:
        return {}


def make_fundamentals(cfg: dict):
    """growth.fundamentals: auto / jquants / csv / none"""
    g, d = cfg.get("growth", {}), cfg["data"]
    kind = g.get("fundamentals", "auto")
    csv_path = g.get("fundamentals_csv", "fundamentals.csv")
    if kind == "auto":
        if d.get("jquants_api_key") or (d.get("source") == "jquants" and d.get("jquants_refresh_token")):
            kind = "jquants"
        elif os.path.exists(csv_path):
            kind = "csv"
        else:
            kind = "none"
    if kind == "jquants":
        return JQuantsFundamentals(client_from_cfg(d), d.get("cache_dir", "data_cache"))
    if kind == "csv":
        return CsvFundamentals(csv_path)
    return NoFundamentals()
