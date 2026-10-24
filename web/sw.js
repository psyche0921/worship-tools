/* 서비스 워커: '앱으로 설치'가 뜨게 하려면 fetch 처리가 있는 워커가 있어야 한다.

   캐시는 '네트워크 우선'이다. 배포하면 바로 새 화면이 보이고, 인터넷이 없을 때만
   마지막으로 받은 것을 보여 준다. API 응답과 로그인은 캐시하지 않는다. */
const CACHE = 'bbc-v2';
// 설치할 때 미리 받아 두는 것(아이콘·설정). 화면(index.html)은 늘 새로 받는다.
const PRECACHE = ['/icon.svg', '/icon-192.png', '/icon-512.png', '/apple-touch-icon.png',
                  '/favicon.ico', '/manifest.webmanifest'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(PRECACHE)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', e => {
  e.waitUntil(caches.keys()
    .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});

function skip(url){
  // 다른 사이트(구글 로그인), API, 로그인 처리는 건드리지 않는다.
  return url.origin !== self.location.origin
      || url.pathname.startsWith('/api/') || url.pathname.startsWith('/auth/');
}

self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  if(e.request.method !== 'GET' || skip(url)) return;
  e.respondWith((async () => {
    try{
      // 브라우저 HTTP 캐시도 건너뛰고 서버에 확인한다(바뀌지 않았으면 304라 가볍다).
      const res = await fetch(e.request, {cache: 'no-cache'});
      if(res && res.ok && res.type === 'basic'){
        const copy = res.clone();
        caches.open(CACHE).then(c => c.put(e.request, copy)).catch(() => {});
      }
      return res;
    }catch(err){
      const hit = await caches.match(e.request);
      if(hit) return hit;
      // 인터넷이 없을 때 주소를 직접 열면 마지막으로 받은 화면이라도 보여 준다.
      if(e.request.mode === 'navigate'){
        const home = await caches.match('/index.html') || await caches.match('/');
        if(home) return home;
      }
      throw err;
    }
  })());
});
