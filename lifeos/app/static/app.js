const $ = (id) => document.getElementById(id);
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>\"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;",
  }[char]));
}

let homeAssistantToken = null;
let tokenWaiter = null;
let authenticationPromise = null;
let authRequestTimer = null;

window.addEventListener("message", (event) => {
  let source;
  try { source = new URL(event.origin); } catch (_) { return; }
  if (event.source !== window.parent) return;
  if (source.hostname !== window.location.hostname) return;
  if (!event.data || event.data.type !== "lifeos-ha-auth" || !event.data.token) return;
  homeAssistantToken = String(event.data.token);
  if (tokenWaiter) tokenWaiter(homeAssistantToken);
});

function requestHomeAssistantSession() {
  if (window.parent === window) return;
  window.parent.postMessage({ type: "lifeos-auth-request" }, "*");
}

function waitForHomeAssistantToken(timeout = 8000) {
  if (homeAssistantToken) return Promise.resolve(homeAssistantToken);
  return new Promise((resolve) => {
    requestHomeAssistantSession();
    authRequestTimer = window.setInterval(requestHomeAssistantSession, 500);
    const timer = window.setTimeout(() => {
      if (authRequestTimer) window.clearInterval(authRequestTimer);
      authRequestTimer = null;
      tokenWaiter = null;
      resolve(null);
    }, timeout);
    tokenWaiter = (token) => {
      window.clearTimeout(timer);
      if (authRequestTimer) window.clearInterval(authRequestTimer);
      authRequestTimer = null;
      tokenWaiter = null;
      resolve(token);
    };
  });
}

function showAuthenticationProblem(message) {
  const status = $("shell-sync");
  if (status) status.textContent = "AUTH REQUIRED";
  const state = $("command-system-state");
  if (state) state.textContent = "SESSION REQUIRED";
  const detail = $("command-system-detail");
  if (detail) detail.textContent = message;
}

async function authenticateBrowser() {
  if (authenticationPromise) return authenticationPromise;
  authenticationPromise = (async () => {
    const haToken = await waitForHomeAssistantToken();
    if (haToken) {
      const exchanged = await fetch("/api/auth/home-assistant", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: haToken }),
      });
      homeAssistantToken = null;
      if (exchanged.ok) return true;
    }
    showAuthenticationProblem(
      "LifeOS could not verify the Home Assistant session. Open LifeOS from the Home Assistant sidebar and refresh once."
    );
    return false;
  })();
  try {
    return await authenticationPromise;
  } finally {
    authenticationPromise = null;
  }
}

async function api(path, method = "GET", body, retried = false) {
  const normalizedMethod = String(method || "GET").toUpperCase();
  const headers = {};
  if (body) headers["Content-Type"] = "application/json";
  const res = await fetch(path, {
    method: normalizedMethod,
    headers,
    body: body ? JSON.stringify(body) : undefined,
    cache: normalizedMethod === "GET" ? "no-store" : "default",
  });
  if (res.status === 401 && !retried && await authenticateBrowser()) {
    return api(path, method, body, true);
  }
  const data = await res.json();
  if (res.status === 401) {
    throw new Error("Home Assistant session required. Open LifeOS from the Home Assistant sidebar.");
  }
  if (!res.ok) throw new Error(data.detail || data.message || `LifeOS request failed (${res.status})`);
  if (normalizedMethod !== "GET") {
    window.dispatchEvent(new CustomEvent("jarvis:data-changed", {
      detail: { path: String(path).split("?")[0], method: normalizedMethod },
    }));
  }
  return data;
}

// ---------- tabs ----------
const ALLOWED_PANELS = new Set(["command", "today", "body", "todo", "budget", "learning", "review"]);

function activatePanel(requestedPanel) {
  const panelName = ALLOWED_PANELS.has(requestedPanel) ? requestedPanel : "today";
  let activeTab = null;
  document.querySelectorAll(".tab").forEach((button) => {
    button.classList.toggle("active", button.dataset.tab === panelName);
    if (button.dataset.tab === panelName) activeTab = button;
  });
  document.querySelectorAll(".panel").forEach((panel) => {
    panel.classList.toggle("active", panel.id === panelName);
  });
  if (panelName === "budget") loadFinance();
  if (panelName === "body") loadBody();
  if (panelName === "todo") loadShopping();
  if (panelName === "today") loadToday();
  if (panelName === "learning") loadLearning();
  if (panelName === "review") loadReview();
  if (panelName === "command") loadCommandCenter();
  document.body.classList.toggle("command-active", panelName === "command");
  if (activeTab && window.matchMedia("(max-width: 650px)").matches) {
    window.requestAnimationFrame(() => window.requestAnimationFrame(() => {
      const nav = activeTab.closest("nav");
      const rail = activeTab.closest(".nav-rail");
      if (!nav || !rail) return;
      const chevronWidth = rail.querySelector(".nav-more")?.getBoundingClientRect().width || 0;
      const visibleWidth = Math.min(nav.clientWidth, Math.max(0, rail.clientWidth - chevronWidth));
      const targetLeft = activeTab.offsetLeft - ((visibleWidth - activeTab.offsetWidth) / 2);
      const maxLeft = Math.max(0, nav.scrollWidth - nav.clientWidth);
      nav.scrollTo({ left: Math.min(maxLeft, Math.max(0, targetLeft)), behavior: "auto" });
    }));
  }
  return panelName;
}

document.querySelectorAll(".tab").forEach((button) => {
  button.addEventListener("click", () => activatePanel(button.dataset.tab));
});

// ---------- Today ----------
function renderTodayPriorities(fusedPriorities, fallback) {
  const priorities = $("today-priorities");
  priorities.replaceChildren();
  const items = Array.isArray(fusedPriorities) && fusedPriorities.length
    ? fusedPriorities.map((priority) => priority.label)
    : fallback;
  if (!items.length) {
    priorities.textContent = "Daily baseline complete · maintain course.";
    return;
  }
  items.slice(0, 5).forEach((priority, index) => {
    const line = document.createElement("div");
    line.className = "priority-line";
    const number = document.createElement("span");
    number.textContent = String(index + 1).padStart(2, "0");
    const text = document.createElement("strong");
    text.textContent = priority;
    line.append(number, text);
    priorities.appendChild(line);
  });
}

