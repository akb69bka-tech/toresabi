/**
 * LINE 作業日誌 — LINE で送った作業を記録し、翌日の作業指示を返す
 *
 * しくみ
 *   LINE（Messaging API）→ この Apps Script（Web アプリ）→ Google スプレッドシート
 *
 *   ・LINE に「収穫 6 20ケース 3人」「剪定 西向き 2.5」のように送ると「記録」シートに1行ずつ追加
 *   ・毎晩（初期値 19 時）翌日の作業指示を LINE に自動で送る
 *   ・「明日」と送ればいつでも翌日の指示を返す
 *
 * 指示の材料
 *   1. 「昨年」シート（去年の作業日誌）の同じ時期にやっていた作業
 *      （今作と昨年の定植日がわかれば「定植から何日目」でそろえる）
 *   2. 薬散などの定期作業の間隔（昨年の同時期の間隔と、今作の前回からの日数）
 *   3. ここ数日つづけている作業（「完了」と送るまで続きとして出す）
 *   4. 「予定」シートに入れた予定
 *   5. 翌日の天気（場所を登録したときだけ。Open-Meteo の無料予報）
 *
 * 設置方法は 使い方.md を参照。
 */

// ===== 基本設定 =====
var TZ = 'Asia/Tokyo';
var SHEET_RECORD = '記録';
var SHEET_LASTYEAR = '昨年';
var SHEET_SCHEDULE = '予定';
var SHEET_SETTINGS = '設定';
var SHEET_SUMMARY = '月別集計';
var HEADER = ['日付', '曜日', '作業内容', '詳細', '所要時間', '収穫ケース数', '作業人数（午前）', '備考', '入力日時', '入力者'];
var WEEK = ['日', '月', '火', '水', '木', '金', '土'];
var HARVEST = '収穫';
var INTERVAL_TASKS_DEFAULT = '薬散,除草剤,IPM';
var DONE_WORDS = ['完了', '終わり', '終了', 'おわり'];
var MAX_LINE_TEXT = 4800; // LINE のテキストは 1 通 5000 文字まで

// ======================================================================
//  純粋な処理（スプレッドシートや LINE に触らない。テスト可能）
// ======================================================================

/** 全角英数を半角に（NFKC。使えない環境では全角英数・空白だけ変換） */
function nfkc(s) {
  s = String(s);
  try { return s.normalize('NFKC'); } catch (e) {
    return s.replace(/[\uFF01-\uFF5E]/g, function (c) { return String.fromCharCode(c.charCodeAt(0) - 0xFEE0); }).replace(/\u3000/g, ' ');
  }
}

/** Date → 日本時間の通し日数（1970/1/1 = 0） */
function dayNum(d) {
  return Math.floor((d.getTime() + 9 * 3600 * 1000) / 86400000);
}
/** 通し日数 → 日本時間 0:00 の Date */
function dnToDate(dn) {
  return new Date(dn * 86400000 - 9 * 3600 * 1000);
}
/** 通し日数 → {y,m,d} */
function dnParts(dn) {
  var d = new Date(dn * 86400000);
  return { y: d.getUTCFullYear(), m: d.getUTCMonth() + 1, d: d.getUTCDate() };
}
function ymdToDn(y, m, d) {
  return Math.floor(Date.UTC(y, m - 1, d) / 86400000);
}
function weekday(dn) {
  return WEEK[((dn + 4) % 7 + 7) % 7];
}
/** 10/31(金) */
function fmtMD(dn) {
  var p = dnParts(dn);
  return p.m + '/' + p.d + '(' + weekday(dn) + ')';
}
function fmtYMD(dn) {
  var p = dnParts(dn);
  return p.y + '/' + p.m + '/' + p.d;
}
/** 小数を見やすく（2.50 → 2.5） */
function fmtNum(x) {
  return String(Math.round(x * 10) / 10);
}
/** セルの値 → 通し日数（Date・シリアル値・"2025/8/9" に対応。だめなら null） */
function cellToDn(v) {
  if (v === null || v === undefined || v === '') return null;
  if (Object.prototype.toString.call(v) === '[object Date]') return isNaN(v.getTime()) ? null : dayNum(v);
  if (typeof v === 'number') return (v > 20000 && v < 80000) ? Math.round(v) - 25569 : null;
  var m = String(v).match(/^(\d{4})[\/\-.](\d{1,2})[\/\-.](\d{1,2})/);
  return m ? ymdToDn(+m[1], +m[2], +m[3]) : null;
}
function toNum(v) {
  if (v === null || v === undefined || v === '') return null;
  if (typeof v === 'number') return isNaN(v) ? null : v;
  var s = nfkc(String(v)).trim();
  return /^-?\d+(\.\d+)?$/.test(s) ? parseFloat(s) : null;
}
function median(arr) {
  if (!arr.length) return null;
  var a = arr.slice().sort(function (x, y) { return x - y; });
  var h = Math.floor(a.length / 2);
  return a.length % 2 ? a[h] : (a[h - 1] + a[h]) / 2;
}

/**
 * シートの行（2 次元配列。先頭行は見出し）→ 作業の配列
 * 日付が 1 日の最初の行にしかない書き方（手書きの Excel そのまま）にも対応し、
 * 作業内容が空の行（月の合計行など）は飛ばす。
 * 作業内容が空でも詳細があれば、直前の作業内容を引き継ぐ。
 */
function rowsToEntries(rows) {
  var out = [];
  var curDn = null;
  var lastTask = null;
  for (var i = 1; i < rows.length; i++) {
    var r = rows[i];
    var dn = cellToDn(r[0]);
    if (dn !== null) { curDn = dn; lastTask = null; }
    if (curDn === null) continue;
    var task = String(r[2] === null || r[2] === undefined ? '' : r[2]).trim();
    var detail = String(r[3] === null || r[3] === undefined ? '' : r[3]).trim();
    if (!task && detail && lastTask) task = lastTask;
    var note = String(r[7] === null || r[7] === undefined ? '' : r[7]).trim();
    if (!task) {
      if (note) out.push({ dn: curDn, task: '', detail: '', hours: null, cases: null, people: null, note: note });
      continue;
    }
    lastTask = task;
    out.push({
      dn: curDn, task: task, detail: detail,
      hours: toNum(r[4]), cases: toNum(r[5]), people: toNum(r[6]), note: note
    });
  }
  return out;
}

/** 日付の書き方を読む。今日・昨日・一昨日・10/31・10月31日 */
function parseDateWord(word, todayDn, preferFuture) {
  var w = nfkc(word);
  if (/^(今日|きょう|本日)$/.test(w)) return todayDn;
  if (/^(昨日|きのう)$/.test(w)) return todayDn - 1;
  if (/^(一昨日|おととい)$/.test(w)) return todayDn - 2;
  if (/^(明日|あした|あす)$/.test(w)) return todayDn + 1;
  if (/^(明後日|あさって)$/.test(w)) return todayDn + 2;
  var m = w.match(/^(?:(\d{4})[\/年])?(\d{1,2})[\/月](\d{1,2})日?(?:\([^)]*\))?$/);
  if (!m) return null;
  var mo = +m[2], d = +m[3];
  if (mo < 1 || mo > 12 || d < 1 || d > 31) return null;
  if (m[1]) return ymdToDn(+m[1], mo, d);
  var y = dnParts(todayDn).y;
  var dn = ymdToDn(y, mo, d);
  if (preferFuture) {
    if (dn < todayDn - 1) dn = ymdToDn(y + 1, mo, d);
  } else if (dn > todayDn + 30) {
    dn = ymdToDn(y - 1, mo, d);
  }
  return dn;
}

