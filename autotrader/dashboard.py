"""状況確認と操作のためのダッシュボード（標準ライブラリのみ、freqtrade の FreqUI を参考に簡易版）。

- 状況: 口座・注文・保有・シグナル・スクリーニング・ログ
- 操作: 判定を今すぐ実行 / 緊急停止・解除 / 戦略の切替
- デモ: 過去データを1日ずつ再生（再生・一歩・一気に・リセット）
- 比較: 4戦略＋買い持ちを同じ相場で横並び
"""
from __future__ import annotations
import json, os, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Optional

from .state import load_json, save_json, read_log
from .simulator import create_sim, sim_step, sim_result, mtm
from .strategies import describe_all, get_strategy


class DashboardApp:
    """サーバが保持する実行状態（デモ再生の途中経過など）"""

    def __init__(self, cfg: dict, runner_factory):
        self.cfg = cfg
        self.state_dir = cfg.get("state_dir", "state")
        self.runner_factory = runner_factory
        self.lock = threading.Lock()
        self.symbols = None
        self.demo_sim = None
        self.demo_meta = {}
        self.busy = ""

    # ---- 共通 ----
    def runner(self):
        return self.runner_factory(self.cfg)

    def history(self, force=False):
        if self.symbols is None or force:
            self.symbols = self.runner().load_history()
        return self.symbols

    # ---- 操作 ----
    def api(self, path: str, body: dict) -> dict:
        try:
            return self._api(path, body)
        except Exception as e:      # 操作の失敗は結果として返す（画面側で表示）
            self.busy = ""
            return {"ok": False, "error": str(e)}

    def _api(self, path: str, body: dict) -> dict:
        with self.lock:
            if path == "/api/cycle":
                self.busy = "判定中"
                try:
                    r = self.runner()
                    res = r.signal_cycle(symbols=self.history(force=True))
                    return {"ok": res.get("ok", False), "date": res.get("date")}
                finally:
                    self.busy = ""
            if path == "/api/stop":
                open(self.cfg["guard"].get("stop_file", "STOP"), "w").close()
                return {"ok": True}
            if path == "/api/resume":
                p = self.cfg["guard"].get("stop_file", "STOP")
                if os.path.exists(p):
                    os.remove(p)
                return {"ok": True}
            if path == "/api/strategy":
                t = body.get("type", "score")
                sc = {"type": t, "params": body.get("params") or {}} if t != "score" else dict(self.cfg["strategy"], type="score")
                get_strategy(sc)                               # 検証
                save_json(self.state_dir, "strategy_selected.json", sc)
                self.demo_sim = None
                return {"ok": True, "type": t}
            if path == "/api/compare":
                self.busy = "比較中"
                try:
                    r = self.runner()
                    res = r.compare(days=int(body.get("days", 500)), symbols=self.history())
                    return {"ok": True, "from": res["from"], "to": res["to"]}
                finally:
                    self.busy = ""
            if path.startswith("/api/demo/"):
                return self.demo_api(path, body)
        return {"ok": False, "error": "unknown"}

    # ---- デモ再生 ----
    def demo_api(self, path: str, body: dict) -> dict:
        if path == "/api/demo/reset":
            self.demo_sim = None
            return {"ok": True}
        if path == "/api/demo/start" or self.demo_sim is None:
            days = int(body.get("days", 250))
            r = self.runner()
            syms = self.history()
            today = r.latest_date(syms)
            r.refresh_watchlist(syms, today, force=True)
            tr = r.tradable(syms) or syms
            dates = sorted({b.d for s in tr for b in s.bars})
            frm = dates[max(0, len(dates) - days)]
            self.demo_sim = create_sim(tr, r.strat, r.risk, frm, None)
            self.demo_meta = {"from": frm, "to": dates[-1], "symbols": len(tr),
                              "strategy": get_strategy(r.strat).label, "days": days}
            if path == "/api/demo/start":
                return {"ok": True, **self.demo_meta}
        sim = self.demo_sim
        if path == "/api/demo/step":
            n = max(1, int(body.get("n", 1)))
            for _ in range(n):
                if sim_step(sim):
                    break
        elif path == "/api/demo/run":
            while not sim_step(sim):
                pass
        return {"ok": True, "finished": sim.finished}

    def demo_state(self) -> Optional[dict]:
        sim = self.demo_sim
        if sim is None:
            return None
        t = min(sim.t, len(sim.dates) - 1)
        cur = sim.dates[t if sim.t < len(sim.dates) else -1]
        held = mtm(sim.ctx, sim.positions, cur)
        eq = sim.cash + held
        return {
            **self.demo_meta,
            "date": cur, "done": sim.t - sim.startT, "total": len(sim.tradeDates), "finished": sim.finished,
            "cash": sim.cash, "equity": eq, "initial": sim.risk["initialCash"], "costs": sim.costs,
            "halted": sim.halted, "haltReason": sim.haltReason,
            "positions": {code: {**p, "price": (lambda c: c.bars[c.idx[cur]].c if c and cur in c.idx else p["avg"])(
                next((c for c in sim.ctx if c.sym.code == code), None))} for code, p in sim.positions.items()},
            "trades": sim.trades[-50:], "events": sim.events[-80:], "equityCurve": sim.equity,
            "result": {k: v for k, v in sim_result(sim)["metrics"].items() if k != "ddSeries"} if sim.finished else None,
            "buyHold": sim_result(sim)["buyHold"]["totalRet"] if sim.finished else None,
        }

    def state(self) -> dict:
        acc = load_json(self.state_dir, "account.json") or {}
        return {
            "mode": self.cfg.get("mode"), "busy": self.busy,
            "account": acc,
            "signals": load_json(self.state_dir, "signals.json"),
            "screener": load_json(self.state_dir, "screener.json"),
            "compare": load_json(self.state_dir, "compare.json"),
            "strategies": describe_all(),
            "strategy": (load_json(self.state_dir, "strategy_selected.json") or load_json(self.state_dir, "strategy_learned.json")
                         or self.cfg["strategy"]).get("type", "score"),
            "demo": self.demo_state(),
            "log": read_log(self.state_dir, 120),
            "stopFile": self.cfg["guard"].get("stop_file", "STOP") if os.path.exists(self.cfg["guard"].get("stop_file", "STOP")) else None,
        }


