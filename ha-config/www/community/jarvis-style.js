// Jarvis global style module — loads the HUD fonts and shared keyframes
// so every dashboard view (and card-mod rules) can use them.
(() => {
  const css = `
  @font-face {
    font-family: 'Orbitron';
    font-style: normal;
    font-weight: 400 900;
    font-display: swap;
    src: url('/local/fonts/orbitron.woff2') format('woff2');
  }
  @font-face {
    font-family: 'Rajdhani';
    font-style: normal;
    font-weight: 500;
    font-display: swap;
    src: url('/local/fonts/rajdhani-500.woff2') format('woff2');
  }
  @font-face {
    font-family: 'Rajdhani';
    font-style: normal;
    font-weight: 600;
    font-display: swap;
    src: url('/local/fonts/rajdhani-600.woff2') format('woff2');
  }
  @font-face {
    font-family: 'Rajdhani';
    font-style: normal;
    font-weight: 700;
    font-display: swap;
    src: url('/local/fonts/rajdhani-700.woff2') format('woff2');
  }
  @keyframes jarvis-glow {
    0%, 100% { text-shadow: 0 0 14px rgba(0, 212, 255, 0.35); }
    50% { text-shadow: 0 0 30px rgba(0, 229, 255, 0.75), 0 0 60px rgba(0, 229, 255, 0.25); }
  }
  @keyframes jarvis-pulse {
    0%, 100% { box-shadow: 0 0 18px rgba(0, 212, 255, 0.15); }
    50% { box-shadow: 0 0 34px rgba(0, 229, 255, 0.35); }
  }
  @keyframes jarvis-scan {
    0% { background-position: -200% 0; }
    100% { background-position: 200% 0; }
  }
  `;
  const style = document.createElement('style');
  style.id = 'jarvis-style';
  style.textContent = css;
  document.head.appendChild(style);
})();

