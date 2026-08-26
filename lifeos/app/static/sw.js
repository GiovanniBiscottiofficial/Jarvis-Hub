// Minimal service worker: makes LifeOS installable and shows the app shell
// when the hub is briefly unreachable. API calls always go to the network.
const CACHE = "lifeos-v4-money-split";
const SHELL = [
  "./", "index.html", "style.css", "app.js", "bodyops-enhanced.js",
  "bodyops-enhanced.css", "money-command.js", "money-command.css",
  "release-watch.js", "release.json", "manifest.json", "icon.svg",
];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)));
  self.skipWaiting();
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.pathname.includes("/api/")) return;
  e.respondWith(
    fetch(e.request)
      .then((res) => {
        const copy = res.clone();
        caches.open(CACHE).then((c) => c.put(e.request, copy));
        return res;
      })
      .catch(() => caches.match(e.request))
  );
});
