(function () {
  "use strict";
  const current = document.querySelector('meta[name="jarvis-release"]')?.content;
  if (!current) return;
  let reloading = false;
  async function checkRelease() {
    if (reloading || document.visibilityState === "hidden") return;
    try {
      const response = await fetch(`/release.json?t=${Date.now()}`, { cache: "no-store" });
      if (!response.ok) return;
      const release = await response.json();
      if (release.ui && release.ui !== current) {
        reloading = true;
        window.location.reload();
      }
    } catch (_) {
      // An offline kiosk keeps the current shell; the next successful check recovers.
    }
  }
  window.setInterval(checkRelease, 60000);
  document.addEventListener("visibilitychange", checkRelease);
  window.addEventListener("online", checkRelease);
})();