/** 時間の書き方を読む。2 / 2.5 / 2h / 2時間 / 2時間半 / 30分 / 1時間30分 */
function parseHours(tok) {
  var m;
  if ((m = tok.match(/^(\d+(?:\.\d+)?)(?:h|hr|hrs|時間|ｈ)?$/i))) return parseFloat(m[1]);
  if ((m = tok.match(/^(\d+)時間半$/))) return +m[1] + 0.5;
  if ((m = tok.match(/^(\d+)時間(\d+)分$/))) return +m[1] + (+m[2]) / 60;
  if ((m = tok.match(/^(\d+)分$/))) return (+m[1]) / 60;
  if (tok === '半日') return 4;
  return null;
}

/**
 * 1 行の作業を読む。
 *   「収穫 6 20ケース 3人」「剪定 西向き 2.5」「薬散2」「誘引 2回目 3時間 完了」
 * 返り値 {task, detail, hours, cases, people, done} / 読めなければ null
 */
function parseEntryLine(line) {
  var s = nfkc(line).replace(/[:：]/g, ' ').trim();
  if (!s) return null;
  var toks = s.split(/\s+/);
  // 「薬散2」「収穫6h」のように作業名と時間がくっついている場合
  var m0 = toks[0].match(/^([^\d\s]+?)(\d+(?:\.\d+)?)(h|時間)?$/i);
  if (m0 && toks[0].indexOf('回目') < 0) toks.splice(0, 1, m0[1], m0[2] + (m0[3] || ''));
  var task = toks.shift();
  if (!task || /^\d/.test(task)) return null;
  var e = { task: task, detail: '', hours: null, cases: null, people: null, done: false };
  var details = [];
  var bare = [];
  toks.forEach(function (t) {
    var m;
    if ((m = t.match(/^(\d+(?:\.\d+)?)(ケース|ｹｰｽ|cs|c|箱|ケ)$/i))) { e.cases = parseFloat(m[1]); return; }
    if ((m = t.match(/^(\d+)(人|名)$/))) { e.people = +m[1]; return; }
    if (DONE_WORDS.indexOf(t) >= 0) { e.done = true; return; }
    if (/^\d+(\.\d+)?$/.test(t)) { bare.push(parseFloat(t)); return; }
    var h = parseHours(t);
    // 「西向き2」「2回目3.5」のように詳細と時間がくっついている場合
    var mj = h === null && e.hours === null && t.match(/^(.*[\u3040-\u30FF\u4E00-\u9FFF])(\d.*)$/);
    var hj = mj ? parseHours(mj[2]) : null;
    if (hj !== null) { details.push(mj[1]); e.hours = hj; return; }
    if (h !== null && e.hours === null) { e.hours = h; return; }
    details.push(t);
  });
  // 単位のない数字：1つ目は時間。収穫なら 2つ目はケース数、3つ目は人数
  bare.forEach(function (n) {
    if (e.hours === null) e.hours = n;
    else if (task === HARVEST && e.cases === null) e.cases = n;
    else if (task === HARVEST && e.people === null) e.people = n;
    else details.push(String(n));
  });
  e.detail = details.join(' ');
  return e;
}

/**
 * メッセージ全体を読む。1 行 = 1 作業。
 * 日付だけの行（「昨日」「10/30」）はそれ以降の行の日付になる。
 * 行頭に日付を書いてもよい（「昨日 収穫 5」）。
 * 「備考 午後部会」「メモ ...」はその日の備考。
 */
function parseRecordMessage(text, todayDn) {
  var lines = String(text).split(/\r?\n/);
  var curDn = todayDn;
  var entries = [];
  var notes = [];
  var errors = [];
  lines.forEach(function (raw) {
    var line = nfkc(raw).trim();
    if (!line) return;
    var toks = line.split(/\s+/);
    var dn = parseDateWord(toks[0], todayDn, false);
    if (dn !== null) {
      curDn = dn;
      toks.shift();
      line = toks.join(' ');
      if (!line) return;
    }
    var nm = line.match(/^(備考|メモ)\s*(.*)$/);
    if (nm) {
      if (nm[2]) notes.push({ dn: curDn, note: nm[2] });
      return;
    }
    var e = parseEntryLine(line);
    if (!e) { errors.push(raw.trim()); return; }
    e.dn = curDn;
    entries.push(e);
  });
  return { entries: entries, notes: notes, errors: errors };
}

/** メッセージがコマンドかどうか見分ける */
function parseCommand(text) {
  var s = nfkc(String(text)).trim();
  var m;
  if (/^(ヘルプ|help|使い方|つかいかた|\?)$/i.test(s)) return { cmd: 'help' };
  if (/^(明日|あした|あす|指示|明日の指示|明日の作業)$/.test(s)) return { cmd: 'plan', offset: 1 };
  if (/^(今日の指示|今日の作業指示|きょうの指示)$/.test(s)) return { cmd: 'plan', offset: 0 };
  if (/^(今日|きょう|本日)$/.test(s)) return { cmd: 'day', offset: 0 };
  if (/^(昨日|きのう)$/.test(s)) return { cmd: 'day', offset: -1 };
  if (/^(取消|取り消し|とりけし|削除|戻す|もどす)$/.test(s)) return { cmd: 'undo' };
  if (/^(今週|週集計)$/.test(s)) return { cmd: 'summary', range: 'week' };
  if (/^(今月|集計|月集計)$/.test(s)) return { cmd: 'summary', range: 'month' };
  if (/^(先月)$/.test(s)) return { cmd: 'summary', range: 'lastmonth' };
  if ((m = s.match(/^予定(?:\s+(.*))?$/))) return { cmd: 'schedule', arg: (m[1] || '').trim() };
  if ((m = s.match(/^定植日\s*(?:は)?\s*(\S+)$/)) && /\d/.test(m[1])) return { cmd: 'plantdate', arg: m[1] };
  if ((m = s.match(/^通知\s*(.*)$/))) return { cmd: 'notify', arg: m[1].trim() };
  if ((m = s.match(/^場所\s*(.*)$/))) return { cmd: 'place', arg: m[1].trim() };
  if ((m = s.match(/^登録\s*(.*)$/))) return { cmd: 'register', arg: m[1].trim() };
  return null;
}

function entryLabel(e) {
  var s = e.task + (e.detail ? ' ' + e.detail : '');
  var extra = [];
  if (e.hours !== null && e.hours !== undefined) extra.push(fmtNum(e.hours) + 'h');
  if (e.cases !== null && e.cases !== undefined) extra.push(fmtNum(e.cases) + 'ケース');
  if (e.people !== null && e.people !== undefined) extra.push(e.people + '人');
  if (e.done) extra.push('完了');
  return s + (extra.length ? '  ' + extra.join(' ') : '');
}

function isDoneEntry(e) {
  if (e.done) return true;
  var t = (e.note || '') + ' ' + (e.detail || '');
  return DONE_WORDS.some(function (w) { return t.indexOf(w) >= 0; });
}

/** 作業ごとにまとめる。返り値 [{task, days, hours, details:{詳細:日数}, firstDn, lastDn}] */
function groupByTask(entries) {
  var map = {};
  entries.forEach(function (e) {
    if (!e.task) return;
    var g = map[e.task] || (map[e.task] = { task: e.task, daySet: {}, hours: 0, details: {}, firstDn: e.dn, lastDn: e.dn });
    g.daySet[e.dn] = true;
    g.hours += e.hours || 0;
    if (e.detail) {
      g.details[e.detail] = g.details[e.detail] || {};
      g.details[e.detail][e.dn] = true;
    }
    g.firstDn = Math.min(g.firstDn, e.dn);
    g.lastDn = Math.max(g.lastDn, e.dn);
  });
  return Object.keys(map).map(function (k) {
    var g = map[k];
    var det = {};
    Object.keys(g.details).forEach(function (d) { det[d] = Object.keys(g.details[d]).length; });
    return { task: g.task, days: Object.keys(g.daySet).length, hours: g.hours, details: det, firstDn: g.firstDn, lastDn: g.lastDn };
  });
}

