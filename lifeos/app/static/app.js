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

async function api(path, method = "GET", body) {
  const headers = {};
  if (body) headers["Content-Type"] = "application/json";
  const saved = localStorage.getItem("lifeos_token");
  if (saved) headers["Authorization"] = "Bearer " + saved;
  const res = await fetch(path, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (res.status === 401) {
    if (saved) localStorage.removeItem("lifeos_token");
    const token = window.prompt("Enter your LifeOS API token");
    if (token) {
      const auth = await fetch("/api/auth", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token }),
      });
      if (auth.ok) {
        localStorage.setItem("lifeos_token", token);
        return api(path, method, body);
      }
    }
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
    if (btn.dataset.tab === "vault") loadVault();
    if (btn.dataset.tab === "budget") loadBudget();
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
    `$${t.vault_total.toFixed(2)} across accounts — see Vault Flow tab for the plan.`;
}

async function override(meal, kind) {
  await api("/api/body/overrides", "POST", { meal, kind });
  await api("/api/body/meals/log", "POST", { name: meal, override_kind: kind });
  loadToday();
}

$("vitamins-btn").onclick = async () => { await api("/api/body/vitamins/take", "POST"); loadToday(); };

$("water-btn").onclick = async () => { await api("/api/body/water", "POST", { glasses: 1 }); loadToday(); };

$("briefing-btn").onclick = async () => {
  const b = await api("/api/briefing");
  $("briefing").textContent = b.speech;
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
  const p = await api("/api/pantry");
  const pantryEl = $("pantry");
  if (!p.items.length) {
    pantryEl.textContent = p.grocy_configured
      ? "Empty — hit Sync from Grocy."
      : "Empty. Set GROCY_URL + GROCY_API_KEY on the lifeos container to sync.";
  } else {
    pantryEl.replaceChildren();
    p.items.forEach((item) => {
      const line = document.createElement("div");
      line.className = "line";
      line.append(el("span", "", item.name), el("span", "", `${item.qty} ${item.unit}`));
      pantryEl.appendChild(line);
    });
  }
  const g = await api("/api/pantry/grocery-suggestions");
  const grocery = $("grocery");
  grocery.replaceChildren(el("div", "muted", `Avg ${g.avg_daily_protein_g}g/day vs ${g.target_g}g target`));
  g.suggestions.forEach((suggestion) => {
    const line = document.createElement("div");
    line.className = "line";
    line.append(el("span", "", suggestion.name), el("span", "muted", `${suggestion.protein_g_per_serving}g/serving`));
    grocery.appendChild(line);
  });
}

