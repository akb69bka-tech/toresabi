#!/usr/bin/env python3
"""株式自動売買 自走エンジン コマンド

  python run.py init                    設定ファイルと銘柄リストの雛形を作る
  python run.py status                  現在の状態
  python run.py fetch                   株価を取得してキャッシュする
  python run.py screen                  全銘柄スクリーニング
  python run.py demo [--days 250]       過去データで運用を再生（成績の要約）
  python run.py backtest [--days 500]   バックテスト（買い持ち比較・堅実さ評価）
  python run.py learn                   再学習（ウォークフォワード検証つき）
  python run.py cycle                   夕方の判定（約定反映→判定→曜日により再学習）
  python run.py orders                  翌朝の発注（live / live-dryrun）
  python run.py loop                    毎日 schedule の時刻に cycle と orders を自動実行
  python run.py dashboard               状況確認画面を起動
  python run.py stop / resume           緊急停止 / 解除
"""
from __future__ import annotations
import argparse, os, sys, time
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from autotrader.config import load_config, LIVE_CONFIRM_PHRASE
from autotrader.runner import Runner, evening_job, morning_job
from autotrader import dashboard

CONFIG_TEMPLATE = """# 株式自動売買 自走エンジン 設定
# キー名はブラウザ版の設定と同じです。ブラウザで検証した値をここへ書き写してください。

mode: paper            # demo / paper / live-dryrun / live  ← この順に進めてください

data:
  source: stooq        # stooq(無料) / jquants(要トークン) / csv(手元のCSV)
  history_days: 600

universe:
  file: universe.csv   # 監視する銘柄リスト。無ければ主要銘柄のサンプルを使います

screener:
  top_n: 20
  min_turnover_yen: 100000000

risk:
  initialCash: 500000
  unit: 1              # 実発注(kabu)では 100 にする必要があります
  allowMinUnit: true
  allocPct: 18
  maxPositions: 5
  feePct: 0.55
  slipPct: 0.1
  useAtrStop: true
  atrMult: 3.5
  stopPct: 10
  takePct: 0
  minHoldDays: 5
  cooldownDays: 10
  maxDailyTrades: 2
  haltDrawdownPct: 15
  maxConsecLosses: 6
  reserveCashPct: 10

broker:
  type: paper          # paper / kabu
  kabu:
    base_url: http://localhost:18081/kabusapi   # 18081=検証環境, 18080=本番
    api_password: ""
    order_password: ""
    account_type: 4    # 2=一般 4=特定 12=NISA

guard:
  max_order_value_yen: 150000
  max_daily_orders: 4
  send_window: ["08:00", "08:55"]

live:
  enabled: false
  confirm_phrase: ""   # 実発注するには「%s」と正確に書く
  min_paper_days: 20

schedule:
  signal_time: "18:00"
  order_time: "08:30"
""" % LIVE_CONFIRM_PHRASE


def cmd_init(args):
    if not os.path.exists("config.yaml"):
        with open("config.yaml", "w", encoding="utf-8") as f:
            f.write(CONFIG_TEMPLATE)
        print("config.yaml を作成しました")
    if not os.path.exists("universe.csv"):
        from autotrader.data.universe import SAMPLE_UNIVERSE
        with open("universe.csv", "w", encoding="utf-8") as f:
            f.write("code,name\n")
            for c, n in SAMPLE_UNIVERSE:
                f.write(f"{c},{n}\n")
        print("universe.csv（主要銘柄のサンプル）を作成しました。JPX の上場銘柄一覧で置き換えられます")
    os.makedirs("state", exist_ok=True)
    print("準備完了。次は: python run.py demo")


def cmd_status(args, cfg):
    r = Runner(cfg)
    s = r.status()
    print(f"モード        : {s['mode']}")
    eq = f"{s['equity']:,.0f}円" if s['equity'] is not None else "—"
    print(f"現金          : {s['cash']:,.0f}円   総資産(前回判定時): {eq}")
    print(f"保有          : {len(s['positions'])}銘柄 {', '.join(s['positions']) or ''}")
    print(f"翌朝の注文    : {len(s['pending'])}件")
    print(f"最終判定日    : {s['lastCycleDate'] or '—'}   ペーパー実績: {s['paperDays']}日")
    print(f"候補銘柄      : {len(s['watchlist'])}銘柄")
    print(f"戦略パラメータ: {s['strategyParams']}")
    if s["halted"]:
        print(f"⛔ 安全装置作動中: {s['haltReason']}")
    if s["stopFile"]:
        print(f"⛔ 緊急停止ファイル: {s['stopFile']}")