function detailText(det, max) {
  var keys = Object.keys(det).sort(function (a, b) { return det[b] - det[a]; });
  if (!keys.length) return '';
  var shown = keys.slice(0, max || 3).map(function (k) { return k + (det[k] > 1 ? ' ' + det[k] + '日' : ''); });
  if (keys.length > (max || 3)) shown.push('ほか');
  return '（' + shown.join('・') + '）';
}

function firstDnOf(entries, task) {
  var best = null;
  entries.forEach(function (e) { if (e.task === task && (best === null || e.dn < best)) best = e.dn; });
  return best;
}

/**
 * 翌日（targetDn）の作業指示を組み立てる。
 * ctx = {
 *   records: 今作の作業[], lastYear: 昨年の作業[], schedules: [{dn, text}],
 *   settings: {thisPlantDn, lastPlantDn, intervalTasks:[...]},
 *   weather: {code, tmax, tmin, pop, wind} | null,
 *   todayDn
 * }
 */
function buildPlan(targetDn, ctx) {
  var rec = ctx.records || [];
  var ly = ctx.lastYear || [];
  var st = ctx.settings || {};
  var intervalTasks = st.intervalTasks || INTERVAL_TASKS_DEFAULT.split(',');
  var lines = [];
  lines.push('【' + fmtMD(targetDn) + ' の作業指示】');

  // --- 天気 ---
  if (ctx.weather) {
    var w = ctx.weather;
    var wl = '天気: ' + weatherName(w.code);
    if (w.tmax !== null && w.tmax !== undefined) wl += '  ' + fmtNum(w.tmax) + '℃/' + fmtNum(w.tmin) + '℃';
    if (w.pop !== null && w.pop !== undefined) wl += '  降水' + w.pop + '%';
    if (w.wind !== null && w.wind !== undefined) wl += '  風' + fmtNum(w.wind) + 'm/s';
    lines.push(wl);
    weatherHints(w).forEach(function (h) { lines.push('⚠ ' + h); });
  }

  // --- 昨年のどの日と比べるか ---
  var lyFirst = null, lyLast = null;
  ly.forEach(function (e) {
    if (lyFirst === null || e.dn < lyFirst) lyFirst = e.dn;
    if (lyLast === null || e.dn > lyLast) lyLast = e.dn;
  });
  var lastPlant = st.lastPlantDn || firstDnOf(ly, '定植');
  var thisPlant = st.thisPlantDn || null;
  if (!thisPlant && lastPlant) {
    var tp = firstDnOf(rec.filter(function (e) { return e.dn > lastPlant + 180; }), '定植');
    if (tp) thisPlant = tp;
  }
  var refDn = null, basis = '';
  if (ly.length) {
    if (thisPlant && lastPlant) {
      refDn = lastPlant + (targetDn - thisPlant);
      basis = '定植から' + (targetDn - thisPlant + 1) + '日目でそろえて比較';
    } else {
      var p = dnParts(targetDn);
      for (var back = 1; back <= 3; back++) {
        var cand = ymdToDn(p.y - back, p.m, p.d);
        if (cand >= lyFirst - 3 && cand <= lyLast + 3) { refDn = cand; break; }
      }
      basis = '昨年の同じ日付と比較';
    }
    if (refDn !== null && (refDn < lyFirst - 3 || refDn > lyLast + 3)) refDn = null;
  }

  var W = 3;
  var win = refDn === null ? [] : ly.filter(function (e) { return e.task && e.dn >= refDn - W && e.dn <= refDn + W; });
  var winGroups = groupByTask(win);
  var winDays = {};
  win.forEach(function (e) { winDays[e.dn] = (winDays[e.dn] || 0) + (e.hours || 0); });
  var winDayCount = Object.keys(winDays).length;

  var todo = [];
  var covered = {};

  // --- 1. 予定 ---
  var sch = (ctx.schedules || []).filter(function (s) { return s.dn === targetDn; });
  sch.forEach(function (s) { todo.push('📌 ' + s.text); });
  var nextSch = (ctx.schedules || []).filter(function (s) { return s.dn > targetDn && s.dn <= targetDn + 3; })
    .sort(function (a, b) { return a.dn - b.dn; });

  // --- 2. 収穫 ---
  var lyHarvest = win.filter(function (e) { return e.task === HARVEST; });
  var lyHarvestDays = {};
  lyHarvest.forEach(function (e) { lyHarvestDays[e.dn] = true; });
  var recentHarvest = rec.filter(function (e) { return e.task === HARVEST && e.dn < targetDn && e.dn >= targetDn - 7; });
  if (Object.keys(lyHarvestDays).length >= 2 || recentHarvest.length) {
    var parts = [];
    var rh = statsHarvest(recentHarvest);
    if (rh.days) parts.push('この1週間 平均' + fmtNum(rh.hours / rh.days) + 'h' + (rh.caseDays ? '・' + fmtNum(rh.cases / rh.caseDays) + 'ケース' : ''));
    var lh = statsHarvest(lyHarvest);
    if (lh.days) parts.push('昨年同時期 平均' + fmtNum(lh.hours / lh.days) + 'h' + (lh.caseDays ? '・' + fmtNum(lh.cases / lh.caseDays) + 'ケース' : ''));
    todo.push(HARVEST + (parts.length ? '（' + parts.join(' / ') + '）' : ''));
    covered[HARVEST] = true;
  } else if (refDn !== null) {
    // 昨年の収穫開始が近いか
    var lyHarvestStart = firstDnOf(ly, HARVEST);
    if (lyHarvestStart && lyHarvestStart > refDn && lyHarvestStart - refDn <= 10) {
      todo.push('もうすぐ収穫開始（昨年は ' + fmtMD(lyHarvestStart + (targetDn - refDn)) + ' ごろに相当する日から）');
    }
    covered[HARVEST] = true;
  }

  // --- 3. 定期作業（薬散など） ---
  var soon = [];
  intervalTasks.forEach(function (task) {
    task = task.trim();
    if (!task) return;
    var lyDays = uniqSorted(ly.filter(function (e) { return e.task === task; }).map(function (e) { return e.dn; }));
    var center = refDn === null ? null : refDn;
    var localIv = [];
    for (var i = 1; i < lyDays.length; i++) {
      if (center === null || Math.abs(lyDays[i] - center) <= 45) localIv.push(lyDays[i] - lyDays[i - 1]);
    }
    var iv = median(localIv);
    if (iv !== null) iv = Math.max(1, Math.round(iv));
    var lastThis = null;
    rec.forEach(function (e) { if (e.task === task && e.dn < targetDn && (lastThis === null || e.dn > lastThis)) lastThis = e.dn; });
    var inWin = win.some(function (e) { return e.task === task; });
    if (lastThis !== null && iv !== null) {
      var since = targetDn - lastThis;
      if (since >= iv) {
        todo.push(task + '（前回 ' + fmtMD(lastThis) + ' から' + since + '日。昨年のこの時期は約' + fmtNum(iv) + '日おき）');
      } else if (iv - since <= 2) {
        soon.push(task + ' は ' + fmtMD(lastThis + Math.round(iv)) + ' ごろ（前回 ' + fmtMD(lastThis) + '・約' + fmtNum(iv) + '日おき）');
      }
    } else if (lastThis === null && inWin) {
      todo.push(task + '（今作の記録がまだありません。昨年はこの時期にやっています' + (iv !== null ? '・約' + fmtNum(iv) + '日おき' : '') + '）');
    } else if (lastThis !== null && iv === null && inWin) {
      todo.push(task + '（昨年はこの時期にやっています。前回 ' + fmtMD(lastThis) + '）');
    }
    covered[task] = true;
  });

  // --- 4. つづきの作業（直近 3 日、完了していないもの） ---
  var recent = rec.filter(function (e) { return e.task && !covered[e.task] && e.dn < targetDn && e.dn >= targetDn - 3; });
  var doneKeys = {};
  rec.forEach(function (e) {
    if (e.task && isDoneEntry(e) && e.dn < targetDn && e.dn >= targetDn - 14) doneKeys[e.task + '|' + (e.detail || '')] = e.dn;
  });
  var contMap = {};
  recent.forEach(function (e) {
    var key = e.task + '|' + (e.detail || '');
    if (doneKeys[key] !== undefined && doneKeys[key] >= e.dn) return;
    if (isDoneEntry(e)) return;
    var c = contMap[key] || (contMap[key] = { task: e.task, detail: e.detail || '', days: {}, hours: 0 });
    c.days[e.dn] = true;
    c.hours += e.hours || 0;
  });
  Object.keys(contMap).map(function (k) { return contMap[k]; })
    .sort(function (a, b) { return b.hours - a.hours; })
    .slice(0, 4)
    .forEach(function (c) {
      todo.push(c.task + (c.detail ? ' ' + c.detail : '') + ' のつづき（直近' + Object.keys(c.days).length + '日で計' + fmtNum(c.hours) + 'h）');
      covered[c.task] = true;
    });

  // --- 5. 昨年の今ごろやっていて、まだ出ていない作業（今作で「完了」済みの作業は出さない） ---
  var lastDone = {}, lastOpen = {};
  rec.forEach(function (e) {
    if (!e.task || e.dn >= targetDn || e.dn < targetDn - 14) return;
    if (isDoneEntry(e)) lastDone[e.task] = Math.max(lastDone[e.task] || 0, e.dn);
    else lastOpen[e.task] = Math.max(lastOpen[e.task] || 0, e.dn);
  });
  Object.keys(lastDone).forEach(function (t) { if (!(lastOpen[t] > lastDone[t])) covered[t] = true; });
  winGroups
    .filter(function (g) { return !covered[g.task] && (g.days >= 2 || g.hours >= 2); })
    .sort(function (a, b) { return b.hours - a.hours; })
    .slice(0, 3)
    .forEach(function (g) {
      todo.push(g.task + detailText(g.details, 2) + '（昨年この時期 ' + g.days + '日・計' + fmtNum(g.hours) + 'h）');
      covered[g.task] = true;
    });

  lines.push('');
  lines.push('■ やること');
  if (todo.length) {
    todo.forEach(function (t, i) { lines.push((i + 1) + '. ' + t); });
  } else {
    lines.push('（材料が足りず提案できません。「昨年」シートに去年の日誌を入れると提案が出ます）');
  }

  if (soon.length || nextSch.length) {
    lines.push('');
    lines.push('■ この先');
    nextSch.forEach(function (s) { lines.push('・' + fmtMD(s.dn) + ' ' + s.text); });
    soon.forEach(function (s) { lines.push('・' + s); });
  }

  // --- まもなく始まる作業（昨年、4〜10 日後に始めたもの） ---
  if (refDn !== null) {
    var startedLater = groupByTask(ly.filter(function (e) { return e.task && e.dn > refDn + W && e.dn <= refDn + 10; }))
      .filter(function (g) {
        if (covered[g.task]) return false;
        // 直前 30 日に昨年やっていなかった作業＝新しく始まる作業
        return !ly.some(function (e) { return e.task === g.task && e.dn >= refDn - 30 && e.dn <= refDn + W; });
      })
      .sort(function (a, b) { return a.firstDn - b.firstDn; })
      .slice(0, 3);
    if (startedLater.length) {
      lines.push('');
      lines.push('■ そろそろ準備');
      startedLater.forEach(function (g) {
        lines.push('・' + g.task + detailText(g.details, 2) + '（昨年は' + (g.firstDn - refDn) + '日後から）');
      });
    }
  }

  // --- 参考: 昨年の今ごろ ---
  if (refDn !== null && winGroups.length) {
    lines.push('');
    lines.push('■ 昨年の今ごろ（' + fmtYMD(refDn - W) + '〜' + dnParts(refDn + W).m + '/' + dnParts(refDn + W).d + '）');
    winGroups.sort(function (a, b) { return b.hours - a.hours; }).slice(0, 6).forEach(function (g) {
      lines.push('・' + g.task + detailText(g.details, 3) + ' ' + g.days + '日 計' + fmtNum(g.hours) + 'h');
    });
    if (winDayCount) {
      var tot = 0;
      Object.keys(winDays).forEach(function (k) { tot += winDays[k]; });
      lines.push('1日あたり 約' + fmtNum(tot / winDayCount) + '時間（' + basis + '）');
    }
    var notes = ly.filter(function (e) { return e.note && e.dn >= refDn - W && e.dn <= refDn + W; })
      .map(function (e) { return dnParts(e.dn).m + '/' + dnParts(e.dn).d + ' ' + e.note; });
    if (notes.length) lines.push('昨年の備考: ' + uniq(notes).slice(0, 3).join('、'));
  } else if (!ly.length) {
    lines.push('');
    lines.push('※「昨年」シートが空です。去年の作業日誌を貼ると、時期に合った提案が出ます。');
  }

  // --- 今日の記録がまだ ---
  if (ctx.todayDn !== undefined && targetDn === ctx.todayDn + 1) {
    var todayCount = rec.filter(function (e) { return e.dn === ctx.todayDn && e.task; }).length;
    if (!todayCount) {
      lines.push('');
      lines.push('📝 今日（' + fmtMD(ctx.todayDn) + '）の作業記録がまだです。送ってください。');
    }
  }
  return truncate(lines.join('\n'));
}