let todayLoadSequence = 0;
async function loadToday() {
  const sequence = ++todayLoadSequence;
  const t = await api("/api/today");
  if (sequence !== todayLoadSequence) return;
  commandText("today-date", new Date().toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" }).toUpperCase());
  const p = t.protein;
  $("protein-bar").style.width = Math.min(100, (p.today_g / p.target_g) * 100) + "%";
  $("protein-label").textContent = `${Math.round(p.today_g)} / ${p.target_g}g`;
  const stepsTarget = t.step_target || 8000;
  $("steps-bar").style.width = Math.min(100, (t.steps_today / stepsTarget) * 100) + "%";
  $("steps-label").textContent = t.steps_today.toLocaleString();
  const w = t.water || { today: 0, target: 8 };
  $("water-bar").style.width = Math.min(100, (w.today / w.target) * 100) + "%";
  $("water-label").textContent = `${w.today} / ${w.target}`;
  $("streaks").textContent =
    `Streaks — vitamins: ${t.streaks.vitamins}d, steps: ${t.streaks.steps}d`;
  $("vitamins-btn").textContent = t.vitamins_taken
    ? `Vitamins done (streak ${t.streaks.vitamins}d)`
    : "Vitamins taken today";
  $("vitamins-btn").disabled = t.vitamins_taken;
  const fallbackPriorities = [];
  if (!t.vitamins_taken) fallbackPriorities.push("Take daily vitamins");
  if (p.today_g < p.target_g) fallbackPriorities.push(`${Math.round(p.target_g - p.today_g)}g protein remaining`);
  if (t.steps_today < stepsTarget) fallbackPriorities.push(`${(stepsTarget - t.steps_today).toLocaleString()} steps remaining`);
  if (w.today < w.target) fallbackPriorities.push(`${w.target - w.today} glasses of water remaining`);
  renderTodayPriorities(t.priorities, fallbackPriorities);

  const sug = $("suggestions");
  sug.replaceChildren();
  t.meal_suggestions.forEach((m) => {
    const div = document.createElement("div");
    div.className = "suggestion";
    div.append(
      el("strong", "", m.name),
      el("div", "meta", `${m.minutes} min · ${m.protein_g}g protein · ${m.calories} cal`)
    );
    const row = document.createElement("div");
    row.className = "row";
    const eat = document.createElement("button");
    eat.textContent = "Eat it";
    eat.onclick = async () => {
      await api("/api/body/meals/log", "POST", { name: m.name, protein_g: m.protein_g, calories: m.calories });
      if (m.id) await api("/api/pantry/chef/feedback", "POST", { recipe_id: m.id, action: "cooked" });
      loadToday();
    };
    if (m.id) {
      const like = el("button", "secondary", "Like");
      like.onclick = () => recordChefFeedback(m.id, "liked", `${m.name} moved up in your preferences.`);
      const skip = el("button", "secondary", "Skip");
      skip.onclick = () => recordChefFeedback(m.id, "skipped", `${m.name} will rank lower for now.`);
      row.append(eat, like, skip);
    } else {
      const sometimes = el("button", "secondary", "Sometimes");
      sometimes.onclick = () => override(m.name, "sometimes");
      const today = el("button", "secondary", "Today");
      today.onclick = () => override(m.name, "today");
      row.append(eat, sometimes, today);
    }
    div.appendChild(row);
    sug.appendChild(div);
  });

  const nud = $("nudges");
  if (t.nudges.length === 0) { nud.textContent = "None right now."; }
  else {
    nud.replaceChildren();
    t.nudges.forEach((n) => {
      const d = document.createElement("div");
      d.className = "nudge";
      d.textContent = n.text + " ";
      const ok = document.createElement("button");
      ok.className = "secondary";
      ok.textContent = "Got it";
      ok.onclick = async () => { await api(`/api/vault/nudges/${n.id}/resolve`, "POST"); loadToday(); };
      d.appendChild(ok);
      nud.appendChild(d);
    });
  }
  $("vault-snapshot").textContent =
    `$${t.vault_total.toFixed(2)} across accounts — open Budget & Vault for the two-check plan.`;
}

async function override(meal, kind) {
  await api("/api/body/overrides", "POST", { meal, kind });
  await api("/api/body/meals/log", "POST", { name: meal, override_kind: kind });
  loadToday();
}

$("vitamins-btn").onclick = async () => { await api("/api/body/vitamins/take", "POST"); loadToday(); };

$("water-btn").onclick = async () => { await api("/api/body/water", "POST", { glasses: 1 }); loadToday(); };

let activeBriefingUtterance = null;
let briefingRunId = 0;
let briefingStartWatchdog = null;
let briefingCompletionWatchdog = null;
function clearBriefingWatchdogs() {
  window.clearTimeout(briefingStartWatchdog);
  window.clearTimeout(briefingCompletionWatchdog);
  briefingStartWatchdog = null;
  briefingCompletionWatchdog = null;
}
function stopBriefing() {
  briefingRunId += 1;
  clearBriefingWatchdogs();
  if ("speechSynthesis" in window) window.speechSynthesis.cancel();
  activeBriefingUtterance = null;
  $("briefing-stop-btn").disabled = true;
  $("briefing-btn").disabled = false;
  $("briefing-status").textContent = "Briefing stopped. Full text remains visible.";
}

function briefingChunks(speech, limit = 240) {
  const sentences = speech.match(/[^.!?]+[.!?]+|[^.!?]+$/g) || [speech];
  const chunks = [];
  let current = "";
  sentences.forEach((sentence) => {
    const next = `${current} ${sentence}`.trim();
    if (current && next.length > limit) { chunks.push(current); current = sentence.trim(); }
    else current = next;
  });
  if (current) chunks.push(current);
  return chunks;
}

async function withTimeout(promise, milliseconds, message) {
  let timer;
  try {
    return await Promise.race([
      promise,
      new Promise((_, reject) => { timer = window.setTimeout(() => reject(new Error(message)), milliseconds); }),
    ]);
  } finally { window.clearTimeout(timer); }
}

function homeAssistantParentOrigin() {
  try {
    const declared = new URLSearchParams(window.location.search).get("parent_origin");
    if (declared) {
      const parsed = new URL(declared);
      if (
        ["http:", "https:"].includes(parsed.protocol)
        && parsed.hostname === window.location.hostname
        && parsed.origin !== window.location.origin
      ) return parsed.origin;
    }
    if (!document.referrer) return null;
    const origin = new URL(document.referrer).origin;
    return origin !== window.location.origin ? origin : null;
  } catch (_) { return null; }
}

function speakThroughHomeAssistant(speech) {
  const parentOrigin = homeAssistantParentOrigin();
  if (!parentOrigin || window.parent === window) {
    return Promise.resolve(false);
  }
  return new Promise((resolve) => {
    const requestId = typeof crypto.randomUUID === "function"
      ? crypto.randomUUID()
      : `briefing-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    const finish = (result) => {
      window.clearTimeout(timer);
      window.removeEventListener("message", onReply);
      resolve(result);
    };
    const onReply = (event) => {
      if (event.source !== window.parent || event.origin !== parentOrigin) return;
      if (!event.data || event.data.type !== "lifeos-speak-result") return;
      if (event.data.requestId !== requestId) return;
      finish(Boolean(event.data.ok));
    };
    const timer = window.setTimeout(() => {
      finish(false);
    }, 8000);
    window.addEventListener("message", onReply);
    try {
      window.parent.postMessage(
        { type: "lifeos-speak-request", requestId, message: speech },
        parentOrigin,
      );
    } catch (_) {
      finish(false);
    }
  });
}

function primeBrowserSpeech() {
  if (!("speechSynthesis" in window) || !("SpeechSynthesisUtterance" in window)) return;
  try {
    const unlock = new SpeechSynthesisUtterance(" ");
    unlock.volume = 0;
    window.speechSynthesis.speak(unlock);
  } catch (_) { /* Home Assistant speech remains the primary embedded path. */ }
}

function speakFullBriefing(speech, trigger) {
  const run = ++briefingRunId;
  const chunks = briefingChunks(speech);
  const voices = window.speechSynthesis.getVoices();
  const voice = voices.find((item) => /^en-US/i.test(item.lang)) || voices.find((item) => /^en/i.test(item.lang)) || null;
  let index = 0;
  const failSpeech = (message) => {
    if (run !== briefingRunId) return;
    clearBriefingWatchdogs();
    briefingRunId += 1;
    window.speechSynthesis.cancel();
    activeBriefingUtterance = null;
    $("briefing-stop-btn").disabled = true;
    trigger.disabled = false;
    $("briefing-status").textContent = `${message} The complete briefing text remains visible.`;
  };
  const speakNext = () => {
    if (run !== briefingRunId) return;
    if (index >= chunks.length) {
      activeBriefingUtterance = null;
      clearBriefingWatchdogs();
      $("briefing-stop-btn").disabled = true;
      trigger.disabled = false;
      $("briefing-status").textContent = "Briefing complete.";
      return;
    }
    const utterance = new SpeechSynthesisUtterance(chunks[index]);
    activeBriefingUtterance = utterance;
    $("briefing-stop-btn").disabled = false;
    $("briefing-status").textContent = `Speech queued · section ${index + 1} of ${chunks.length}. Stop is available.`;
    utterance.voice = voice;
    utterance.rate = 0.92;
    utterance.pitch = 0.96;
    utterance.onstart = () => {
      if (run !== briefingRunId) return;
      window.clearTimeout(briefingStartWatchdog);
      briefingStartWatchdog = null;
      const completionLimit = Math.max(10000, Math.min(30000, chunks[index].length * 110));
      briefingCompletionWatchdog = window.setTimeout(() => failSpeech("Speech playback did not complete."), completionLimit);
      $("briefing-status").textContent = `Speaking the complete briefing · section ${index + 1} of ${chunks.length}.`;
    };
    utterance.onend = () => { if (run !== briefingRunId) return; clearBriefingWatchdogs(); index += 1; speakNext(); };
    utterance.onerror = () => {
      if (run !== briefingRunId) return;
      failSpeech("Speech was blocked or interrupted.");
    };
    briefingStartWatchdog = window.setTimeout(() => failSpeech("Speech did not start in this browser."), 3000);
    window.speechSynthesis.speak(utterance);
  };
  window.speechSynthesis.cancel();
  speakNext();
}

$("briefing-stop-btn").onclick = stopBriefing;
$("briefing-btn").onclick = async () => {
  const trigger = $("briefing-btn");
  primeBrowserSpeech();
  trigger.disabled = true;
  $("briefing-status").textContent = "Preparing Giovanni’s full briefing…";
  try {
    const b = await withTimeout(api("/api/briefing"), 12000, "Briefing request timed out");
    const speech = String(b.speech || "");
    $("briefing").textContent = speech || "No briefing text was returned.";
    if (b.briefing_period) {
      $("briefing-status").textContent = `${String(b.briefing_period).toUpperCase()} briefing ready · current LifeOS context.`;
    }
    if (!speech) {
      $("briefing-status").textContent = "Briefing is empty. Try again after LifeOS finishes syncing.";
      return;
    }
    if (await speakThroughHomeAssistant(speech)) {
      $("briefing-stop-btn").disabled = true;
      $("briefing-status").textContent = "Briefing sent to Jarvis · speaking through the X1 speakers.";
      trigger.disabled = false;
      return;
    }
    if (!("speechSynthesis" in window) || !("SpeechSynthesisUtterance" in window)) {
      $("briefing-status").textContent = "Speech is unavailable in this browser. The complete briefing is shown above.";
      return;
    }
    speakFullBriefing(speech, trigger);
  } catch (error) {
    $("briefing-status").textContent = `Briefing unavailable: ${error.message}`;
  } finally {
    if (!activeBriefingUtterance) trigger.disabled = false;
  }
};

// ---------- Body Ops ----------
async function loadWorkouts() {
  const plans = await api("/api/body/workouts/plan");
  const w = $("workouts");
  if (!plans.length) { w.textContent = "Nothing planned."; return; }
  w.replaceChildren();
  plans.forEach((p) => {
    const line = document.createElement("div");
    line.className = "line";
    const detail = el("span", "", p.kind);
    detail.append(" ", el("span", "muted", `(${p.date}, ${p.minutes} min${p.source === "treat_balance" ? ", balances a treat" : ""})`));
    line.appendChild(detail);
    if (p.done) {
      line.appendChild(el("span", "tag good", "done"));
    } else {
      const btn = document.createElement("button");
      btn.className = "secondary";
      btn.textContent = "Done";
      btn.onclick = async () => { await api(`/api/body/workouts/plan/${p.id}/done`, "POST"); loadWorkouts(); };
      line.appendChild(btn);
    }
    w.appendChild(line);
  });
}

function renderChefSuggestions(chef) {
  const container = $("chef-suggestions");
  container.replaceChildren();
  $("chef-readiness").textContent = chef.suggestions.length ? `${chef.suggestions.length} OPTIONS READY` : "PANTRY INPUT NEEDED";
  if (!chef.suggestions.length) {
    container.appendChild(el("div", "empty-state", "Add pantry items and Jarvis will build safe options around what is available."));
    return;
  }
  chef.suggestions.forEach((recipe, index) => {
    const card = document.createElement("article");
    card.className = "chef-option";
    const head = document.createElement("div");
    head.className = "chef-option-head";
    const title = document.createElement("div");
    title.append(el("span", "chef-rank", `${String(index + 1).padStart(2, "0")} · ${recipe.tier}`), el("h3", "", recipe.name));
    head.append(title, el("strong", "chef-coverage", `${recipe.stock_coverage}% STOCKED`));
    const metrics = document.createElement("div");
    metrics.className = "chef-metrics";
    metrics.append(el("span", "", `${recipe.minutes} MIN`), el("span", "", `${recipe.protein_g}G PROTEIN`), el("span", "", `${recipe.calories} CAL`));
    const missing = recipe.missing.length
      ? `Missing: ${recipe.missing.map((item) => item.name).join(", ")}`
      : "Ready from current stock";
    const actions = document.createElement("div");
    actions.className = "chef-actions";
    const market = el("button", "secondary", recipe.missing.length ? "Add missing to market list" : "Market list ready");
    market.disabled = !recipe.missing.length;
    market.onclick = async () => {
      market.disabled = true;
      try {
        const result = await api("/api/pantry/market-list/build", "POST", { recipe_ids: [recipe.id], include_low_stock: false });
        $("chef-status").textContent = `${result.added} item${result.added === 1 ? "" : "s"} added for ${recipe.name}. ${result.checkout}`;
        loadPantry();
      } catch (error) { $("chef-status").textContent = error.message; market.disabled = false; }
    };
    const cooked = el("button", "", "Cooked & log");
    cooked.onclick = async () => {
      cooked.disabled = true;
      try {
        await api("/api/body/meals/log", "POST", { name: recipe.name, protein_g: recipe.protein_g, calories: recipe.calories });
        await api("/api/pantry/chef/feedback", "POST", { recipe_id: recipe.id, action: "cooked" });
        $("chef-status").textContent = `${recipe.name} logged. Jarvis will use that choice when ranking future meals.`;
        loadPantry(); loadToday();
      } catch (error) { $("chef-status").textContent = error.message; cooked.disabled = false; }
    };
    const liked = el("button", "secondary", "Like");
    liked.onclick = () => recordChefFeedback(recipe.id, "liked", `${recipe.name} moved up in your preferences.`);
    const skip = el("button", "secondary", "Skip");
    skip.onclick = () => recordChefFeedback(recipe.id, "skipped", `${recipe.name} will rank lower for now.`);
    actions.append(market, cooked, liked, skip);
    card.append(head, metrics, el("p", "chef-why", `${recipe.why} ${missing}`), actions);
    container.appendChild(card);
  });
}

async function recordChefFeedback(recipeId, action, message) {
  try {
    await api("/api/pantry/chef/feedback", "POST", { recipe_id: recipeId, action });
    $("chef-status").textContent = message;
    loadPantry();
  } catch (error) { $("chef-status").textContent = error.message; }
}

function renderMarketList(items) {
  const grocery = $("grocery");
  grocery.replaceChildren();
  if (!items.length) {
    grocery.appendChild(el("div", "empty-state", "Market list clear · mark pantry items out or add missing recipe ingredients."));
    return;
  }
  const groups = new Map([
    ["food", []],
    ["home", []],
  ]);
  items.forEach((item) => {
    const route = item.shopping_type === "food" ? "food" : "home";
    groups.get(route).push(item);
  });
  groups.forEach((routeItems, route) => {
    if (!routeItems.length) return;
    const group = document.createElement("section");
    group.className = "market-group";
    const title = route === "food"
      ? "FOOD · FOOD LION + INSTACART"
      : "HOME, PERSONAL & OTHER · WALMART + AMAZON";
    group.appendChild(el("h3", "", title));
    routeItems.forEach((item) => {
      const row = document.createElement("div");
      row.className = "market-row";
      const details = document.createElement("span");
      details.append(
        el("strong", "", item.item),
        el("small", "muted", `${item.department || "Other"} · ${item.qty || 1} ${item.unit || "item"} · ${item.reason || item.source || "Shopping list"}${item.estimated_price != null ? ` · est. $${Number(item.estimated_price).toFixed(2)}` : ""}`),
      );
      const actions = document.createElement("div");
      actions.className = "market-actions";
      const query = encodeURIComponent(item.item);
      const retailers = route === "food"
        ? [
            ["Food Lion list", "https://foodlion.com/personal-list"],
            ["Instacart", `https://www.instacart.com/store/s?k=${query}`],
          ]
        : [
            ["Walmart", `https://www.walmart.com/search?q=${query}`],
            ["Amazon", `https://www.amazon.com/s?k=${query}`],
          ];
      retailers.forEach(([label, href]) => {
        const link = el("a", "retailer-link secondary", label);
        link.href = href;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.setAttribute("aria-label", `Open ${item.item} with ${label}`);
        actions.appendChild(link);
      });
      const reroute = el("button", "secondary route-toggle", route === "food" ? "Move to home & personal" : "Move to food");
      reroute.onclick = async () => {
        reroute.disabled = true;
        try {
          const target = route === "food" ? "home" : "food";
          await api(`/api/pantry/grocery/${item.id}/type`, "POST", { shopping_type: target });
          $("shopping-status").textContent = `${item.item} now routes to ${target === "food" ? "Food Lion and Instacart" : "Walmart and Amazon"}.`;
          loadShopping();
        } catch (error) {
          $("shopping-status").textContent = `Route not changed: ${error.message}`;
          reroute.disabled = false;
        }
      };
      const done = el("button", "secondary", "Got it");
      done.onclick = async () => {
        done.disabled = true;
        try {
          await api("/api/pantry/grocery/remove", "POST", { item: item.item });
          $("shopping-status").textContent = `${item.item} removed from the shopping list.`;
          loadShopping();
        } catch (error) {
          $("shopping-status").textContent = `Item not removed: ${error.message}`;
          done.disabled = false;
        }
      };
      actions.append(reroute, done);
      row.append(details, actions);
      group.appendChild(row);
    });
    grocery.appendChild(group);
  });
}

async function loadPantry() {
  const pantryEl = $("pantry");
  pantryEl.textContent = "Loading inventory…";
  try {
    const [p, chef] = await Promise.all([api("/api/pantry"), api("/api/pantry/chef")]);
    renderChefSuggestions(chef);
    $("pantry-readiness").textContent = `${p.items.length} ITEMS · ${chef.pantry.out} OUT · ${chef.pantry.low} LOW`;
    pantryEl.replaceChildren();
    if (!p.items.length) {
      pantryEl.appendChild(el("div", "empty-state", p.grocy_configured
        ? "Inventory empty · add an item or sync Grocy"
        : "Inventory empty · local items can be added now; Grocy is not configured"));
    } else {
      p.items.forEach((item) => {
        const line = document.createElement("div");
        line.className = "inventory-row";
        const identity = el("span", "", item.name);
        identity.appendChild(el("small", "muted", `${item.qty} ${item.unit || "units"} · ${item.protein_g_per_serving || 0}g protein/serving`));
        const actions = document.createElement("div");
        actions.className = "inventory-actions";
        const out = el("button", "secondary", "Ran out");
        out.disabled = Number(item.qty) <= 0;
        out.onclick = async () => {
          out.disabled = true;
          try {
            await api(`/api/pantry/items/${item.id}/out`, "POST");
            $("pantry-status").textContent = `${item.name} marked out and added to the market list.`;
            loadPantry();
          } catch (error) { $("pantry-status").textContent = error.message; out.disabled = false; }
        };
        const remove = el("button", "secondary danger-subtle", "Remove");
        remove.onclick = async () => {
          remove.disabled = true;
          try {
            await api(`/api/pantry/items/${item.id}`, "DELETE");
            $("pantry-status").textContent = `${item.name} removed from local inventory.`;
            loadPantry();
          } catch (error) { $("pantry-status").textContent = error.message; remove.disabled = false; }
        };
        actions.append(out, remove);
        line.append(identity, actions);
        pantryEl.appendChild(line);
      });
    }
  } catch (error) {
    pantryEl.replaceChildren(el("div", "empty-state error-state", `Pantry unavailable · ${error.message}`));
    $("chef-suggestions").replaceChildren(el("div", "empty-state error-state", `Chef Jarvis unavailable · ${error.message}`));
  }
}

