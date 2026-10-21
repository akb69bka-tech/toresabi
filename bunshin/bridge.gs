/**
 * 分身ノート ブリッジ（Google Apps Script）
 * ------------------------------------------------------------
 * 自分の Google アカウントに置く小さな中継所。分身ノート（index.html）から
 * 呼ばれて、次のことをします。
 *   save  : 記録をスプレッドシートに追記する
 *   gmail : 自分の Gmail を検索して、件名・差出人・冒頭を返す
 *   line  : LINE 公式アカウント（Messaging API）で自分に通知を送る
 *   ping  : 設定の確認
 *
 * 置き方（詳しくは README.md）
 *   1. https://script.google.com/ → 新しいプロジェクト → このファイルの中身を貼る
 *   2. 左の「プロジェクトの設定」→「スクリプト プロパティ」に次を追加
 *        TOKEN                      … 好きな合言葉（分身ノート側にも同じものを入れる）
 *        SHEET_ID                   … 任意。空なら初回同期時に「分身ノート_記録」を自動作成
 *        LINE_CHANNEL_ACCESS_TOKEN  … 任意。LINE 通知を使うとき
 *        LINE_USER_ID               … 任意。通知を受け取る自分のユーザーID（U で始まる）
 *   3. 「デプロイ」→「新しいデプロイ」→ 種類「ウェブアプリ」
 *        次のユーザーとして実行: 自分 ／ アクセスできるユーザー: 全員
 *      → 表示された URL（/exec で終わる）を分身ノートの「連携」タブに貼る
 *
 * 注意
 *   ・「全員」にしますが、TOKEN が合わない呼び出しは何もしません。TOKEN は長めに。
 *   ・Gmail を読む権限は、最初に実行したときに承認画面が出ます（自分のアカウントだけ）。
 *   ・コードを直したら、「デプロイを管理」→ 鉛筆 → バージョン「新バージョン」で更新が必要です。
 */

var PROPS = PropertiesService.getScriptProperties();
function prop_(k){ return (PROPS.getProperty(k) || '').trim(); }
function json_(obj){ return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON); }

function doGet(e){ return json_({ ok: true, app: 'bunshin-bridge', hint: '分身ノートからは POST で呼びます' }); }

function doPost(e){
  var req;
  try { req = JSON.parse((e && e.postData && e.postData.contents) || '{}'); } catch (err) { return json_({ ok: false, error: 'JSON が読めません' }); }
  var token = prop_('TOKEN');
  if (!token) return json_({ ok: false, error: 'ブリッジ側の TOKEN が未設定です（スクリプト プロパティ）' });
  if (req.token !== token) return json_({ ok: false, error: '合言葉（TOKEN）が違います' });
  try {
    switch (req.action) {
      case 'ping':  return json_({ ok: true, sheet: !!prop_('SHEET_ID'), gmail: canGmail_(), line: !!(prop_('LINE_CHANNEL_ACCESS_TOKEN') && prop_('LINE_USER_ID')) });
      case 'save':  return json_({ ok: true, saved: saveNotes_(req.notes || []) });
      case 'gmail': return json_({ ok: true, mails: searchGmail_(req.query, req.max) });
      case 'line':  return json_(pushLine_(req.text || ''));
      default:      return json_({ ok: false, error: '不明な action: ' + req.action });
    }
  } catch (err) {
    return json_({ ok: false, error: String(err && err.message || err) });
  }
}

/* ---------- スプレッドシート保存 ---------- */
var HEADER = ['id', '日時', '種類', '内容', '結論・結果', 'タグ', '取り込み元', '完了', '期限', '同期日時'];
function sheet_(){
  var id = prop_('SHEET_ID'), ss;
  if (id) { ss = SpreadsheetApp.openById(id); }
  else { ss = SpreadsheetApp.create('分身ノート_記録'); PROPS.setProperty('SHEET_ID', ss.getId()); }
  var sh = ss.getSheetByName('記録') || ss.insertSheet('記録');
  if (sh.getLastRow() === 0) { sh.appendRow(HEADER); sh.setFrozenRows(1); }
  return sh;
}
function saveNotes_(notes){
  if (!notes.length) return 0;
  var sh = sheet_();
  var ids = {}; // 既存 id → 行番号（更新に対応）
  var last = sh.getLastRow();
  if (last > 1) sh.getRange(2, 1, last - 1, 1).getValues().forEach(function(r, i){ if (r[0]) ids[String(r[0])] = i + 2; });
  var now = Utilities.formatDate(new Date(), Session.getScriptTimeZone(), 'yyyy/MM/dd HH:mm');
  var appendRows = [];
  notes.forEach(function(n){
    var row = [n.id, n.date, n.type, n.body, n.result, n.tags, n.source, n.done, n.due, now];
    if (ids[n.id]) sh.getRange(ids[n.id], 1, 1, row.length).setValues([row]);
    else appendRows.push(row);
  });
  if (appendRows.length) sh.getRange(sh.getLastRow() + 1, 1, appendRows.length, HEADER.length).setValues(appendRows);
  return notes.length;
}

/* ---------- Gmail 検索 ---------- */
function canGmail_(){ try { GmailApp.getInboxUnreadCount(); return true; } catch (e) { return false; } }
function searchGmail_(query, max){
  var q = (query || 'newer_than:7d').trim();
  var n = Math.min(Math.max(parseInt(max, 10) || 20, 1), 50);
  var threads = GmailApp.search(q, 0, n);
  return threads.map(function(t){
    var msgs = t.getMessages(), m = msgs[msgs.length - 1];
    var body = '';
    try { body = m.getPlainBody(); } catch (e) {}
    body = body.replace(/\r/g, '').replace(/\n{3,}/g, '\n\n').trim();
    return {
      id: m.getId(),
      from: m.getFrom(),
      subject: m.getSubject() || '(件名なし)',
      date: Utilities.formatDate(m.getDate(), Session.getScriptTimeZone(), 'yyyy/MM/dd HH:mm'),
      snippet: body.slice(0, 400),
      link: 'https://mail.google.com/mail/u/0/#all/' + t.getId()
    };
  });
}

/* ---------- LINE 通知（Messaging API push） ---------- */
function pushLine_(text){
  var token = prop_('LINE_CHANNEL_ACCESS_TOKEN'), to = prop_('LINE_USER_ID');
  if (!token || !to) return { ok: false, error: 'LINE_CHANNEL_ACCESS_TOKEN と LINE_USER_ID をスクリプト プロパティに設定してください' };
  if (!text.trim()) return { ok: false, error: '送る文章がありません' };
  var res = UrlFetchApp.fetch('https://api.line.me/v2/bot/message/push', {
    method: 'post',
    contentType: 'application/json',
    headers: { Authorization: 'Bearer ' + token },
    payload: JSON.stringify({ to: to, messages: [{ type: 'text', text: text.slice(0, 5000) }] }),
    muteHttpExceptions: true
  });
  var code = res.getResponseCode();
  if (code >= 200 && code < 300) return { ok: true };
  return { ok: false, error: 'LINE API ' + code + ': ' + res.getContentText().slice(0, 200) };
}

/* エディタから手で実行して、承認とシート作成を済ませるための関数 */
function setupTest(){
  var sh = sheet_();
  Logger.log('シート: ' + sh.getParent().getUrl());
  Logger.log('Gmail: ' + (canGmail_() ? 'OK' : 'NG'));
}