function statsHarvest(list) {
  var days = {}, caseDays = {}, hours = 0, cases = 0;
  list.forEach(function (e) {
    days[e.dn] = true;
    hours += e.hours || 0;
    if (e.cases !== null && e.cases !== undefined && e.cases > 0) { cases += e.cases; caseDays[e.dn] = true; }
  });
  return { days: Object.keys(days).length, hours: hours, cases: cases, caseDays: Object.keys(caseDays).length };
}

function uniqSorted(a) {
  var s = {};
  a.forEach(function (x) { s[x] = true; });
  return Object.keys(s).map(Number).sort(function (x, y) { return x - y; });
}
function uniq(a) {
  var s = {}, out = [];
  a.forEach(function (x) { if (!s[x]) { s[x] = true; out.push(x); } });
  return out;
}
function truncate(t) {
  return t.length > MAX_LINE_TEXT ? t.slice(0, MAX_LINE_TEXT) + '\n…（長いので省略）' : t;
}

/** WMO 天気コード → 日本語（Open-Meteo の weather_code） */
function weatherName(code) {
  if (code === null || code === undefined) return '不明';
  if (code === 0) return '晴れ';
  if (code <= 2) return '晴れ時々くもり';
  if (code === 3) return 'くもり';
  if (code === 45 || code === 48) return '霧';
  if (code >= 51 && code <= 57) return '霧雨';
  if (code >= 61 && code <= 67) return '雨';
  if (code >= 71 && code <= 77) return '雪';
  if (code >= 80 && code <= 82) return 'にわか雨';
  if (code >= 85 && code <= 86) return 'にわか雪';
  if (code >= 95) return '雷雨';
  return '不明';
}
/** 天気からの注意（目安） */
function weatherHints(w) {
  var h = [];
  if (w.wind !== null && w.wind !== undefined && w.wind >= 10) h.push('強風の予報。天窓・サイドの閉め忘れ、ビニールのばたつきを確認');
  if (w.tmin !== null && w.tmin !== undefined && w.tmin <= 8) h.push('冷え込みの予報。暖房機・内張り（二重）の確認');
  if (w.tmax !== null && w.tmax !== undefined && w.tmax >= 30) h.push('高温の予報。換気と潅水を多めに');
  if (w.code !== null && w.code !== undefined && w.code >= 95) h.push('雷雨の予報。ハウスの開口部を確認');
  return h;
}

