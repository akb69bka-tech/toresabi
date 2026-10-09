@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 株式自動売買 自走エンジン
where python >nul 2>nul || (echo Python が見つかりません。https://www.python.org/downloads/ から 3.11 以上を入れて「Add python.exe to PATH」にチェックしてください & pause & exit /b 1)
if not exist .venv (echo 初回準備中... & python -m venv .venv)
call .venv\Scripts\activate.bat
pip install -q -r requirements.txt
if not exist config.yaml (python run.py init --demo)
echo.
echo ブラウザが開きます。閉じるときはこの黒い画面を閉じてください。
python run.py dashboard --open
pause