async function loadBody() {
  loadWorkouts();
  loadPantry();
  const s = await api("/api/body/summary");
  const hist = s.weighins.map((w) => `${w.ts.slice(0, 10)}: ${w.weight_lb} lb`).join(" · ");
  $("weight-history").textContent = hist || "No weigh-ins yet.";
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
    const token = localStorage.getItem("lifeos_token");
    const res = await fetch("/api/body/meals/photo", {
      method: "POST",
      headers: token ? { Authorization: "Bearer " + token } : undefined,
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
  const r = await api("/api/pantry/sync", "POST");
  if (r.message) $("pantry").textContent = r.message;
  else loadPantry();
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
async function loadBudget() {
  const o = await api("/api/budget/overview");
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
  bl.innerHTML = "";
  o.bills.forEach((b) => {
    const line = document.createElement("div");
    line.className = "line";
    line.innerHTML = `<span>${b.name} <span class="muted">(due ${b.due_day}${b.note ? " · " + b.note : ""})</span></span>
      <span>$${b.amount.toFixed(2)}</span>`;
    const btn = document.createElement("button");
    btn.className = "secondary";
    if (b.paid) {
      line.innerHTML += '<span class="tag good">paid</span>';
      btn.textContent = "Undo";
      btn.onclick = async () => { await api(`/api/budget/bills/${b.id}/unpaid`, "POST"); loadBudget(); };
    } else {
      btn.textContent = "Paid";
      btn.onclick = async () => { await api(`/api/budget/bills/${b.id}/paid`, "POST"); loadBudget(); };
    }
    line.appendChild(btn);
    bl.appendChild(line);
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
        loadBudget();
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
      loadBudget();
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

async function dispatchWindfall(route) {
  const amount = parseFloat($("wf-amount").value);
  if (isNaN(amount) || amount <= 0) return;
  const r = await api("/api/budget/windfall", "POST", { amount, route });
  $("wf-amount").value = "";
  const parts = r.debt_payments.map((p) => `${p.debt} −$${p.paid.toFixed(2)}`);
  if (r.to_buckets > 0) parts.push(`buckets +$${r.to_buckets.toFixed(2)}`);
  if (r.kept_in_onepay > 0) parts.push(`OnePay buffer +$${r.kept_in_onepay.toFixed(2)}`);
  $("wf-result").textContent = "Routed: " + parts.join(", ");
  loadBudget();
}
$("wf-debt").onclick = () => dispatchWindfall("debt");
$("wf-split").onclick = () => dispatchWindfall("split");
$("wf-buffer").onclick = () => dispatchWindfall("buffer");

$("bal-btn").onclick = async () => {
  const balance = parseFloat($("bal-amount").value);
  if (isNaN(balance)) return;
  await api(`/api/vault/accounts/${$("bal-account").value}/balance`, "PUT", { balance });
  $("bal-amount").value = "";
  loadBudget();
};

// ---------- Vault Flow ----------
async function loadVault() {
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

  const bills = await api("/api/vault/bills");
  const bl = $("bills");
  bl.replaceChildren();
  if (!bills.length) bl.appendChild(el("div", "muted", "No bills yet."));
  bills.forEach((b) => {
    const line = document.createElement("div");
    line.className = "line";
    const name = el("span", "", b.name);
    name.append(" ", el("span", "muted", `(due ${b.due_day})`));
    line.append(name, el("span", "", `$${b.amount.toFixed(2)}`));
    const paid = document.createElement("button");
    paid.className = "secondary"; paid.textContent = "Paid";
    paid.onclick = async () => { await api(`/api/vault/bills/${b.id}/paid`, "POST"); loadVault(); };
    line.appendChild(paid);
    bl.appendChild(line);
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

$("dep-btn").onclick = async () => {
  const amount = parseFloat($("dep-amount").value);
  if (!amount) return;
  await api("/api/vault/deposits", "POST", {
    amount, account_id: parseInt($("dep-account").value, 10), source: "manual",
  });
  $("dep-amount").value = "";
  loadVault();
};

$("goal-btn").onclick = async () => {
  const name = $("goal-name").value.trim();
  if (!name) return;
  await api("/api/vault/goals", "POST", {
    name, target: parseFloat($("goal-target").value) || 0,
  });
  $("goal-name").value = ""; $("goal-target").value = "";
  loadVault();
};

$("goal-add-btn").onclick = async () => {
  const name = $("goal-select").value;
  const amount = parseFloat($("goal-amount").value);
  if (!name || !amount) return;
  await api("/api/vault/goals/contribute", "POST", { name, amount });
  $("goal-amount").value = "";
  loadVault();
};

$("bill-btn").onclick = async () => {
  const name = $("bill-name").value.trim();
  const amount = parseFloat($("bill-amount").value);
  const due_day = parseInt($("bill-day").value, 10);
  if (!name || !amount || !due_day) return;
  await api("/api/vault/bills", "POST", { name, amount, due_day });
  $("bill-name").value = ""; $("bill-amount").value = ""; $("bill-day").value = "";
  loadVault();
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
    (simulation.predicted_actions || []).forEach((prediction) => {
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
    const openPerimeter = security.open_perimeter || [];
    const hazards = security.active_hazards || [];
    const people = (occupancy.people_home || []).map(friendlyEntity);
    const secure = Boolean(security.secure);
    const alarmState = String(security.alarm || "unknown").toLowerCase();
    const awaiting = telemetry.link_state === "awaiting_data";
    $("command").classList.add(awaiting ? "is-awaiting" : secure ? "is-nominal" : "is-attention");
    commandText("command-mode", String(context.house_mode || "unknown").toUpperCase());
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
        ? `${context.pending_proposals || proposals.length} pending proposal(s). Perimeter integrity confirmed.`
        : `Review required: ${[...openPerimeter, ...hazards].map(friendlyEntity).join(", ") || (["unknown", "unavailable"].includes(alarmState) ? "alarm telemetry is unknown" : "alarm state is not secure")}.`
    );
    commandText("command-updated", `SYNC ${new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`);
    commandText("command-proposal-count", `${proposals.length} PENDING`);
    renderCommandProposals(proposals);
    renderLifeOSPulse(lifeos);
    renderHardwareTelemetry(hardware);
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