/** 期間の集計テキスト */
function buildSummary(entries, fromDn, toDn, title) {
  var list = entries.filter(function (e) { return e.task && e.dn >= fromDn && e.dn <= toDn; });
  var lines = ['【' + title + '（' + fmtMD(fromDn) + '〜' + fmtMD(toDn) + '）】'];
  if (!list.length) { lines.push('記録がありません'); return lines.join('\n'); }
  var total = 0;
  list.forEach(function (e) { total += e.hours || 0; });
  var days = uniqSorted(list.map(function (e) { return e.dn; })).length;
  lines.push('作業した日: ' + days + '日  合計 ' + fmtNum(total) + 'h');
  var h = statsHarvest(list.filter(function (e) { return e.task === HARVEST; }));
  if (h.days) lines.push('収穫: ' + h.days + '日 ' + fmtNum(h.hours) + 'h' + (h.caseDays ? ' / ' + fmtNum(h.cases) + 'ケース（記録' + h.caseDays + '日、1日平均' + fmtNum(h.cases / h.caseDays) + '）' : ''));
  lines.push('');
  groupByTask(list).sort(function (a, b) { return b.hours - a.hours; }).forEach(function (g) {
    lines.push('・' + g.task + ' ' + fmtNum(g.hours) + 'h（' + g.days + '日）' + detailText(g.details, 3));
  });
  return truncate(lines.join('\n'));
}

function buildDayText(entries, dn) {
  var list = entries.filter(function (e) { return e.dn === dn && (e.task || e.note); });
  var lines = ['【' + fmtMD(dn) + ' の記録】'];
  if (!list.length) { lines.push('まだありません'); return lines.join('\n'); }
  var total = 0;
  list.forEach(function (e) {
    if (e.task) { lines.push('・' + entryLabel(e)); total += e.hours || 0; }
    if (e.note) lines.push('　備考: ' + e.note);
  });
  lines.push('合計 ' + fmtNum(total) + 'h');
  return lines.join('\n');
}

function helpText() {
  return [
    '【使い方】',
    '■ 記録（1行に1つ。まとめて送れます）',
    '収穫 6 20ケース 3人',
    '剪定 西向き 2.5',
    '薬散 2',
    '誘引 2回目 3時間 完了',
    '備考 午後部会',
    '',
    '・作業名 → 詳細 → 時間 の順。時間は 2 / 2.5 / 2時間半 / 30分',
    '・ケース数は「20ケース」、人数は「3人」',
    '・別の日は先頭に日付：「昨日 収穫 5」「10/30」の行のあとに続けて書く',
    '・「完了」を付けると翌日の「つづき」に出なくなります',
    '',
    '■ 見る・直す',
    '明日 … 明日の作業指示',
    '今日 / 昨日 … その日の記録',
    '今週 / 今月 / 先月 … 集計',
    '取消 … 最後に送った記録を消す',
    '',
    '■ 設定',
    '予定 11/5 午後部会 … 予定を入れる（「予定」だけで一覧）',
    '定植日 8/28 … 今作の定植日（昨年と「定植から何日目」でそろえます）',
    '通知 19時 / 通知 オフ … 毎晩の指示の時刻',
    '場所 安芸市 … 天気予報の場所（「場所 33.51 133.90」でも可）'
  ].join('\n');
}

// ======================================================================
//  ここから下は Google（スプレッドシート・LINE・天気）とのやりとり
// ======================================================================

function props() { return PropertiesService.getScriptProperties(); }
function ss() { return SpreadsheetApp.getActiveSpreadsheet() || SpreadsheetApp.openById(props().getProperty('SHEET_ID')); }

function getSheet(name, header) {
  var s = ss().getSheetByName(name);
  if (!s) {
    s = ss().insertSheet(name);
    if (header) {
      s.getRange(1, 1, 1, header.length).setValues([header]).setFontWeight('bold').setBackground('#e8f5e9');
      s.setFrozenRows(1);
    }
  }
  return s;
}

function readEntries(name) {
  var s = ss().getSheetByName(name);
  if (!s || s.getLastRow() < 2) return [];
  var vals = s.getRange(1, 1, s.getLastRow(), Math.max(8, Math.min(s.getLastColumn(), 10))).getValues();
  return rowsToEntries(vals);
}

function readSettings() {
  var s = getSheet(SHEET_SETTINGS, ['項目', '値', '説明']);
  var vals = s.getLastRow() >= 2 ? s.getRange(2, 1, s.getLastRow() - 1, 2).getValues() : [];
  var o = {};
  vals.forEach(function (r) { if (r[0]) o[String(r[0]).trim()] = r[1]; });
  return o;
}
function writeSetting(key, value, note) {
  var s = getSheet(SHEET_SETTINGS, ['項目', '値', '説明']);
  var last = s.getLastRow();
  if (last >= 2) {
    var keys = s.getRange(2, 1, last - 1, 1).getValues();
    for (var i = 0; i < keys.length; i++) {
      if (String(keys[i][0]).trim() === key) { s.getRange(i + 2, 2).setValue(value); return; }
    }
  }
  s.appendRow([key, value, note || '']);
}

function planSettings(st) {
  return {
    thisPlantDn: cellToDn(st['今作の定植日']),
    lastPlantDn: cellToDn(st['昨年の定植日']),
    intervalTasks: String(st['定期作業'] || INTERVAL_TASKS_DEFAULT).split(/[,、]/)
  };
}

function readSchedules() {
  var s = getSheet(SHEET_SCHEDULE, ['日付', '内容', '入力日時']);
  if (s.getLastRow() < 2) return [];
  return s.getRange(2, 1, s.getLastRow() - 1, 2).getValues()
    .map(function (r) { return { dn: cellToDn(r[0]), text: String(r[1] || '').trim() }; })
    .filter(function (x) { return x.dn !== null && x.text; });
}

function todayDn() { return dayNum(new Date()); }

/** 翌日の指示テキスト */
function makePlanText(targetDn) {
  var st = readSettings();
  return buildPlan(targetDn, {
    records: readEntries(SHEET_RECORD),
    lastYear: readEntries(SHEET_LASTYEAR),
    schedules: readSchedules(),
    settings: planSettings(st),
    weather: fetchWeather(st, targetDn),
    todayDn: todayDn()
  });
}

