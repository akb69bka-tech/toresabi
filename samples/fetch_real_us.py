#!/usr/bin/env python3
"""実データのサンプルを取得する（オラクル・ヤフー・NVIDIA の 1995〜2014 年日足）。

出典: backtrader プロジェクトが同梱している Yahoo Finance 由来の公開データ
      https://github.com/mementum/backtrader/tree/master/datas
用途: 実データで自走エンジンの一式（スクリーニング・比較・再学習・デモ再生）を
      動かして確かめる。データは再配布せず、実行時に取得する。

使い方:
    python samples/fetch_real_us.py            # samples/real_us/ に展開
    cd samples/real_us && python ../../run.py compare --days 500
"""
import os, sys, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "real_us")
BASE = "https://raw.githubusercontent.com/mementum/backtrader/master/datas/"
FILES = {"ORCL": ("orcl-1995-2014.txt", "オラクル"), "YHOO": ("yhoo-1996-2014.txt", "ヤフー"),
         "NVDA": ("nvda-1999-2014.txt", "NVIDIA")}

CONFIG = """# 実データ（米国株3銘柄・1995〜2014）で自走エンジンを試す設定
mode: demo
data: {source: csv, csv_dir: data_csv, history_days: 0}
universe: {file: universe.csv}
screener: {enabled: false}      # 3銘柄しか無いので全銘柄を対象にする
"""


def main():
    os.makedirs(os.path.join(OUT, "data_csv"), exist_ok=True)
    for code, (fname, name) in FILES.items():
        dst = os.path.join(OUT, "data_csv", f"{code}.csv")
        if os.path.exists(dst):
            print(f"{code}: 取得済み"); continue
        print(f"{code}: 取得中 {BASE + fname}")
        urllib.request.urlretrieve(BASE + fname, dst)
    with open(os.path.join(OUT, "universe.csv"), "w", encoding="utf-8") as f:
        f.write("code,name\n" + "".join(f"{c},{n}\n" for c, (_, n) in FILES.items()))
    with open(os.path.join(OUT, "config.yaml"), "w", encoding="utf-8") as f:
        f.write(CONFIG)
    print(f"完了: {OUT}\n次: cd {os.path.relpath(OUT)} && python ../../run.py compare --days 500")


if __name__ == "__main__":
    sys.exit(main())
