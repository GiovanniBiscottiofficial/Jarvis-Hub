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
