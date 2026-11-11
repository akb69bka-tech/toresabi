/* 台風進路予測システム — Service Worker
   - アプリ本体（HTML / Leaflet / アイコン）は事前キャッシュしてオフラインでも起動できるようにする
   - 地図タイルは stale-while-revalidate（上限つき）で、見た範囲はオフラインでも表示
   - 数値予報・気象庁JSONは network-first。通信できないときは最後に取れた応答を返す
   バージョンを変えると古いキャッシュは自動で削除される。 */
const VERSION = 'v2.0.0';
const SHELL_CACHE = `shell-${VERSION}`;
const TILE_CACHE  = 'tiles-v1';
const DATA_CACHE  = 'data-v1';
const FONT_CACHE  = 'fonts-v1';
const TILE_LIMIT  = 600;

const SHELL = [
    './',
    './index.html',
    './manifest.webmanifest',
    './vendor/leaflet/leaflet.js',
    './vendor/leaflet/leaflet.css',
    './vendor/leaflet/images/marker-icon.png',
    './vendor/leaflet/images/marker-icon-2x.png',
    './vendor/leaflet/images/marker-shadow.png',
    './vendor/leaflet/images/layers.png',
    './vendor/leaflet/images/layers-2x.png',
    './icons/icon.svg',
    './icons/icon-192.png',
    './icons/icon-512.png',
    './icons/icon-maskable-512.png'
];

self.addEventListener('install', e => {
    e.waitUntil(caches.open(SHELL_CACHE).then(c => c.addAll(SHELL)));
});

self.addEventListener('activate', e => {
    e.waitUntil((async () => {
        const keep = new Set([SHELL_CACHE, TILE_CACHE, DATA_CACHE, FONT_CACHE]);
        for (const k of await caches.keys()) if (!keep.has(k)) await caches.delete(k);
        await self.clients.claim();
    })());
});

self.addEventListener('message', e => {
    if (e.data && e.data.type === 'SKIP_WAITING') self.skipWaiting();
});

const isTile = u => /basemaps\.cartocdn\.com|arcgisonline\.com/.test(u.hostname);
const isData = u => /open-meteo\.com|jma\.go\.jp/.test(u.hostname);
const isFont = u => /fonts\.(googleapis|gstatic)\.com/.test(u.hostname);

/* キャッシュ数の上限を守る（古いものから消す） */
async function trim(name, limit){
    const c = await caches.open(name);
    const keys = await c.keys();
    if (keys.length <= limit) return;
    for (const k of keys.slice(0, keys.length - limit)) await c.delete(k);
}

async function staleWhileRevalidate(req, name, limit){
    const c = await caches.open(name);
    const hit = await c.match(req);
    const net = fetch(req).then(res => {
        if (res && (res.ok || res.type === 'opaque')){
            c.put(req, res.clone());
            if (limit) trim(name, limit);
        }
        return res;
    }).catch(() => null);
    return hit || (await net) || Response.error();
}

async function networkFirst(req, name){
    const c = await caches.open(name);
    try{
        const res = await fetch(req);
        if (res && res.ok) c.put(req, res.clone());
        return res;
    }catch(err){
        const hit = await c.match(req, { ignoreSearch: false });
        if (hit) return hit;
        throw err;
    }
}

self.addEventListener('fetch', e => {
    const req = e.request;
    if (req.method !== 'GET') return;
    const url = new URL(req.url);

    if (isTile(url)){ e.respondWith(staleWhileRevalidate(req, TILE_CACHE, TILE_LIMIT)); return; }
    if (isData(url)){ e.respondWith(networkFirst(req, DATA_CACHE)); return; }
    if (isFont(url)){ e.respondWith(staleWhileRevalidate(req, FONT_CACHE)); return; }

    if (url.origin === location.origin){
        /* アプリ本体：キャッシュ優先。ナビゲーション要求は index.html にフォールバック */
        e.respondWith((async () => {
            const c = await caches.open(SHELL_CACHE);
            const hit = await c.match(req, { ignoreSearch: true });
            if (hit) return hit;
            try{
                const res = await fetch(req);
                if (res && res.ok) c.put(req, res.clone());
                return res;
            }catch(err){
                if (req.mode === 'navigate') return (await c.match('./index.html')) || Response.error();
                throw err;
            }
        })());
    }
});