async function loadShopping() {
  const grocery = $("grocery");
  grocery.replaceChildren(el("div", "empty-state", "Loading routed shopping list…"));
  try {
    renderMarketList(await api("/api/pantry/grocery"));
  } catch (error) {
    const message = String(error.message || "Shopping service unavailable");
    const state = message.includes("Home Assistant session required")
      ? "Authentication required · open this To-do view through Home Assistant."
      : `Shopping list stale or offline · ${message}`;
    grocery.replaceChildren(el("div", "empty-state error-state", state));
  }
}

function friendlyScaleSource(source) {
  const value = String(source || "").replace(/^home_assistant:/, "").replace(/^sensor\./, "").replaceAll("_", " ").trim();
  return value ? value.replace(/\b\w/g, (letter) => letter.toUpperCase()).replace(/\bIhome\b/g, "iHome") : "Home Assistant weight sensor";
}

function formatScaleTimestamp(value) {
  const timestamp = new Date(value);
  if (!value || Number.isNaN(timestamp.valueOf())) return String(value || "Time unavailable");
  return timestamp.toLocaleString([], {
    month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
  });
}

function scaleReadinessView(readiness, error) {
  if (!readiness) {
    return {
      state: "unavailable",
      title: "SCALE TELEMETRY UNAVAILABLE",
      route: "Manual weight entry remains available.",
      detail: error ? `Readiness endpoint unavailable · ${error.message}` : "Readiness endpoint unavailable.",
      latest: null,
      fallback: null,
    };
  }

  const state = readiness.configured ? "configured" : (readiness.automation_ready ? "awaiting" : "unconfigured");
  const title = readiness.configured
    ? "TUYA SCALE CONNECTED"
    : (readiness.automation_ready ? "TUYA AUTOMATION READY · AWAITING WEIGHT SENSOR" : "TUYA SCALE NOT CONFIGURED");
  const weight = readiness.latest ? Number(readiness.latest.weight_lb) : NaN;

  return {
    state,
    title,
    route: readiness.bridge || "Tuya scale → Home Assistant weight sensor → LifeOS",
    detail: state === "awaiting"
      ? "Make sure the scale appears in the same Smart Life/Tuya account, then reload the Tuya integration in Home Assistant."
      : (readiness.guidance || "Waiting for scale readiness guidance."),
    latest: readiness.latest ? {
      weight: Number.isFinite(weight) ? `${weight.toFixed(1)} lb` : "Weight unavailable",
      timestamp: formatScaleTimestamp(readiness.latest.ts),
      source: friendlyScaleSource(readiness.latest.source),
    } : null,
    fallback: {
      route: readiness.fallback_bridge || "Apple Health → Health Auto Export → LifeOS",
      configured: Boolean(readiness.health_fallback_configured),
    },
  };
}

function renderScaleReadiness(readiness, error) {
  const scale = $("scale-readiness");
  const view = scaleReadinessView(readiness, error);
  scale.className = `scale-readiness is-${view.state}`;
  scale.replaceChildren(
    el("strong", "scale-state", view.title),
    el("span", "scale-route", view.route),
  );

  if (view.latest) {
    const reading = el("div", "scale-reading");
    reading.append(
      el("strong", "scale-weight", view.latest.weight),
      el("span", "scale-reading-meta", `${view.latest.timestamp} · ${view.latest.source}`),
    );
    scale.appendChild(reading);
  } else if (view.state !== "unavailable") {
    scale.appendChild(el("small", "scale-no-reading", "No Home Assistant weight reading received yet."));
  }

  scale.appendChild(el("small", "scale-guidance", view.detail));
  if (view.fallback) {
    const fallback = el("div", "scale-fallback");
    fallback.append(
      el("span", "scale-fallback-label", "APPLE HEALTH FALLBACK"),
      el("strong", view.fallback.configured ? "is-ready" : "", view.fallback.configured ? "CONFIGURED" : "NOT CONFIGURED"),
      el("small", "", view.fallback.route),
    );
    scale.appendChild(fallback);
  }
}

async function loadBody() {
  loadWorkouts();
  loadPantry();
  const [s, readinessResult] = await Promise.all([
    api("/api/body/summary"),
    api("/api/body/scale/readiness")
      .then((value) => ({ value, error: null }))
      .catch((error) => ({ value: null, error })),
  ]);
  const hist = s.weighins.map((w) => `${w.ts.slice(0, 10)}: ${w.weight_lb} lb`).join(" · ");
  $("weight-history").textContent = hist || "No weigh-ins yet.";
  renderScaleReadiness(readinessResult.value, readinessResult.error);
  if (s.snack_suggestions.length) {
    $("snack-card").hidden = false;
    $("snacks").replaceChildren();
    s.snack_suggestions.forEach((m) => {
      const d = document.createElement("div");
      d.className = "suggestion";
      d.append(el("strong", "", m.name), el("div", "meta", `${m.protein_g}g protein · ${m.minutes} min`));
      const b = document.createElement("button");
      b.textContent = "Eat it";
      b.onclick = async () => { await api("/api/body/meals/log", "POST", { name: m.name }); loadBody(); loadToday(); };
      d.appendChild(b);
      $("snacks").appendChild(d);
    });
  } else { $("snack-card").hidden = true; }
}

$("weight-btn").onclick = async () => {
  const v = parseFloat($("weight-input").value);
  if (!v) return;
  const r = await api("/api/body/weighin", "POST", { weight_lb: v });
  $("weight-msg").textContent = r.message;
  $("weight-input").value = "";
  loadBody();
};

$("steps-btn").onclick = async () => {
  const v = parseInt($("steps-input").value, 10);
  if (!v) return;
  await api("/api/body/steps", "POST", { count: v });
  $("steps-input").value = "";
  loadToday();
};

$("workout-btn").onclick = async () => {
  const kind = $("workout-kind").value.trim();
  if (!kind) return;
  await api("/api/body/workouts/plan", "POST", {
    kind, minutes: parseInt($("workout-min").value, 10) || 15,
  });
  $("workout-kind").value = ""; $("workout-min").value = "";
  loadWorkouts();
};

async function renderPhotoReview(photo) {
  const review = $("photo-review");
  if (!photo || photo.status === "logged") {
    review.hidden = true;
    return;
  }
  let foods = [];
  try { foods = JSON.parse(photo.foods_json || "[]"); } catch (_) {}
  review.hidden = false;
  review.innerHTML = `
    <strong>${escapeHtml(photo.meal_name || "Photo meal")}</strong>
    <div class="muted">${escapeHtml(foods.join(", ") || "Foods not identified")} · ${escapeHtml(photo.confidence || "low")} confidence</div>
    <div class="row">
      <input id="photo-name" value="${escapeHtml(photo.meal_name || "Photo meal")}" placeholder="meal name">
      <input id="photo-protein" type="number" min="0" step="1" value="${photo.protein_g ?? 0}" placeholder="protein g">
      <input id="photo-calories" type="number" min="0" step="1" value="${photo.calories ?? 0}" placeholder="calories">
      <button id="photo-log-btn">Log meal</button>
    </div>
    <div class="muted">${escapeHtml(photo.notes || photo.error || "Check the estimate before logging.")}</div>`;
  $("photo-log-btn").onclick = async () => {
    const r = await api(`/api/body/meals/photos/${photo.id}/log`, "POST", {
      name: $("photo-name").value.trim(),
      protein_g: Number($("photo-protein").value) || 0,
      calories: Number($("photo-calories").value) || 0,
    });
    $("photo-msg").textContent = `${r.meal} logged — ${Math.round(r.protein_today)}g protein today.`;
    review.hidden = true;
    loadToday();
  };
}

$("photo-btn").onclick = async () => {
  const file = $("photo-input").files[0];
  if (!file) {
    $("photo-msg").textContent = "Choose a meal photo first.";
    return;
  }
  const button = $("photo-btn");
  button.disabled = true;
  button.textContent = "Analyzing…";
  $("photo-msg").textContent = "Saving photo and asking Jarvis for an estimate…";
  try {
    const form = new FormData();
    form.append("photo", file);
    await api("/api/health");
    const res = await fetch("/api/body/meals/photo", {
      method: "POST",
      body: form,
    });
    const r = await res.json();
    if (!res.ok) throw new Error(r.detail || `Photo analysis failed (${res.status})`);
    $("photo-msg").textContent = r.message;
    await renderPhotoReview(r.estimate ? {
      id: r.photo_id, status: "needs_review", meal_name: r.estimate.meal_name,
      foods_json: JSON.stringify(r.estimate.foods), protein_g: r.estimate.protein_g,
      calories: r.estimate.calories, confidence: r.estimate.confidence, notes: r.estimate.notes,
    } : { id: r.photo_id, status: "needs_review", error: r.message });
  } catch (err) {
    $("photo-msg").textContent = err.message;
  } finally {
    button.disabled = false;
    button.textContent = "Analyze";
    $("photo-input").value = "";
  }
};

$("pantry-sync-btn").onclick = async () => {
  const button = $("pantry-sync-btn");
  button.disabled = true;
  $("pantry-status").textContent = "Syncing commissioned Grocy inventory…";
  try {
    const r = await api("/api/pantry/sync", "POST");
    $("pantry-status").textContent = r.message || `${r.synced || 0} Grocy items synchronized.`;
    if (r.ok) loadPantry();
  } catch (error) { $("pantry-status").textContent = `Grocy sync failed: ${error.message}`; }
  finally { button.disabled = false; }
};

$("market-build-btn").onclick = async () => {
  const button = $("market-build-btn");
  button.disabled = true;
  $("shopping-status").textContent = "Jarvis is checking low and depleted stock…";
  try {
    const result = await api("/api/pantry/market-list/build", "POST", { recipe_ids: [], include_low_stock: true });
    $("shopping-status").textContent = `${result.added} low-stock item${result.added === 1 ? "" : "s"} reviewed. ${result.checkout}`;
    loadShopping();
  } catch (error) { $("shopping-status").textContent = `Low-stock review failed: ${error.message}`; }
  finally { button.disabled = false; }
};

$("shopping-add-btn").onclick = async () => {
  const item = $("shopping-item").value.trim();
  const shoppingType = $("shopping-type").value;
  if (!item) {
    $("shopping-status").textContent = "Enter an item to add to the shopping list.";
    $("shopping-item").focus();
    return;
  }
  const button = $("shopping-add-btn");
  button.disabled = true;
  $("shopping-status").textContent = `Adding ${item}…`;
  try {
    await api("/api/pantry/grocery", "POST", { item, shopping_type: shoppingType });
    $("shopping-status").textContent = `${item} added for ${shoppingType === "food" ? "Food Lion or Instacart" : "Walmart or Amazon"}.`;
    $("shopping-item").value = "";
    loadShopping();
  } catch (error) {
    $("shopping-status").textContent = `Item not added: ${error.message}`;
  } finally {
    button.disabled = false;
  }
};

$("pantry-add-btn").onclick = async () => {
  const name = $("pantry-item").value.trim();
  const qty = Number($("pantry-qty").value);
  const protein = Number($("pantry-protein").value || 0);
  if (!name || !Number.isFinite(qty) || qty < 0 || !Number.isFinite(protein) || protein < 0) {
    $("pantry-status").textContent = "Enter an item name and non-negative quantity/protein values.";
    return;
  }
  const button = $("pantry-add-btn");
  button.disabled = true;
  $("pantry-status").textContent = `Adding ${name}…`;
  try {
    await api("/api/pantry/items", "POST", {
      name, qty, unit: $("pantry-unit").value.trim(), protein_g_per_serving: protein,
      category: $("pantry-category").value, low_stock_threshold: 1,
    });
    $("pantry-status").textContent = `${name} saved to local pantry inventory.`;
    $("pantry-item").value = ""; $("pantry-qty").value = "1"; $("pantry-unit").value = ""; $("pantry-protein").value = "";
    loadPantry();
  } catch (error) { $("pantry-status").textContent = `Pantry item not saved: ${error.message}`; }
  finally { button.disabled = false; }
};

$("meal-btn").onclick = async () => {
  const name = $("meal-name").value.trim();
  if (!name) return;
  await api("/api/body/meals/log", "POST", {
    name, protein_g: parseFloat($("meal-protein").value) || 0,
  });
  $("meal-name").value = ""; $("meal-protein").value = "";
  loadToday();
};

// ---------- Budget ----------
function money(value) {
  return Number(value || 0).toLocaleString([], { style: "currency", currency: "USD" });
}