// ---------- LINE ----------
function lineToken() {
  var t = props().getProperty('LINE_TOKEN');
  if (!t) throw new Error('LINE_TOKEN が未設定です（メニュー「LINE作業日誌 → 初期設定」）');
  return t;
}
function lineApi(path, payload, method) {
  var res = UrlFetchApp.fetch('https://api.line.me' + path, {
    method: method || 'post',
    contentType: 'application/json',
    headers: { Authorization: 'Bearer ' + lineToken() },
    payload: payload ? JSON.stringify(payload) : undefined,
    muteHttpExceptions: true
  });
  if (res.getResponseCode() >= 300) console.error('LINE API ' + path + ' ' + res.getResponseCode() + ' ' + res.getContentText());
  return res;
}
function reply(token, text) {
  lineApi('/v2/bot/message/reply', { replyToken: token, messages: [{ type: 'text', text: truncate(text) }] });
}
function push(to, text) {
  return lineApi('/v2/bot/message/push', { to: to, messages: [{ type: 'text', text: truncate(text) }] });
}
function displayName(userId) {
  try {
    var res = lineApi('/v2/bot/profile/' + userId, null, 'get');
    if (res.getResponseCode() === 200) return JSON.parse(res.getContentText()).displayName;
  } catch (err) { console.error(err); }
  return '';
}

function members() {
  try { return JSON.parse(props().getProperty('MEMBERS') || '[]'); } catch (e) { return []; }
}
function saveMembers(m) { props().setProperty('MEMBERS', JSON.stringify(m)); }
function findMember(userId) {
  return members().filter(function (m) { return m.id === userId; })[0] || null;
}

/** LINE からの受け口 */
function doPost(e) {
  try {
    var body = JSON.parse(e.postData.contents);
    (body.events || []).forEach(handleEvent);
  } catch (err) {
    console.error(err && err.stack || err);
  }
  return ContentService.createTextOutput('OK');
}
/** ブラウザで Web アプリの URL を開いたとき（動作確認用） */
function doGet() {
  return ContentService.createTextOutput('LINE作業日誌は動いています');
}

function handleEvent(ev) {
  if (ev.type === 'follow') {
    reply(ev.replyToken, '友だち追加ありがとうございます。\n最初に「登録 合言葉」と送ってください（合言葉はスプレッドシートの持ち主が決めたもの）。');
    return;
  }
  if (ev.type !== 'message' || !ev.message || ev.message.type !== 'text') return;
  var userId = ev.source && ev.source.userId;
  var text = ev.message.text;
  try {
    reply(ev.replyToken, handleText(userId, text));
  } catch (err) {
    console.error(err && err.stack || err);
    reply(ev.replyToken, 'エラーが起きました: ' + (err && err.message || err) + '\nもう一度送るか、「ヘルプ」と送ってください。');
  }
}

function handleText(userId, text) {
  var cmd = parseCommand(text);
  var me = findMember(userId);
  if (cmd && cmd.cmd === 'register') return cmdRegister(userId, cmd.arg);
  if (!me) return 'まだ登録されていません。「登録 合言葉」と送ってください。';
  if (!cmd) return cmdRecord(me, text);
  var t = todayDn();
  switch (cmd.cmd) {
    case 'help': return helpText();
    case 'plan': return makePlanText(t + cmd.offset);
    case 'day': return buildDayText(readEntries(SHEET_RECORD), t + cmd.offset);
    case 'undo': return cmdUndo(me);
    case 'summary': return cmdSummary(cmd.range);
    case 'schedule': return cmdSchedule(cmd.arg);
    case 'plantdate': return cmdPlantDate(cmd.arg);
    case 'notify': return cmdNotify(cmd.arg);
    case 'place': return cmdPlace(cmd.arg);
  }
  return helpText();
}

function cmdRegister(userId, pass) {
  var want = props().getProperty('PASSCODE');
  if (!want) return '合言葉がまだ決まっていません。スプレッドシートの「LINE作業日誌 → 初期設定」で決めてください。';
  if (findMember(userId)) return 'もう登録されています。「ヘルプ」で使い方を見られます。';
  if (nfkc(String(pass)) !== nfkc(String(want))) return '合言葉が違います。';
  var m = members();
  var name = displayName(userId) || ('メンバー' + (m.length + 1));
  m.push({ id: userId, name: name });
  saveMembers(m);
  return name + ' さんを登録しました。\n毎晩、翌日の作業指示が届きます。\n\n' + helpText();
}

function cmdRecord(me, text) {
  var t = todayDn();
  var parsed = parseRecordMessage(text, t);
  if (!parsed.entries.length && !parsed.notes.length) {
    return '読み取れませんでした。\n例:「収穫 6 20ケース 3人」「剪定 西向き 2.5」\n「ヘルプ」で使い方を表示します。';
  }
  var lock = LockService.getScriptLock();
  lock.waitLock(20000);
  try {
    var sheet = getSheet(SHEET_RECORD, HEADER);
    var seq = (parseInt(props().getProperty('SEQ') || '0', 10) || 0) + 1;
    props().setProperty('SEQ', String(seq));
    var stamp = Utilities.formatDate(new Date(), TZ, 'yyyy/MM/dd HH:mm:ss') + ' #' + seq; // 送信ごとに別の印（取消で使う）
    var known = {};
    readEntries(SHEET_LASTYEAR).concat(readEntries(SHEET_RECORD)).forEach(function (e) { if (e.task) known[e.task] = true; });
    var rows = parsed.entries.map(function (e) {
      return [dnToDate(e.dn), weekday(e.dn), e.task, e.detail, e.hours === null ? '' : Math.round(e.hours * 100) / 100,
        e.cases === null ? '' : e.cases, e.people === null ? '' : e.people, e.done ? '完了' : '', stamp, me.name];
    });
    parsed.notes.forEach(function (n) {
      rows.push([dnToDate(n.dn), weekday(n.dn), '', '', '', '', '', n.note, stamp, me.name]);
    });
    var start = sheet.getLastRow() + 1;
    sheet.getRange(start, 9, rows.length, 1).setNumberFormat('@'); // 入力日時は文字のまま（取消で照合する）
    sheet.getRange(start, 1, rows.length, HEADER.length).setValues(rows);
    sheet.getRange(start, 1, rows.length, 1).setNumberFormat('yyyy/mm/dd');
    props().setProperty('LAST_' + me.id, stamp);

    var days = uniqSorted(parsed.entries.map(function (e) { return e.dn; }).concat(parsed.notes.map(function (n) { return n.dn; })));
    var lines = ['✅ 記録しました（' + rows.length + '件）'];
    days.forEach(function (dn) {
      lines.push('');
      lines.push('■ ' + fmtMD(dn));
      parsed.entries.filter(function (e) { return e.dn === dn; }).forEach(function (e) { lines.push('・' + entryLabel(e)); });
      parsed.notes.filter(function (n) { return n.dn === dn; }).forEach(function (n) { lines.push('・備考 ' + n.note); });
    });
    var all = readEntries(SHEET_RECORD);
    days.forEach(function (dn) {
      var tot = 0;
      all.forEach(function (e) { if (e.dn === dn) tot += e.hours || 0; });
      lines.push(fmtMD(dn) + ' の合計 ' + fmtNum(tot) + 'h');
    });
    var warn = [];
    parsed.entries.forEach(function (e) {
      if (e.hours === null) warn.push('「' + e.task + '」に時間がありません');
      if (!known[e.task]) warn.push('「' + e.task + '」は初めての作業名です');
    });
    parsed.errors.forEach(function (x) { warn.push('読めなかった行: ' + x); });
    if (warn.length) lines.push('\n※ ' + uniq(warn).join('\n※ '));
    lines.push('（間違えたら「取消」）');
    return lines.join('\n');
  } finally {
    lock.releaseLock();
  }
}

