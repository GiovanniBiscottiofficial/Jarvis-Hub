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

window.addEventListener("message", (event) => {
  let source;
  try { source = new URL(event.origin); } catch (_) { return; }
  if (source.hostname !== window.location.hostname) return;
  if (!event.data || event.data.type !== "lifeos-ha-auth" || !event.data.token) return;
  homeAssistantToken = String(event.data.token);
  if (tokenWaiter) tokenWaiter(homeAssistantToken);
});

function waitForHomeAssistantToken(timeout = 1600) {
  if (homeAssistantToken) return Promise.resolve(homeAssistantToken);
  return new Promise((resolve) => {
    const timer = window.setTimeout(() => {
      tokenWaiter = null;
      resolve(null);
    }, timeout);
    tokenWaiter = (token) => {
      window.clearTimeout(timer);
      tokenWaiter = null;
      resolve(token);
    };
  });
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
    const token = window.prompt("Enter your LifeOS API token");
    if (!token) return false;
    const auth = await fetch("/api/auth", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token }),
    });
    return auth.ok;
  })();
  try {
    return await authenticationPromise;
  } finally {
    authenticationPromise = null;
  }
}

async function api(path, method = "GET", body, retried = false) {
  const headers = {};
  if (body) headers["Content-Type"] = "application/json";
  const res = await fetch(path, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (res.status === 401 && !retried && await authenticateBrowser()) {
    return api(path, method, body, true);
  }
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || data.message || `LifeOS request failed (${res.status})`);
  return data;
}

// ---------- tabs ----------
document.querySelectorAll(".tab").forEach((btn) =>
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".panel").forEach((p) => p.classList.remove("active"));
    btn.classList.add("active");
    $(btn.dataset.tab).classList.add("active");
    if (btn.dataset.tab === "budget") loadFinance();
    if (btn.dataset.tab === "body") loadBody();
    if (btn.dataset.tab === "review") loadReview();
    if (btn.dataset.tab === "command") loadCommandCenter();
    document.body.classList.toggle("command-active", btn.dataset.tab === "command");
  })
);

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

