const $ = (id) => document.getElementById(id);

async function api(path, method = "GET", body) {
  const res = await fetch(path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (res.status === 401) {
    const token = window.prompt("Enter your LifeOS API token");
    if (token) {
      const auth = await fetch("/api/auth", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token }),
      });
      if (auth.ok) return api(path, method, body);
    }
  }
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || `LifeOS request failed (${res.status})`);
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
  })
);

// ---------- Today ----------
async function loadToday() {
  const t = await api("/api/today");
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

  const sug = $("suggestions");
  sug.innerHTML = "";
  t.meal_suggestions.forEach((m) => {
    const div = document.createElement("div");
    div.className = "suggestion";
    div.innerHTML = `<strong>${m.name}</strong>
      <div class="meta">${m.minutes} min · ${m.protein_g}g protein · ${m.calories} cal</div>`;
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
    nud.innerHTML = "";
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
  w.innerHTML = "";
  plans.forEach((p) => {
    const line = document.createElement("div");
    line.className = "line";
    line.innerHTML = `<span>${p.kind} <span class="muted">(${p.date}, ${p.minutes} min${p.source === "treat_balance" ? ", balances a treat" : ""})</span></span>`;
    if (p.done) {
      line.innerHTML += '<span class="tag good">done</span>';
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
  const el = $("pantry");
  if (!p.items.length) {
    el.textContent = p.grocy_configured
      ? "Empty — hit Sync from Grocy."
      : "Empty. Set GROCY_URL + GROCY_API_KEY on the lifeos container to sync.";
  } else {
    el.innerHTML = p.items
      .map((i) => `<div class="line"><span>${i.name}</span><span>${i.qty} ${i.unit}</span></div>`)
      .join("");
  }
  const g = await api("/api/pantry/grocery-suggestions");
  $("grocery").innerHTML =
    `<div class="muted">Avg ${g.avg_daily_protein_g}g/day vs ${g.target_g}g target</div>` +
    g.suggestions
      .map((s) => `<div class="line"><span>${s.name}</span><span class="muted">${s.protein_g_per_serving}g/serving</span></div>`)
      .join("");
}

async function loadBody() {
  loadWorkouts();
  loadPantry();
  const s = await api("/api/body/summary");
  const hist = s.weighins.map((w) => `${w.ts.slice(0, 10)}: ${w.weight_lb} lb`).join(" · ");
  $("weight-history").textContent = hist || "No weigh-ins yet.";
  if (s.snack_suggestions.length) {
    $("snack-card").hidden = false;
    $("snacks").innerHTML = "";
    s.snack_suggestions.forEach((m) => {
      const d = document.createElement("div");
      d.className = "suggestion";
      d.innerHTML = `<strong>${m.name}</strong>
        <div class="meta">${m.protein_g}g protein · ${m.minutes} min</div>`;
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

$("photo-btn").onclick = async () => {
  const file = $("photo-input").files[0];
  if (!file) return;
  const form = new FormData();
  form.append("photo", file);
  const res = await fetch("/api/body/meals/photo", { method: "POST", body: form });
  const r = await res.json();
  $("photo-msg").textContent = r.message;
  $("photo-input").value = "";
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
  acc.innerHTML = "";
  const sel = $("dep-account");
  sel.innerHTML = "";
  accounts.forEach((a) => {
    const line = document.createElement("div");
    line.className = "line";
    line.innerHTML = `<span>${a.name}${a.vaultborne ? ' <span class="tag">Vaultborne</span>' : ""}</span>
      <span>$${a.balance.toFixed(2)}</span>`;
    acc.appendChild(line);
    const opt = document.createElement("option");
    opt.value = a.id; opt.textContent = a.name;
    sel.appendChild(opt);
  });

  const bills = await api("/api/vault/bills");
  const bl = $("bills");
  bl.innerHTML = bills.length ? "" : '<div class="muted">No bills yet.</div>';
  bills.forEach((b) => {
    const line = document.createElement("div");
    line.className = "line";
    line.innerHTML = `<span>${b.name} <span class="muted">(due ${b.due_day})</span></span>
      <span>$${b.amount.toFixed(2)}</span>`;
    const paid = document.createElement("button");
    paid.className = "secondary"; paid.textContent = "Paid";
    paid.onclick = async () => { await api(`/api/vault/bills/${b.id}/paid`, "POST"); loadVault(); };
    line.appendChild(paid);
    bl.appendChild(line);
  });

  const goals = await api("/api/vault/goals");
  const gl = $("goals");
  const gsel = $("goal-select");
  gsel.innerHTML = "";
  if (!goals.length) {
    gl.textContent = "No goals yet — add one below or say \"add 50 to my vacation fund\".";
  } else {
    gl.innerHTML = "";
    goals.forEach((g) => {
      const pct = g.target ? Math.min(100, (g.saved / g.target) * 100) : 0;
      const div = document.createElement("div");
      div.className = "bar-row";
      div.innerHTML = `<span>${g.name}</span>
        <div class="bar"><div class="fill" style="width:${pct}%"></div></div>
        <span>$${g.saved.toFixed(0)}${g.target ? " / $" + g.target.toFixed(0) : ""}</span>`;
      gl.appendChild(div);
      const opt = document.createElement("option");
      opt.value = g.name; opt.textContent = g.name;
      gsel.appendChild(opt);
    });
  }

  const plan = await api("/api/vault/plan");
  const pl = $("plan");
  pl.innerHTML = `
    <div class="line"><span>Available</span><span>$${plan.total_available.toFixed(2)}</span></div>
    <div class="line"><span>Unpaid bills</span><span>$${plan.unpaid_bills_total.toFixed(2)}</span></div>
    <div class="line"><strong>Leftover after bills</strong>
      <strong>$${plan.leftover_after_bills.toFixed(2)}</strong></div>`;
  plan.recommendations.forEach((r) => {
    const line = document.createElement("div");
    line.className = "line";
    const cls = r.recommend === "pay now" ? "good" : "warn";
    line.innerHTML = `<span>${r.bill} <span class="muted">(due ${r.due_day}, ${r.status})</span></span>
      <span class="tag ${cls}">${r.recommend}</span>`;
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
  $("review-stats").innerHTML = `
    <div class="line"><span>Weight change</span>
      <span>${r.weight.delta_lb === null ? "–" : r.weight.delta_lb + " lb"}</span></div>
    <div class="line"><span>Avg protein</span>
      <span>${r.avg_daily_protein_g}g / ${r.protein_target_g}g</span></div>
    <div class="line"><span>Avg steps</span><span>${r.avg_daily_steps.toLocaleString()}</span></div>
    <div class="line"><span>Money in</span><span>$${r.money_in.toFixed(2)}</span></div>
    <div class="line"><span>Bills paid this month</span>
      <span>$${r.bills_paid_this_month.toFixed(2)}</span></div>
    <div class="line"><span>Treats / workouts</span>
      <span>${r.treats_this_week} / ${r.workouts_this_week}</span></div>
    <div class="line"><span>Streaks</span>
      <span>vitamins ${r.streaks.vitamins}d · steps ${r.streaks.steps}d</span></div>`;
  loadProfiles();
}

async function loadProfiles() {
  const profiles = await api("/api/profiles");
  const sel = $("profile-select");
  sel.innerHTML = "";
  profiles.forEach((p) => {
    const opt = document.createElement("option");
    opt.value = p.id; opt.textContent = p.name; opt.selected = p.active;
    sel.appendChild(opt);
  });
  const list = $("profiles");
  list.innerHTML = "";
  profiles.forEach((p) => {
    const line = document.createElement("div");
    line.className = "line";
    line.innerHTML = `<span>${p.name}${p.active ? ' <span class="tag good">active</span>' : ""}</span>
      <span class="muted">${p.protein_target_g}g · ${p.step_target.toLocaleString()} steps</span>`;
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
