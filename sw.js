const CACHE_NAME = 'spaceguo-v1';
const URLS_TO_CACHE = [
  '/spaceguo-app/',
  '/spaceguo-app/index.html',
  '/spaceguo-app/manifest.json'
];

// 설치: 핵심 파일 캐시
self.addEventListener('install', function(event) {
  event.waitUntil(
    caches.open(CACHE_NAME).then(function(cache) {
      return cache.addAll(URLS_TO_CACHE);
    })
  );
  self.skipWaiting();
});

// 활성화: 이전 캐시 삭제
self.addEventListener('activate', function(event) {
  event.waitUntil(
    caches.keys().then(function(keys) {
      return Promise.all(
        keys.filter(function(k){ return k !== CACHE_NAME; })
            .map(function(k){ return caches.delete(k); })
      );
    })
  );
  self.clients.claim();
});

// 요청 처리: 네트워크 우선, 실패 시 캐시
self.addEventListener('fetch', function(event) {
  // Firebase 요청은 캐시하지 않음
  if(event.request.url.includes('firestore') ||
     event.request.url.includes('googleapis') ||
     event.request.url.includes('firebase')){
    return;
  }
  event.respondWith(
    fetch(event.request)
      .then(function(res){
        var resClone = res.clone();
        caches.open(CACHE_NAME).then(function(cache){
          cache.put(event.request, resClone);
        });
        return res;
      })
      .catch(function(){
        return caches.match(event.request);
      })
  );
});