PAGE = r"""<!DOCTYPE html><html lang="ja"><head><meta charset="utf-8"><title>自走エンジン</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{--bg:#0b1220;--card:rgba(22,34,54,.8);--bd:rgba(255,255,255,.1);--mu:#8ea3bf;--sky:#38bdf8;--g:#22c55e;--r:#fb7185;--a:#f59e0b;--v:#a78bfa}
body{margin:0;font-family:-apple-system,"Segoe UI","Hiragino Kaku Gothic ProN","Noto Sans JP",sans-serif;background:var(--bg);color:#f1f5f9}
.c{max-width:1140px;margin:0 auto;padding:1rem}h1{font-size:1.25rem;margin:.2rem 0 .8rem;display:flex;gap:.6rem;align-items:center;flex-wrap:wrap}
.tabs{display:flex;gap:.4rem;margin-bottom:.8rem;flex-wrap:wrap}.tab{border:1px solid var(--bd);background:rgba(255,255,255,.04);color:var(--mu);padding:.45rem .9rem;border-radius:10px;cursor:pointer;font-weight:600;font-size:.83rem}.tab.on{background:var(--sky);color:#04202e;border-color:transparent}
.card{background:var(--card);border:1px solid var(--bd);border-radius:14px;padding:1rem;margin-bottom:.9rem}h2{font-size:.95rem;margin:0 0 .7rem}
.g{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:.6rem}.s{background:rgba(255,255,255,.04);border-radius:10px;padding:.55rem .75rem}.k{font-size:.68rem;color:var(--mu)}.v{font-size:1.1rem;font-weight:700}
table{width:100%;border-collapse:collapse;font-size:.79rem}th{text-align:left;color:var(--mu);font-weight:600;font-size:.7rem;padding:.4rem;border-bottom:1px solid var(--bd)}td{padding:.4rem;border-bottom:1px solid rgba(255,255,255,.05)}.n{text-align:right}.pos{color:var(--g)}.neg{color:var(--r)}
.b{display:inline-block;padding:.1rem .5rem;border-radius:6px;font-size:.7rem;font-weight:700}.buy{background:rgba(34,197,94,.2);color:#4ade80}.sell{background:rgba(251,113,133,.2);color:#fb7185}.hold{background:rgba(142,163,191,.15);color:var(--mu)}
.btn{border:1px solid var(--bd);background:rgba(255,255,255,.06);color:#f1f5f9;padding:.5rem .85rem;border-radius:10px;cursor:pointer;font-weight:600;font-size:.82rem;font-family:inherit}.btn.p{background:var(--sky);color:#04202e;border-color:transparent}.btn.d{color:var(--r);border-color:rgba(251,113,133,.4)}.btn.gr{background:var(--g);color:#04220f;border-color:transparent}.btn:disabled{opacity:.4;cursor:not-allowed}
.row{display:flex;gap:.5rem;flex-wrap:wrap;align-items:center;margin-bottom:.6rem}select,input{background:rgba(255,255,255,.05);border:1px solid var(--bd);color:#f1f5f9;padding:.45rem .6rem;border-radius:9px;font-size:.82rem}select option{background:#16223a}
.mode{padding:.35rem .8rem;border-radius:9px;font-weight:700;font-size:.8rem}.mode.live{background:var(--r);color:#2a0a10}.mode.paper{background:var(--sky);color:#04202e}.mode.demo{background:var(--v);color:#1e1240}.mode.dry{background:var(--a);color:#2a1a00}
.log{font-family:ui-monospace,Menlo,monospace;font-size:.73rem;max-height:280px;overflow:auto;line-height:1.6}.warn{color:#fcd34d}.error{color:var(--r)}
.halt{background:rgba(251,113,133,.12);border:1px solid rgba(251,113,133,.4);border-radius:10px;padding:.7rem;margin-bottom:.8rem}
.bar{height:8px;background:rgba(255,255,255,.06);border-radius:99px;overflow:hidden;margin:.5rem 0}.bar>div{height:100%;background:linear-gradient(90deg,var(--sky),var(--v))}
canvas{width:100%;height:220px;display:block;background:rgba(0,0,0,.2);border-radius:10px}.hint{color:var(--mu);font-size:.76rem;line-height:1.6}
.verd{border-radius:10px;padding:.7rem .9rem;font-size:.84rem;line-height:1.6;border:1px solid var(--bd);background:rgba(255,255,255,.03)}
</style></head><body><div class="c">
<h1>📈 株式自動売買 自走エンジン <span id="mode" class="mode"></span><span id="busy" class="hint"></span><span id="upd" class="hint" style="margin-left:auto"></span></h1>
<div id="halt"></div>
<div class="tabs"><span class="tab on" data-t="ops">運用状況</span><span class="tab" data-t="demo">🎬 デモ再生</span><span class="tab" data-t="cmp">⚖️ 戦略比較</span><span class="tab" data-t="log">ログ</span></div>

<section id="t-ops">
<div class="card"><h2>操作</h2><div class="row">
<button class="btn p" onclick="api('/api/cycle')">⚡ 今すぐ判定</button>
<button class="btn d" onclick="api('/api/stop')">⛔ 緊急停止</button>
<button class="btn" onclick="api('/api/resume')">▶ 停止解除</button>
<span style="flex:1"></span>
<label class="hint">戦略 <select id="stSel" onchange="api('/api/strategy',{type:this.value})"></select></label>
</div><div class="hint" id="stDesc"></div></div>
<div class="card"><h2>口座</h2><div class="g" id="stats"></div></div>
<div class="card"><h2>翌朝の注文</h2><table id="orders"></table></div>
<div class="card"><h2>保有ポジション</h2><table id="pos"></table></div>
<div class="card"><h2>本日のシグナル</h2><table id="sig"></table></div>
<div class="card"><h2>スクリーニング上位</h2><table id="scr"></table></div>
</section>

<section id="t-demo" style="display:none">
<div class="card"><h2>🎬 デモ再生（過去データを1日ずつ）</h2>
<p class="hint">いま選んでいる戦略と資金設定で、スクリーニングを通った銘柄に対して自動売買を再生します。資産がどう増減し、どこで買ってどこで損切りされるかを実際の運用と同じ順序で確認できます。</p>
<div class="row"><label class="hint">日数 <input id="dmDays" type="number" value="250" style="width:80px"></label>
<button class="btn p" id="dmPlay" onclick="demoToggle()">▶ 再生</button>
<button class="btn" onclick="demoStep(1)">⏭ 1日</button><button class="btn" onclick="demoStep(20)">⏩ 20日</button>
<button class="btn" onclick="api('/api/demo/run')">⏭⏭ 最後まで</button>
<button class="btn d" onclick="demoReset()">↺ リセット</button></div>
<div class="bar"><div id="dmBar" style="width:0%"></div></div><div class="hint" id="dmProg">未開始</div><div id="dmHalt"></div></div>
<div class="card"><h2>デモ口座</h2><div class="g" id="dmStats"></div></div>
<div class="card"><h2>資産の推移</h2><canvas id="dmChart"></canvas></div>
<div class="card"><h2>保有</h2><table id="dmPos"></table></div>
<div class="card"><h2>売買の記録</h2><div class="log" id="dmLog"></div></div>
</section>

<section id="t-cmp" style="display:none">
<div class="card"><h2>⚖️ 戦略比較（同じ相場・同じ資金管理）</h2>
<p class="hint">買って持ち続けた場合を基準に、4つの戦略を横並びで比較します。<b>買い持ちに負けている戦略で自動売買をする意味はありません。</b>上昇相場では買い持ちが勝ちやすく、下落相場では戦略が守りに働くのが普通です。</p>
<div class="row"><label class="hint">日数 <input id="cmDays" type="number" value="500" style="width:80px"></label><button class="btn p" onclick="api('/api/compare',{days:+document.getElementById('cmDays').value})">⚖️ 比較を実行</button></div>
<div id="cmVerd" class="verd" style="display:none"></div></div>
<div class="card"><table id="cmTable"></table></div>
<div class="card"><h2>資産推移の比較</h2><canvas id="cmChart"></canvas><div class="hint" id="cmLegend"></div></div>
</section>

<section id="t-log" style="display:none"><div class="card"><h2>ログ</h2><div class="log" id="log"></div></div></section>
</div><script>
const y=n=>n==null?'—':Math.round(n).toLocaleString('ja-JP')+'円';
const pc=n=>n==null?'—':(n>=0?'+':'')+n.toFixed(1)+'%';
const st=(k,v,c)=>`<div class="s"><div class="k">${k}</div><div class="v ${c||''}">${v}</div></div>`;
const COL={buyhold:'#8ea3bf',score:'#38bdf8',momentum:'#22c55e',pullback:'#f59e0b',donchian:'#a78bfa'};
let S=null, playing=null, tab='ops';
document.querySelectorAll('.tab').forEach(t=>t.onclick=()=>{tab=t.dataset.t;document.querySelectorAll('.tab').forEach(x=>x.classList.toggle('on',x===t));['ops','demo','cmp','log'].forEach(k=>document.getElementById('t-'+k).style.display=k===tab?'':'none');render();});
async function api(p,b){const r=await fetch(p,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b||{})});const j=await r.json();await load();return j;}
async function load(){S=await (await fetch('/state.json')).json();render();}
async function demoStep(n){await api('/api/demo/step',{n});}
async function demoReset(){if(playing){clearInterval(playing);playing=null;}await api('/api/demo/reset');}
async function demoToggle(){if(playing){clearInterval(playing);playing=null;render();return;}
 if(!S.demo){await api('/api/demo/start',{days:+document.getElementById('dmDays').value});}
 playing=setInterval(async()=>{const j=await api('/api/demo/step',{n:3});if(j.finished){clearInterval(playing);playing=null;render();}},350);render();}
function chart(cv,series,initial){const dpr=devicePixelRatio||1,W=cv.clientWidth,H=cv.clientHeight;cv.width=W*dpr;cv.height=H*dpr;const x=cv.getContext('2d');x.setTransform(dpr,0,0,dpr,0,0);x.clearRect(0,0,W,H);
 const all=series.flatMap(s=>s.pts.map(p=>p.e));if(!all.length){x.fillStyle='#8ea3bf';x.font='13px sans-serif';x.textAlign='center';x.fillText('データがありません',W/2,H/2);return;}
 let mn=Math.min(...all,initial||Infinity),mx=Math.max(...all,initial||-Infinity);const pad=(mx-mn)*.08||1;mn-=pad;mx+=pad;const L=62,R=10,T=10,B=22,cw=W-L-R,ch=H-T-B;
 x.strokeStyle='rgba(255,255,255,.07)';x.fillStyle='#8ea3bf';x.font='10px sans-serif';x.textAlign='right';for(let i=0;i<=4;i++){const yy=T+ch*i/4,v=mx-(mx-mn)*i/4;x.beginPath();x.moveTo(L,yy);x.lineTo(W-R,yy);x.stroke();x.fillText(Math.round(v).toLocaleString(),L-6,yy+3);}
 if(initial){const yy=T+ch*(1-(initial-mn)/(mx-mn));x.setLineDash([4,4]);x.strokeStyle='rgba(142,163,191,.5)';x.beginPath();x.moveTo(L,yy);x.lineTo(W-R,yy);x.stroke();x.setLineDash([]);}
 const n=Math.max(...series.map(s=>s.pts.length));series.forEach(s=>{if(s.pts.length<2)return;x.beginPath();s.pts.forEach((p,i)=>{const px=L+cw*i/(n-1),py=T+ch*(1-(p.e-mn)/(mx-mn));i?x.lineTo(px,py):x.moveTo(px,py);});x.strokeStyle=s.color;x.lineWidth=s.w||1.6;x.stroke();});
 const f=series.find(s=>s.pts.length);x.fillStyle='#8ea3bf';x.textAlign='left';x.fillText(f.pts[0].d,L,H-7);x.textAlign='right';x.fillText(f.pts[f.pts.length-1].d,W-R,H-7);}
function render(){if(!S)return;const m=S.mode||'paper';const el=document.getElementById('mode');el.textContent={demo:'デモ',paper:'ペーパー','live-dryrun':'ライブ試運転',live:'ライブ（実発注）'}[m]||m;el.className='mode '+(m==='live'?'live':m==='live-dryrun'?'dry':m);
 document.getElementById('busy').textContent=S.busy?'⏳ '+S.busy:'';document.getElementById('upd').textContent='更新 '+new Date().toLocaleTimeString('ja-JP');
 const a=S.account||{},sg=S.signals||{};document.getElementById('halt').innerHTML=a.halted?`<div class="halt">⛔ 安全装置が作動中：${a.haltReason}</div>`:(S.stopFile?`<div class="halt">⛔ 緊急停止中（${S.stopFile}）。発注は行いません。「停止解除」で再開します。</div>`:'');
 const sel=document.getElementById('stSel');if(sel.options.length!==S.strategies.length){sel.innerHTML=S.strategies.map(s=>`<option value="${s.type}">${s.label}</option>`).join('');}sel.value=S.strategy;const d=S.strategies.find(s=>s.type===S.strategy);document.getElementById('stDesc').textContent=d?d.description:'';
 const eq=sg.equity!=null?sg.equity:a.cash,pnl=eq-(a.initialCash||0);
 document.getElementById('stats').innerHTML=st('総資産',y(eq),pnl>=0?'pos':'neg')+st('損益',y(pnl),pnl>=0?'pos':'neg')+st('現金',y(a.cash))+st('保有',Object.keys(a.positions||{}).length+'銘柄')+st('決済回数',(a.trades||[]).length+'回')+st('最終判定',a.lastCycleDate||'—')+st('ペーパー実績',(a.paperDays||0)+'日')+st('候補銘柄',(a.watchlist||[]).length+'銘柄');
 const o=sg.orders||[];document.getElementById('orders').innerHTML=o.length?'<tr><th>銘柄</th><th>売買</th><th class="n">数量</th><th class="n">参考価格</th><th class="n">概算</th><th>理由</th><th>注文ID</th></tr>'+o.map(x=>`<tr><td>${x.code} ${x.name||''}</td><td><span class="b ${x.side}">${x.side==='buy'?'買い':'売り'}</span></td><td class="n">${x.qty}</td><td class="n">${x.estPrice}</td><td class="n">${y(x.estPrice*x.qty)}</td><td>${x.reason||''}</td><td>${x.orderId||''}</td></tr>`).join(''):'<tr><td class="hint">注文はありません</td></tr>';
 const p=a.positions||{},pk=Object.keys(p),px={};(sg.signals||[]).forEach(x=>px[x.code]=x.price);
 document.getElementById('pos').innerHTML=pk.length?'<tr><th>銘柄</th><th class="n">数量</th><th class="n">取得</th><th class="n">現在</th><th class="n">評価損益</th><th class="n">損切</th><th>取得日</th></tr>'+pk.map(c=>{const q=p[c],cur=px[c]??q.avg,g=(cur-q.avg)*q.qty;return `<tr><td>${c}</td><td class="n">${q.qty}</td><td class="n">${q.avg.toFixed(1)}</td><td class="n">${cur}</td><td class="n ${g>=0?'pos':'neg'}">${y(g)}</td><td class="n">${q.stop?q.stop.toFixed(1):'—'}</td><td>${q.entryDate}</td></tr>`}).join(''):'<tr><td class="hint">保有はありません</td></tr>';
 const sgs=sg.signals||[];document.getElementById('sig').innerHTML=sgs.length?'<tr><th>銘柄</th><th>判定</th><th class="n">スコア</th><th class="n">終値</th><th>根拠</th></tr>'+sgs.slice(0,30).map(x=>`<tr><td>${x.code} ${x.name||''}${x.held?' <span class="b hold">保有</span>':''}</td><td><span class="b ${x.action}">${{buy:'買い',sell:'売り',hold:'見送り'}[x.action]}</span></td><td class="n">${x.score.toFixed(1)}</td><td class="n">${x.price}</td><td class="hint">${(x.votes||[]).join(' / ')}${x.blocked?' <span class="warn">'+x.blocked+'</span>':''}</td></tr>`).join(''):'<tr><td class="hint">判定はまだありません。「今すぐ判定」を押してください</td></tr>';
 const sc=(S.screener||{}).rows||[];document.getElementById('scr').innerHTML=sc.length?'<tr><th>順位</th><th>銘柄</th><th class="n">終値</th><th class="n">6か月</th><th class="n">安定性</th><th class="n">ATR%</th><th>判定</th></tr>'+sc.slice(0,25).map(r=>`<tr><td>${r.rank||'—'}</td><td>${r.code} ${r.name||''}</td><td class="n">${r.price??'—'}</td><td class="n ${(r.mom126||0)>=0?'pos':'neg'}">${r.mom126!=null?r.mom126.toFixed(1)+'%':'—'}</td><td class="n">${r.trendQ!=null?r.trendQ.toFixed(0)+'%':'—'}</td><td class="n">${r.atrPct!=null?r.atrPct.toFixed(1):'—'}</td><td>${r.ok?'<span class="pos">候補</span>':'<span class="hint">'+(r.reasons||[]).join('、')+'</span>'}</td></tr>`).join(''):'<tr><td class="hint">未実行</td></tr>';
 document.getElementById('log').innerHTML=(S.log||[]).slice().reverse().map(l=>`<div class="${l.level}"><span class="hint">${l.t}</span> ${l.m}</div>`).join('');
 // --- デモ ---
 const D=S.demo;const pb=document.getElementById('dmPlay');pb.textContent=playing?'⏸ 一時停止':'▶ 再生';pb.className=playing?'btn d':'btn p';
 if(!D){document.getElementById('dmProg').textContent='未開始 —「再生」を押すと開始します';document.getElementById('dmBar').style.width='0%';document.getElementById('dmHalt').innerHTML='';document.getElementById('dmStats').innerHTML='';document.getElementById('dmPos').innerHTML='';document.getElementById('dmLog').innerHTML='';if(tab==='demo')chart(document.getElementById('dmChart'),[],null);}
 else{document.getElementById('dmBar').style.width=(D.done/D.total*100).toFixed(1)+'%';document.getElementById('dmProg').textContent=`${D.strategy}｜${D.symbols}銘柄｜${D.date}（${D.done}/${D.total}営業日${D.finished?'・完了':''}）`;
  document.getElementById('dmHalt').innerHTML=D.halted?`<div class="halt" style="margin-top:.6rem">⛔ 安全装置が作動：${D.haltReason}</div>`:'';
  const dp=D.equity-D.initial;document.getElementById('dmStats').innerHTML=st('デモ資産',y(D.equity),dp>=0?'pos':'neg')+st('損益',y(dp)+' ('+pc(dp/D.initial*100)+')',dp>=0?'pos':'neg')+st('現金',y(D.cash))+st('取引',D.trades.length+'回')+st('売買コスト',y(D.costs),'neg')+(D.result?st('買い持ち',pc(D.buyHold))+st('最大下落',D.result.maxDD.toFixed(1)+'%','neg'):'');
  const dk=Object.keys(D.positions);document.getElementById('dmPos').innerHTML=dk.length?'<tr><th>銘柄</th><th class="n">数量</th><th class="n">取得</th><th class="n">現在</th><th class="n">評価損益</th><th class="n">損切</th><th>取得日</th></tr>'+dk.map(c=>{const q=D.positions[c],g=(q.price-q.avg)*q.qty;return `<tr><td>${c}</td><td class="n">${q.qty}</td><td class="n">${q.avg.toFixed(1)}</td><td class="n">${q.price}</td><td class="n ${g>=0?'pos':'neg'}">${y(g)}</td><td class="n">${q.stop?q.stop.toFixed(1):'—'}</td><td>${q.entryDate}</td></tr>`}).join(''):'<tr><td class="hint">保有なし</td></tr>';
  document.getElementById('dmLog').innerHTML=D.events.slice().reverse().map(e=>`<div><span class="hint">${e.d}</span> ${e.m}</div>`).join('')||'<div class="hint">まだ売買はありません</div>';
  if(tab==='demo')chart(document.getElementById('dmChart'),[{pts:D.equityCurve,color:dp>=0?'#22c55e':'#fb7185',w:2}],D.initial);}
 // --- 比較 ---
 const C=S.compare;if(C&&C.rows){const vd=document.getElementById('cmVerd');vd.style.display='';vd.innerHTML=`<b>${C.from} 〜 ${C.to}（${C.symbols}銘柄）</b><br>${C.verdict}`;
  document.getElementById('cmTable').innerHTML='<tr><th>戦略</th><th class="n">損益</th><th class="n">最終資産</th><th class="n">最大下落</th><th class="n">売買</th><th class="n">コスト</th><th class="n">勝ち月率</th><th class="n">最長連敗</th><th></th></tr>'+C.rows.map(r=>`<tr><td><span style="display:inline-block;width:10px;height:10px;border-radius:3px;background:${COL[r.type]||'#fff'};margin-right:.4rem"></span>${r.label}</td><td class="n ${r.totalRet>=0?'pos':'neg'}"><b>${pc(r.totalRet)}</b></td><td class="n">${y(r.finalEquity)}</td><td class="n neg">${r.maxDD.toFixed(1)}%</td><td class="n">${r.trades}</td><td class="n">${y(r.costs)}</td><td class="n">${r.winMonthRate!=null?r.winMonthRate.toFixed(0)+'%':'—'}</td><td class="n">${r.maxLoseStreak}</td><td>${r.halted?'<span class="warn">⛔停止</span>':''}</td></tr>`).join('');
  if(tab==='cmp'){const ser=Object.entries(C.curves||{}).map(([t,pts])=>({pts,color:COL[t]||'#fff',w:t==='buyhold'?1.2:1.8}));chart(document.getElementById('cmChart'),ser,C.rows[0]&&C.rows[0].finalEquity?null:null);document.getElementById('cmLegend').innerHTML=C.rows.map(r=>`<span style="margin-right:1rem"><span style="display:inline-block;width:10px;height:10px;border-radius:3px;background:${COL[r.type]};margin-right:.3rem"></span>${r.label}</span>`).join('');}}
}
load();setInterval(()=>{if(!playing)load();},30000);
</script></body></html>"""


