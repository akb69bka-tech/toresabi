// Code.gs の動作テスト（Google と LINE を偽物に差し替えて Node で動かす）
//   node line-sagyo-nisshi/test.js
// 去年の日誌のファイルを渡すと、その実データでも指示を作ってみせる:
//   node line-sagyo-nisshi/test.js 昨年.json   （見出し行つきの 2 次元配列 JSON）
'use strict';
const vm = require('vm');
const fs = require('fs');
const path = require('path');
const assert = require('assert');

function load(nowISO) {
  const sent = [];
  const sheets = {};
  const store = {};
  let triggers = [];
  const NOW = new Date(nowISO);

  function makeSheet(name) {
    const sh = {
      name, data: [], formats: {},
      getName: () => name,
      getLastRow: () => sh.data.length,
      getLastColumn: () => sh.data.reduce((m, r) => Math.max(m, r.length), 0),
      getRange(r, c, nr = 1, nc = 1) {
        return {
          setValues(v) {
            for (let i = 0; i < nr; i++) {
              while (sh.data.length < r + i) sh.data.push([]);
              for (let j = 0; j < nc; j++) {
                let val = v[i][j];
                // シートは "yyyy/MM/dd HH:mm:ss" を日時に変換してしまう（文字書式でない限り）
                if (typeof val === 'string' && /^\d{4}\/\d\d\/\d\d \d\d:\d\d:\d\d$/.test(val) && sh.formats[(c + j)] !== '@') val = new Date(val.replace(/\//g, '-').replace(' ', 'T') + '+09:00');
                sh.data[r - 1 + i][c - 1 + j] = val;
              }
            }
            return this;
          },
          setValue(v) { return this.setValues([[v]]); },
          getValues() {
            const out = [];
            for (let i = 0; i < nr; i++) { const row = []; for (let j = 0; j < nc; j++) { const x = (sh.data[r - 1 + i] || [])[c - 1 + j]; row.push(x === undefined ? '' : x); } out.push(row); }
            return out;
          },
          getDisplayValues() {
            return this.getValues().map(row => row.map(x => x instanceof Date ? 'DATE' : String(x)));
          },
          setNumberFormat(f) { for (let j = 0; j < nc; j++) sh.formats[c + j] = f; return this; },
          setFontWeight() { return this; }, setBackground() { return this; }, setNote() { return this; }
        };
      },
      appendRow(row) { sh.data.push(row.slice()); },
      deleteRow(i) { sh.data.splice(i - 1, 1); },
      clear() { sh.data = []; },
      setFrozenRows() {}, setFrozenColumns() {}, setColumnWidth() {}
    };
    return sh;
  }
  const book = {
    getSheetByName: n => sheets[n] || null,
    insertSheet: n => (sheets[n] = makeSheet(n)),
    getSheets: () => Object.values(sheets),
    deleteSheet: s => { delete sheets[s.name]; },
    setSpreadsheetTimeZone() {},
    getId: () => 'SHEET'
  };
  class FakeDate extends Date {
    constructor(...a) { if (a.length) super(...a); else super(NOW.getTime()); }
    static now() { return NOW.getTime(); }
  }
  const ctx = {
    console, Date: FakeDate,
    SpreadsheetApp: { getActiveSpreadsheet: () => book, openById: () => book },
    PropertiesService: { getScriptProperties: () => ({
      getProperty: k => (k in store ? store[k] : null),
      setProperty: (k, v) => { store[k] = String(v); },
      deleteProperty: k => { delete store[k]; }
    }) },
    LockService: { getScriptLock: () => ({ waitLock() {}, releaseLock() {} }) },
    Utilities: { formatDate: (d, tz, f) => {
      const j = new Date(d.getTime() + 9 * 3600e3);
      const p = n => String(n).padStart(2, '0');
      return f.replace('yyyy', j.getUTCFullYear()).replace('MM', p(j.getUTCMonth() + 1)).replace('dd', p(j.getUTCDate()))
        .replace('HH', p(j.getUTCHours())).replace('mm', p(j.getUTCMinutes())).replace('ss', p(j.getUTCSeconds()));
    } },
    UrlFetchApp: { fetch: (url, opt) => {
      if (url.indexOf('/v2/bot/profile/') >= 0) return { getResponseCode: () => 200, getContentText: () => JSON.stringify({ displayName: 'たろう' }) };
      if (url.indexOf('api.line.me') >= 0) { sent.push({ url, body: JSON.parse(opt.payload) }); return { getResponseCode: () => 200, getContentText: () => '{}' }; }
      if (url.indexOf('geocoding-api') >= 0) return { getResponseCode: () => 200, getContentText: () => JSON.stringify({ results: [{ name: '安芸市', latitude: 33.5, longitude: 133.9, admin1: '高知県', country_code: 'JP' }] }) };
      if (url.indexOf('api.open-meteo.com') >= 0) {
        const days = []; for (let i = 0; i < 7; i++) { const d = new Date(NOW.getTime() + 9 * 3600e3 + i * 86400e3); days.push(d.toISOString().slice(0, 10)); }
        return { getResponseCode: () => 200, getContentText: () => JSON.stringify({ daily: { time: days, weather_code: days.map(() => 3), temperature_2m_max: days.map(() => 21), temperature_2m_min: days.map(() => 6), precipitation_probability_max: days.map(() => 20), wind_speed_10m_max: days.map(() => 4) } }) };
      }
      throw new Error('unexpected fetch ' + url);
    } },
    ContentService: { createTextOutput: t => ({ text: t }) },
    ScriptApp: {
      getProjectTriggers: () => triggers,
      deleteTrigger: t => { triggers = triggers.filter(x => x !== t); },
      newTrigger: fn => { const b = { timeBased: () => b, everyDays: () => b, atHour: h => { b.h = h; return b; }, inTimezone: () => b, create: () => { triggers.push({ getHandlerFunction: () => fn, h: b.h }); } }; return b; }
    }
  };
  vm.createContext(ctx);
  vm.runInContext(fs.readFileSync(path.join(__dirname, 'Code.gs'), 'utf8'), ctx);
  return { ctx, sent, sheets, store, triggers: () => triggers };
}

function lineSay(env, text, user = 'U1') {
  env.sent.length = 0;
  env.ctx.doPost({ postData: { contents: JSON.stringify({ events: [{ type: 'message', replyToken: 'R', source: { userId: user }, message: { type: 'text', text } }] }) } });
  assert.strictEqual(env.sent.length, 1, 'reply for: ' + text);
  return env.sent[0].body.messages[0].text;
}

// 合成した「昨年」データ（2025/8〜12）
function syntheticLastYear() {
  const rows = [['日付', '曜日', '作業内容', '詳細', '所要時間', '収穫ケース数', '作業人数（午前）', '備考']];
  const base = Date.UTC(2025, 7, 1) / 86400000;
  for (let i = 0; i < 150; i++) {
    const dn = base + i;
    const serial = dn + 25569;
    const day = [];
    if (i === 28) day.push(['定植', '', 10]);
    if (i >= 60) day.push(['収穫', '', 6, 20, 3]);
    if (i >= 85 && i < 110) day.push(['剪定', i % 2 ? '東向き' : '西向き', 3]);
    if (i >= 95 && i < 98) day.push(['設備', '二重ビニール張り', 7]);
    if (i % 7 === 0) day.push(['薬散', '', 2]);
    day.forEach((t, k) => rows.push([k === 0 ? serial : '', '', t[0], t[1], t[2], t[3] || '', t[4] || '', '']));
    if (i % 30 === 29) rows.push(['', '', '', '', '=SUM()', '', '', '']);
  }
  return rows;
}

// ---------------- テスト ----------------
const env = load('2026-10-31T18:00:00+09:00');
const c = env.ctx;
c.setupSheets();
const ly = syntheticLastYear();
const lySheet = env.sheets['昨年'];
lySheet.data = ly;
c.PropertiesService.getScriptProperties().setProperty('LINE_TOKEN', 'x');
c.PropertiesService.getScriptProperties().setProperty('PASSCODE', 'なす2026');

// 未登録は記録できない
assert(/登録されていません/.test(lineSay(env, '収穫 5')));
assert(/違います/.test(lineSay(env, '登録 ちがう')));
assert(/たろう さんを登録/.test(lineSay(env, '登録 なす２０２６')));

// 記録
let r = lineSay(env, '収穫 6 20ケース 3人\n剪定 西向き 2.5\n誘引 2回目 3 完了\n備考 午後部会');
assert(/記録しました（4件）/.test(r), r);
assert(/10\/31\(土\) の合計 11\.5h/.test(r), r);
assert(/「誘引」は初めての作業名/.test(r), r);
const rec = env.sheets['記録'].data;
assert.strictEqual(rec.length, 5);
assert.strictEqual(rec[1][2], '収穫'); assert.strictEqual(rec[1][5], 20); assert.strictEqual(rec[1][6], 3);
assert.strictEqual(rec[3][7], '完了');
assert.strictEqual(typeof rec[1][8], 'string', '入力日時は文字のまま');
assert.strictEqual(c.dayNum(rec[1][0]), c.ymdToDn(2026, 10, 31));

// 別の日付
r = lineSay(env, '昨日\n収穫 5.5 18ケース\n薬散 2');
assert(/10\/30\(金\)/.test(r), r);
// 取消（直前の 2 件だけ消える）
r = lineSay(env, '取消');
assert(/取り消しました（2件）/.test(r), r);
assert.strictEqual(env.sheets['記録'].data.length, 5);
assert(/取り消せる記録がありません/.test(lineSay(env, '取消')));
lineSay(env, '10/24 薬散 2');

// 見る
r = lineSay(env, '今日');
assert(/収穫  6h 20ケース 3人/.test(r) && /合計 11\.5h/.test(r), r);
r = lineSay(env, '今月');
assert(/10月/.test(r) && /収穫: 1日/.test(r), r);

// 予定・設定
assert(/予定を入れました: 11\/1\(日\) 午後 部会/.test(lineSay(env, '予定 11/1 午後 部会')));
assert(/11\/1\(日\) 午後 部会/.test(lineSay(env, '予定')));
assert(/2026\/9\/2/.test(lineSay(env, '定植日 9/2')));
assert(/安芸市/.test(lineSay(env, '場所 安芸市')));
assert(/20時ごろ/.test(lineSay(env, '通知 20時')));
assert.strictEqual(env.triggers().length, 1);
assert.strictEqual(env.triggers()[0].h, 20);

// 明日の指示
r = lineSay(env, '明日');
console.log('----- 明日の指示（合成データ） -----\n' + r + '\n');
assert(/11\/1\(日\) の作業指示/.test(r), r);
assert(/天気: くもり/.test(r) && /冷え込み/.test(r), r);
assert(/📌 午後 部会/.test(r), r);
assert(/1\. 📌/.test(r) && /収穫/.test(r), r);
assert(/薬散（前回 10\/24\(土\) から8日。昨年のこの時期は約7日おき）/.test(r), r);
assert(/剪定 西向き のつづき/.test(r), r);
assert(!/誘引 2回目 のつづき/.test(r), '完了した作業はつづきに出さない');
assert(/定植から61日目/.test(r), r);
assert(/そろそろ準備/.test(r) && /二重ビニール張り/.test(r), r);

// 毎晩の自動送信
env.sent.length = 0;
c.sendDailyPlan();
assert.strictEqual(env.sent.length, 1);
assert(/push/.test(env.sent[0].url) && env.sent[0].body.to === 'U1');
assert(env.sheets['月別集計'].data.length >= 3);

// ヘルプ・読めない文
assert(/使い方/.test(lineSay(env, 'ヘルプ')));
assert(/読み取れませんでした/.test(lineSay(env, '１２３')));

// 実データでの確認（任意）
if (process.argv[2]) {
  const real = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
  const e2 = load('2026-10-31T19:00:00+09:00');
  e2.ctx.setupSheets();
  e2.sheets['昨年'].data = real;
  console.log('----- 実データ: 11/1 の指示（記録なし・日付で比較） -----\n' + e2.ctx.makePlanText(e2.ctx.todayDn() + 1));
}
console.log('OK: すべてのテストに合格');
