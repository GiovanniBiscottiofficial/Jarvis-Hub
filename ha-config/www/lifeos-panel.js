class JarvisLifeOSPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._retryTimer = null;
    this._refreshPromise = null;
    this._appOrigin = `${window.location.protocol}//${window.location.hostname}:8090`;

    const style = document.createElement("style");
    style.textContent = `
      :host { display: block; width: 100%; height: 100%; min-height: 100vh; background: #04070f; }
      iframe { display: block; width: 100%; height: 100%; min-height: 100vh; border: 0; background: #04070f; }
    `;
    this._frame = document.createElement("iframe");
    this._frame.title = "LifeOS";
    this._frame.allow = "camera; microphone; autoplay";
    this._frame.src = `${this._appOrigin}/?embedded=home-assistant`;
    this.shadowRoot.append(style, this._frame);

    this._onMessage = async (event) => {
      if (event.source !== this._frame.contentWindow || event.origin !== this._appOrigin) return;
      if (!event.data) return;
      if (event.data.type === "lifeos-auth-request") {
        this._sendSession();
        return;
      }
      if (event.data.type !== "lifeos-speak-request") return;
      const reply = event.ports && event.ports[0];
      const message = String(event.data.message || "").trim().slice(0, 12000);
      if (!reply) return;
      if (!this._hass || !message) {
        reply.postMessage({ ok: false, error: "Home Assistant voice bridge is unavailable." });
        return;
      }
      try {
        await this._hass.callService("script", "jarvis_say", {
          message,
          urgent: true,
        });
        reply.postMessage({ ok: true, output: "home_assistant" });
      } catch (error) {
        reply.postMessage({
          ok: false,
          error: error instanceof Error ? error.message : "Jarvis speaker call failed.",
        });
      }
    };
    this._onLoad = () => this._beginSessionDelivery();
  }

  set hass(value) {
    this._hass = value;
    this._sendSession();
  }

  set panel(value) {
    this._panel = value;
  }

  connectedCallback() {
    window.addEventListener("message", this._onMessage);
    this._frame.addEventListener("load", this._onLoad);
    this._beginSessionDelivery();
  }

  disconnectedCallback() {
    window.removeEventListener("message", this._onMessage);
    this._frame.removeEventListener("load", this._onLoad);
    this._stopRetrying();
  }

  async _accessToken() {
    const hass = this._hass;
    if (!hass) return null;
    const candidates = [
      hass.auth,
      hass.connection && hass.connection.options && hass.connection.options.auth,
      hass.connection && hass.connection.auth,
    ].filter(Boolean);
    for (const auth of candidates) {
      if (auth.expired && typeof auth.refreshAccessToken === "function") {
        if (!this._refreshPromise) {
          this._refreshPromise = Promise.resolve(auth.refreshAccessToken())
            .finally(() => { this._refreshPromise = null; });
        }
        await this._refreshPromise;
      }
      const token = (
        (auth.data && auth.data.access_token) ||
        auth.accessToken ||
        (auth.currentSession && auth.currentSession.access_token) ||
        (auth._token && auth._token.access_token)
      );
      if (token) return token;
    }
    return null;
  }

  async _sendSession() {
    let token = null;
    try { token = await this._accessToken(); } catch (_) { return false; }
    if (!token || !this._frame.contentWindow) return false;
    this._frame.contentWindow.postMessage({ type: "lifeos-ha-auth", token }, this._appOrigin);
    return true;
  }

  _beginSessionDelivery() {
    this._sendSession();
    this._stopRetrying();
    this._retryTimer = window.setInterval(() => this._sendSession(), 500);
    window.setTimeout(() => this._stopRetrying(), 10000);
  }

  _stopRetrying() {
    if (this._retryTimer) window.clearInterval(this._retryTimer);
    this._retryTimer = null;
  }
}

if (!customElements.get("jarvis-lifeos-panel")) {
  customElements.define("jarvis-lifeos-panel", JarvisLifeOSPanel);
}
