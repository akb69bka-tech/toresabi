#!/bin/bash
# macOS / Linux 用。ターミナルで: bash 成長株発掘.sh
cd "$(dirname "$0")"
command -v python3 >/dev/null || { echo "Python 3.11 以上を入れてください"; exit 1; }
[ -d .venv ] || python3 -m venv .venv
source .venv/bin/activate
pip install -q -r requirements.txt
grep -Eq 'jquants_api_key: *"[^"]+"' config.yaml 2>/dev/null || { echo "J-Quants の API キーが未設定です。"; python run.py setkey; }
echo "全上場銘柄の株価と決算を取得して採点します。初回は1〜2時間かかることがあります。"
{ python run.py growth --top 50; python run.py growth --validate; } | tee 成長株発掘_結果.txt
echo "結果は 成長株発掘_結果.txt に保存しました"
