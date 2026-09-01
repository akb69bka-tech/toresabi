"""監視対象の銘柄リスト。

本来は JPX が公開する「東証上場銘柄一覧」を CSV(code,name) に変換して指定する。
無い場合は、主要銘柄のサンプルで動かす。
"""
from __future__ import annotations
import csv, os
from typing import List

from ..simulator import Symbol

SAMPLE_UNIVERSE = [
    ("7203", "トヨタ自動車"), ("6758", "ソニーグループ"), ("9432", "日本電信電話"), ("8306", "三菱UFJ"),
    ("4063", "信越化学工業"), ("6861", "キーエンス"), ("9984", "ソフトバンクグループ"), ("8035", "東京エレクトロン"),
    ("6501", "日立製作所"), ("7974", "任天堂"), ("6098", "リクルートHD"), ("4502", "武田薬品工業"),
    ("8031", "三井物産"), ("8058", "三菱商事"), ("4568", "第一三共"), ("6902", "デンソー"),
    ("7741", "HOYA"), ("6367", "ダイキン工業"), ("6954", "ファナック"), ("9433", "KDDI"),
    ("4503", "アステラス製薬"), ("3382", "セブン&アイHD"), ("8766", "東京海上HD"), ("8316", "三井住友FG"),
    ("8411", "みずほFG"), ("6702", "富士通"), ("6752", "パナソニックHD"), ("7267", "ホンダ"),
    ("7751", "キヤノン"), ("2914", "日本たばこ産業"), ("4452", "花王"), ("6273", "SMC"),
    ("6857", "アドバンテスト"), ("5108", "ブリヂストン"), ("4519", "中外製薬"), ("6594", "ニデック"),
    ("9022", "JR東海"), ("9020", "JR東日本"), ("8801", "三井不動産"), ("8802", "三菱地所"),
    ("1925", "大和ハウス工業"), ("1928", "積水ハウス"), ("4901", "富士フイルムHD"), ("6971", "京セラ"),
    ("7011", "三菱重工業"), ("5401", "日本製鉄"), ("3407", "旭化成"), ("4188", "三菱ケミカルG"),
    ("6326", "クボタ"), ("7269", "スズキ"), ("8001", "伊藤忠商事"), ("8002", "丸紅"),
    ("2802", "味の素"), ("2502", "アサヒGHD"), ("9101", "日本郵船"), ("9104", "商船三井"),
    ("9201", "日本航空"), ("9202", "ANAHD"), ("6178", "日本郵政"), ("4661", "オリエンタルランド"),
    ("8591", "オリックス"), ("8604", "野村HD"), ("6146", "ディスコ"), ("6920", "レーザーテック"),
    ("6723", "ルネサス"), ("6963", "ローム"), ("2503", "キリンHD"), ("4755", "楽天グループ"),
]


def load_universe(path: str, max_symbols: int = 0, default_unit: int = 100) -> List[Symbol]:
    syms: List[Symbol] = []
    if path and os.path.exists(path):
        for enc in ("utf-8-sig", "cp932"):
            try:
                with open(path, encoding=enc, newline="") as f:
                    for row in csv.reader(f):
                        if not row or not row[0].strip():
                            continue
                        code = row[0].strip()
                        if not code[:1].isdigit():
                            continue          # ヘッダ行
                        name = row[1].strip() if len(row) > 1 else code
                        unit = int(row[2]) if len(row) > 2 and row[2].strip().isdigit() else default_unit
                        syms.append(Symbol(code=code, name=name, unit=unit))
                break
            except UnicodeDecodeError:
                syms = []
                continue
    if not syms:
        syms = [Symbol(code=c, name=n, unit=default_unit) for c, n in SAMPLE_UNIVERSE]
    if max_symbols > 0:
        syms = syms[:max_symbols]
    return syms
