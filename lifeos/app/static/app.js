const $ = (id) => document.getElementById(id);

async function api(path, method = "GET", body) {
  const res = await fetch(path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  return res.json();
}

// ---------- tabs ----------
document.querySelectorAll(".tab").forEach((btn) =>
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".panel").forEach((p) => p.classList.remove("active"));
    btn.classList.add("active");
    $(btn.dataset.tab).classList.add("active");
    if (btn.dataset.tab === "vault") loadVault();
    if (btn.dataset.tab === "body") loadBody();
  })
);

// ---------- Today ----------
async function loadToday() {
  const t = await api("/api/today");
  const p = t.protein;
  $("protein-bar").style.width = Math.min(100, (p.today_g / p.target_g) * 100) + "%";
  $("protein-label").textContent = `${Math.round(p.today_g)} / ${p.target_g}g`;
  const stepsTarget = 8000;
  $("steps-bar").style.width = Math.min(100, (t.steps_today / stepsTarget) * 100) + "%";
  $("steps-label").textContent = t.steps_today.toLocaleString();
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

// ---------- Body Ops ----------
async function loadBody() {
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

$("meal-btn").onclick = async () => {
  const name = $("meal-name").value.trim();
  if (!name) return;
  await api("/api/body/meals/log", "POST", {
    name, protein_g: parseFloat($("meal-protein").value) || 0,
  });
  $("meal-name").value = ""; $("meal-protein").value = "";
  loadToday();
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

$("bill-btn").onclick = async () => {
  const name = $("bill-name").value.trim();
  const amount = parseFloat($("bill-amount").value);
  const due_day = parseInt($("bill-day").value, 10);
  if (!name || !amount || !due_day) return;
  await api("/api/vault/bills", "POST", { name, amount, due_day });
  $("bill-name").value = ""; $("bill-amount").value = ""; $("bill-day").value = "";
  loadVault();
};

loadToday();