function countdown(days) {
  const count = Number(days);
  if (count === 0) return "TODAY";
  return `${count} ${count === 1 ? "DAY" : "DAYS"}`;
}

function billPaid(bill, payday) {
  const period = payday && payday.period;
  const month = payday && String(payday.nominal_date || "").slice(0, 7);
  if (bill.paid_period) return Boolean(!period || bill.paid_period === period);
  return Boolean(bill.paid_month && (!month || bill.paid_month === month));
}

function assignedPaycheck(bill) {
  const explicit = Number(bill.paycheck);
  if (explicit === 1 || explicit === 2) return explicit;
  return Number(bill.due_day) <= 14 ? 1 : 2;
}

function billAppliesToPayday(bill, payday) {
  if (assignedPaycheck(bill) !== Number(payday.paycheck)) return false;
  if (bill.one_time && bill.start_period !== payday.period) return false;
  return !bill.start_period || String(payday.period) >= String(bill.start_period);
}

async function loadBudgetData() {
  const [o, allBills] = await Promise.all([api("/api/budget/overview"), api("/api/vault/bills")]);
  const runway = $("paycheck-runway");
  runway.replaceChildren();
  const upcoming = (o.paydays || []).slice(0, 3);
  if (!upcoming.length) runway.appendChild(el("div", "empty-state error-state", "Payday calendar unavailable"));
  upcoming.forEach((payday) => {
    const instrument = el("article", `paycheck-instrument paycheck-${payday.paycheck}`);
    const head = el("div", "paycheck-head");
    head.append(el("span", "paycheck-label", `${payday.label || `Paycheck ${payday.paycheck}`} · ${Number(payday.paycheck) === 1 ? "MONTH-END" : "15TH"}`), el("strong", "paycheck-countdown", countdown(payday.days_away)));
    const date = new Date(`${payday.date}T12:00:00`);
    const dateText = Number.isNaN(date.valueOf()) ? payday.date : date.toLocaleDateString([], { weekday: "short", month: "short", day: "numeric", year: "numeric" });
    const stack = allBills.filter((bill) => billAppliesToPayday(bill, payday));
    instrument.append(
      head,
      el("div", "paycheck-amount", money(payday.amount || 2064.24)),
      el("div", "paycheck-date", `ARRIVES ${dateText.toUpperCase()}`),
      el("div", "paycheck-funds", `${stack.length} BILL${stack.length === 1 ? "" : "S"} · ${money(stack.reduce((sum, bill) => sum + Number(bill.amount || 0), 0))} STACK`)
    );
    runway.appendChild(instrument);
  });
  $("budget-period").textContent =
    `Paycheck #${o.period.paycheck} · ${o.period.month}`;
  $("sts-amount").textContent = `$${o.safe_to_spend.toFixed(2)}`;
  $("sts-amount").className =
    "sts-amount " + (o.safe_to_spend >= 0 ? "good-text" : "warn-text");
  $("budget-breakdown").innerHTML = `
    <div class="line"><span>OnePay in</span><span>$${o.paycheck_in.onepay.toFixed(2)}</span></div>
    <div class="line"><span>Truliant savings split</span><span>$${o.paycheck_in.truliant.toFixed(2)}</span></div>
    <div class="line"><span>Bills allocated</span><span>−$${o.allocated.toFixed(2)}</span></div>
    <div class="line"><span>Relay bucket transfers</span><span>MANUAL · NOT DEDUCTED</span></div>`;
  const auditCls =
    ["balanced", "scheduled"].includes(o.audit) ? "good" : o.audit === "buffered" ? "" : "warn";
  let auditHtml =
    `<span class="tag ${auditCls}">audit: ${o.audit}</span>
     <div class="muted">${o.audit_note}</div>`;
  if (o.auto_shift && o.auto_shift.suggestions.length) {
    auditHtml += `<div class="muted">Auto-shift: slide ${o.auto_shift.suggestions
      .map((s) => `${s.name} ($${s.amount.toFixed(0)})`)
      .join(", ")} to next check → frees $${o.auto_shift.shiftable.toFixed(2)}${
      o.auto_shift.covers_deficit ? " — covers the gap" : " — still short"}</div>`;
  }
  $("audit-badge").innerHTML = auditHtml;

  const bl = $("budget-bills");
  bl.replaceChildren();
  if (!allBills.length) bl.appendChild(el("div", "empty-state", "No bills configured"));
  upcoming.forEach((stackPayday, payIndex) => {
    const paycheck = Number(stackPayday.paycheck);
    const bills = allBills.filter((bill) => billAppliesToPayday(bill, stackPayday));
    const stack = el("section", "bill-stack");
    const stackHead = el("header", "bill-stack-head");
    const payDate = new Date(`${stackPayday.date}T12:00:00`).toLocaleDateString([], { month: "short", day: "numeric" });
    stackHead.append(el("strong", "", `PAY ${payIndex + 1} · PAYCHECK ${paycheck} · ${payDate.toUpperCase()}`), el("span", "", `${bills.length} ITEMS · ${money(bills.reduce((sum, bill) => sum + Number(bill.amount || 0), 0))}`));
    stack.appendChild(stackHead);
    if (!bills.length) stack.appendChild(el("div", "empty-state", `No Paycheck ${paycheck} obligations`));
    bills.sort((a, b) => Number(a.due_day) - Number(b.due_day)).forEach((bill) => {
      const paid = billPaid(bill, stackPayday);
      const row = el("div", `bill-row ${paid ? "is-paid" : ""}`);
      const identity = el("span", "bill-identity", bill.name);
      identity.appendChild(el("small", "", `DUE ${bill.due_day}${bill.note ? ` · ${bill.note}` : ""}`));
      const amount = el("strong", "", money(bill.amount));
      const action = el("button", "secondary", paid ? "Undo paid" : "Mark paid");
      const rowStatus = el("span", "bill-row-status", "");
      action.disabled = !stackPayday || !stackPayday.period;
      if (action.disabled) action.title = "Paycheck period unavailable; refresh finance telemetry.";
      action.onclick = async () => {
        action.disabled = true;
        const periodQuery = stackPayday && stackPayday.period ? `?period=${encodeURIComponent(stackPayday.period)}` : "";
        try { await api(`/api/budget/bills/${bill.id}/${paid ? "unpaid" : "paid"}${periodQuery}`, "POST"); await loadFinance(); }
        catch (error) { action.disabled = false; rowStatus.textContent = error.message; }
      };
      row.append(identity, amount, paid ? el("span", "tag good", "PAID") : el("span", "tag", "OPEN"), action, rowStatus);
      stack.appendChild(row);
    });
    bl.appendChild(stack);
  });

  const acc = $("budget-accounts");
  acc.innerHTML = "";
  const bsel = $("bal-account");
  bsel.innerHTML = "";
  o.accounts.forEach((a) => {
    const line = document.createElement("div");
    line.className = "line";
    line.innerHTML = `<span>${a.name}${a.role ? ` <span class="muted">(${a.role})</span>` : ""}</span>
      <span>$${a.balance.toFixed(2)}</span>`;
    acc.appendChild(line);
    const opt = document.createElement("option");
    opt.value = a.id; opt.textContent = a.name;
    bsel.appendChild(opt);
  });
  acc.innerHTML += `<div class="line"><span>Free pocket cash <span class="muted">(OnePay after commitments)</span></span>
    <span class="${o.pocket_cash >= 0 ? "good-text" : "warn-text"}">$${o.pocket_cash.toFixed(2)}</span></div>
    <div class="line"><strong>Ecosystem cash</strong>
    <strong>$${o.ecosystem_cash.toFixed(2)}</strong></div>`;

  const fl = $("budget-funds");
  fl.innerHTML = "";
  o.funds.forEach((g) => {
    const pct = g.target ? Math.min(100, (g.saved / g.target) * 100) : 0;
    const div = document.createElement("div");
    div.className = "bar-row";
    div.innerHTML = `<span>${g.name}</span>
      <div class="bar"><div class="fill" style="width:${pct}%"></div></div>
      <span>$${g.saved.toFixed(0)}${g.target ? " / $" + g.target.toFixed(0) : ""}
        <span class="muted">$${g.monthly.toFixed(0)}/mo target · manual</span></span>`;
    if (g.monthly > 0) {
      const add = document.createElement("button");
      add.className = "secondary";
      add.textContent = `+$${(g.monthly / 2).toFixed(0)}`;
      add.onclick = async () => {
        await api(`/api/budget/funds/${g.id}/contribute`, "POST", { amount: g.monthly / 2 });
        loadFinance();
      };
      div.appendChild(add);
    }
    fl.appendChild(div);
  });

  const dl = $("budget-debts");
  dl.replaceChildren();
  if (!o.debts.length) dl.appendChild(el("div", "muted", "Debt free 🎉"));
  o.debts.forEach((d) => {
    const pct = d.total ? Math.min(100, ((d.total - d.remaining) / d.total) * 100) : 0;
    const div = document.createElement("div");
    div.className = "bar-row";
    const identity = el("span", "", `#${Number(d.priority || 50)} · ${d.name}`);
    identity.appendChild(el("small", "muted", d.priority_reason || "Priority classification needed"));
    const bar = el("div", "bar");
    const fill = el("div", "fill");
    fill.style.width = `${pct}%`;
    bar.appendChild(fill);
    div.append(identity, bar, el("span", "", `$${d.remaining.toFixed(0)} left`));
    const pay = document.createElement("button");
    pay.className = "secondary";
    pay.textContent = `Pay $${d.installment.toFixed(0)}`;
    pay.onclick = async () => {
      await api(`/api/budget/debts/${d.id}/payment`, "POST", { amount: d.installment });
      loadFinance();
    };
    div.appendChild(pay);
    dl.appendChild(div);
  });
  if (o.debts.length) {
    const total = el("div", "line");
    total.append(el("strong", "", "Total remaining"), el("strong", "", `$${o.total_debt.toFixed(2)}`));
    dl.appendChild(total);
  }

  const netWorth = $("budget-networth");
  netWorth.replaceChildren();
  const retirementTotal = el("div", "line");
  const retirementIdentity = el("span", "", "Employer retirement account");
  retirementIdentity.appendChild(el("small", "muted", `Contributions $${o.retirement_summary.contributions.toFixed(2)} · employer match / market movement unclassified $${o.retirement_summary.unclassified_difference.toFixed(2)}${o.retirement_summary.as_of ? ` · as of ${o.retirement_summary.as_of}` : ""}`));
  retirementTotal.append(retirementIdentity, el("strong", "", `$${o.retirement_summary.balance.toFixed(2)}`));
  netWorth.appendChild(retirementTotal);
  o.assets.forEach((asset) => {
    const line = el("div", "line");
    const identity = el("span", "", asset.name === "401(k)" ? "401(k) · Pre-tax" : asset.name);
    identity.appendChild(el("small", "muted", `+$${asset.per_paycheck.toFixed(2)}/pay · YTD $${Number(asset.ytd_contributions || 0).toFixed(2)} · lifetime $${Number(asset.lifetime_contributions || 0).toFixed(2)}${asset.as_of ? ` · as of ${asset.as_of}` : ""}`));
    line.append(identity, el("span", "", asset.balance_verified ? `$${asset.balance.toFixed(2)}` : "Balance awaiting data"));
    netWorth.appendChild(line);
  });
  const netTotal = el("div", "line");
  netTotal.append(el("strong", "", "Net worth"), el("strong", "", `$${o.net_worth.toFixed(2)}`));
  netWorth.appendChild(netTotal);

  const f = await api("/api/budget/forecast");
  $("budget-forecast").innerHTML = f.forecast
    .map((p) => `<div class="line"><span>${p.period}
      <span class="muted">(bills $${p.bills.toFixed(0)})</span></span>
      <span class="${p.surplus >= 0 ? "good-text" : "warn-text"}">${p.surplus >= 0 ? "+" : ""}$${p.surplus.toFixed(0)}
        <span class="muted">buffer $${p.projected_buffer.toFixed(0)}</span></span></div>`)
    .join("") +
    (o.debt_free && o.debt_free.checks
      ? `<div class="line"><strong>Debt-free horizon</strong>
         <strong>${o.debt_free.period} (${o.debt_free.checks} checks)</strong></div>`
      : "");
}

function renderFinanceLoadError(targetId, label, error) {
  const target = $(targetId);
  target.replaceChildren();
  const state = el("div", "empty-state error-state");
  state.appendChild(el("span", "", `${label} unavailable · ${error.message}`));
  const retry = el("button", "secondary", "Retry");
  retry.onclick = loadFinance;
  state.appendChild(retry);
  target.appendChild(state);
}

async function loadBudget() {
  try {
    await loadBudgetData();
    return { ok: true, name: "payroll and budget" };
  } catch (error) {
    [
      ["paycheck-runway", "Payday runway"], ["budget-breakdown", "Current allocation"],
      ["budget-bills", "Bill stacks"], ["budget-accounts", "Account position"],
      ["budget-funds", "Sinking buckets"], ["budget-debts", "Debt plan"],
      ["budget-networth", "Net worth"], ["budget-forecast", "Cash-flow forecast"],
    ].forEach(([id, label]) => renderFinanceLoadError(id, label, error));
    return { ok: false, name: "payroll and budget", error };
  }
}

