#!/bin/bash
# macOS / Linux 用。ターミナルで: bash 起動.sh
cd "$(dirname "$0")"
command -v python3 >/dev/null || { echo "Python 3.11 以上を入れてください"; exit 1; }
[ -d .venv ] || python3 -m venv .venv
source .venv/bin/activate
pip install -q -r requirements.txt
[ -f config.yaml ] || python run.py init --demo
echo "ブラウザが開きます。止めるときは Ctrl+C"
python run.py dashboard --open