function cmdUndo(me) {
  var stamp = props().getProperty('LAST_' + me.id);
  if (!stamp) return '取り消せる記録がありません。';
  var lock = LockService.getScriptLock();
  lock.waitLock(20000);
  try {
    var sheet = getSheet(SHEET_RECORD, HEADER);
    var last = sheet.getLastRow();
    if (last < 2) return '取り消せる記録がありません。';
    var from = Math.max(2, last - 99);
    var vals = sheet.getRange(from, 1, last - from + 1, HEADER.length).getDisplayValues();
    var removed = [];
    for (var i = vals.length - 1; i >= 0; i--) {
      if (vals[i][8] === stamp && vals[i][9] === me.name) {
        removed.push(vals[i][0] + ' ' + vals[i][2] + ' ' + vals[i][3] + ' ' + vals[i][4]);
        sheet.deleteRow(from + i);
      }
    }
    props().deleteProperty('LAST_' + me.id);
    if (!removed.length) return '取り消せる記録が見つかりませんでした（シートを直接直した場合など）。';
    return '🗑 取り消しました（' + removed.length + '件）\n・' + removed.reverse().join('\n・');
  } finally {
    lock.releaseLock();
  }
}

function cmdSummary(range) {
  var t = todayDn();
  var p = dnParts(t);
  var entries = readEntries(SHEET_RECORD);
  if (range === 'week') {
    var mondayOffset = (((t + 4) % 7) + 6) % 7; // 月曜始まり
    return buildSummary(entries, t - mondayOffset, t, '今週');
  }
  if (range === 'lastmonth') {
    var y = p.m === 1 ? p.y - 1 : p.y, m = p.m === 1 ? 12 : p.m - 1;
    return buildSummary(entries, ymdToDn(y, m, 1), ymdToDn(p.y, p.m, 1) - 1, y + '年' + m + '月');
  }
  return buildSummary(entries, ymdToDn(p.y, p.m, 1), t, p.m + '月');
}

function cmdSchedule(arg) {
  var t = todayDn();
  var sheet = getSheet(SHEET_SCHEDULE, ['日付', '内容', '入力日時']);
  if (!arg) {
    var list = readSchedules().filter(function (s) { return s.dn >= t; }).sort(function (a, b) { return a.dn - b.dn; }).slice(0, 15);
    if (!list.length) return '今後の予定はありません。\n「予定 11/5 午後部会」のように送ると登録します。';
    return '【今後の予定】\n' + list.map(function (s) { return '・' + fmtMD(s.dn) + ' ' + s.text; }).join('\n');
  }
  var toks = arg.split(/\s+/);
  var dn = parseDateWord(toks[0], t, true);
  if (dn === null || toks.length < 2) return '「予定 11/5 午後部会」のように、日付と内容を送ってください。';
  var textBody = toks.slice(1).join(' ');
  sheet.appendRow([dnToDate(dn), textBody, Utilities.formatDate(new Date(), TZ, 'yyyy/MM/dd HH:mm:ss')]);
  sheet.getRange(sheet.getLastRow(), 1).setNumberFormat('yyyy/mm/dd');
  return '📌 予定を入れました: ' + fmtMD(dn) + ' ' + textBody + '\n（前日の指示に出ます。消すときは「予定」シートの行を削除）';
}

function cmdPlantDate(arg) {
  var dn = parseDateWord(arg, todayDn(), false);
  if (dn === null) return '「定植日 8/28」のように送ってください。';
  writeSetting('今作の定植日', dnToDate(dn), '昨年の定植日とそろえて比べます');
  var ly = readEntries(SHEET_LASTYEAR);
  var lp = cellToDn(readSettings()['昨年の定植日']) || firstDnOf(ly, '定植');
  var msg = '今作の定植日を ' + fmtYMD(dn) + ' にしました。';
  if (lp) msg += '\n昨年の定植日 ' + fmtYMD(lp) + ' と「定植から何日目」でそろえて指示を出します（今日は ' + (todayDn() - dn + 1) + '日目）。';
  else msg += '\n昨年の定植日がわからないので、日付どうしで比べます（設定シートの「昨年の定植日」に入れると改善）。';
  return msg;
}

function cmdNotify(arg) {
  var a = nfkc(arg);
  if (/^(オフ|off|停止|なし|止める)$/i.test(a)) {
    removeTriggers();
    writeSetting('通知時刻', 'オフ', '毎晩の指示を送る時刻（時）');
    return '毎晩の指示を止めました。「通知 19時」で再開します。';
  }
  var m = a.match(/^(\d{1,2})(?:時|:00)?$/);
  if (!m || +m[1] > 23) return '「通知 19時」のように送ってください（止めるときは「通知 オフ」）。';
  installTrigger(+m[1]);
  writeSetting('通知時刻', +m[1], '毎晩の指示を送る時刻（時）');
  return '毎日 ' + (+m[1]) + '時ごろに翌日の指示を送ります（その時間帯の0〜59分のどこかで届きます）。';
}

function cmdPlace(arg) {
  var a = nfkc(arg).trim();
  if (!a) return '「場所 安芸市」または「場所 33.51 133.90」（緯度 経度）のように送ってください。';
  if (/^(オフ|off|なし)$/i.test(a)) {
    writeSetting('緯度', '', ''); writeSetting('経度', '', '');
    return '天気予報を止めました。';
  }
  var m = a.match(/^(-?\d+(?:\.\d+)?)[\s,]+(-?\d+(?:\.\d+)?)$/);
  var lat, lon, label = a;
  if (m) { lat = +m[1]; lon = +m[2]; }
  else {
    var g = geocode(a) || geocode(a.replace(/(市|町|村|区)$/, ''));
    if (!g) return '「' + a + '」が見つかりませんでした。「場所 33.51 133.90」のように緯度・経度で送ってください（Google マップで地点を長押しすると表示されます）。';
    lat = g.latitude; lon = g.longitude; label = g.name + (g.admin1 ? '（' + g.admin1 + '）' : '');
  }
  writeSetting('緯度', lat, '天気予報の場所');
  writeSetting('経度', lon, '天気予報の場所');
  writeSetting('場所の名前', label, '');
  return '天気予報の場所を ' + label + '（' + lat + ', ' + lon + '）にしました。';
}

// ---------- 天気（Open-Meteo・無料・キー不要） ----------
function geocode(name) {
  try {
    var url = 'https://geocoding-api.open-meteo.com/v1/search?count=1&language=ja&name=' + encodeURIComponent(name);
    var res = UrlFetchApp.fetch(url, { muteHttpExceptions: true });
    if (res.getResponseCode() !== 200) return null;
    var j = JSON.parse(res.getContentText());
    var r = (j.results || []).filter(function (x) { return x.country_code === 'JP'; })[0] || (j.results || [])[0];
    return r || null;
  } catch (e) { console.error(e); return null; }
}

function fetchWeather(st, targetDn) {
  var lat = toNum(st['緯度']), lon = toNum(st['経度']);
  if (lat === null || lon === null) return null;
  try {
    var url = 'https://api.open-meteo.com/v1/forecast?latitude=' + lat + '&longitude=' + lon +
      '&daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,wind_speed_10m_max' +
      '&wind_speed_unit=ms&timezone=Asia%2FTokyo&forecast_days=7';
    var res = UrlFetchApp.fetch(url, { muteHttpExceptions: true });
    if (res.getResponseCode() !== 200) return null;
    var d = JSON.parse(res.getContentText()).daily;
    var p = dnParts(targetDn);
    var key = p.y + '-' + ('0' + p.m).slice(-2) + '-' + ('0' + p.d).slice(-2);
    var i = d.time.indexOf(key);
    if (i < 0) return null;
    return {
      code: d.weather_code[i], tmax: d.temperature_2m_max[i], tmin: d.temperature_2m_min[i],
      pop: d.precipitation_probability_max ? d.precipitation_probability_max[i] : null,
      wind: d.wind_speed_10m_max ? d.wind_speed_10m_max[i] : null
    };
  } catch (e) { console.error(e); return null; }
}

