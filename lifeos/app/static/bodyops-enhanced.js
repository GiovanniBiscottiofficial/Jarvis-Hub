(function () {
  "use strict";

  const state = { loading: false, loaded: false, data: null };
  const byId = (id) => document.getElementById(id);
  const node = (tag, className, text) => {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (text !== undefined) item.textContent = text;
    return item;
  };
  const append = (parent, ...children) => {
    children.filter(Boolean).forEach((child) => parent.appendChild(child));
    return parent;
  };
  const formatNumber = (value, suffix = "") =>
    value === null || value === undefined ? "Awaiting data" : `${Number(value).toLocaleString()}${suffix}`;

  function metric(label, value, note) {
    const box = node("div", "bodyops-metric");
    append(box, node("span", "bodyops-label", label), node("strong", "", value));
    if (note) box.appendChild(node("small", "", note));
    return box;
  }

  function section(kicker, title, className = "") {
    const card = node("section", `bodyops-card ${className}`.trim());
    const heading = node("div", "bodyops-heading");
    append(heading, node("span", "bodyops-kicker", kicker), node("h3", "", title));
    card.appendChild(heading);
    return card;
  }

  function list(items, emptyText) {
    const container = node("ul", "bodyops-list");
    if (!items || !items.length) {
      container.appendChild(node("li", "bodyops-empty", emptyText));
      return container;
    }
    items.forEach((item) => container.appendChild(node("li", "", item)));
    return container;
  }

  function renderReadiness(data) {
    const card = section("READINESS / EXPLAINABLE", "Today’s operating range", "bodyops-readiness");
    const top = node("div", "bodyops-readiness-top");
    const score = append(node("div", `bodyops-score band-${data.band}`),
      node("strong", "", String(data.score)), node("span", "", "/ 100"));
    const summary = node("div", "bodyops-readiness-copy");
    append(summary,
      node("span", "bodyops-band", data.band.toUpperCase()),
      node("p", "", data.explanation),
      node("small", "", `${data.confidence_percent}% signal confidence · missing data receives neutral credit.`));
    append(top, score, summary);
    card.appendChild(top);
    const components = node("div", "bodyops-components");
    data.components.forEach((item) => {
      const row = node("div", `bodyops-component ${item.available ? "available" : "awaiting"}`);
      const copy = node("div", "");
      append(copy, node("strong", "", item.label), node("small", "", item.reason));
      append(row, copy, node("span", "", `${item.points}/${item.maximum}`));
      components.appendChild(row);
    });
    card.appendChild(components);
    return card;
  }

  function makeSelect(id, label, options) {
    const wrapper = node("label", "bodyops-field");
    wrapper.appendChild(node("span", "", label));
    const select = node("select");
    select.id = id;
    select.appendChild(new Option("Not entered", ""));
    options.forEach(([value, text]) => select.appendChild(new Option(text, String(value))));
    wrapper.appendChild(select);
    return wrapper;
  }

  function renderCheckin(checkin) {
    const card = section("CHECK-IN / SELF-REPORTED", "Tell Jarvis how you feel", "bodyops-checkin");
    card.appendChild(node("p", "bodyops-note", "A 20-second check-in improves today’s guidance. It is wellness context, not a diagnosis."));
    const form = node("form", "bodyops-checkin-form");
    form.id = "bodyops-checkin-form";
    const sleep = node("label", "bodyops-field");
    append(sleep, node("span", "", "Sleep hours"));
    const sleepInput = node("input");
    sleepInput.id = "bodyops-sleep";
    sleepInput.type = "number";
    sleepInput.min = "0";
    sleepInput.max = "24";
    sleepInput.step = "0.25";
    sleepInput.inputMode = "decimal";
    if (checkin && checkin.sleep_hours !== null) sleepInput.value = checkin.sleep_hours;
    sleep.appendChild(sleepInput);
    const scale = [[1, "1 · Low"], [2, "2"], [3, "3 · Moderate"], [4, "4"], [5, "5 · High"]];
    append(form, sleep,
      makeSelect("bodyops-sleep-quality", "Sleep quality", scale),
      makeSelect("bodyops-energy", "Energy", scale),
      makeSelect("bodyops-mood", "Mood", scale),
      makeSelect("bodyops-soreness", "Soreness", [[1, "1 · None"], [2, "2"], [3, "3 · Moderate"], [4, "4"], [5, "5 · High"]]));
    if (checkin) {
      ["sleep_quality", "energy", "mood", "soreness"].forEach((key) => {
        const control = byId(`bodyops-${key.replace("_", "-")}`) || form.querySelector(`#bodyops-${key.replace("_", "-")}`);
        if (control && checkin[key] !== null) control.value = String(checkin[key]);
      });
    }
    const action = node("div", "bodyops-checkin-action");
    const submit = node("button", "");
    submit.type = "submit";
    submit.textContent = "Save check-in";
    const status = node("span", "bodyops-inline-status", checkin ? "Today’s check-in is saved." : "No check-in yet today.");
    status.id = "bodyops-checkin-status";
    status.setAttribute("role", "status");
    append(action, submit, status);
    append(form, action);
    form.addEventListener("submit", saveCheckin);
    card.appendChild(form);
    return card;
  }

  function renderTargets(targets, completion) {
    const card = section("ADAPTIVE PLAN", "Targets that respect recovery", "bodyops-targets");
    const grid = node("div", "bodyops-metric-grid");
    append(grid,
      metric("Protein", `${targets.protein_g} g`, `${completion.protein_percent}% complete`),
      metric("Movement", formatNumber(targets.steps), `${completion.steps_percent}% complete`),
      metric("Hydration", `${targets.water_glasses} glasses`, `${completion.water_percent}% complete`),
      metric("Energy", `${targets.calories} kcal`, "Not reduced by readiness"));
    append(card, grid, node("p", "bodyops-guidance", targets.workout_guidance), node("small", "", targets.reason));
    return card;
  }

  function renderProtocol(loop) {
    const card = section("DAILY PROTOCOL", "Morning launch & evening review", "bodyops-protocol");
    const columns = node("div", "bodyops-protocol-grid");
    const morning = node("div", "bodyops-protocol-column");
    append(morning, node("h4", "", "Morning"), node("blockquote", "", loop.morning.affirmation),
      list([loop.morning.vitamins, loop.morning.hydration], "Morning protocol ready."));
    if (loop.morning.dinner) {
      const dinner = node("div", "bodyops-dinner");
      append(dinner, node("span", "bodyops-label", "CHEF JARVIS / DINNER"),
        node("strong", "", loop.morning.dinner.name),
        node("p", "", loop.morning.dinner.prep),
        node("small", "", `${loop.morning.dinner.protein_g} g protein · ${loop.morning.dinner.stock_coverage}% pantry coverage`));
      morning.appendChild(dinner);
    }
    const evening = node("div", "bodyops-protocol-column");
    append(evening, node("h4", "", "Evening"), node("span", "bodyops-label", "WINS"),
      list(loop.evening.wins, "No completed signals yet."), node("span", "bodyops-label", "STILL OPEN"),
      list(loop.evening.remaining, "Core targets are in good shape."), node("p", "bodyops-tomorrow", loop.evening.tomorrow));
    append(columns, morning, evening);
    card.appendChild(columns);
    return card;
  }

  function sourceQuality(source) {
    const value = String(source || "unknown");
    if (value.startsWith("home_assistant:")) return "MEASURED / HOME ASSISTANT";
    if (value.includes("health_auto_export")) return "IMPORTED / HEALTH";
    return "SELF-REPORTED";
  }

  function renderTrends(weights) {
    const card = section("TREND / SOURCE-AWARE", "Weight direction", "bodyops-trends");
    const grid = node("div", "bodyops-metric-grid compact");
    [7, 30, 90].forEach((days) => {
      const windowData = weights.windows[String(days)];
      grid.appendChild(metric(`${days}-day average`, formatNumber(windowData.average_lb, " lb"), `${windowData.samples} readings`));
    });
    card.appendChild(grid);
    card.appendChild(node("p", "bodyops-note", weights.interpretation));
    const readings = node("div", "bodyops-readings");
    (weights.history || []).slice(0, 5).forEach((item) => {
      const row = node("div", "bodyops-reading");
      append(row, node("strong", "", `${Number(item.weight_lb).toFixed(1)} lb`),
        node("span", "", new Date(String(item.ts).replace(" ", "T")).toLocaleDateString()),
        node("small", "", sourceQuality(item.source)));
      readings.appendChild(row);
    });
    if (!readings.childNodes.length) readings.appendChild(node("p", "bodyops-empty", "No scale or manual readings yet."));
    card.appendChild(readings);
    return card;
  }

  function renderHabits(habits) {
    const card = section("PATTERNS / EVIDENCE", "What Jarvis is learning", "bodyops-habits");
    if (!habits.length) {
      card.appendChild(node("p", "bodyops-empty", "Keep logging normally. Jarvis waits for at least three real entries before suggesting a pattern."));
      return card;
    }
    habits.forEach((habit) => {
      const item = node("article", "bodyops-insight");
      append(item, node("span", "bodyops-label", `${habit.metric.toUpperCase()} · ${habit.confidence.toUpperCase()}`),
        node("strong", "", habit.pattern), node("p", "", habit.suggestion), node("small", "", habit.caution));
      card.appendChild(item);
    });
    return card;
  }

  function renderTimeline(timeline) {
    const card = section("ONE RECORD", "Body timeline", "bodyops-timeline");
    const feed = node("div", "bodyops-feed");
    (timeline || []).slice(0, 16).forEach((event) => {
      const row = node("article", "bodyops-event");
      const stamp = new Date(String(event.ts).replace(" ", "T"));
      const value = typeof event.value === "boolean" ? (event.value ? "Done" : "Pending") : `${event.value ?? "—"} ${event.unit || ""}`.trim();
      append(row, node("time", "", Number.isNaN(stamp.valueOf()) ? event.ts : stamp.toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })),
        node("strong", "", event.label || event.kind), node("span", "", value), node("small", "", `${event.kind.toUpperCase()} · ${String(event.quality).toUpperCase()}`));
      feed.appendChild(row);
    });
    if (!feed.childNodes.length) feed.appendChild(node("p", "bodyops-empty", "Your timeline will fill as Jarvis receives check-ins, meals, movement, hydration, and scale readings."));
    card.appendChild(feed);
    return card;
  }

  function render(loop) {
    const root = byId("bodyops-command-loop");
    if (!root) return;
    const header = node("header", "bodyops-command-header");
    const title = node("div", "");
    append(title, node("span", "bodyops-kicker", "JARVIS / BODY INTELLIGENCE"), node("h2", "", "Body Ops daily loop"),
      node("p", "", "Observe, adapt, fuel, review—without treating missing data as failure."));
    const status = node("div", `bodyops-status band-${loop.readiness.band}`);
    append(status, node("span", "", "TODAY"), node("strong", "", loop.readiness.band.toUpperCase()));
    append(header, title, status);
    const grid = node("div", "bodyops-dashboard");
    append(grid, renderReadiness(loop.readiness), renderCheckin(loop.readiness.checkin),
      renderTargets(loop.targets, loop.completion), renderProtocol(loop), renderTrends(loop.readiness.weights),
      renderHabits(loop.habits), renderTimeline(loop.timeline));
    const policy = node("footer", "bodyops-policy");
    append(policy, node("strong", "", "Your data, with provenance."),
      node("span", "", "Stored locally. Measured and self-reported signals stay labeled. Missing entries are unknown—not failures. Body Ops provides wellness guidance, never medical diagnosis."));
    root.replaceChildren(header, grid, policy);
  }

  function renderError(message) {
    const root = byId("bodyops-command-loop");
    if (!root) return;
    const card = section("BODY OPS / DEGRADED", "The daily loop could not load", "bodyops-error");
    append(card, node("p", "", message));
    const retry = node("button", "", "Retry Body Ops");
    retry.addEventListener("click", () => load(true));
    card.appendChild(retry);
    root.replaceChildren(card);
  }

  async function load(force = false) {
    if (state.loading || (state.loaded && !force)) return;
    const root = byId("bodyops-command-loop");
    if (!root) return;
    state.loading = true;
    root.setAttribute("aria-busy", "true");
    if (!state.loaded) root.replaceChildren(node("div", "bodyops-loading", "Jarvis is assembling today’s Body Ops picture…"));
    try {
      state.data = await api("/api/body/daily-loop");
      state.loaded = true;
      render(state.data);
    } catch (error) {
      renderError(error && error.message ? error.message : "LifeOS did not return a usable response.");
    } finally {
      state.loading = false;
      root.removeAttribute("aria-busy");
    }
  }

  async function saveCheckin(event) {
    event.preventDefault();
    const status = byId("bodyops-checkin-status");
    const button = event.currentTarget.querySelector("button[type='submit']");
    const value = (id, numeric = true) => {
      const raw = byId(id).value;
      return raw === "" ? null : numeric ? Number(raw) : raw;
    };
    const payload = {
      sleep_hours: value("bodyops-sleep"), sleep_quality: value("bodyops-sleep-quality"),
      energy: value("bodyops-energy"), mood: value("bodyops-mood"), soreness: value("bodyops-soreness"), source: "manual",
    };
    button.disabled = true;
    status.textContent = "Saving check-in…";
    try {
      await api("/api/body/checkin", "POST", payload);
      status.textContent = "Check-in saved. Recalculating today’s plan…";
      state.loaded = false;
      await load(true);
    } catch (error) {
      status.textContent = error && error.message ? error.message : "Check-in could not be saved.";
      button.disabled = false;
    }
  }

  function initialize() {
    const bodyGrid = document.querySelector("#body .body-grid");
    if (!bodyGrid || byId("bodyops-command-loop")) return;
    const style = document.createElement("link");
    style.rel = "stylesheet";
    style.href = "bodyops-enhanced.css?v=1";
    document.head.appendChild(style);
    const root = node("div", "bodyops-command-loop");
    root.id = "bodyops-command-loop";
    root.setAttribute("aria-live", "polite");
    bodyGrid.insertBefore(root, bodyGrid.firstChild);
    const tab = document.querySelector('[data-tab="body"]');
    if (tab) tab.addEventListener("click", () => load(true));
    if (byId("body").classList.contains("active")) load();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", initialize);
  else initialize();
})();
