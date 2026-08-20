#!/usr/bin/env bash
# Turn the X1's own screen into a Google-Home-style hub display:
# boots straight into the Jarvis dashboard, fullscreen, no desktop.
# Run AFTER setup-x1.sh:  bash bootstrap/setup-kiosk.sh
#
# Wayland-native kiosk: a lightweight Weston compositor runs Chromium in
# native Wayland mode (smooth touch, proper HiDPI, no X server).
#   - Touch works natively; the on-screen keyboard (weston-keyboard) pops up
#     automatically when a text field is tapped.
#   - A floating ⌂ Home button is injected into every non-Jarvis page
#     (Netflix, YouTube, ...) so you can always get back to the dashboard.
#   - The boot splash shows ONLY on the first launch of the session; the
#     Home button and idle screen go straight to the dashboard.
#   - After IDLE_MINUTES without touch the ambient clock screen takes over;
#     tap anywhere on it to return. The screen itself never blanks or sleeps.
set -euo pipefail

DASH_URL="${DASH_URL:-http://localhost:8123/jarvis-hub/wall-plus?kiosk}"
IDLE_URL="${IDLE_URL:-http://localhost:8123/local/jarvis-idle.html}"
KIOSK_URL="${KIOSK_URL:-http://localhost:8123/local/jarvis-splash.html?next=/jarvis-hub/wall-plus%3Fkiosk}"
# Minutes of no touch before the ambient idle screen takes over (0 = never)
IDLE_MINUTES="${IDLE_MINUTES:-7}"
KIOSK_USER="${KIOSK_USER:-$USER}"
# HiDPI zoom: the X1 Tablet's 3000x2000 panel needs 2x+ to be readable.
# Tune with e.g.  KIOSK_SCALE=2.5 bash bootstrap/setup-kiosk.sh
KIOSK_SCALE="${KIOSK_SCALE:-2.25}"

echo "==> Jarvis kiosk setup (Wayland/Weston, user: $KIOSK_USER, scale: ${KIOSK_SCALE}x)"

echo "==> Installing Weston + Chromium + helpers..."
sudo apt-get update
sudo apt-get install -y --no-install-recommends \
  weston swayidle chromium-browser python3-websocket curl

# snap chromium: make sure it may talk to the Wayland socket
if snap list chromium >/dev/null 2>&1; then
  sudo snap connect chromium:wayland 2>/dev/null || true
fi

echo "==> Auto-login on tty1..."
sudo mkdir -p /etc/systemd/system/getty@tty1.service.d
sudo tee /etc/systemd/system/getty@tty1.service.d/autologin.conf >/dev/null <<EOF
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin ${KIOSK_USER} --noclear %I \$TERM
EOF

sudo mkdir -p /opt/jarvis-kiosk
# retire the old X11 hub bar if a previous version installed it
sudo rm -f /opt/jarvis-kiosk/hub-bar.py /opt/jarvis-kiosk/idle-watch.sh

echo "==> Weston config..."
# weston-keyboard lives in different dirs across releases
WKBD=""
for p in /usr/libexec/weston-keyboard /usr/lib/weston/weston-keyboard \
         /usr/lib/x86_64-linux-gnu/weston/weston-keyboard; do
  if [ -x "$p" ]; then WKBD="$p"; break; fi
done
IM_BLOCK=""
if [ -n "$WKBD" ]; then IM_BLOCK=$(printf '[input-method]\npath=%s' "$WKBD"); fi
sudo tee /opt/jarvis-kiosk/weston.ini >/dev/null <<EOF
[core]
idle-time=0
require-input=false

[shell]
panel-position=none
background-color=0xff05080f
locking=false
animation=fade

[autolaunch]
path=/opt/jarvis-kiosk/session.sh

${IM_BLOCK}

[libinput]
touchscreen_calibrator=true
EOF

echo "==> Kiosk session (Chromium native Wayland + idle watcher + Home overlay)..."
sudo tee /opt/jarvis-kiosk/session.sh >/dev/null <<EOF
#!/usr/bin/env bash
# Launched by Weston's [autolaunch] — owns the whole kiosk session.
export JARVIS_DASH_URL="${DASH_URL}"
export JARVIS_IDLE_URL="${IDLE_URL}"