// Registry-backed room surface. Unlike hard-coded Lovelace room cards, this
// follows Home Assistant's Areas registry whenever rooms are added or renamed.
class JarvisAreasCard extends HTMLElement {
  setConfig(config) {
    this.config = config || {};
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._loading && !this._areas) this._loadRegistries();
    if (this._areas) this._render();
  }

  getCardSize() {
    return Math.max(1, Math.ceil((this._areas?.length || 1) / 2));
  }

  async _loadRegistries() {
    this._loading = true;
    try {
      const [areas, devices, entities] = await Promise.all([
        this._hass.callWS({ type: 'config/area_registry/list' }),
        this._hass.callWS({ type: 'config/device_registry/list' }),
        this._hass.callWS({ type: 'config/entity_registry/list' }),
      ]);
      this._areas = [...areas].sort((a, b) => a.name.localeCompare(b.name));
      this._deviceAreas = new Map(devices.map((device) => [device.id, device.area_id]));
      this._entities = entities;
      this._error = null;
    } catch (error) {
      this._error = error instanceof Error ? error.message : String(error);
    } finally {
      this._loading = false;
      this._render();
    }
  }

  _entitiesFor(areaId) {
    return (this._entities || []).filter((entry) => {
      if (entry.disabled_by) return false;
      return (entry.area_id || this._deviceAreas.get(entry.device_id)) === areaId;
    });
  }

  _navigate(path) {
    history.pushState(null, '', path);
    window.dispatchEvent(new CustomEvent('location-changed'));
  }

  async _toggleLights(areaId, lights) {
    const anyOn = lights.some((entityId) => this._hass.states[entityId]?.state === 'on');
    await this._hass.callService('light', anyOn ? 'turn_off' : 'turn_on', { area_id: areaId });
  }

  _render() {
    if (!this.shadowRoot) this.attachShadow({ mode: 'open' });
    const style = document.createElement('style');
    style.textContent = `
      :host { display: block; }
      ha-card { padding: 12px; background: linear-gradient(145deg, rgba(8,25,39,.96), rgba(3,12,21,.98)); border: 1px solid rgba(61,211,244,.2); }
      .grid { display: grid; grid-template-columns: repeat(auto-fit,minmax(210px,1fr)); gap: 8px; }
      .area { min-height: 116px; padding: 13px; color: #dff8ff; background: rgba(9,31,46,.92); border: 1px solid rgba(78,220,255,.17); border-left: 3px solid #36d7f5; text-align: left; }
      .area:hover { background: rgba(15,48,67,.96); border-color: rgba(78,220,255,.45); }
      .top { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
      h3 { margin: 0; font: 600 1rem Rajdhani, sans-serif; letter-spacing: .05em; }
      .pip { width: 7px; height: 7px; border-radius: 50%; background: #4ee58a; box-shadow: 0 0 9px #4ee58a; }
      .meta { margin: 8px 0 11px; color: #7194a8; font: .65rem ui-monospace, monospace; letter-spacing: .05em; text-transform: uppercase; }
      .actions { display: flex; gap: 7px; }
      button { min-height: 39px; padding: 7px 11px; color: #8eeeff; background: #0a2738; border: 1px solid rgba(83,222,250,.25); font: 600 .65rem ui-monospace, monospace; letter-spacing: .06em; }
      button:disabled { color: #526d7b; opacity: .65; }
      .empty,.error { padding: 20px; color: #7894a4; border: 1px dashed rgba(83,222,250,.2); text-align: center; }
      .error { color: #ff9d8f; }
    `;
    const card = document.createElement('ha-card');
    if (this._error) {
      const error = document.createElement('div');
      error.className = 'error';
      error.textContent = `Area registry unavailable: ${this._error}`;
      card.appendChild(error);
    } else if (!this._areas) {
      const loading = document.createElement('div');
      loading.className = 'empty';
      loading.textContent = 'Synchronizing Home Assistant rooms…';
      card.appendChild(loading);
    } else if (!this._areas.length) {
      const empty = document.createElement('div');
      empty.className = 'empty';
      empty.textContent = 'No Areas configured. Use SET → Areas to create rooms.';
      card.appendChild(empty);
    } else {
      const grid = document.createElement('div');
      grid.className = 'grid';
      for (const area of this._areas) {
        const entries = this._entitiesFor(area.area_id);
        const lights = entries.map((entry) => entry.entity_id).filter((id) => id.startsWith('light.'));
        const active = entries.filter((entry) => {
          const state = this._hass.states[entry.entity_id]?.state;
          return state && !['off', 'unavailable', 'unknown'].includes(state);
        }).length;
        const room = document.createElement('section');
        room.className = 'area';
        const top = document.createElement('div');
        top.className = 'top';
        const name = document.createElement('h3');
        name.textContent = area.name;
        const pip = document.createElement('span');
        pip.className = 'pip';
        top.append(name, pip);
        const meta = document.createElement('div');
        meta.className = 'meta';
        meta.textContent = `${entries.length} entities · ${active} active · ${lights.length} lights`;
        const actions = document.createElement('div');
        actions.className = 'actions';
        const lightButton = document.createElement('button');
        lightButton.textContent = lights.some((id) => this._hass.states[id]?.state === 'on') ? 'LIGHTS OFF' : 'LIGHTS ON';
        lightButton.disabled = !lights.length;
        lightButton.addEventListener('click', () => this._toggleLights(area.area_id, lights));
        const manageButton = document.createElement('button');
        manageButton.textContent = 'ROOM DETAILS';
        manageButton.addEventListener('click', () => this._navigate(`/config/areas/area/${area.area_id}`));
        actions.append(lightButton, manageButton);
        room.append(top, meta, actions);
        grid.appendChild(room);
      }
      card.appendChild(grid);
    }
    this.shadowRoot.replaceChildren(style, card);
  }
}

if (!customElements.get('jarvis-areas-card')) {
  customElements.define('jarvis-areas-card', JarvisAreasCard);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: 'jarvis-areas-card',
    name: 'Jarvis Areas',
    description: 'Live Home Assistant Areas registry for the Jarvis dashboard.',
  });
}