def cmd_demo(args, cfg):
    r = Runner(cfg)
    res = r.demo(days=args.days)
    if not res.get("ok"):
        print("再生できませんでした"); return
    print(f"\n【デモ再生】{res['from']} 〜 {res['to']}  {res['symbols']}銘柄")
    print(f"  最終資産   : {res['finalEquity']:,.0f}円 （{res['totalRet']:+.2f}%）")
    print(f"  買い持ち   : {res['buyHoldRet']:+.2f}%  → 自動売買との差 {res['totalRet']-res['buyHoldRet']:+.2f}pt")
    print(f"  最大下落   : {res['maxDD']:.1f}%  （買い持ち {res['buyHoldDD']:.1f}%）")
    print(f"  取引       : {res['trades']}回  勝率 {res['winRate']:.0f}%  勝ち月率 {res['winMonthRate']:.0f}%  最長連敗 {res['maxLoseStreak']}")
    print(f"  売買コスト : {res['costs']:,.0f}円")
    if res["halted"]:
        print(f"  ⛔ {res['haltReason']}")
    if res["totalRet"] < res["buyHoldRet"]:
        print("  ⚠️ この期間は買って持ち続ける方が有利でした。設定の見直しか、実データでの再検証を。")


def cmd_backtest(args, cfg):
    args.days = args.days or 500
    cmd_demo(args, cfg)


def cmd_learn(args, cfg):
    r = Runner(cfg)
    res = r.learn()
    print(f"\n【再学習】{'採用' if res['adopt'] else '現状維持'}: {res['reason']}")
    wf = res.get("wf")
    if wf:
        for row in wf["rows"]:
            print(f"  fold{row['fold']}: 学習 {row['trainRet']:+.2f}% → 検証 {row['testRet']:+.2f}% "
                  f"(現行 {row['currentTestRet']:+.2f}%, 買い持ち {row['testBH']:+.2f}%) {row['params']}")


def cmd_screen(args, cfg):
    r = Runner(cfg)
    syms = r.load_history()
    wl = r.refresh_watchlist(syms, r.latest_date(syms), force=True)
    from autotrader.state import load_json
    rows = (load_json(r.state_dir, "screener.json") or {}).get("rows", [])
    print(f"\n候補 {len(wl)}銘柄:")
    for row in rows:
        if row.get("ok"):
            print(f"  {row['rank']:2d} {row['code']} {row['name']:<12s} {row['price']:>9,.1f}円  "
                  f"6か月 {row['mom126']:+6.1f}%  安定 {row['trendQ']:3.0f}%  ATR {row['atrPct']:.1f}%")
    from autotrader.state import save_account
    save_account(r.state_dir, r.acc)


def cmd_loop(args, cfg):
    sched = cfg["schedule"]
    print(f"自動実行を開始（判定 {sched['signal_time']} / 発注 {sched['order_time']}、Ctrl+C で終了）")
    done = set()
    while True:
        now = datetime.now()
        key_s = (now.date(), "s"); key_o = (now.date(), "o")
        hm = now.strftime("%H:%M")
        try:
            if hm >= sched["signal_time"] and key_s not in done:
                done.add(key_s); evening_job(cfg)
            if hm >= sched["order_time"] and hm < sched["signal_time"] and key_o not in done:
                done.add(key_o); morning_job(cfg)
        except Exception as e:  # 1回の失敗でループを止めない
            print(f"エラー: {e}")
        time.sleep(30)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["init", "status", "fetch", "screen", "demo", "backtest", "learn",
                                    "cycle", "orders", "loop", "dashboard", "stop", "resume"])
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--days", type=int, default=250)
    args = ap.parse_args()
    if args.cmd == "init":
        return cmd_init(args)
    cfg = load_config(args.config)
    if args.cmd == "status":   return cmd_status(args, cfg)
    if args.cmd == "fetch":    Runner(cfg).load_history(); return
    if args.cmd == "screen":   return cmd_screen(args, cfg)
    if args.cmd == "demo":     return cmd_demo(args, cfg)
    if args.cmd == "backtest": return cmd_backtest(args, cfg)
    if args.cmd == "learn":    return cmd_learn(args, cfg)
    if args.cmd == "cycle":    evening_job(cfg); return
    if args.cmd == "orders":   print(morning_job(cfg)); return
    if args.cmd == "loop":     return cmd_loop(args, cfg)
    if args.cmd == "dashboard":
        from autotrader.guard import Guard
        return dashboard.serve(cfg, Guard(cfg, cfg.get("state_dir", "state")))
    if args.cmd == "stop":
        open(cfg["guard"].get("stop_file", "STOP"), "w").close(); print("緊急停止ファイルを作成しました。発注は行われません"); return
    if args.cmd == "resume":
        p = cfg["guard"].get("stop_file", "STOP")
        if os.path.exists(p): os.remove(p)
        print("緊急停止を解除しました")


if __name__ == "__main__":
    main()
