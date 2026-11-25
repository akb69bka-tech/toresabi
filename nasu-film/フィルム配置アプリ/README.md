# 安芸ナス フィルム配置アプリ

200×300mm の実寸フィルムの上で、デザインの部品を動かすためのページです。右上でデザインを切り替えます。

- **C 窓あき「高知県産」（新・最初に開く）**：中のナスが見えるよう、印刷を上の帯（高知県産）と下（産地・サイズ）に集めたデザイン。真ん中は透明の窓。サイズ表示は L・3本入 と M・5本入 を部品の目のマークで切り替え。
- **B 金の筆「安芸ナス」**：元の画像をそのまま部品に分けたもの。

- 公開版：https://claude.ai/artifact/XENXQrATfifRnabucU39hA
- 手元で使うとき：このフォルダで `python3 -m http.server` を実行し、ブラウザで `index.html` を開きます。

## 中身
- `index.html`：配置エディタの本体です。
- `parts.json`：デザインBの8つの部品の線データです。mm単位で、各部品の中心を基準にしています。
- `parts_c.json`：デザインCの部品です。`make_parts_c.py` で作りました（フォント：Dela Gothic One、Yuji Syuku、Noto Serif JP、Zen Maru Gothic、Inter。いずれも SIL Open Font License）。
- `ref.jpg`：見比べ用の元画像です。

## Illustratorでの使い方
1. 「Illustrator 用（SVG）」で書き出したファイルを Illustrator で開きます。
2. 「別名で保存」で .ai にします。

書き出したファイルは次のようになっています。
- 大きさは 200mm × 300mm の実寸です。
- 文字はすべて線のデータに変換済みです（アウトライン化）。
- グループは「白版」「2色目の版」「ガイド_印刷しない」の3つです。
