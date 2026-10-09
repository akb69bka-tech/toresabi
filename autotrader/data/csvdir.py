"""ローカルの CSV ディレクトリ（{csv_dir}/{code}.csv）から読む。証券会社からダウンロードした日足向け"""
from __future__ import annotations
import os
from typing import List

from ..simulator import Bar
from .base import parse_csv_bars


class CsvDirSource:
    name = "csv"

    def __init__(self, csv_dir: str):
        self.dir = csv_dir

    def fetch(self, code: str, days: int) -> List[Bar]:
        p = os.path.join(self.dir, f"{code}.csv")
        if not os.path.exists(p):
            return []
        for enc in ("utf-8-sig", "cp932", "utf-8"):
            try:
                with open(p, encoding=enc) as f:
                    bars = parse_csv_bars(f.read())
                return bars[-days:] if days > 0 else bars
            except UnicodeDecodeError:
                continue
        return []