# Ambient idle screen after ${IDLE_MINUTES} min of no input (0 = disabled).
if [ "${IDLE_MINUTES}" != "0" ]; then
  swayidle timeout $(( IDLE_MINUTES * 60 )) /opt/jarvis-kiosk/go-idle.sh &
fi

# Floating ⌂ Home button on non-Jarvis pages (Netflix, YouTube, ...)
/opt/jarvis-kiosk/home-overlay.py &

# Chromium relaunch loop. The splash shows ONLY on the first launch of the
# session (real boot); if Chromium is ever closed/crashes it comes back
# directly on the dashboard — the ⌂ Home button never relaunches it.
FIRST_URL="${KIOSK_URL}"
while true; do
  chromium-browser --kiosk --noerrdialogs --disable-infobars \\
    --disable-session-crashed-bubble --check-for-update-interval=31536000 \\
    --ozone-platform=wayland --enable-wayland-ime \\
    --force-renderer-accessibility \\
    --remote-debugging-port=9222 \\
    --force-device-scale-factor=${KIOSK_SCALE} \\
    "\${FIRST_URL}"
  FIRST_URL="${DASH_URL}"
  sleep 1
done
EOF
sudo chmod +x /opt/jarvis-kiosk/session.sh

echo "==> Idle handoff script..."
sudo tee /opt/jarvis-kiosk/go-idle.sh >/dev/null <<'SHEOF'
#!/usr/bin/env bash
# Navigate the kiosk to the ambient idle screen (via Chromium DevTools).
# Tapping the idle screen navigates itself back to the dashboard.
IDLE_URL="${JARVIS_IDLE_URL:-http://localhost:8123/local/jarvis-idle.html}"
DEVTOOLS="http://127.0.0.1:9222"
current=$(curl -fsS --max-time 3 "$DEVTOOLS/json/list" 2>/dev/null) || exit 0
echo "$current" | grep -q "jarvis-idle.html" && exit 0
echo "$current" | grep -q "jarvis-splash.html" && exit 0
enc_url=$(python3 -c "import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1],safe=''))" "$IDLE_URL")
new_id=$(curl -fsS --max-time 3 -X PUT "$DEVTOOLS/json/new?$enc_url" 2>/dev/null | python3 -c "import json,sys;print(json.load(sys.stdin).get('id',''))" 2>/dev/null)
[ -n "$new_id" ] || new_id=$(curl -fsS --max-time 3 "$DEVTOOLS/json/new?$enc_url" 2>/dev/null | python3 -c "import json,sys;print(json.load(sys.stdin).get('id',''))" 2>/dev/null)
[ -n "$new_id" ] || exit 0
echo "$current" | python3 -c "
import json, sys
for t in json.load(sys.stdin):
    if t.get('type') == 'page' and t.get('id') and t['id'] != '$new_id':
        print(t['id'])
" | while read -r tid; do
  curl -fsS --max-time 3 "$DEVTOOLS/json/close/$tid" >/dev/null 2>&1
done
SHEOF
sudo chmod +x /opt/jarvis-kiosk/go-idle.sh

echo "==> Home button overlay daemon..."
sudo tee /opt/jarvis-kiosk/home-overlay.py >/dev/null <<'PYEOF'
#!/usr/bin/env python3
"""Inject a floating ⌂ Home button into every non-Jarvis page.

Watches Chromium over the DevTools protocol; whenever a tab is on an
external site (Netflix, YouTube, ...) it injects a fixed-position button
that navigates straight back to the dashboard. Jarvis pages (Home
Assistant / LifeOS) get no button — they have their own navigation.
"""
import json
import os
import time
import urllib.request

import websocket

DEVTOOLS = "http://127.0.0.1:9222"
DASH_URL = os.environ.get(
    "JARVIS_DASH_URL", "http://localhost:8123/jarvis-hub/wall-plus?kiosk"
)
LOCAL_HOSTS = ("localhost", "127.0.0.1")