async function dispatchWindfall(route) {
  const amount = parseFloat($("wf-amount").value);
  if (isNaN(amount) || amount <= 0) return;
  const r = await api("/api/budget/windfall", "POST", { amount, route });
  $("wf-amount").value = "";
  const parts = r.debt_payments.map((p) => `${p.debt} −$${p.paid.toFixed(2)}`);
  if (r.to_buckets > 0) parts.push(`buckets +$${r.to_buckets.toFixed(2)}`);
  if (r.kept_in_onepay > 0) parts.push(`OnePay buffer +$${r.kept_in_onepay.toFixed(2)}`);
  $("wf-result").textContent = "Routed: " + parts.join(", ");
  loadFinance();
}
$("wf-debt").onclick = () => dispatchWindfall("debt");
$("wf-split").onclick = () => dispatchWindfall("split");
$("wf-buffer").onclick = () => dispatchWindfall("buffer");

$("bal-btn").onclick = async () => {
  const balance = parseFloat($("bal-amount").value);
  const status = $("bal-status");
  if (!Number.isFinite(balance)) {
    status.textContent = "Enter the actual account balance, including a negative balance when applicable.";
    return;
  }
  const button = $("bal-btn");
  const account = $("bal-account").selectedOptions[0]?.textContent || "Account";
  button.disabled = true;
  status.textContent = `Updating ${account}…`;
  try {
    await api(`/api/vault/accounts/${$("bal-account").value}/balance`, "PUT", { balance });
    $("bal-amount").value = "";
    await loadFinance();
    status.textContent = `${account} reconciled to ${money(balance)}.`;
  } catch (error) {
    status.textContent = `Balance not updated: ${error.message}`;
  } finally {
    button.disabled = false;
  }
};

// ---------- Consolidated Budget & Vault support ----------
async function loadVaultData() {
  const accounts = await api("/api/vault/accounts");
  const acc = $("accounts");
  acc.replaceChildren();
  const sel = $("dep-account");
  sel.replaceChildren();
  accounts.forEach((a) => {
    const line = document.createElement("div");
    line.className = "line";
    const name = el("span", "", a.name);
    if (a.vaultborne) name.append(" ", el("span", "tag", "Vaultborne"));
    line.append(name, el("span", "", `$${a.balance.toFixed(2)}`));
    acc.appendChild(line);
    const opt = document.createElement("option");
    opt.value = a.id; opt.textContent = a.name;
    sel.appendChild(opt);
  });

  const goals = await api("/api/vault/goals");
  const gl = $("goals");
  const gsel = $("goal-select");
  gsel.replaceChildren();
  if (!goals.length) {
    gl.textContent = "No goals yet — add one below or say \"add 50 to my vacation fund\".";
  } else {
    gl.replaceChildren();
    goals.forEach((g) => {
      const pct = g.target ? Math.min(100, (g.saved / g.target) * 100) : 0;
      const div = document.createElement("div");
      div.className = "bar-row";
      const bar = el("div", "bar");
      const fill = el("div", "fill");
      fill.style.width = `${pct}%`;
      bar.appendChild(fill);
      div.append(
        el("span", "", g.name),
        bar,
        el("span", "", `$${g.saved.toFixed(0)}${g.target ? " / $" + g.target.toFixed(0) : ""}`)
      );
      gl.appendChild(div);
      const opt = document.createElement("option");
      opt.value = g.name; opt.textContent = g.name;
      gsel.appendChild(opt);
    });
  }

  const plan = await api("/api/vault/plan");
  const pl = $("plan");
  pl.replaceChildren();
  [
    ["Available now", `$${plan.total_available.toFixed(2)}`, false],
    ["Scheduled this first-pay cycle", `$${plan.unpaid_bills_total.toFixed(2)}`, false],
    ["Current cash before first pay", `$${plan.leftover_after_bills.toFixed(2)}`, true],
  ].forEach(([label, value, strong]) => {
    const line = el("div", "line");
    line.append(el(strong ? "strong" : "span", "", label), el(strong ? "strong" : "span", "", value));
    pl.appendChild(line);
  });
  plan.recommendations.forEach((r) => {
    const line = document.createElement("div");
    line.className = "line";
    const cls = "good";
    const bill = el("span", "", r.bill);
    const dueDate = r.due_date ? new Date(`${r.due_date}T12:00:00`).toLocaleDateString([], { month: "short", day: "numeric" }) : r.due_day;
    bill.append(" ", el("span", "muted", `(due ${dueDate}, ${r.status})`));
    line.append(bill, el("span", `tag ${cls}`, r.recommend));
    pl.appendChild(line);
  });
  if (plan.food_nudges.length) {
    const h = document.createElement("div");
    h.className = "muted";
    h.style.marginTop = "8px";
    h.textContent = "Food-money nudges:";
    pl.appendChild(h);
    plan.food_nudges.forEach((n) => {
      const d = document.createElement("div");
      d.className = "nudge";
      d.textContent = n.text;
      pl.appendChild(d);
    });
  }
}

async function loadVault() {
  try {
    await loadVaultData();
    $("accounts-status").textContent = "";
    $("accounts-status").className = "inline-status muted";
    $("goals-status").textContent = "";
    $("goals-status").className = "inline-status muted";
    return { ok: true, name: "accounts and goals" };
  } catch (error) {
    $("accounts-status").textContent = `Deposit and reconciliation tools unavailable: ${error.message}`;
    $("accounts-status").className = "inline-status error-state";
    $("goals-status").textContent = `Goal controls unavailable: ${error.message}`;
    $("goals-status").className = "inline-status error-state";
    renderFinanceLoadError("plan", "Vault plan", error);
    return { ok: false, name: "accounts and goals", error };
  }
}

async function loadSpending() {
  const list = $("spending-list");
  list.textContent = "Loading recent entries…";
  try {
    const spending = await api("/api/vault/spending");
    $("spending-week").textContent = money(spending.week_total);
    $("spending-month").textContent = money(spending.month_total);
    list.replaceChildren();
    if (!spending.entries.length) {
      list.appendChild(el("div", "empty-state", "No spending recorded in the last seven days"));
      return { ok: true, name: "spending ledger" };
    }
    spending.entries.forEach((entry) => {
      const row = el("div", "transaction-row");
      const identity = el("span", "", entry.merchant || "Uncategorized spending");
      identity.appendChild(el("small", "", String(entry.ts || "").replace("T", " ").slice(0, 16)));
      row.append(identity, el("strong", "", money(entry.amount)));
      list.appendChild(row);
    });
    return { ok: true, name: "spending ledger" };
  } catch (error) {
    renderFinanceLoadError("spending-list", "Spending ledger", error);
    return { ok: false, name: "spending ledger", error };
  }
}

async function loadFinance() {
  $("finance-status").textContent = "Synchronizing finance telemetry…";
  const results = await Promise.all([loadBudget(), loadVault(), loadSpending()]);
  const failed = results.filter((result) => !result.ok);
  $("finance-status").textContent = failed.length
    ? `Degraded · retry the affected ${failed.map((item) => item.name).join(", ")} sections below.`
    : "All finance ledgers synchronized.";
  $("finance-status").className = `inline-status ${failed.length ? "error-state" : "muted"}`;
}

$("spending-btn").onclick = async () => {
  const amount = Number($("spending-amount").value);
  const merchant = $("spending-merchant").value.trim();
  if (!Number.isFinite(amount) || amount <= 0 || !merchant) {
    $("spending-status").textContent = "Enter a positive amount and a merchant or purpose.";
    return;
  }
  const button = $("spending-btn");
  button.disabled = true;
  $("spending-status").textContent = `Recording ${money(amount)} for ${merchant}…`;
  try {
    await api("/api/vault/spending", "POST", { amount, merchant });
    $("spending-amount").value = ""; $("spending-merchant").value = "";
    $("spending-status").textContent = `${money(amount)} recorded in the LifeOS spending ledger.`;
    await loadSpending();
  } catch (error) { $("spending-status").textContent = `Spending was not recorded: ${error.message}`; }
  finally { button.disabled = false; }
};

$("dep-btn").onclick = async () => {
  const amount = parseFloat($("dep-amount").value);
  if (!amount) return;
  await api("/api/vault/deposits", "POST", {
    amount, account_id: parseInt($("dep-account").value, 10), source: "manual",
  });
  $("dep-amount").value = "";
  loadFinance();
};

$("goal-btn").onclick = async () => {
  const name = $("goal-name").value.trim();
  if (!name) return;
  await api("/api/vault/goals", "POST", {
    name, target: parseFloat($("goal-target").value) || 0,
  });
  $("goal-name").value = ""; $("goal-target").value = "";
  loadFinance();
};

$("goal-add-btn").onclick = async () => {
  const name = $("goal-select").value;
  const amount = parseFloat($("goal-amount").value);
  if (!name || !amount) return;
  await api("/api/vault/goals/contribute", "POST", { name, amount });
  $("goal-amount").value = "";
  loadFinance();
};

$("bill-btn").onclick = async () => {
  const name = $("bill-name").value.trim();
  const amount = parseFloat($("bill-amount").value);
  const due_day = parseInt($("bill-day").value, 10);
  const paycheck = parseInt($("bill-paycheck").value, 10);
  if (!name || !Number.isFinite(amount) || amount <= 0 || !Number.isInteger(due_day) || due_day < 1 || due_day > 31 || ![1, 2].includes(paycheck)) {
    $("bill-status").textContent = "Enter a bill name, positive amount, valid due day, and paycheck stack.";
    return;
  }
  const button = $("bill-btn");
  button.disabled = true;
  $("bill-status").textContent = `Adding ${name} to Paycheck ${paycheck}…`;
  try {
    await api("/api/vault/bills", "POST", { name, amount, due_day, paycheck });
    $("bill-name").value = ""; $("bill-amount").value = ""; $("bill-day").value = ""; $("bill-paycheck").value = "1";
    $("bill-status").textContent = `${name} assigned to Paycheck ${paycheck}.`;
    await loadFinance();
  } catch (error) { $("bill-status").textContent = `Bill was not added: ${error.message}`; }
  finally { button.disabled = false; }
};

// ---------- Learning Ledger ----------
function learningPreferenceRow(preference, actions = []) {
  const row = el("article", "learning-row");
  const copy = el("div", "learning-copy");
  const eyebrow = el(
    "span",
    "learning-row-label",
    `${String(preference.domain || "general").toUpperCase()} · ${String(preference.sentiment || "prefer").toUpperCase()}`,
  );
  const title = el("strong", "", `${preference.subject}: ${preference.value}`);
  const evidence = el(
    "small",
    "",
    `${Math.round(Number(preference.confidence || 0) * 100)}% confidence · ${preference.evidence_count || 0} explicit signal${preference.evidence_count === 1 ? "" : "s"}`,
  );
  const reason = el("p", "", preference.reason || "Awaiting an explanation from the learning engine.");
  copy.append(eyebrow, title, evidence, reason);
  row.appendChild(copy);
  if (actions.length) {
    const controls = el("div", "learning-actions");
    actions.forEach(({ label, decision, className = "secondary" }) => {
      const button = el("button", className, label);
      button.onclick = () => decideLearningPreference(preference.id, decision, button);
      controls.appendChild(button);
    });
    row.appendChild(controls);
  }
  return row;
}

function renderLearningList(targetId, items, emptyMessage, actions) {
  const target = $(targetId);
  target.replaceChildren();
  if (!items.length) {
    target.appendChild(el("div", "empty-state", emptyMessage));
    return;
  }
  items.forEach((item) => target.appendChild(learningPreferenceRow(item, actions)));
}

async function loadLearning() {
  const status = $("learning-status");
  status.textContent = "Synchronizing local evidence and decisions…";
  status.className = "inline-status muted";
  try {
    const data = await api("/api/learning");
    const summary = data.summary || {};
    $("learning-confirmed-count").textContent = summary.confirmed ?? 0;
    $("learning-candidate-count").textContent = summary.candidate ?? 0;
    $("learning-rejected-count").textContent = summary.rejected ?? 0;
    $("learning-observation-count").textContent = summary.observations ?? 0;
    const preferences = Array.isArray(data.preferences) ? data.preferences : [];
    renderLearningList(
      "learning-candidates",
      preferences.filter((item) => item.status === "candidate"),
      "No unreviewed learning candidates.",
      [
        { label: "Confirm guidance", decision: "confirm", className: "pill" },
        { label: "Reject", decision: "reject" },
      ],
    );
    renderLearningList(
      "learning-confirmed",
      preferences.filter((item) => item.status === "confirmed"),
      "Nothing confirmed yet. Jarvis will not treat candidates as truth.",
      [{ label: "Forget", decision: "forget", className: "secondary danger-subtle" }],
    );
    renderLearningList(
      "learning-rejected",
      preferences.filter((item) => item.status === "rejected"),
      "No rejected guidance. Rejected candidates remain visible here.",
      [{ label: "Reconsider", decision: "reconsider", className: "secondary" }],
    );
    const evidence = $("learning-evidence");
    evidence.replaceChildren();
    const observations = Array.isArray(data.recent_observations) ? data.recent_observations : [];
    if (!observations.length) {
      evidence.appendChild(el("div", "empty-state", "No explicit observations recorded yet."));
    } else {
      observations.forEach((observation) => {
        const row = el("div", "learning-evidence-row");
        const copy = el("span", "", `${observation.subject}: ${observation.value}`);
        copy.appendChild(el("small", "", `${observation.signal} · ${observation.source} · ${String(observation.ts || "").replace("T", " ")}`));
        row.append(copy, el("b", "", String(observation.domain || "general").toUpperCase()));
        evidence.appendChild(row);
      });
    }
    status.textContent = `${data.profile || "Giovanni"} · learning ledger synchronized · inferences remain action-locked.`;
  } catch (error) {
    status.textContent = `Learning ledger unavailable: ${error.message}`;
    status.className = "inline-status error-state";
    ["learning-candidates", "learning-confirmed", "learning-rejected", "learning-evidence"].forEach((id) => {
      const target = $(id);
      target.replaceChildren();
      const retry = el("button", "secondary", "Retry learning ledger");
      retry.onclick = loadLearning;
      target.append(el("div", "empty-state", "This section could not be loaded."), retry);
    });
  }
}

