// 使い方: node farm/tools/html_to_png.js <html> [幅] [高さ]  → 同じ名前の .png と .pdf を出す
const { chromium } = require('playwright');
const path = require('path');
(async () => {
  const src = path.resolve(process.argv[2]);
  const w = +(process.argv[3] || 1600), h = +(process.argv[4] || 1131);
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: w, height: h }, deviceScaleFactor: 2 });
  await page.goto('file://' + src);
  await page.screenshot({ path: src.replace(/\.html$/, '.png') });
  await page.pdf({ path: src.replace(/\.html$/, '.pdf'), width: w + 'px', height: h + 'px', printBackground: true });
  await browser.close();
})();
