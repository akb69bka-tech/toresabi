"""状況確認用の簡易ダッシュボード（標準ライブラリのみ）。"""
from __future__ import annotations
import json
from http.server import BaseHTTPRequestHandler, HTTPServer

from .state import load_json, read_log

PAGE = """<!DOCTYPE html><html lang="ja"><head><meta charset="utf-8"><title>自走エンジン 状況</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
body{margin:0;font-family:-apple-system,"Segoe UI","Hiragino Kaku Gothic ProN","Noto Sans JP",sans-serif;background:#0b1220;color:#f1f5f9}
.c{max-width:1100px;margin:0 auto;padding:1.2rem 1rem}
h1{font-size:1.3rem;margin:.2rem 0 1rem}.card{background:rgba(22,34,54,.8);border:1px solid rgba(255,255,255,.1);border-radius:14px;padding:1rem;margin-bottom:1rem}
h2{font-size:.95rem;margin:0 0 .7rem}.g{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:.6rem}
.s{background:rgba(255,255,255,.04);border-radius:10px;padding:.6rem .8rem}.k{font-size:.7rem;color:#8ea3bf}.v{font-size:1.15rem;font-weight:700}
table{width:100%;border-collapse:collapse;font-size:.8rem}th{text-align:left;color:#8ea3bf;font-weight:600;font-size:.72rem;padding:.4rem;border-bottom:1px solid rgba(255,255,255,.1)}
td{padding:.4rem;border-bottom:1px solid rgba(255,255,255,.05)}.n{text-align:right}.pos{color:#22c55e}.neg{color:#fb7185}
.b{display:inline-block;padding:.1rem .5rem;border-radius:6px;font-size:.7rem;font-weight:700}.buy{background:rgba(34,197,94,.2);color:#4ade80}.sell{background:rgba(251,113,133,.2);color:#fb7185}.hold{background:rgba(142,163,191,.15);color:#8ea3bf}
.mode{padding:.5rem .9rem;border-radius:10px;font-weight:700;display:inline-block}.mode.live{background:#fb7185;color:#2a0a10}.mode.paper{background:#38bdf8;color:#04202e}.mode.demo{background:#a78bfa;color:#1e1240}.mode.dry{background:#f59e0b;color:#2a1a00}
.log{font-family:ui-monospace,Menlo,monospace;font-size:.74rem;max-height:300px;overflow:auto;line-height:1.6}.warn{color:#fcd34d}.error{color:#fb7185}
.halt{background:rgba(251,113,133,.12);border:1px solid rgba(251,113,133,.4);border-radius:10px;padding:.7rem;margin-bottom:.8rem}
</style></head><body><div class="c">
<h1>📈 株式自動売買 自走エンジン <span id="mode" class="mode"></span> <span id="upd" style="font-size:.75rem;color:#8ea3bf"></span></h1>
<div id="halt"></div>
<div class="card"><h2>口座</h2><div class="g" id="stats"></div></div>
<div class="card"><h2>翌朝の注文</h2><table id="orders"></table></div>
<div class="card"><h2>保有ポジション</h2><table id="pos"></table></div>
<div class="card"><h2>本日のシグナル</h2><table id="sig"></table></div>
<div class="card"><h2>スクリーニング上位</h2><table id="scr"></table></div>
<div class="card"><h2>ログ</h2><div class="log" id="log"></div></div>
</div><script>
const y=n=>n==null?'—':Math.round(n).toLocaleString('ja-JP')+'円';
const st=(k,v,c)=>`<div class="s"><div class="k">${k}</div><div class="v ${c||''}">${v}</div></div>`;
async function load(){
  const d=await (await fetch('/state.json')).json();
  const m=d.mode||'paper'; const el=document.getElementById('mode');
  el.textContent={demo:'デモ',paper:'ペーパー','live-dryrun':'ライブ試運転',live:'ライブ（実発注）'}[m]||m;
  el.className='mode '+(m==='live'?'live':m==='live-dryrun'?'dry':m);
  document.getElementById('upd').textContent='更新 '+new Date().toLocaleTimeString('ja-JP');
  const a=d.account||{}, s=d.signals||{};
  const eq=s.equity!=null?s.equity:a.cash; const pnl=eq-(a.initialCash||0);
  document.getElementById('halt').innerHTML=a.halted?`<div class="halt">⛔ 安全装置が作動中：${a.haltReason}</div>`:(d.stopFile?`<div class="halt">⛔ 緊急停止ファイルがあるため発注しません：${d.stopFile}</div>`:'');
  document.getElementById('stats').innerHTML=st('総資産',y(eq),pnl>=0?'pos':'neg')+st('損益',y(pnl),pnl>=0?'pos':'neg')+st('現金',y(a.cash))+st('保有',Object.keys(a.positions||{}).length+'銘柄')+st('決済回数',(a.trades||[]).length+'回')+st('最終判定',a.lastCycleDate||'—')+st('ペーパー実績',(a.paperDays||0)+'日')+st('候補銘柄',(a.watchlist||[]).length+'銘柄');
  const o=s.orders||[]; document.getElementById('orders').innerHTML=o.length?'<tr><th>銘柄</th><th>売買</th><th class="n">数量</th><th class="n">参考価格</th><th class="n">概算</th><th>理由</th><th>注文ID</th></tr>'+o.map(x=>`<tr><td>${x.code} ${x.name||''}</td><td><span class="b ${x.side}">${x.side==='buy'?'買い':'売り'}</span></td><td class="n">${x.qty}</td><td class="n">${x.estPrice}</td><td class="n">${y(x.estPrice*x.qty)}</td><td>${x.reason||''}</td><td>${x.orderId||''}</td></tr>`).join(''):'<tr><td style="color:#8ea3bf">注文はありません</td></tr>';
  const p=a.positions||{}; const pk=Object.keys(p); const px={}; (s.signals||[]).forEach(x=>px[x.code]=x.price);
  document.getElementById('pos').innerHTML=pk.length?'<tr><th>銘柄</th><th class="n">数量</th><th class="n">取得</th><th class="n">現在</th><th class="n">評価損益</th><th class="n">損切</th><th>取得日</th></tr>'+pk.map(c=>{const q=p[c];const cur=px[c]??q.avg;const g=(cur-q.avg)*q.qty;return `<tr><td>${c}</td><td class="n">${q.qty}</td><td class="n">${q.avg.toFixed(1)}</td><td class="n">${cur}</td><td class="n ${g>=0?'pos':'neg'}">${y(g)}</td><td class="n">${q.stop?q.stop.toFixed(1):'—'}</td><td>${q.entryDate}</td></tr>`}).join(''):'<tr><td style="color:#8ea3bf">保有はありません</td></tr>';
  const sg=s.signals||[]; document.getElementById('sig').innerHTML=sg.length?'<tr><th>銘柄</th><th>判定</th><th class="n">スコア</th><th class="n">終値</th><th>根拠</th></tr>'+sg.slice(0,30).map(x=>`<tr><td>${x.code} ${x.name||''}${x.held?' <span class="b hold">保有</span>':''}</td><td><span class="b ${x.action}">${{buy:'買い',sell:'売り',hold:'見送り'}[x.action]}</span></td><td class="n">${x.score.toFixed(1)}</td><td class="n">${x.price}</td><td style="color:#8ea3bf">${(x.votes||[]).join(' / ')}${x.blocked?' <span class="warn">'+x.blocked+'</span>':''}</td></tr>`).join(''):'<tr><td style="color:#8ea3bf">判定はまだありません</td></tr>';
  const sc=(d.screener||{}).rows||[]; document.getElementById('scr').innerHTML=sc.length?'<tr><th>順位</th><th>銘柄</th><th class="n">終値</th><th class="n">6か月</th><th class="n">安定性</th><th class="n">ATR%</th><th>判定</th></tr>'+sc.slice(0,25).map(r=>`<tr><td>${r.rank||'—'}</td><td>${r.code} ${r.name||''}</td><td class="n">${r.price??'—'}</td><td class="n ${(r.mom126||0)>=0?'pos':'neg'}">${r.mom126!=null?r.mom126.toFixed(1)+'%':'—'}</td><td class="n">${r.trendQ!=null?r.trendQ.toFixed(0)+'%':'—'}</td><td class="n">${r.atrPct!=null?r.atrPct.toFixed(1):'—'}</td><td>${r.ok?'<span class="pos">候補</span>':'<span style="color:#8ea3bf">'+(r.reasons||[]).join('、')+'</span>'}</td></tr>`).join(''):'<tr><td style="color:#8ea3bf">未実行</td></tr>';
  document.getElementById('log').innerHTML=(d.log||[]).slice().reverse().map(l=>`<div class="${l.level}"><span style="color:#8ea3bf">${l.t}</span> ${l.m}</div>`).join('');
}
load(); setInterval(load,30000);
</script></body></html>"""


def make_handler(state_dir: str, guard):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path.startswith("/state.json"):
                body = json.dumps({
                    "mode": (load_json(state_dir, "account.json") or {}).get("mode"),
                    "account": load_json(state_dir, "account.json"),
                    "signals": load_json(state_dir, "signals.json"),
                    "screener": load_json(state_dir, "screener.json"),
                    "log": read_log(state_dir, 150),
                    "stopFile": guard.stop_file() if guard else None,
                }, ensure_ascii=False, default=str).encode("utf-8")
                self.send_response(200); self.send_header("Content-Type", "application/json; charset=utf-8")
            else:
                body = PAGE.encode("utf-8")
                self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    return H


def serve(cfg: dict, guard=None):
    host = cfg["dashboard"].get("host", "127.0.0.1")
    port = int(cfg["dashboard"].get("port", 8765))
    srv = HTTPServer((host, port), make_handler(cfg.get("state_dir", "state"), guard))
    print(f"ダッシュボード: http://{host}:{port}/  （Ctrl+C で終了）")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