async function decideLearningPreference(preferenceId, decision, button) {
  button.disabled = true;
  const previous = button.textContent;
  button.textContent = `${decision === "confirm" ? "Confirming" : decision === "reject" ? "Rejecting" : decision === "reconsider" ? "Reopening" : "Forgetting"}…`;
  try {
    await api(`/api/learning/preferences/${preferenceId}/decision`, "POST", {
      decision,
      reason: `${decision} selected by Giovanni in the LifeOS Learning Ledger`,
    });
    await loadLearning();
  } catch (error) {
    $("learning-status").textContent = `Learning decision was not saved: ${error.message}`;
    $("learning-status").className = "inline-status error-state";
    button.disabled = false;
    button.textContent = previous;
  }
}

$("learning-submit").onclick = async () => {
  const subject = $("learning-subject").value.trim();
  const value = $("learning-value").value.trim();
  if (!subject || !value) {
    $("learning-status").textContent = "Describe what the preference is about and what Jarvis should learn.";
    return;
  }
  const button = $("learning-submit");
  button.disabled = true;
  $("learning-status").textContent = "Recording explicit evidence…";
  try {
    await api("/api/learning/feedback", "POST", {
      domain: $("learning-domain").value,
      subject,
      value,
      signal: $("learning-signal").value,
      source: "lifeos_ui",
      context: { surface: "learning_ledger" },
    });
    $("learning-subject").value = "";
    $("learning-value").value = "";
    await loadLearning();
  } catch (error) {
    $("learning-status").textContent = `Evidence was not recorded: ${error.message}`;
    $("learning-status").className = "inline-status error-state";
  } finally {
    button.disabled = false;
  }
};

// ---------- Review + profiles ----------
function reviewMoney(value) {
  return Number(value || 0).toLocaleString(undefined, { style: "currency", currency: "USD" });
}

