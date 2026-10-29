# 農園AIエージェント栽培チーム

記録係（人間）の下に、栽培記録担当の部署を置く。定義は `.claude/agents/`。

| 役 | 名前 | エージェント | 仕事 |
|---|---|---|---|
| 部署長（栽培データまとめ） | 芽吹（めぶき） | `saibai-kiroku-bucho` | 栽培データ整理・全体の取りまとめ |
| 売上 | 稔（みのる） | `uriage-tanto` | 売上集計 |
| ハウス環境 | 陽（はる） | `house-kankyo-tanto` | 環境データ整理 |
| アグリハーブ | 薫（かおる） | `agriherb-tanto` | アグリハーブ栽培データ整理 |
| 栽培方針 | 凪（なぎ） | `saibai-hoshin-tanto` | ハウス1・2・3・5の温度から方針案 |
| 議事録 | 言葉（ことは） | `gijiroku-tanto` | ミーティング文字起こし→方針・指針・日程表 |

場所の呼び方（5連・丘・坂など）と「迷ったら取る／取らない」の判断ルールは `farm/glossary.md` に書く。言い方が増えたらここに追記する。

## 毎日の流れ
1. ミーティング音声を文字起こしして `farm/meetings/raw/YYYY-MM-DD.txt` に置く（またはチャットに貼る）
2. 「議事録担当で今日の議事録を作って」と依頼 → `farm/meetings/YYYY-MM-DD.html` と `.pdf`（状況の分析つき）と `.md`
3. 各データは `farm/records/{cultivation,sales,house-env,agriherb}/` に置き、部署長に「今日の分をまとめて」と依頼

## 注意
- サブエージェント同士は直接呼び合えないため、部署長は「誰に何を渡すか」を示し、実際の呼び出しはメインのClaudeが行う。
- 音声ファイルそのものはClaudeが直接聞けないため、文字起こし（スマホの録音文字起こし、Whisper等）が先に必要。
- PDFは `node farm/tools/html_to_pdf.js farm/meetings/YYYY-MM-DD.html` で作る（Playwright使用）。
- チーム構成図は `farm/diagram/team-chart.html`（PNG・PDFも同じ場所）。作り直しは `node farm/tools/html_to_png.js farm/diagram/team-chart.html`。
