// 分身ノート: オフラインで開けるようにアプリ本体をキャッシュする
const VERSION = 'bunshin-v1';
const SHELL = ['./', './index.html', './manifest.webmanifest', './icon-192.png', './icon-512.png', './icon-maskable-512.png', './apple-touch-icon.png'];
const FONT = 'bunshin-font-v1';

self.addEventListener('install', e => {
  e.waitUntil(caches.open(VERSION).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys()
    .then(keys => Promise.all(keys.filter(k => k !== VERSION && k !== FONT).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});
self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  // ページ本体（共有シートから ?text=... 付きで開かれることもある）: まずネット、だめならキャッシュ
  if (req.mode === 'navigate'){
    e.respondWith(fetch(req).then(res => { const copy = res.clone(); caches.open(VERSION).then(c => c.put('./index.html', copy)); return res; })
      .catch(() => caches.match('./index.html')));
    return;
  }
  if (/fonts\.(googleapis|gstatic)\.com$/.test(url.hostname)){
    e.respondWith(caches.open(FONT).then(c => c.match(req).then(hit => hit || fetch(req).then(res => { c.put(req, res.clone()); return res; }))));
    return;
  }
  if (url.origin === self.location.origin){
    e.respondWith(caches.match(req, { ignoreSearch: true }).then(hit => hit || fetch(req)));
  }
});