function reviewDate(value) {
  if (!value) return "–";
  return new Date(`${value}T12:00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function renderReviewSignals(id, items, emptyMessage) {
  const target = $(id);
  target.replaceChildren();
  if (!Array.isArray(items) || !items.length) {
    target.className = "review-signal-list muted";
    target.textContent = emptyMessage;
    return;
  }
  target.className = "review-signal-list";
  items.forEach((item) => {
    const row = el("div", "review-signal");
    row.append(
      el("span", "review-domain", String(item.domain || "signal").toUpperCase()),
      el("strong", "", item.title || "Signal"),
      el("p", "", item.evidence || "No evidence detail returned."),
    );
    target.appendChild(row);
  });
}

function renderReviewPriorities(items) {
  const target = $("review-priorities");
  target.replaceChildren();
  target.className = "review-priority-list";
  (items || []).slice(0, 3).forEach((item, index) => {
    const row = el("div", "review-priority");
    row.append(
      el("span", "review-priority-number", String(index + 1).padStart(2, "0")),
      el("strong", "", item.title || "Maintain course"),
      el("p", "", item.evidence || "No corrective action is supported."),
    );
    target.appendChild(row);
  });
}

function renderReviewCoverage(confidence) {
  const target = $("review-coverage");
  target.replaceChildren();
  Object.entries(confidence.coverage || {}).forEach(([name, value]) => {
    const row = el("div", "review-coverage-row");
    const label = el("span", "", name);
    const meter = el("div", "review-meter");
    const fill = el("span", "");
    fill.style.width = `${Math.max(0, Math.min(100, Number(value || 0)))}%`;
    meter.appendChild(fill);
    row.append(label, meter, el("strong", "", `${Number(value || 0)}%`));
    target.appendChild(row);
  });
}

function renderReviewTrends(trends) {
  const target = $("review-trends");
  target.replaceChildren();
  const labels = { protein: "Protein", steps: "Steps", spending: "Spending", workouts: "Workouts" };
  Object.entries(trends || {}).forEach(([key, item]) => {
    const card = el("div", `review-trend ${item.favorable === true ? "good" : item.favorable === false ? "warn" : ""}`);
    const percent = item.percent === null ? "NEW SIGNAL" : `${item.percent > 0 ? "+" : ""}${item.percent}%`;
    let current = Number(item.current || 0).toLocaleString();
    if (key === "protein") current += "g";
    if (key === "spending") current = reviewMoney(item.current);
    card.append(
      el("span", "", labels[key] || key),
      el("strong", "", current),
      el("small", "", `${percent} · ${String(item.direction || "steady").toUpperCase()}`),
    );
    target.appendChild(card);
  });
}

let weeklyReviewSpeech = "";
let weeklyReviewUtterance = null;
let weeklyReviewAudio = null;
let weeklyReviewAudioUrl = null;

function clearWeeklyReviewAudio() {
  if (weeklyReviewAudio) {
    const audio = weeklyReviewAudio;
    weeklyReviewAudio = null;
    audio.onplay = null;
    audio.onended = null;
    audio.onerror = null;
    audio.pause();
    audio.removeAttribute("src");
    audio.load();
  }
  if (weeklyReviewAudioUrl) URL.revokeObjectURL(weeklyReviewAudioUrl);
  weeklyReviewAudioUrl = null;
}

async function fetchWeeklyReviewAudio(retried = false) {
  const response = await fetch("/api/speech/local", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text: weeklyReviewSpeech }),
    cache: "no-store",
  });
  if (response.status === 401 && !retried && await authenticateBrowser()) {
    return fetchWeeklyReviewAudio(true);
  }
  if (!response.ok) {
    let message = `Local voice request failed (${response.status})`;
    try { message = (await response.json()).detail || message; } catch (_) { /* WAV endpoint may not return JSON. */ }
    throw new Error(message);
  }
  return response.blob();
}

function stopWeeklyReview() {
  if ("speechSynthesis" in window) window.speechSynthesis.cancel();
  clearWeeklyReviewAudio();
  weeklyReviewUtterance = null;
  $("review-stop-btn").disabled = true;
  $("review-speak-btn").disabled = false;
  $("review-speak-status").textContent = "Weekly brief stopped. The full text remains visible.";
}

async function speakWeeklyReview() {
  if (!weeklyReviewSpeech) return;
  const button = $("review-speak-btn");
  button.disabled = true;
  clearWeeklyReviewAudio();
  $("review-speak-status").textContent = "Jarvis is generating the weekly brief with local Piper…";
  try {
    const blob = await fetchWeeklyReviewAudio();
    weeklyReviewAudioUrl = URL.createObjectURL(blob);
    weeklyReviewAudio = new Audio(weeklyReviewAudioUrl);
    weeklyReviewAudio.onplay = () => {
      $("review-stop-btn").disabled = false;
      $("review-speak-status").textContent = "Jarvis is delivering the weekly brief through the X1 audio output.";
    };
    weeklyReviewAudio.onended = () => {
      clearWeeklyReviewAudio();
      $("review-stop-btn").disabled = true;
      button.disabled = false;
      $("review-speak-status").textContent = "Weekly brief complete.";
    };
    weeklyReviewAudio.onerror = () => {
      clearWeeklyReviewAudio();
      $("review-stop-btn").disabled = true;
      button.disabled = false;
      $("review-speak-status").textContent = "Piper audio could not be played. The complete brief remains visible.";
    };
    $("review-stop-btn").disabled = false;
    await weeklyReviewAudio.play();
  } catch (error) {
    clearWeeklyReviewAudio();
    $("review-stop-btn").disabled = true;
    button.disabled = false;
    $("review-speak-status").textContent = `Jarvis voice unavailable: ${error.message}. The complete brief remains visible.`;
  }
}

async function loadReview() {
  $("review-status").className = "inline-status";
  $("review-status").textContent = "Reconciling the last seven days…";
  try {
    const r = await api("/api/review/weekly");
    weeklyReviewSpeech = String(r.speech || "");
    $("review-speech").textContent = weeklyReviewSpeech || "No weekly narration was returned.";
    $("review-score").textContent = r.operating_score === null ? "–" : Number(r.operating_score || 0);
    $("review-verdict").textContent = r.verdict || "Weekly picture available";
    $("review-period").textContent = `${reviewDate(r.period_start)} — ${reviewDate(r.period_end)}`.toUpperCase();
    $("review-confidence").textContent = `${String(r.confidence?.label || "limited").toUpperCase()} EVIDENCE · ${Number(r.confidence?.score || 0)}%`;
    $("review-confidence-state").textContent = String(r.confidence?.label || "limited").toUpperCase();
    renderReviewCoverage(r.confidence || { coverage: {} });
    renderReviewTrends(r.trends);
    renderReviewSignals("review-wins", r.wins, "No strong win is supported yet; keep logging the baseline.");
    renderReviewSignals("review-watch", r.watch, "No material watch item surfaced this week.");
    renderReviewPriorities(r.priorities);
    $("review-policy").textContent = r.policy || "Recommendations remain advisory.";
    const stats = $("review-stats");
    stats.replaceChildren();
    [
      ["Weight change", r.weight.delta_lb === null ? "Not enough readings" : `${r.weight.delta_lb > 0 ? "+" : ""}${r.weight.delta_lb} lb`],
      ["Protein target days", `${r.target_days?.protein || 0} / 7`],
      ["Step target days", `${r.target_days?.steps || 0} / 7`],
      ["Vitamins taken", `${r.target_days?.vitamins || 0} / 7`],
      ["Workouts", Number(r.workouts_this_week || 0).toLocaleString()],
      ["Spending", reviewMoney(r.spending_this_week)],
      ["Money in", reviewMoney(r.money_in)],
      ["Next payday", r.next_payday ? `${r.next_payday.label} · ${reviewDate(r.next_payday.date)}` : "Not available"],
    ].forEach(([label, value]) => {
      const line = el("div", "line");
      line.append(el("span", "", label), el("span", "", value));
      stats.appendChild(line);
    });
    $("review-speak-btn").disabled = !weeklyReviewSpeech;
    $("review-speak-status").textContent = weeklyReviewSpeech ? "Ready for Giovanni." : "No spoken brief is available.";
    $("review-status").textContent = `Weekly intelligence current through ${reviewDate(r.period_end)}.`;
    await loadProfiles();
  } catch (error) {
    weeklyReviewSpeech = "";
    $("review-speak-btn").disabled = true;
    $("review-status").className = "inline-status error-state";
    $("review-status").textContent = `Weekly intelligence is unavailable: ${error.message}`;
    $("review-verdict").textContent = "Weekly evidence could not be loaded";
    $("review-speech").textContent = "Jarvis is holding recommendations until the data connection recovers.";
  }
}

$("review-speak-btn").onclick = speakWeeklyReview;
$("review-stop-btn").onclick = stopWeeklyReview;

async function loadProfiles() {
  const profiles = await api("/api/profiles");
  const sel = $("profile-select");
  sel.replaceChildren();
  profiles.forEach((p) => {
    const opt = document.createElement("option");
    opt.value = p.id; opt.textContent = p.name; opt.selected = p.active;
    sel.appendChild(opt);
  });
  const list = $("profiles");
  list.replaceChildren();
  profiles.forEach((p) => {
    const line = document.createElement("div");
    line.className = "line";
    const name = el("span", "", p.name);
    if (p.active) name.append(" ", el("span", "tag good", "active"));
    line.append(name, el("span", "muted", `${p.protein_target_g}g · ${p.step_target.toLocaleString()} steps`));
    list.appendChild(line);
  });
}

$("profile-select").onchange = async (e) => {
  await api(`/api/profiles/${e.target.value}/activate`, "POST");
  loadToday();
};

$("profile-btn").onclick = async () => {
  const name = $("profile-name").value.trim();
  if (!name) return;
  await api("/api/profiles", "POST", {
    name, protein_target_g: parseFloat($("profile-protein").value) || 100,
  });
  $("profile-name").value = ""; $("profile-protein").value = "";
  loadProfiles();
};

const requestedPanel = new URLSearchParams(window.location.search).get("tab");
activatePanel(requestedPanel || "today");
loadProfiles();

// ---------- Jarvis Command Center ----------
function commandText(id, value) {
  const element = $(id);
  if (element) element.textContent = value;
}

function friendlyEntity(entity) {
  return (entity || "system").replace(/^[^.]+\./, "").replaceAll("_", " ");
}

function commandLoading(isLoading) {
  const refresh = $("command-refresh");
  refresh.disabled = isLoading;
  refresh.classList.toggle("is-loading", isLoading);
  if (isLoading) refresh.lastChild.textContent = " Synchronizing";
  else refresh.lastChild.textContent = " Refresh telemetry";
  commandText("shell-sync", isLoading ? "SYNCING" : "SYNC READY");
}

function commandError(message) {
  const center = $("command");
  center.classList.add("has-error");
  commandText("command-system-state", "LINK DEGRADED");
  commandText("command-system-detail", message || "Unable to reach the LifeOS context service.");
  commandText("command-updated", "SYNC FAILED");
}

async function simulateProposal(proposal, button) {
  button.disabled = true;
  button.textContent = "Simulating…";
  try {
    const response = await api(`/api/actions/${encodeURIComponent(proposal.action_id)}`, "POST", {
      proposal_id: proposal.id,
      confirmed: Boolean(proposal.requires_confirmation),
      dry_run: true,
      requested_by: "command_center",
      data: { message: proposal.summary },
    });
    if (!response.ok) throw new Error(response.detail || "Simulation was not accepted.");
    button.textContent = "Simulated";
    await loadCommandCenter();
    const result = $("simulation-result");
    result.replaceChildren();
    result.className = "simulation-result simulation-success";
    result.append(
      el("strong", "", "PROPOSAL · DRY RUN COMPLETE"),
      el("span", "", `${proposal.summary} was simulated. No house state changed; the outcome is recorded in the audit.`)
    );
  } catch (error) {
    button.disabled = false;
    button.textContent = "Retry simulation";
    commandError(error.message);
  }
}

async function dismissProposal(proposal, button) {
  button.disabled = true;
  button.textContent = "Dismissing…";
  try {
    const response = await api(`/api/proposals/${proposal.id}/dismiss`, "POST", {
      requested_by: "command_center",
    });
    if (!response.ok) throw new Error(response.detail || "Proposal could not be dismissed.");
    await loadCommandCenter();
  } catch (error) {
    button.disabled = false;
    button.textContent = "Dismiss";
    commandError(error.message);
  }
}

function renderCommandProposals(proposals) {
  const proposalList = $("command-proposal-list");
  proposalList.replaceChildren();
  if (!proposals.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = "No pending proposals · automation queue clear";
    proposalList.appendChild(empty);
    return;
  }

  proposals.forEach((proposal) => {
    const row = document.createElement("div");
    row.className = "proposal-row";
    const copy = document.createElement("div");
    const title = document.createElement("strong");
    title.textContent = proposal.summary;
    const reason = document.createElement("div");
    reason.className = "muted";
    reason.textContent = `${proposal.reason || "Context engine recommendation"} · ${proposal.requires_confirmation ? "confirmation required" : "pre-authorized policy"}`;
    copy.append(title, reason);
    const controls = document.createElement("div");
    controls.className = "proposal-controls";
    const risk = document.createElement("span");
    const riskLevel = String(proposal.risk || "low").toLowerCase();
    risk.className = `risk risk-${riskLevel}`;
    risk.textContent = `${riskLevel} risk`;
    const simulate = document.createElement("button");
    simulate.className = "secondary compact";
    simulate.textContent = proposal.requires_confirmation ? "Simulate + confirm" : "Run simulation";
    simulate.title = "Dry run only — no device will be controlled";
    simulate.onclick = () => simulateProposal(proposal, simulate);
    const dismiss = document.createElement("button");
    dismiss.className = "secondary compact dismiss-control";
    dismiss.textContent = "Dismiss";
    dismiss.onclick = () => dismissProposal(proposal, dismiss);
    controls.append(risk, simulate, dismiss);
    row.append(copy, controls);
    proposalList.appendChild(row);
  });
}

function renderCommandEvents(events) {
  const eventList = $("command-event-list");
  eventList.replaceChildren();
  commandText("command-event-count", `${events.length} EVENT${events.length === 1 ? "" : "S"}`);
  if (!events.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = "Telemetry channel quiet · no recent events";
    eventList.appendChild(empty);
    return;
  }
  events.forEach((event) => {
    const row = document.createElement("div");
    row.className = "event-row";
    const marker = document.createElement("span");
    marker.className = "event-marker";
    const time = document.createElement("time");
    const parsedTime = event.ts ? new Date(event.ts.replace(" ", "T")) : null;
    time.textContent = parsedTime && !Number.isNaN(parsedTime.valueOf())
      ? parsedTime.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })
      : "--:--:--";
    const description = document.createElement("span");
    const entity = document.createElement("strong");
    entity.textContent = friendlyEntity(event.entity_id);
    const transition = document.createElement("small");
    transition.textContent = `${event.previous_state || "—"} → ${event.state || event.event_type || "updated"}`;
    description.append(entity, transition);
    row.append(marker, time, description);
    eventList.appendChild(row);
  });
}

function renderHardwareTelemetry(hardware) {
  const list = $("command-hardware-list");
  const definitions = [
    ["camera", "Camera"],
    ["bluetooth", "Bluetooth"],
    ["microphone", "Microphone"],
    ["speakers", "Speakers"],
    ["touchscreen", "Touchscreen"],
    ["external_storage", "External storage"],
    ["battery", "Battery"],
    ["mains", "Mains power"],
    ["temperature", "CPU temperature"],
  ];
  let unknownCount = 0;
  let warningCount = 0;
  list.replaceChildren();
  definitions.forEach(([key, label]) => {
    const rawValue = hardware[key] ?? "unknown";
    const normalized = String(rawValue).trim().toLowerCase();
    const item = document.createElement("div");
    item.className = "hardware-item";
    const marker = document.createElement("span");
    marker.className = "hardware-marker";
    const copy = document.createElement("span");
    const name = document.createElement("small");
    name.textContent = label;
    const value = document.createElement("strong");
    let displayValue = String(rawValue).toUpperCase();
    let state = "good";
    if (normalized === "unknown" || normalized === "unavailable" || normalized === "") {
      state = "unknown";
      displayValue = "NO DATA";
      unknownCount += 1;
    } else if (key === "battery") {
      const level = Number.parseFloat(normalized);
      displayValue = Number.isFinite(level) ? `${Math.round(level)}%` : displayValue;
      if (Number.isFinite(level) && level < 20) { state = "warning"; warningCount += 1; }
    } else if (key === "temperature") {
      const temperature = Number.parseFloat(normalized);
      displayValue = Number.isFinite(temperature) ? `${Math.round(temperature)}°C` : displayValue;
      if (Number.isFinite(temperature) && temperature >= 80) { state = "warning"; warningCount += 1; }
    } else if (["off", "disconnected", "not_connected"].includes(normalized)) {
      state = "warning";
      displayValue = key === "mains" ? "BATTERY" : "OFFLINE";
      warningCount += 1;
    } else if (key === "mains") {
      displayValue = "CONNECTED";
    }
    item.classList.add(`hardware-${state}`);
    name.textContent = label;
    value.textContent = displayValue;
    copy.append(name, value);
    item.append(marker, copy);
    list.appendChild(item);
  });
  const verdict = $("command-hardware-verdict");
  verdict.className = unknownCount ? "telemetry-unknown" : warningCount ? "telemetry-warning" : "";
  verdict.textContent = unknownCount
    ? `${unknownCount} UNAVAILABLE`
    : warningCount
      ? `${warningCount} REVIEW`
      : "NODE NOMINAL";
}

function renderVoiceTelemetry(voice = {}, conversation = {}) {
  const ready = voice.ready === true;
  const muted = voice.microphone_muted === true || voice.state === "muted";
  const state = $("voice-io-state");
  state.className = muted ? "voice-state-muted" : ready ? "voice-state-ready" : "voice-state-degraded";
  state.textContent = muted ? "PRIVACY MUTED" : ready ? "LISTENING" : "VOICE DEGRADED";
  commandText("voice-endpoint", String(voice.endpoint || "UNKNOWN").toUpperCase());
  commandText("voice-wake-word", String(voice.wake_word || "hey_jarvis").replaceAll("_", " ").toUpperCase());
  const signal = voice.signal && typeof voice.signal === "object" ? voice.signal : {};
  const dbfs = Number(signal.dbfs);
  const signalLabel = signal.tested
    ? `${String(signal.signal || "unknown").toUpperCase()}${Number.isFinite(dbfs) ? ` · ${dbfs.toFixed(1)} DBFS` : ""}`
    : "NOT TESTED";
  commandText("voice-signal", signalLabel);
  const pipeline = [voice.pipewire, voice.satellite, voice.assistant_state]
    .filter(Boolean)
    .map((value) => String(value).toUpperCase())
    .join(" / ");
  commandText("voice-pipeline", pipeline || "AWAITING");
  commandText(
    "voice-conversation",
    voice.continuous_conversation === true ? "FOLLOW-UPS ACTIVE" : "SINGLE TURN",
  );
  commandText(
    "voice-interrupt",
    voice.interrupt_word ? `SAY ${String(voice.interrupt_word).toUpperCase()}` : "UNAVAILABLE",
  );
  commandText("voice-reason", voice.reason || "Voice telemetry awaiting detail.");
  const privacy = voice.privacy && typeof voice.privacy === "object" ? voice.privacy : {};
  commandText("voice-storage", privacy.raw_audio_stored === false ? "NO RAW AUDIO STORED" : "STORAGE POLICY UNKNOWN");
  commandText("voice-mute", muted ? "MICROPHONE MUTED" : ready ? "MICROPHONE OPEN" : "MIC STATE UNKNOWN");
  const phase = String(conversation.phase || "unavailable").replaceAll("_", " ").toUpperCase();
  commandText("conversation-phase", conversation.connected === true ? phase : "VOICE LINK OFFLINE");
  commandText(
    "conversation-user",
    conversation.phase === "listening"
      ? "Listening…"
      : conversation.last_user || "Say “Hey Jarvis” to begin.",
  );
  commandText(
    "conversation-assistant",
    conversation.last_assistant || "The latest response will appear here.",
  );
  const retention = conversation.privacy?.retention;
  commandText(
    "conversation-privacy",
    retention
      ? `EPHEMERAL · ${String(retention).toUpperCase()} · NO AUDIO STORED`
      : "EPHEMERAL · LATEST EXCHANGE ONLY · NO AUDIO STORED",
  );
}

function renderSupervisor(supervisor = {}) {
  const components = Array.isArray(supervisor.components) ? supervisor.components : [];
  const attention = Number(supervisor.attention_count || 0);
  const active = supervisor.state === "online";
  const stale = supervisor.state === "stale" || supervisor.state === "degraded";
  const paused = supervisor.repairs_enabled === false;
  const state = $("supervisor-state");
  state.className = !active ? (stale ? "voice-state-muted" : "voice-state-awaiting") : paused || attention ? "voice-state-muted" : "voice-state-ready";
  state.textContent = stale ? "SUPERVISOR STALE" : !active ? "AWAITING HEARTBEAT" : paused ? "MAINTENANCE PAUSED" : attention ? `${attention} NEED ATTENTION` : "WATCH ACTIVE";
  commandText("supervisor-healthy", String(supervisor.healthy_count ?? "—"));
  commandText("supervisor-attention", String(supervisor.attention_count ?? "—"));
  commandText("supervisor-policy", supervisor.policy || "Allow-listed reversible service restarts only.");
  const list = $("supervisor-components");
  list.replaceChildren();
  if (!components.length) {
    const empty = document.createElement("span");
    empty.className = "muted";
    empty.textContent = "Supervisor telemetry pending.";
    list.appendChild(empty);
  } else {
    components.forEach((component) => {
      const row = document.createElement("div");
      row.className = `supervisor-row supervisor-${String(component.state || "unknown").toLowerCase()}`;
      const name = document.createElement("strong");
      name.textContent = component.label || friendlyEntity(component.id);
      const detail = document.createElement("span");
      detail.textContent = `${String(component.state || "unknown").toUpperCase()} · ${String(component.decision || "observing").replaceAll("_", " ").toUpperCase()}`;
      row.title = component.detail || "No diagnostic detail";
      row.append(name, detail);
      list.appendChild(row);
    });
  }
  const boundaries = $("supervisor-boundaries");
  boundaries.replaceChildren();
  (Array.isArray(supervisor.protected_boundaries) ? supervisor.protected_boundaries : []).forEach((boundary) => {
    const item = document.createElement("span");
    item.textContent = `NO AUTO ${String(boundary).replaceAll("_", " ").toUpperCase()}`;
    boundaries.appendChild(item);
  });
}

function formatPerceptionTime(value) {
  if (!value) return "NO OBSERVATION";
  const normalized = typeof value === "string" ? value.replace(" ", "T") : value;
  const observed = new Date(normalized);
  if (Number.isNaN(observed.valueOf())) return "TIME UNAVAILABLE";
  return observed.toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).toUpperCase();
}

function renderPerception(perception) {
  const panel = $("command-perception");
  const rawPresence = String(perception.visual_presence ?? "unknown").trim().toLowerCase();
  const rawSignalAvailable = rawPresence === "on" || rawPresence === "off";
  const linkStateProvided = perception.link_state !== undefined
    && perception.link_state !== null
    && String(perception.link_state).trim() !== "";
  const linkState = String(perception.link_state || "").trim().toLowerCase();
  const hasSignal = rawSignalAvailable && (!linkStateProvided || linkState === "online");
  const occupied = hasSignal && (rawPresence === "on" || Boolean(perception.room_occupied));
  const state = !hasSignal ? "awaiting" : occupied ? "occupied" : "clear";
  const confidence = Number(perception.confidence);
  const gesture = perception.last_gesture && typeof perception.last_gesture === "object"
    ? perception.last_gesture
    : {};
  const privacy = perception.privacy || {};
  const retention = Number(privacy.metadata_retention_hours);

  panel.classList.remove("perception-awaiting", "perception-occupied", "perception-clear");
  panel.classList.add(`perception-${state}`);
  commandText("perception-presence", state === "occupied" ? "OCCUPIED" : state === "clear" ? "CLEAR" : "AWAITING SIGNAL");
  commandText("perception-confidence", Number.isFinite(confidence) && hasSignal
    ? `CONFIDENCE ${Math.round(Math.max(0, Math.min(1, confidence)) * 100)}%`
    : "CONFIDENCE —");
  commandText("perception-observed", formatPerceptionTime(perception.last_observation_at));
  const signal = String(perception.presence_source || "awaiting").replaceAll("_", " ").toUpperCase();
  const faceCount = Math.max(0, Number(perception.face_count) || 0);
  commandText("perception-signal", `${signal} / ${faceCount} ${faceCount === 1 ? "FACE" : "FACES"}`);
  commandText("perception-gesture", gesture.gesture
    ? `${String(gesture.gesture).replaceAll("_", " ").toUpperCase()} / ${String(gesture.app || "BROWSER").toUpperCase()}`
    : "NONE / —");
  commandText("perception-processing", `${String(privacy.processing || "local").toUpperCase()} PROCESSING`);
  commandText("perception-storage", privacy.raw_frames_stored ? "RAW-FRAME STORAGE ENABLED" : "NO RAW-FRAME STORAGE");
  commandText("perception-identity", privacy.identity_recognition ? "IDENTITY RECOGNITION ENABLED" : "IDENTITY RECOGNITION DISABLED");
  commandText("perception-retention", Number.isFinite(retention) ? `METADATA RETENTION ${retention}H` : "METADATA RETENTION —");
  commandText("perception-authority", privacy.gesture_can_confirm_actions
    ? "GESTURES MAY CONFIRM ACTIONS"
    : "GESTURES CANNOT CONFIRM ACTIONS");
}

function ratioPercent(value, target) {
  return `${Math.max(0, Math.min(100, (Number(value) / Math.max(1, Number(target))) * 100))}%`;
}

function renderLifeOSPulse(lifeos) {
  const body = lifeos.body || {};
  const vault = lifeos.vault || {};
  commandText("lifeos-profile", `PROFILE ${String(lifeos.profile || "—").toUpperCase()}`);
  commandText("lifeos-score", Number.isFinite(Number(lifeos.daily_score)) ? String(lifeos.daily_score) : "—");
  commandText("lifeos-protein", `${Math.round(body.protein_g || 0)} / ${body.protein_target_g || "—"}g`);
  commandText("lifeos-steps", `${Number(body.steps || 0).toLocaleString()} / ${Number(body.step_target || 0).toLocaleString()}`);
  commandText("lifeos-water", `${body.water || 0} / ${body.water_target || "—"}`);
  commandText("lifeos-vitamins", body.vitamins_taken ? "COMPLETE" : "OPEN");
  commandText("lifeos-runway", Number.isFinite(Number(vault.left_after_due_bills))
    ? Number(vault.left_after_due_bills).toLocaleString([], { style: "currency", currency: "USD", maximumFractionDigits: 0 })
    : "—");
  $("lifeos-protein-fill").style.width = ratioPercent(body.protein_g, body.protein_target_g);
  $("lifeos-steps-fill").style.width = ratioPercent(body.steps, body.step_target);
  $("lifeos-water-fill").style.width = ratioPercent(body.water, body.water_target);
  const priorities = $("lifeos-priorities");
  priorities.replaceChildren();
  const items = Array.isArray(lifeos.priorities) ? lifeos.priorities : [];
  if (!items.length) {
    priorities.textContent = "No open priorities · daily baseline complete";
    return;
  }
  items.forEach((priority) => {
    const item = document.createElement("span");
    item.className = `priority-token priority-${String(priority.domain || "lifeos").toLowerCase()}`;
    item.textContent = priority.label;
    priorities.appendChild(item);
  });
}

function renderCapabilities(capabilities) {
  const list = $("command-capability-list");
  list.replaceChildren();
  (capabilities || []).forEach((capability) => {
    const item = document.createElement("div");
    item.className = `capability-item ${capability.ready ? "is-ready" : "needs-dependency"}`;
    const marker = document.createElement("span");
    marker.className = "capability-marker";
    const copy = document.createElement("span");
    const name = document.createElement("strong");
    name.textContent = capability.name;
    const detail = document.createElement("small");
    detail.textContent = capability.ready ? "READY" : `NEEDS ${capability.dependency}`;
    copy.append(name, detail);
    item.append(marker, copy);
    list.appendChild(item);
  });
}

function renderAudit(audit) {
  const list = $("command-audit-list");
  list.replaceChildren();
  if (!audit.length) { list.textContent = "No recorded simulations or proposal decisions."; return; }
  audit.forEach((entry) => {
    const row = document.createElement("div");
    row.className = "audit-row";
    const action = document.createElement("span");
    action.textContent = friendlyEntity(entry.action_id);
    const outcome = document.createElement("strong");
    outcome.textContent = String(entry.outcome || "recorded").toUpperCase();
    row.append(action, outcome);
    list.appendChild(row);
  });
}

async function runBehaviorSimulation(behavior, button) {
  document.querySelectorAll(".simulation-trigger").forEach((control) => { control.disabled = true; });
  button.textContent = `Simulating ${behavior}…`;
  const result = $("simulation-result");
  result.className = "simulation-result is-loading";
  result.textContent = "Computing predicted actions and policy gates.";
  try {
    const simulation = await api(`/api/simulations/${behavior}`, "POST", { overrides: {} });
    result.replaceChildren();
    result.className = "simulation-result";
    const heading = document.createElement("strong");
    heading.textContent = `${String(simulation.behavior || behavior).toUpperCase()} · DRY RUN`;
    result.appendChild(heading);
    const predictions = simulation.predicted_actions || [];
    predictions.forEach((prediction) => {
      const row = document.createElement("div");
      row.className = "simulation-action";
      const copy = document.createElement("span");
      copy.textContent = prediction.policy?.name || friendlyEntity(prediction.action_id);
      const policy = document.createElement("small");
      const risk = prediction.policy?.risk || "low";
      policy.textContent = `${risk} risk · ${prediction.policy?.requires_confirmation ? "confirmation required" : "policy permits"}`;
      row.append(copy, policy);
      result.appendChild(row);
    });
    if (!predictions.length) {
      const observation = document.createElement("div");
      observation.className = "simulation-observation";
      if (behavior === "perception") {
        const scenario = simulation.scenario || {};
        const presence = scenario.visual_presence === true ? "OCCUPIED" : scenario.visual_presence === false ? "CLEAR" : "AWAITING SIGNAL";
        const confidence = Number(scenario.confidence);
        const summary = document.createElement("span");
        summary.textContent = `Observation metadata: ${presence}${Number.isFinite(confidence) ? ` · ${Math.round(confidence * 100)}% confidence` : ""}.`;
        const boundary = document.createElement("small");
        boundary.textContent = "No actions predicted · no house state changed · no raw frames stored.";
        observation.append(summary, boundary);
      } else {
        observation.textContent = "No actions predicted. The simulation changed no house state.";
      }
      result.appendChild(observation);
    }
  } catch (error) {
    result.className = "simulation-result has-error";
    result.textContent = error.message || "Simulation service unavailable.";
  } finally {
    document.querySelectorAll(".simulation-trigger").forEach((control) => {
      control.disabled = false;
      control.textContent = `Simulate ${control.dataset.behavior}`;
    });
  }
}

function renderCommandPolicies(actions) {
  const policyList = $("command-policy-list");
  policyList.replaceChildren();
  Object.entries(actions).forEach(([id, action]) => {
    const row = document.createElement("div");
    row.className = "policy-row";
    const name = document.createElement("span");
    const title = document.createElement("strong");
    title.textContent = action.name;
    const code = document.createElement("small");
    code.textContent = id;
    name.append(title, code);
    const policy = document.createElement("span");
    const riskLevel = String(action.risk || "low").toLowerCase();
    policy.className = `risk risk-${riskLevel}`;
    policy.textContent = action.remote_execution
      ? `${riskLevel} · ${action.requires_confirmation ? "confirm" : "permitted"}`
      : `${riskLevel} · local only`;
    row.append(name, policy);
    policyList.appendChild(row);
  });
}

async function loadCommandCenter() {
  commandLoading(true);
  $("command").classList.remove("has-error", "is-attention", "is-nominal", "is-awaiting");
  try {
    const [payload, audit] = await Promise.all([
      api("/api/command-center?event_limit=40"),
      api("/api/actions/audit?limit=6"),
    ]);
    const context = payload.context || {};
    const proposals = Array.isArray(payload.proposals) ? payload.proposals : [];
    const actions = payload.actions || {};
    const capabilities = Array.isArray(payload.capabilities) ? payload.capabilities : [];
    const security = context.security || {};
    const occupancy = context.occupancy || {};
    const hardware = context.hardware || {};
    const telemetry = context.telemetry || {};
    const lifeos = context.lifeos || {};
    const perception = context.perception || {};
    const sanctuary = payload.sanctuary || context.sanctuary || {};
    const openPerimeter = security.open_perimeter || [];
    const hazards = security.active_hazards || [];
    const people = (occupancy.people_home || []).map(friendlyEntity);
    const secure = Boolean(security.secure);
    const alarmState = String(security.alarm || "unknown").toLowerCase();
    const awaiting = telemetry.link_state === "awaiting_data";
    $("command").classList.add(awaiting ? "is-awaiting" : secure ? "is-nominal" : "is-attention");
    commandText("command-mode", String(sanctuary.mode || context.house_mode || "unknown").toUpperCase());
    commandText("command-occupancy", people.length ? people.join(", ") : "VACANT");
    commandText("command-alarm", alarmState.toUpperCase());
    commandText("command-perimeter", openPerimeter.length ? `${openPerimeter.length} OPEN` : "SECURE");
    commandText("command-hazards", hazards.length ? `${hazards.length} ACTIVE` : "CLEAR");
    commandText("command-link", String(telemetry.link_state || "unknown").replaceAll("_", " ").toUpperCase());
    commandText("command-system-state", awaiting ? "AWAITING DATA" : secure ? "NOMINAL" : "ATTENTION");
    commandText(
      "command-system-detail",
      awaiting
        ? "Context engine online. Waiting for the first Home Assistant event."
        : secure
        ? `${sanctuary.reason || "Sanctuary state synchronized."} ${context.pending_proposals || proposals.length} pending proposal(s). ${(sanctuary.missing_capabilities || []).length ? `Not commissioned: ${sanctuary.missing_capabilities.join(", ")}.` : "Perimeter integrity confirmed."}`
        : `Review required: ${[...openPerimeter, ...hazards].map(friendlyEntity).join(", ") || (["unknown", "unavailable"].includes(alarmState) ? "alarm telemetry is unknown" : "alarm state is not secure")}.`
    );
    commandText("command-updated", `SYNC ${new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`);
    commandText("command-proposal-count", `${proposals.length} PENDING`);
    renderCommandProposals(proposals);
    renderLifeOSPulse(lifeos);
    renderHardwareTelemetry(hardware);
    renderVoiceTelemetry(context.voice || {}, context.conversation || {});
    renderSupervisor(context.supervisor || {});
    renderPerception(perception);
    renderCapabilities(capabilities);
    renderCommandEvents(context.recent_events || []);
    renderCommandPolicies(actions);
    renderAudit(Array.isArray(audit) ? audit : []);
  } catch (error) {
    commandError(error.message);
  } finally {
    commandLoading(false);
  }
}

$("command-refresh").onclick = loadCommandCenter;
document.querySelectorAll(".simulation-trigger").forEach((button) => {
  button.onclick = () => runBehaviorSimulation(button.dataset.behavior, button);
});
$("command-fullscreen").onclick = () => {
  if (document.fullscreenElement) document.exitFullscreen();
  else document.documentElement.requestFullscreen();
};
document.addEventListener("fullscreenchange", () => {
  $("command-fullscreen").lastChild.textContent = document.fullscreenElement
    ? " Exit full screen"
    : " Full screen";
});

// ---------- cross-surface synchronization ----------
let operatingSyncTimer = null;

function activeSurface() {
  return document.querySelector(".tab.active")?.dataset.tab || "today";
}

function runSafely(loader) {
  Promise.resolve().then(loader).catch(() => {
    // Each surface owns its visible degraded/error state. A background refresh
    // must never replace another panel's status or create an unhandled promise.
  });
}

function refreshOperatingPicture({ mutation = false } = {}) {
  const active = activeSurface();
  if (mutation || active === "today") runSafely(loadToday);
  if (active === "budget") runSafely(loadFinance);
  if (active === "body") runSafely(loadBody);
  if (active === "todo") runSafely(loadShopping);
  if (active === "learning") runSafely(loadLearning);
  if (active === "review") runSafely(loadReview);
  if (active === "command") runSafely(loadCommandCenter);
  window.dispatchEvent(new CustomEvent("jarvis:refresh-active", { detail: { active, mutation } }));
}

window.addEventListener("jarvis:data-changed", () => {
  window.clearTimeout(operatingSyncTimer);
  operatingSyncTimer = window.setTimeout(() => refreshOperatingPicture({ mutation: true }), 120);
});

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") refreshOperatingPicture();
});

window.setInterval(() => {
  if (document.visibilityState === "visible") refreshOperatingPicture();
}, 60000);
