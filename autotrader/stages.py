"""運用段階の判定と「次にやること」の案内。

demo → paper → live-dryrun → live の順に進む。
前の段階の実績が無ければ次へ進めないようにして、画面上で案内する。
段階の切り替えは config.yaml を書き換えて行う（live だけは手で書く必要がある）。
"""
from __future__ import annotations
import os, re
from typing import Any, Dict, Optional

from .config import LIVE_CONFIRM_PHRASE

ORDER = ["demo", "paper", "live-dryrun", "live"]
LABELS = {"demo": "デモ（擬似相場で慣れる）", "paper": "ペーパー（実データ・仮想のお金）",
          "live-dryrun": "ライブ試運転（証券会社に接続・発注はしない）", "live": "ライブ（実際に発注）"}


def describe(cfg: Dict[str, Any], acc: Dict[str, Any], state_dir: str) -> Dict[str, Any]:
    mode = cfg.get("mode", "paper")
    paper_days = int(acc.get("paperDays") or 0)
    need_paper = int(cfg.get("live", {}).get("min_paper_days", 20))
    trades = len(acc.get("trades") or [])
    cmp_done = os.path.exists(os.path.join(state_dir, "compare.json"))
    demo_done = os.path.exists(os.path.join(state_dir, "demo.json")) or cmp_done
    kabu = cfg.get("broker", {}).get("kabu", {})
    broker_ok = cfg.get("broker", {}).get("type") == "kabu" and kabu.get("api_password") and kabu.get("order_password")
    unit_ok = int(cfg.get("risk", {}).get("unit", 100)) >= 100

    steps = []
    if mode == "demo":
        steps.append({"done": demo_done, "text": "「デモ再生」を最後まで動かして、資産がどう増減するか見る"})
        steps.append({"done": cmp_done, "text": "「戦略比較」を実行して、買い持ちより良い戦略があるか確かめる"})
        steps.append({"done": False, "text": "納得したら下のボタンで「ペーパー運用」へ進む（実際の株価・仮想のお金）",
                      "action": "advance", "label": "ペーパー運用へ進む", "enabled": demo_done})
        nxt = "paper"
    elif mode == "paper":
        steps.append({"done": paper_days > 0, "text": "毎日 18:00 以降に自動で判定される（「自動運転」をオンにしておく）"})
        steps.append({"done": paper_days >= need_paper, "text": f"ペーパー運用を {need_paper} 営業日つづける（いま {paper_days} 日）"})
        steps.append({"done": trades > 0, "text": "決済が何回か発生し、損益の出方に納得できる"})
        steps.append({"done": False,
                      "text": "進むには、kabuステーションを入れて config.yaml の broker に API パスワードを書く（ここだけは手作業）",
                      "action": None})
        steps.append({"done": False, "text": "書けたら下のボタンで「ライブ試運転」へ進む（発注はしない）",
                      "action": "advance", "label": "ライブ試運転へ進む",
                      "enabled": paper_days >= need_paper and bool(broker_ok) and unit_ok})
        if not unit_ok:
            steps.append({"done": False, "text": "実発注は100株単位のみ。config.yaml の risk.unit を 100 にする"})
        nxt = "live-dryrun"
    elif mode == "live-dryrun":
        steps.append({"done": paper_days >= need_paper, "text": f"ペーパー実績 {paper_days}/{need_paper} 日"})
        steps.append({"done": False, "text": "ログの［試運転］の注文内容を数日分、自分の目で確かめる"})
        steps.append({"done": False, "text": f"実発注に進むには config.yaml の live.enabled を true、live.confirm_phrase に「{LIVE_CONFIRM_PHRASE}」と書く（ボタンでは進めません）"})
        steps.append({"done": False, "text": "最初は guard.max_order_value_yen を小さく（例: 50000）して少額で始める"})
        nxt = "live"
    else:
        steps.append({"done": True, "text": "実発注で運用中。毎日ログと通知を確認する"})
        steps.append({"done": False, "text": "不安になったら「緊急停止」。保有は損切り条件で自動的に手仕舞われる"})
        nxt = None
    return {"mode": mode, "label": LABELS.get(mode, mode), "next": nxt, "steps": steps,
            "paperDays": paper_days, "needPaperDays": need_paper}


def _set_yaml_key(text: str, path: list, value: str) -> str:
    """config.yaml の単純なキーを書き換える（無ければ追記）。ネストは2段まで"""
    if len(path) == 1:
        pat = re.compile(rf"^{re.escape(path[0])}:\s*.*$", re.M)
        line = f"{path[0]}: {value}"
        return pat.sub(line, text, count=1) if pat.search(text) else text.rstrip("\n") + f"\n{line}\n"
    parent, key = path
    m = re.search(rf"^{re.escape(parent)}:\s*(\{{[^\n]*\}})?\s*$", text, re.M)
    if m and m.group(1):          # inline 形式 data: {source: demo, ...}
        inner = m.group(1)
        if re.search(rf"\b{re.escape(key)}\s*:", inner):
            inner2 = re.sub(rf"\b{re.escape(key)}\s*:\s*[^,}}]+", f"{key}: {value}", inner, count=1)
        else:
            inner2 = inner[:-1].rstrip() + f", {key}: {value}}}"
        return text[:m.start(1)] + inner2 + text[m.end(1):]
    if m:
        block = re.compile(rf"^{re.escape(parent)}:\s*\n((?:[ \t]+.*\n?)*)", re.M)
        bm = block.search(text)
        body = bm.group(1)
        kp = re.compile(rf"^([ \t]+){re.escape(key)}:\s*.*$", re.M)
        if kp.search(body):
            body2 = kp.sub(lambda mm: f"{mm.group(1)}{key}: {value}", body, count=1)
        else:
            body2 = f"  {key}: {value}\n" + body
        return text[:bm.start(1)] + body2 + text[bm.end(1):]
    return text.rstrip("\n") + f"\n{parent}:\n  {key}: {value}\n"


def advance(cfg: Dict[str, Any], config_path: str, acc: Dict[str, Any], state_dir: str) -> Dict[str, Any]:
    """次の段階へ config.yaml を書き換える。live へはボタンでは進めない"""
    d = describe(cfg, acc, state_dir)
    step = next((s for s in d["steps"] if s.get("action") == "advance"), None)
    if not step or not step.get("enabled"):
        return {"ok": False, "error": "まだ進める条件がそろっていません"}
    nxt = d["next"]
    if nxt == "live":
        return {"ok": False, "error": "実発注への切り替えは config.yaml を手で書く必要があります"}
    text = open(config_path, encoding="utf-8").read() if os.path.exists(config_path) else ""
    text = _set_yaml_key(text, ["mode"], nxt)
    if nxt == "paper":
        text = _set_yaml_key(text, ["data", "source"], "stooq")
    open(config_path, "w", encoding="utf-8").write(text)
    return {"ok": True, "mode": nxt}