// ---------- 毎晩の自動送信 ----------
function sendDailyPlan() {
  var m = members();
  if (!m.length) return;
  var text = makePlanText(todayDn() + 1);
  m.forEach(function (x) { push(x.id, text); });
  try { updateMonthlySummary(); } catch (e) { console.error(e); }
}

function removeTriggers() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'sendDailyPlan') ScriptApp.deleteTrigger(t);
  });
}
function installTrigger(hour) {
  removeTriggers();
  ScriptApp.newTrigger('sendDailyPlan').timeBased().everyDays(1).atHour(hour).inTimezone(TZ).create();
}

// ---------- 月別集計シート ----------
function updateMonthlySummary() {
  var entries = readEntries(SHEET_RECORD).filter(function (e) { return e.task; });
  var sheet = getSheet(SHEET_SUMMARY, null);
  sheet.clear();
  if (!entries.length) { sheet.getRange(1, 1).setValue('記録がまだありません'); return; }
  var months = {}, tasks = {}, cell = {}, cases = {};
  entries.forEach(function (e) {
    var p = dnParts(e.dn);
    var mk = p.y + '/' + ('0' + p.m).slice(-2);
    months[mk] = true;
    tasks[e.task] = (tasks[e.task] || 0) + (e.hours || 0);
    cell[mk + '|' + e.task] = (cell[mk + '|' + e.task] || 0) + (e.hours || 0);
    if (e.task === HARVEST && e.cases) cases[mk] = (cases[mk] || 0) + e.cases;
  });
  var ms = Object.keys(months).sort();
  var ts = Object.keys(tasks).sort(function (a, b) { return tasks[b] - tasks[a]; });
  var rows = [['作業（時間）'].concat(ms).concat(['合計'])];
  ts.forEach(function (t) {
    var r = [t], sum = 0;
    ms.forEach(function (mk) { var v = cell[mk + '|' + t] || 0; sum += v; r.push(v ? Math.round(v * 10) / 10 : ''); });
    r.push(Math.round(sum * 10) / 10);
    rows.push(r);
  });
  var totRow = ['合計'], grand = 0;
  ms.forEach(function (mk) {
    var s = 0;
    ts.forEach(function (t) { s += cell[mk + '|' + t] || 0; });
    grand += s;
    totRow.push(Math.round(s * 10) / 10);
  });
  totRow.push(Math.round(grand * 10) / 10);
  rows.push(totRow);
  var caseRow = ['収穫ケース数'], cg = 0;
  ms.forEach(function (mk) { cg += cases[mk] || 0; caseRow.push(cases[mk] || ''); });
  caseRow.push(cg || '');
  rows.push(caseRow);
  sheet.getRange(1, 1, rows.length, rows[0].length).setValues(rows);
  sheet.getRange(1, 1, 1, rows[0].length).setFontWeight('bold').setBackground('#e8f5e9');
  sheet.getRange(rows.length - 1, 1, 2, rows[0].length).setFontWeight('bold');
  sheet.setFrozenRows(1);
  sheet.setFrozenColumns(1);
}

// ---------- スプレッドシートのメニュー ----------
function onOpen() {
  SpreadsheetApp.getUi().createMenu('LINE作業日誌')
    .addItem('初期設定（LINEの鍵・合言葉・通知時刻）', 'setupWizard')
    .addItem('明日の指示をここで確認', 'previewPlan')
    .addItem('明日の指示を今すぐLINEに送る', 'sendDailyPlan')
    .addItem('月別集計を更新', 'updateMonthlySummary')
    .addItem('登録メンバーを見る', 'showMembers')
    .addToUi();
}

function setupSheets() {
  var book = ss();
  book.setSpreadsheetTimeZone(TZ);
  props().setProperty('SHEET_ID', book.getId());
  var rec = getSheet(SHEET_RECORD, HEADER);
  rec.setColumnWidth(1, 100); rec.setColumnWidth(3, 110); rec.setColumnWidth(4, 200); rec.setColumnWidth(8, 160);
  var ly = getSheet(SHEET_LASTYEAR, HEADER.slice(0, 8));
  if (ly.getLastRow() < 2) ly.getRange(2, 1).setNote('ここに去年の作業日誌（Excel）を、見出しを1行目にしてそのまま貼り付けてください');
  getSheet(SHEET_SCHEDULE, ['日付', '内容', '入力日時']);
  var st = readSettings();
  if (st['定期作業'] === undefined) writeSetting('定期作業', INTERVAL_TASKS_DEFAULT, '間隔で指示する作業（カンマ区切り）');
  if (st['今作の定植日'] === undefined) writeSetting('今作の定植日', '', 'LINEで「定植日 8/28」でも設定可');
  if (st['昨年の定植日'] === undefined) writeSetting('昨年の定植日', '', '空なら「昨年」シートの「定植」の日を使う');
  if (st['緯度'] === undefined) writeSetting('緯度', '', 'LINEで「場所 安芸市」でも設定可');
  if (st['経度'] === undefined) writeSetting('経度', '', '');
  var sheet1 = book.getSheetByName('シート1') || book.getSheetByName('Sheet1');
  if (sheet1 && sheet1.getLastRow() === 0 && book.getSheets().length > 1) book.deleteSheet(sheet1);
}

function setupWizard() {
  var ui = SpreadsheetApp.getUi();
  setupSheets();
  var r1 = ui.prompt('初期設定 1/3', 'LINE Developers の「チャネルアクセストークン（長期）」を貼り付けてください\n（設定済みでそのままにする場合は空のままOK）', ui.ButtonSet.OK_CANCEL);
  if (r1.getSelectedButton() !== ui.Button.OK) return;
  if (r1.getResponseText().trim()) props().setProperty('LINE_TOKEN', r1.getResponseText().trim());
  var r2 = ui.prompt('初期設定 2/3', 'LINE で登録するときの「合言葉」を決めてください（例: なす2026）\n現在: ' + (props().getProperty('PASSCODE') || '未設定'), ui.ButtonSet.OK_CANCEL);
  if (r2.getSelectedButton() !== ui.Button.OK) return;
  if (r2.getResponseText().trim()) props().setProperty('PASSCODE', r2.getResponseText().trim());
  var r3 = ui.prompt('初期設定 3/3', '毎晩、翌日の指示を送る時刻（0〜23 の数字。初期値 19）', ui.ButtonSet.OK_CANCEL);
  if (r3.getSelectedButton() !== ui.Button.OK) return;
  var h = parseInt(nfkc(r3.getResponseText()), 10);
  if (isNaN(h) || h < 0 || h > 23) h = 19;
  installTrigger(h);
  writeSetting('通知時刻', h, '毎晩の指示を送る時刻（時）');
  ui.alert('設定しました。\n\n次に「デプロイ → 新しいデプロイ → ウェブアプリ」で URL を作り、LINE Developers の Webhook URL に貼ってください（使い方.md の手順 4）。');
}

function previewPlan() {
  var text = makePlanText(todayDn() + 1);
  SpreadsheetApp.getUi().alert(text);
}

function showMembers() {
  var m = members();
  SpreadsheetApp.getUi().alert(m.length ? m.map(function (x) { return '・' + x.name; }).join('\n') +
    '\n\n（外したい人がいる場合は、Apps Script の「プロジェクトの設定 → スクリプト プロパティ」の MEMBERS を編集）' : 'まだ誰も登録されていません');
}