INJECT_JS = """
(() => {
  if (document.getElementById('jarvis-home-btn')) return;
  const b = document.createElement('div');
  b.id = 'jarvis-home-btn';
  b.textContent = '\\u2302';
  Object.assign(b.style, {
    position: 'fixed', left: '10px', bottom: '10px', zIndex: 2147483647,
    width: '64px', height: '64px', lineHeight: '64px', textAlign: 'center',
    fontSize: '40px', color: '#38e1ff', background: 'rgba(11,18,32,.82)',
    border: '1px solid rgba(56,225,255,.35)', borderRadius: '14px',
    cursor: 'pointer', userSelect: 'none',
    boxShadow: '0 2px 14px rgba(0,0,0,.55)',
    backdropFilter: 'blur(6px)',
  });
  b.addEventListener('click', () => { location.href = %s; });
  const add = () => document.body && document.body.appendChild(b);
  if (document.body) add();
  else document.addEventListener('DOMContentLoaded', add);
})();
""" % json.dumps(DASH_URL)


def pages():
    with urllib.request.urlopen(DEVTOOLS + "/json/list", timeout=3) as r:
        return [t for t in json.load(r) if t.get("type") == "page"]


def is_external(url: str) -> bool:
    if not url.startswith(("http://", "https://")):
        return False
    host = url.split("/")[2].split(":")[0]
    return host not in LOCAL_HOSTS


def inject(ws_url: str) -> None:
    ws = websocket.create_connection(ws_url, timeout=5)
    try:
        ws.send(json.dumps({
            "id": 1,
            "method": "Runtime.evaluate",
            "params": {"expression": INJECT_JS},
        }))
        ws.recv()
    finally:
        ws.close()


def main() -> None:
    done = {}  # tab id -> last url injected
    while True:
        time.sleep(3)
        try:
            tabs = pages()
        except Exception:
            continue
        live = set()
        for tab in tabs:
            tid, url = tab.get("id"), tab.get("url", "")
            if not tid:
                continue
            live.add(tid)
            if not is_external(url):
                done.pop(tid, None)
                continue
            if done.get(tid) == url:
                continue
            ws_url = tab.get("webSocketDebuggerUrl")
            if not ws_url:
                continue
            try:
                inject(ws_url)
                done[tid] = url
            except Exception:
                pass
        for tid in list(done):
            if tid not in live:
                done.pop(tid, None)


if __name__ == "__main__":
    main()
PYEOF
sudo chmod +x /opt/jarvis-kiosk/home-overlay.py

echo "==> Auto-start Weston on tty1..."
KIOSK_HOME=$(eval echo "~${KIOSK_USER}")
PROFILE="${KIOSK_HOME}/.bash_profile"
# retire the old startx auto-start if a previous version installed it
if [ -f "$PROFILE" ]; then
  sudo sed -i '/# Auto-start the Jarvis kiosk on tty1/,+3d' "$PROFILE"
fi
if ! grep -q 'weston' "$PROFILE" 2>/dev/null; then
  sudo tee -a "$PROFILE" >/dev/null <<'EOF'
# Auto-start the Jarvis kiosk (Weston/Wayland) on tty1
if [ -z "$WAYLAND_DISPLAY" ] && [ "$(tty)" = "/dev/tty1" ]; then
  exec weston --config=/opt/jarvis-kiosk/weston.ini
fi
EOF
  sudo chown "${KIOSK_USER}:" "$PROFILE"
fi
# the old X11 session file is no longer used
sudo rm -f "${KIOSK_HOME}/.xinitrc"

echo ""
echo "=========================================================="
echo " Wayland kiosk installed. Reboot and the X1 boots straight"
echo " into the Jarvis dashboard, fullscreen."
echo ""
echo " Tips:"
echo "  - On Netflix/YouTube/any app a floating ⌂ button (bottom-left)"
echo "    jumps straight back to the dashboard (no splash — boot-only)."
echo "  - The on-screen keyboard pops up by itself when you tap a"
echo "    text field."
echo "  - After ${IDLE_MINUTES} min of no touch the ambient clock screen"
echo "    appears; tap anywhere to get the dashboard back."
echo "    Change it: IDLE_MINUTES=15 bash bootstrap/setup-kiosk.sh (0 = off)"
echo "  - Log into HA once in that Chromium ('remember me') so it"
echo "    stays signed in."
echo "  - Too big/small? KIOSK_SCALE=2.5 bash bootstrap/setup-kiosk.sh (then reboot)"
echo "  - Escape to a terminal any time: Ctrl+Alt+F2."
echo "=========================================================="
