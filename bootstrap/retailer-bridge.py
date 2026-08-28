#!/usr/bin/env python3
"""Local, secret-protected Food Lion bridge for the X1 Chromium kiosk.

The bridge never reads cookies, credentials, payment fields, or checkout pages.
It exposes only offer metadata and confirmed quantity changes for product tiles.
"""

from __future__ import annotations

import json
import os
import secrets
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import requests
import websocket


DEVTOOLS = os.environ.get("RETAILER_DEVTOOLS_URL", "http://127.0.0.1:9222").rstrip("/")
SECRET = os.environ.get("RETAILER_BRIDGE_SECRET", "").strip()
HOST = os.environ.get("RETAILER_BRIDGE_HOST", "0.0.0.0")
PORT = int(os.environ.get("RETAILER_BRIDGE_PORT", "8766"))
BOGO_URL = "https://foodlion.com/browse-aisles/categories/1/bogo"


class BridgeError(RuntimeError):
    pass


class CDP:
    def __init__(self, url: str):
        self.socket = websocket.create_connection(url, timeout=15, suppress_origin=True)
        self.next_id = 0

    def close(self) -> None:
        self.socket.close()

    def command(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.next_id += 1
        command_id = self.next_id
        self.socket.send(json.dumps({"id": command_id, "method": method, "params": params or {}}))
        while True:
            message = json.loads(self.socket.recv())
            if message.get("id") != command_id:
                continue
            if "error" in message:
                raise BridgeError(message["error"].get("message", "Chromium command failed"))
            return message.get("result", {})

    def evaluate(self, expression: str) -> Any:
        result = self.command("Runtime.evaluate", {
            "expression": expression,
            "awaitPromise": True,
            "returnByValue": True,
        })
        value = result.get("result", {})
        if value.get("subtype") == "error":
            raise BridgeError(value.get("description", "Browser evaluation failed"))
        return value.get("value")


def _targets() -> list[dict[str, Any]]:
    response = requests.get(f"{DEVTOOLS}/json/list", timeout=5)
    response.raise_for_status()
    return [item for item in response.json() if item.get("type") == "page"]


def _foodlion_target(prefer_bogo: bool = False) -> dict[str, Any] | None:
    targets = [item for item in _targets() if urllib.parse.urlparse(item.get("url", "")).hostname in {"foodlion.com", "www.foodlion.com"}]
    if prefer_bogo:
        return next((item for item in targets if "/bogo" in item.get("url", "")), None) or (targets[0] if targets else None)
    return targets[0] if targets else None


def _open_foodlion() -> dict[str, Any]:
    target = _foodlion_target(prefer_bogo=True)
    if target:
        return target
    response = requests.put(f"{DEVTOOLS}/json/new?{urllib.parse.quote(BOGO_URL, safe=':/?=&')}", timeout=5)
    response.raise_for_status()
    return response.json()


def _with_page(callback, navigate_bogo: bool = False):
    target = _open_foodlion()
    client = CDP(target["webSocketDebuggerUrl"])
    try:
        client.command("Runtime.enable")
        current = client.evaluate("location.href") or ""
        if navigate_bogo and "/browse-aisles/categories/1/bogo" not in current:
            client.command("Page.enable")
            client.command("Page.navigate", {"url": BOGO_URL})
            deadline = time.time() + 25
            while time.time() < deadline:
                if client.evaluate("document.readyState") == "complete":
                    break
                time.sleep(0.25)
            time.sleep(2)
        return callback(client)
    finally:
        client.close()


STATUS_JS = r"""(() => {
  const text = document.body?.innerText || '';
  const signedIn = /Hi,\s*Giovanni/i.test(text) || !!document.querySelector('[aria-label*="account" i]');
  const storeMatch = text.match(/\b\d{3,5}\s+[A-Za-z][A-Za-z .'-]+(?:St|Street|Rd|Road|Ave|Avenue|Dr|Drive|Blvd|Way)\b/i);
  return {ready: signedIn, store: storeMatch ? storeMatch[0] : null,
    message: signedIn ? 'Signed-in Food Lion session ready' : 'Open Food Lion and sign in on this X1'};
})()"""


SCAN_JS = r"""(() => {
  const money = value => { const match = String(value || '').match(/\$([0-9]+(?:\.[0-9]{1,2})?)/); return match ? Number(match[1]) : null; };
  const products = [];
  for (const tile of document.querySelectorAll('.product-tile_content')) {
    const link = tile.querySelector('a[href*="/product/"]') || tile.closest('div')?.querySelector('a[href*="/product/"]');
    const href = link?.href || '';
    const id = href.match(/\/([A-Za-z0-9_-]+)(?:\?.*)?$/)?.[1];
    const text = tile.innerText || '';
    const lines = text.split('\n').map(v => v.trim()).filter(Boolean);
    const name = (link?.innerText || lines[0] || '').trim();
    const prices = [...text.matchAll(/\$([0-9]+(?:\.[0-9]{1,2})?)/g)].map(m => Number(m[1]));
    if (!id || !name || !prices.length || products.some(item => item.id === id)) continue;
    const save = text.match(/Save\s+\$([0-9]+(?:\.[0-9]{1,2})?)/i);
    products.push({id, name, url: href, price: prices[0], original_price: prices[1] || null,
      savings: save ? Number(save[1]) : Math.max(0, (prices[1] || prices[0]) - prices[0]),
      deal_type: /BOGO|buy\s+one\s+get\s+one/i.test(text) || (prices[1] && prices[1] >= prices[0] * 1.8) ? 'bogo' : 'sale',
      detail: lines.slice(0, 9).join(' · ')});
  }
  const body = document.body?.innerText || '';
  const store = body.match(/\b\d{3,5}\s+[A-Za-z][A-Za-z .'-]+(?:St|Street|Rd|Road|Ave|Avenue|Dr|Drive|Blvd|Way)\b/i)?.[0] || null;
  return {products, store};
})()"""


def _cart_js(items: list[dict[str, Any]]) -> str:
    payload = json.dumps(items)
    return f"""(async () => {{
      const requested = {payload};
      const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
      const results = [];
      for (const item of requested) {{
        const link = [...document.querySelectorAll('a[href*="/product/"]')]
          .find(node => new URL(node.href).pathname.split('/').filter(Boolean).pop() === item.id);
        const tile = link?.closest('.product-tile_content') || link?.closest('[class*="product-tile"]');
        if (!tile) {{ results.push({{id:item.id, ok:false, reason:'not_visible'}}); continue; }}
        tile.scrollIntoView({{block:'center'}}); await sleep(180);
        let input = tile.querySelector('input[aria-label="Quantity in cart"]');
        let current = input ? Number(input.value || 0) : 0;
        if (!current) {{
          const add = [...tile.querySelectorAll('button')].find(button => button.offsetParent !== null && /add to cart/i.test(button.textContent || ''));
          if (!add) {{ results.push({{id:item.id, ok:false, reason:'add_control_missing'}}); continue; }}
          add.click(); await sleep(900); current = 1;
        }}
        while (current < item.quantity) {{
          const increase = [...tile.querySelectorAll('button[aria-label="Increase quantity"]')].find(button => button.offsetParent !== null);
          if (!increase) break; increase.click(); current += 1; await sleep(450);
        }}
        while (current > item.quantity) {{
          const decrease = [...tile.querySelectorAll('button[aria-label="Decrease quantity"]')].find(button => button.offsetParent !== null);
          if (!decrease) break; decrease.click(); current -= 1; await sleep(450);
        }}
        input = tile.querySelector('input[aria-label="Quantity in cart"]');
        const actual = input ? Number(input.value || current) : current;
        results.push({{id:item.id, ok:actual === item.quantity, quantity:actual}});
      }}
      return {{results, updated:results.filter(item => item.ok).length}};
    }})()"""


class Handler(BaseHTTPRequestHandler):
    server_version = "JarvisRetailerBridge/1.0"

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        supplied = self.headers.get("X-Jarvis-Bridge-Secret", "")
        return bool(SECRET) and secrets.compare_digest(supplied, SECRET)

    def _body(self) -> dict[str, Any]:
        length = min(int(self.headers.get("Content-Length", "0")), 65536)
        return json.loads(self.rfile.read(length) or b"{}")

    def do_GET(self) -> None:
        if not self._authorized():
            return self._json(401, {"detail": "unauthorized"})
        if self.path != "/status":
            return self._json(404, {"detail": "not found"})
        try:
            self._json(200, _with_page(lambda page: page.evaluate(STATUS_JS)))
        except Exception:
            self._json(503, {"ready": False, "message": "Chromium or Food Lion is unavailable"})

    def do_POST(self) -> None:
        if not self._authorized():
            return self._json(401, {"detail": "unauthorized"})
        try:
            body = self._body()
            if self.path == "/scan":
                return self._json(200, _with_page(lambda page: page.evaluate(SCAN_JS), navigate_bogo=True))
            if self.path == "/cart":
                if body.get("confirmed") is not True:
                    return self._json(409, {"detail": "confirmation required"})
                items = body.get("items")
                if not isinstance(items, list) or not items:
                    return self._json(400, {"detail": "items required"})
                result = _with_page(lambda page: page.evaluate(_cart_js(items)), navigate_bogo=True)
                return self._json(200, result)
            return self._json(404, {"detail": "not found"})
        except (BridgeError, requests.RequestException, websocket.WebSocketException, ValueError, json.JSONDecodeError):
            return self._json(503, {"detail": "Food Lion browser operation failed"})

    def log_message(self, format: str, *args: Any) -> None:
        print(f"retailer-bridge: {self.address_string()} {format % args}")


if __name__ == "__main__":
    if not SECRET:
        raise SystemExit("RETAILER_BRIDGE_SECRET must be configured")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
