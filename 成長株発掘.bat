@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 成長株発掘
where python >nul 2>nul || (echo Python が見つかりません。https://www.python.org/downloads/ から 3.11 以上を入れて「Add python.exe to PATH」にチェックしてください & pause & exit /b 1)
if not exist .venv (echo 初回準備中... & python -m venv .venv)
call .venv\Scripts\activate.bat
pip install -q -r requirements.txt
findstr /r /c:"jquants_api_key: *\"..*\"" config.yaml >nul 2>nul || (echo J-Quants の API キーが未設定です。& python run.py setkey)
echo.
echo 全上場銘柄の株価と決算を取得して採点します。初回は1〜2時間かかることがあります。
python run.py growth --top 50 > 成長株発掘_結果.txt
python run.py growth --validate >> 成長株発掘_結果.txt
type 成長株発掘_結果.txt
echo.
echo 結果は「成長株発掘_結果.txt」に保存しました。画面で見るときは 起動.bat の「成長株発掘」タブへ。
pause