def make_handler(app: DashboardApp):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body: bytes, ctype: str):
            self.send_response(code); self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

        def do_GET(self):
            if self.path.startswith("/state.json"):
                self._send(200, json.dumps(app.state(), ensure_ascii=False, default=str).encode("utf-8"),
                           "application/json; charset=utf-8")
            else:
                self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")

        def do_POST(self):
            # ローカル専用の操作画面。外部からの操作を受け付けない
            host = self.headers.get("Host", "")
            if not (host.startswith("127.0.0.1") or host.startswith("localhost")):
                self._send(403, b'{"ok":false,"error":"local only"}', "application/json"); return
            n = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                body = {}
            try:
                res = app.api(self.path, body)
            except Exception as e:  # 操作の失敗で画面を落とさない
                res = {"ok": False, "error": str(e)}
            self._send(200, json.dumps(res, ensure_ascii=False, default=str).encode("utf-8"),
                       "application/json; charset=utf-8")
    return H


def serve(cfg: dict, runner_factory):
    host = cfg["dashboard"].get("host", "127.0.0.1")
    port = int(cfg["dashboard"].get("port", 8765))
    app = DashboardApp(cfg, runner_factory)
    srv = HTTPServer((host, port), make_handler(app))
    print(f"ダッシュボード: http://{host}:{port}/  （Ctrl+C で終了）")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