async function loadToday() {
  const [t, commandPayload] = await Promise.all([
    api("/api/today"),
    api("/api/command-center?event_limit=1").catch(() => ({ context: {} })),
  ]);
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
  renderTodayPriorities(commandPayload.context?.lifeos?.priorities, fallbackPriorities);

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
    eat.onclick = async () => { await api("/api/body/meals/log", "POST", { name: m.name }); loadToday(); };
    const sometimes = document.createElement("button");
    sometimes.className = "secondary";
    sometimes.textContent = "Sometimes";
    sometimes.onclick = () => override(m.name, "sometimes");
    const today = document.createElement("button");
    today.className = "secondary";
    today.textContent = "Today";
    today.onclick = () => override(m.name, "today");
    row.append(eat, sometimes, today);
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
  trigger.disabled = true;
  $("briefing-status").textContent = "Preparing Giovanni’s full briefing…";
  try {
    const b = await withTimeout(api("/api/briefing"), 12000, "Briefing request timed out");
    const speech = String(b.speech || "");
    $("briefing").textContent = speech || "No briefing text was returned.";
    if (!speech) {
      $("briefing-status").textContent = "Briefing is empty. Try again after LifeOS finishes syncing.";
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

async function loadPantry() {
  const pantryEl = $("pantry");
  pantryEl.textContent = "Loading inventory…";
  try {
    const [p, g] = await Promise.all([api("/api/pantry"), api("/api/pantry/grocery-suggestions")]);
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
        const remove = el("button", "secondary", "Remove");
        remove.onclick = async () => {
          remove.disabled = true;
          try {
            await api(`/api/pantry/items/${item.id}`, "DELETE");
            $("pantry-status").textContent = `${item.name} removed from local inventory.`;
            loadPantry();
          } catch (error) { $("pantry-status").textContent = error.message; remove.disabled = false; }
        };
        line.append(identity, remove);
        pantryEl.appendChild(line);
      });
    }
    const grocery = $("grocery");
    grocery.replaceChildren(el("div", "muted", `Avg ${g.avg_daily_protein_g}g/day vs ${g.target_g}g target`));
    if (!g.suggestions.length) grocery.appendChild(el("div", "empty-state", "No grocery additions suggested"));
    g.suggestions.forEach((suggestion) => {
      const line = document.createElement("div");
      line.className = "line";
      line.append(el("span", "", suggestion.name), el("span", "muted", `${suggestion.protein_g_per_serving}g/serving`));
      grocery.appendChild(line);
    });
  } catch (error) {
    pantryEl.replaceChildren(el("div", "empty-state error-state", `Pantry unavailable · ${error.message}`));
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
    await api("/api/pantry/items", "POST", { name, qty, unit: $("pantry-unit").value.trim(), protein_g_per_serving: protein });
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
  return Boolean((bill.paid_period && (!period || bill.paid_period === period)) || (bill.paid_month && (!month || bill.paid_month === month)));
}

function assignedPaycheck(bill) {
  const explicit = Number(bill.paycheck);
  if (explicit === 1 || explicit === 2) return explicit;
  return Number(bill.due_day) <= 14 ? 1 : 2;
}

async function loadBudgetData() {
  const [o, allBills] = await Promise.all([api("/api/budget/overview"), api("/api/vault/bills")]);
  const runway = $("paycheck-runway");
  runway.replaceChildren();
  const upcoming = [1, 2].map((number) => (o.paydays || []).find((payday) => Number(payday.paycheck) === number)).filter(Boolean);
  if (!upcoming.length) runway.appendChild(el("div", "empty-state error-state", "Payday calendar unavailable"));
  upcoming.forEach((payday) => {
    const instrument = el("article", `paycheck-instrument paycheck-${payday.paycheck}`);
    const head = el("div", "paycheck-head");
    head.append(el("span", "paycheck-label", `${payday.label || `Paycheck ${payday.paycheck}`} · ${Number(payday.paycheck) === 1 ? "MONTH-END" : "15TH"}`), el("strong", "paycheck-countdown", countdown(payday.days_away)));
    const date = new Date(`${payday.date}T12:00:00`);
    const dateText = Number.isNaN(date.valueOf()) ? payday.date : date.toLocaleDateString([], { weekday: "short", month: "short", day: "numeric", year: "numeric" });
    const stack = allBills.filter((bill) => assignedPaycheck(bill) === Number(payday.paycheck));
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
    <div class="line"><span>Bucket contributions</span><span>−$${o.bucket_contribution.toFixed(2)}</span></div>`;
  const auditCls =
    o.audit === "balanced" ? "good" : o.audit === "buffered" ? "" : "warn";
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
  [1, 2].forEach((paycheck) => {
    const bills = allBills.filter((bill) => assignedPaycheck(bill) === paycheck);
    const stackPayday = upcoming.find((payday) => Number(payday.paycheck) === paycheck);
    const stack = el("section", "bill-stack");
    const stackHead = el("header", "bill-stack-head");
    stackHead.append(el("strong", "", `PAYCHECK ${paycheck}`), el("span", "", `${bills.length} ITEMS · ${money(bills.reduce((sum, bill) => sum + Number(bill.amount || 0), 0))}`));
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
  acc.innerHTML += `<div class="line"><span>Protected savings <span class="muted">(never counted as spendable)</span></span>
    <span>$${o.protected_cash.toFixed(2)}</span></div>
    <div class="line"><span>Free pocket cash <span class="muted">(OnePay after commitments)</span></span>
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
        <span class="muted">+$${g.monthly.toFixed(0)}/mo</span></span>`;
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
  dl.innerHTML = o.debts.length ? "" : '<div class="muted">Debt free 🎉</div>';
  o.debts.forEach((d) => {
    const pct = d.total ? Math.min(100, ((d.total - d.remaining) / d.total) * 100) : 0;
    const div = document.createElement("div");
    div.className = "bar-row";
    div.innerHTML = `<span>${d.name}</span>
      <div class="bar"><div class="fill" style="width:${pct}%"></div></div>
      <span>$${d.remaining.toFixed(0)} left</span>`;
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
    dl.innerHTML += `<div class="line"><strong>Total remaining</strong>
      <strong>$${o.total_debt.toFixed(2)}</strong></div>`;
  }

  $("budget-networth").innerHTML =
    o.assets
      .map((a) => `<div class="line"><span>${a.name}
        <span class="muted">(+$${a.per_paycheck.toFixed(2)}/check)</span></span>
        <span>$${a.balance.toFixed(2)}</span></div>`)
      .join("") +
    `<div class="line"><strong>Net worth</strong><strong>$${o.net_worth.toFixed(2)}</strong></div>`;

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
  if (isNaN(balance)) return;
  await api(`/api/vault/accounts/${$("bal-account").value}/balance`, "PUT", { balance });
  $("bal-amount").value = "";
  loadFinance();
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
    ["Available", `$${plan.total_available.toFixed(2)}`, false],
    ["Unpaid bills", `$${plan.unpaid_bills_total.toFixed(2)}`, false],
    ["Leftover after bills", `$${plan.leftover_after_bills.toFixed(2)}`, true],
  ].forEach(([label, value, strong]) => {
    const line = el("div", "line");
    line.append(el(strong ? "strong" : "span", "", label), el(strong ? "strong" : "span", "", value));
    pl.appendChild(line);
  });
  plan.recommendations.forEach((r) => {
    const line = document.createElement("div");
    line.className = "line";
    const cls = r.recommend === "pay now" ? "good" : "warn";
    const bill = el("span", "", r.bill);
    bill.append(" ", el("span", "muted", `(due ${r.due_day}, ${r.status})`));
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

// ---------- Review + profiles ----------
async function loadReview() {
  const r = await api("/api/review/weekly");
  $("review-speech").textContent = r.speech;
  const stats = $("review-stats");
  stats.replaceChildren();
  [
    ["Weight change", r.weight.delta_lb === null ? "–" : `${r.weight.delta_lb} lb`],
    ["Avg protein", `${r.avg_daily_protein_g}g / ${r.protein_target_g}g`],
    ["Avg steps", r.avg_daily_steps.toLocaleString()],
    ["Money in", `$${r.money_in.toFixed(2)}`],
    ["Bills paid this month", `$${r.bills_paid_this_month.toFixed(2)}`],
    ["Treats / workouts", `${r.treats_this_week} / ${r.workouts_this_week}`],
    ["Streaks", `vitamins ${r.streaks.vitamins}d · steps ${r.streaks.steps}d`],
  ].forEach(([label, value]) => {
    const line = el("div", "line");
    line.append(el("span", "", label), el("span", "", value));
    stats.appendChild(line);
  });
  loadProfiles();
}

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

loadToday();
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
