(function () {
  "use strict";

  const state = { payload: null, loading: false, loaded: false, reloadRequested: false };
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
  const currency = (value) => Number(value || 0).toLocaleString([], { style: "currency", currency: "USD" });
  const localDate = (value) => {
    const parsed = new Date(`${String(value).slice(0, 10)}T12:00:00`);
    return Number.isNaN(parsed.valueOf()) ? String(value || "—") : parsed.toLocaleDateString([], { month: "short", day: "numeric", year: "numeric" });
  };

  function heading(index, title, status) {
    const head = node("header", "money-card-head");
    const identity = node("div", "");
    append(identity, node("span", "money-kicker", index), node("h3", "", title));
    append(head, identity, node("span", "money-state", status));
    return head;
  }

  function errorCard(message) {
    const card = node("section", "money-card money-error");
    append(card, heading("MONEY OS / DEGRADED", "Command center unavailable", "RETRY REQUIRED"), node("p", "", message));
    const retry = node("button", "secondary", "Retry Money Command");
    retry.addEventListener("click", () => load(true));
    card.appendChild(retry);
    return card;
  }

  function summaryCard(data) {
    const card = node("section", "money-card money-summary");
    const cash = data.accounts.reduce((sum, account) => sum + Number(account.balance), 0);
    append(card, heading("MONEY OS / CONTROL PLANE", "Financial operating picture", data.readiness.status.replace("_", " ").toUpperCase()));
    const facts = node("div", "money-summary-grid");
    [
      ["Ecosystem cash", currency(cash), `${data.accounts.length} commissioned accounts`],
      ["Inbox", String(data.pending_count), data.readiness.guidance],
      ["Forecast low", currency(data.forecast.lowest_projected_cash), data.forecast.shortfall ? "Shortfall detected" : "No projected shortfall"],
      ["Next payday", data.paycheck_missions.length ? localDate(data.paycheck_missions[0].date) : "Unavailable", data.paycheck_missions.length ? `${data.paycheck_missions[0].days_away} days · ${currency(data.paycheck_missions[0].amount)}` : "Calendar unavailable"],
    ].forEach(([label, value, note]) => {
      const fact = node("div", "money-summary-fact");
      append(fact, node("span", "money-label", label), node("strong", "", value), node("small", "", note));
      facts.appendChild(fact);
    });
    card.appendChild(facts);
    const policy = node("div", "money-policy");
    append(policy, node("strong", "", "JARVIS AUTHORITY"), node("span", "", "Analyze, categorize, forecast, and propose automatically. Verification, reconciliation, paycheck funding, and cycle closure always require Giovanni’s explicit confirmation."));
    card.appendChild(policy);
    return card;
  }

  function importControls(data) {
    const box = node("div", "money-import");
    const account = node("select");
    account.id = "money-import-account";
    account.setAttribute("aria-label", "Statement account");
    data.accounts.forEach((item) => account.appendChild(new Option(item.name, String(item.id))));
    const file = node("input");
    file.id = "money-import-file";
    file.type = "file";
    file.accept = ".csv,.ofx,.qfx,text/csv,application/x-ofx";
    file.setAttribute("aria-label", "CSV or OFX statement file");
    const button = node("button", "secondary", "Import for review");
    button.type = "button";
    button.addEventListener("click", async () => {
      const status = byId("money-import-status");
      if (!file.files.length) {
        status.textContent = "Choose a CSV, OFX, or QFX statement first.";
        return;
      }
      button.disabled = true;
      status.textContent = "Reading statement locally…";
      try {
        const content = await file.files[0].text();
        const result = await api("/api/money/import", "POST", { content, account_id: Number(account.value), format: "auto", source: `file:${file.files[0].name.slice(0, 60)}` });
        status.textContent = `${result.imported} imported for review · ${result.duplicates} duplicates skipped. No balance changed.`;
        file.value = "";
        await load(true);
      } catch (error) {
        status.textContent = `Statement not imported: ${error.message}`;
        button.disabled = false;
      }
    });
    const status = node("div", "money-inline", "Imports stay pending until you review them.");
    status.id = "money-import-status";
    status.setAttribute("role", "status");
    status.setAttribute("aria-live", "polite");
    append(box, account, file, button, status);
    return box;
  }

  function reviewTransaction(transaction, decision, button, category) {
    const row = button.closest(".money-transaction");
    const status = row.querySelector(".money-row-status");
    if (decision === "exclude") {
      button.disabled = true;
      status.textContent = "Excluding from all ledgers…";
      api(`/api/money/transactions/${transaction.id}/review`, "POST", { decision: "exclude", note: "Excluded by Giovanni" })
        .then(() => load(true)).catch((error) => { button.disabled = false; status.textContent = error.message; });
      return;
    }
    if (button.dataset.confirm !== "armed") {
      button.dataset.confirm = "armed";
      button.textContent = "Confirm ledger post";
      status.textContent = `${currency(transaction.amount)} ${transaction.direction} will post to ${transaction.account_name}. Tap again to confirm.`;
      window.setTimeout(() => {
        if (button.isConnected && button.dataset.confirm === "armed") {
          button.dataset.confirm = "";
          button.textContent = "Verify & post";
          status.textContent = "Confirmation expired; no change made.";
        }
      }, 10000);
      return;
    }
    button.disabled = true;
    status.textContent = "Posting confirmed transaction…";
    api(`/api/money/transactions/${transaction.id}/review`, "POST", { decision: "verify", confirm: true, category, note: "Verified by Giovanni" })
      .then(() => load(true)).catch((error) => { button.disabled = false; status.textContent = error.message; });
  }

  function matchTransaction(transaction, select, button) {
    const row = button.closest(".money-transaction");
    const status = row.querySelector(".money-row-status");
    if (!select.value) {
      status.textContent = "Choose the already logged spending entry to match.";
      return;
    }
    if (button.dataset.confirm !== "armed") {
      button.dataset.confirm = "armed";
      button.textContent = "Confirm match";
      status.textContent = "This links the imported debit without changing the balance or creating duplicate spending. Tap again to confirm.";
      return;
    }
    button.disabled = true;
    api(`/api/money/transactions/${transaction.id}/review`, "POST", { decision: "match", spending_id: Number(select.value), confirm: true, note: "Matched by Giovanni" })
      .then(() => load(true)).catch((error) => { button.disabled = false; status.textContent = error.message; });
  }

  function transactionCard(data) {
    const card = node("section", "money-card money-inbox");
    append(card, heading("INBOX / REVIEW", "Transaction inbox", `${data.pending_count} PENDING`), importControls(data));
    const list = node("div", "money-transaction-list");
    const pending = data.transaction_inbox.filter((item) => item.status === "pending");
    if (!pending.length) list.appendChild(node("p", "money-empty", "Inbox clear. Imported transactions will appear here before affecting balances."));
    pending.forEach((transaction) => {
      const row = node("article", "money-transaction");
      const identity = node("div", "money-transaction-name");
      append(identity, node("strong", "", transaction.merchant || "Imported transaction"), node("small", "", `${localDate(transaction.posted_date)} · ${transaction.account_name} · ${transaction.source}`));
      const amount = node("strong", `money-amount ${transaction.direction}`, `${transaction.direction === "debit" ? "−" : "+"}${currency(transaction.amount)}`);
      const category = node("select");
      category.setAttribute("aria-label", `Category for ${transaction.merchant}`);
      ["Uncategorized", "Groceries", "Dining", "Transportation", "Utilities", "Health", "Personal", "Transfer"].forEach((name) => category.appendChild(new Option(name, name, false, name === transaction.category)));
      const actions = node("div", "money-row-actions");
      const verify = node("button", "", "Verify & post");
      const exclude = node("button", "secondary", "Exclude");
      verify.addEventListener("click", () => reviewTransaction(transaction, "verify", verify, category.value));
      exclude.addEventListener("click", () => reviewTransaction(transaction, "exclude", exclude, category.value));
      append(actions, verify, exclude);
      const match = node("div", "money-match-controls");
      const matchSelect = node("select");
      matchSelect.setAttribute("aria-label", `Match ${transaction.merchant} to existing spending`);
      matchSelect.appendChild(new Option("Match existing spending…", ""));
      data.recent_spending.forEach((entry) => matchSelect.appendChild(new Option(`${localDate(entry.ts)} · ${entry.merchant || "Spending"} · ${currency(entry.amount)}`, String(entry.id))));
      const matchButton = node("button", "secondary", "Match without reposting");
      matchButton.disabled = !data.recent_spending.length;
      matchButton.addEventListener("click", () => matchTransaction(transaction, matchSelect, matchButton));
      append(match, matchSelect, matchButton);
      const status = node("span", "money-row-status", "Pending review · no ledger effect");
      status.setAttribute("role", "status");
      append(row, identity, amount, category, actions, match, status);
      list.appendChild(row);
    });
    card.appendChild(list);
    return card;
  }

  function reconcileAccount(account, button, input, reason, status) {
    const actual = Number(input.value);
    if (!Number.isFinite(actual)) {
      status.textContent = "Enter the actual balance from the bank app or statement.";
      return;
    }
    if (button.dataset.confirm !== "armed") {
      api(`/api/money/accounts/${account.id}/reconcile`, "POST", { actual_balance: actual, reason: reason.value || "Statement reconciliation", confirm: false })
        .then((preview) => {
          button.dataset.confirm = "armed";
          button.textContent = "Confirm reconciliation";
          status.textContent = `Difference ${currency(preview.preview.difference)}. Tap again to replace ${currency(preview.preview.ledger_balance)} with ${currency(actual)}.`;
        }).catch((error) => { status.textContent = error.message; });
      return;
    }
    button.disabled = true;
    api(`/api/money/accounts/${account.id}/reconcile`, "POST", { actual_balance: actual, reason: reason.value || "Statement reconciliation", confirm: true })
      .then(() => load(true)).catch((error) => { button.disabled = false; status.textContent = error.message; });
  }

  function accountsCard(data) {
    const card = node("section", "money-card money-accounts");
    append(card, heading("TRUTH / RECONCILIATION", "Verified cash position", data.readiness.last_reconciled ? `LAST ${localDate(data.readiness.last_reconciled)}` : "NOT YET VERIFIED"));
    const list = node("div", "money-account-list");
    data.accounts.forEach((account) => {
      const row = node("article", "money-account");
      const head = node("div", "money-account-head");
      append(head, node("strong", "", account.name), node("span", "", currency(account.balance)));
      const controls = node("div", "money-reconcile-controls");
      const input = node("input");
      input.type = "number"; input.step = ".01"; input.inputMode = "decimal"; input.placeholder = "Actual balance"; input.setAttribute("aria-label", `${account.name} actual balance`);
      const reason = node("input");
      reason.placeholder = "Reason / statement date"; reason.setAttribute("aria-label", `${account.name} reconciliation reason`);
      const button = node("button", "secondary", "Preview reconciliation");
      const status = node("span", "money-row-status", "No change until confirmed.");
      status.setAttribute("role", "status");
      button.addEventListener("click", () => reconcileAccount(account, button, input, reason, status));
      append(controls, input, reason, button, status);
      append(row, head, controls);
      list.appendChild(row);
    });
    card.appendChild(list);
    if (data.reconciliations.length) {
      const history = node("details", "money-history");
      history.appendChild(node("summary", "", "Recent reconciliation audit"));
      data.reconciliations.forEach((item) => history.appendChild(node("div", "money-history-row", `${localDate(item.created_at)} · ${item.account_name} · ${currency(item.previous_balance)} → ${currency(item.actual_balance)} · difference ${currency(item.difference)}`)));
      card.appendChild(history);
    }
    return card;
  }

  function paycheckAction(mission, button, status) {
    const closing = mission.status === "funded";
    const endpoint = closing ? "close" : "fund";
    if (button.dataset.confirm !== "armed") {
      button.dataset.confirm = "armed";
      button.textContent = closing ? "Confirm cycle close" : `Confirm ${currency(mission.amount)} deposit`;
      const retirement = mission.payroll_contributions.map((asset) => `${asset.name} ${currency(asset.contribution)}`).join(" · ");
      status.textContent = closing ? "This records the current OnePay balance as the cycle close." : `${currency(mission.distribution.truliant)} goes to Truliant and ${currency(mission.distribution.onepay)} goes to OnePay. Payroll benefits: ${retirement}. Relay stays unchanged.`;
      return;
    }
    button.disabled = true;
    const body = { confirm: true };
    api(`/api/money/paychecks/${encodeURIComponent(mission.period)}/${endpoint}`, "POST", body)
      .then(() => load(true)).catch((error) => { button.disabled = false; status.textContent = error.message; });
  }

  function missionsCard(data) {
    const card = node("section", "money-card money-missions");
    append(card, heading("PAYDAY / MISSIONS", "Paycheck execution packets", "P1 → P2"));
    const list = node("div", "money-mission-list");
    data.paycheck_missions.slice(0, 4).forEach((mission) => {
      const item = node("article", `money-mission status-${mission.status}`);
      const head = node("div", "money-mission-head");
      const identity = node("div", "");
      append(identity, node("span", "money-label", `${mission.label.toUpperCase()} · ${mission.period}`), node("strong", "", `${localDate(mission.date)} · ${mission.days_away} days`));
      append(head, identity, node("span", "money-state", mission.status.toUpperCase()));
      const facts = node("div", "money-mission-facts");
      [["Paycheck", mission.amount], ["OnePay remainder", mission.distribution.onepay], ["Truliant fixed split", mission.distribution.truliant], ["Payroll benefits", mission.payroll_contribution_total], ["Relay direct deposit", mission.distribution.relay], ["Bills", mission.bill_total], ["OnePay after planned bills", mission.planned_remaining]].forEach(([label, value]) => {
        const fact = node("div", ""); append(fact, node("span", "", label), node("strong", "", currency(value))); facts.appendChild(fact);
      });
      const bills = node("div", "money-mission-bills");
      bills.tabIndex = 0;
      bills.setAttribute("role", "region");
      bills.setAttribute("aria-label", `${mission.period} planned bills`);
      mission.bills.forEach((bill) => bills.appendChild(node("span", bill.paid ? "is-paid" : "", `${bill.name} · ${currency(bill.amount)} · due ${localDate(bill.due_date)}`)));
      if (!mission.bills.length) bills.appendChild(node("span", "money-empty", "No obligations assigned."));
      const controls = node("div", "money-mission-controls");
      const action = node("button", "", mission.status === "planned" ? "Fund paycheck" : mission.status === "funded" ? "Close paycheck" : "Cycle closed");
      action.disabled = mission.status === "closed";
      const retirement = mission.payroll_contributions.map((asset) => `${asset.name} ${currency(asset.contribution)}`).join(" · ");
      const status = node("span", "money-row-status", mission.status === "planned" ? `$309.00 → Truliant · ${currency(mission.distribution.onepay)} → OnePay · ${retirement} → payroll benefits · Relay unchanged. Confirmation required.` : mission.status === "funded" ? "Close after balances are verified." : `Closed at ${currency(mission.closing_balance)}`);
      action.addEventListener("click", () => paycheckAction(mission, action, status));
      append(controls, action, status);
      append(item, head, facts, bills, controls);
      list.appendChild(item);
    });
    card.appendChild(list);
    return card;
  }

  function forecastRows(result, container) {
    container.replaceChildren();
    const summary = node("div", "money-forecast-summary");
    append(summary, node("strong", result.shortfall ? "money-warn" : "money-good", `Lowest cash ${currency(result.lowest_projected_cash)}`), node("span", "", `Observed spending baseline ${currency(result.average_daily_spending)} / day`));
    container.appendChild(summary);
    result.forecast.forEach((period) => {
      const row = node("div", "money-forecast-row");
      append(row, node("span", "", `${period.period} · ${localDate(period.payday)}`), node("small", "", `Income ${currency(period.income)} · bills ${currency(period.bills)} · everyday ${currency(period.everyday_spending)}`), node("strong", period.projected_cash < 0 ? "money-warn" : "", currency(period.projected_cash)));
      container.appendChild(row);
    });
    container.appendChild(node("p", "money-policy-note", result.policy));
  }

  function forecastCard(data) {
    const card = node("section", "money-card money-forecast");
    append(card, heading("FORECAST / WHAT-IF", "30 / 60 / 90-day runway", data.forecast.shortfall ? "ATTENTION" : "NOMINAL"));
    const form = node("form", "money-simulation-controls");
    const fields = [
      ["money-sim-once", "One-time spending", "0"],
      ["money-sim-daily", "Daily spending change", "0"],
      ["money-sim-income", "Income change / check", "0"],
    ];
    fields.forEach(([id, label, value]) => {
      const wrapper = node("label", ""); wrapper.appendChild(node("span", "", label));
      const input = node("input"); input.id = id; input.type = "number"; input.step = ".01"; input.value = value; wrapper.appendChild(input); form.appendChild(wrapper);
    });
    const simulate = node("button", "secondary", "Run safe simulation");
    simulate.type = "submit";
    form.appendChild(simulate);
    const output = node("div", "money-forecast-output");
    forecastRows(data.forecast, output);
    form.addEventListener("submit", async (event) => {
      event.preventDefault(); simulate.disabled = true; simulate.textContent = "Simulating…";
      try {
        const result = await api("/api/money/simulate", "POST", {
          one_time_spending: Number(byId("money-sim-once").value) || 0,
          daily_spending_change: Number(byId("money-sim-daily").value) || 0,
          income_change_per_check: Number(byId("money-sim-income").value) || 0,
          periods: 6,
        });
        forecastRows(result, output);
      } catch (error) { output.replaceChildren(node("p", "money-warn", error.message)); }
      finally { simulate.disabled = false; simulate.textContent = "Run safe simulation"; }
    });
    append(card, form, output);
    return card;
  }

  function debtCard(data) {
    const card = node("section", "money-card money-debt-ops");
    append(card, heading("DEBT / OPERATIONS", "Payoff strategy", data.debts.length ? `${data.debts.length} ACTIVE` : "CLEAR"));
    if (!data.debts.length) {
      card.appendChild(node("p", "money-empty", "No active debt balances."));
      return card;
    }
    const total = data.debts.reduce((sum, debt) => sum + Number(debt.remaining), 0);
    card.appendChild(node("p", "money-debt-total", `${currency(total)} total remaining`));
    const strategies = node("div", "money-strategies");
    const payoff = data.payoff_strategies;
    const addStrategy = (name, rows, note, recommended = false) => {
      const strategy = node("div", "money-strategy");
      append(strategy, node("span", "money-label", `${name}${recommended ? " · RECOMMENDED" : ""}`), node("small", "", note));
      const order = node("ol", "money-strategy-order");
      rows.forEach((debt) => {
        const item = node("li", "");
        append(item, node("strong", "", debt.name), node("span", "", `${currency(debt.remaining)}${Number(debt.apr) ? ` · ${Number(debt.apr).toFixed(2)}% APR` : ""}`));
        if (name === "Priority") item.appendChild(node("small", "", debt.priority_reason || "Priority classification needed"));
        order.appendChild(item);
      });
      if (!rows.length) order.appendChild(node("li", "money-warn", `Unavailable until APR is entered for: ${payoff.missing_apr.join(", ")}.`));
      strategy.appendChild(order);
      strategies.appendChild(strategy);
    };
    addStrategy("Priority", payoff.priority, "Essential services and income continuity first.", true);
    addStrategy("Snowball", payoff.snowball, "Smallest remaining balance first.");
    addStrategy("Avalanche", payoff.avalanche, payoff.avalanche_ready ? "Highest APR first." : "Real APR data required; no fallback to Snowball.");
    card.appendChild(strategies);

    const editor = node("details", "money-debt-editor");
    editor.appendChild(node("summary", "", "Update APR and priority data"));
    data.debts.forEach((debt) => {
      const row = node("div", "money-debt-edit-row");
      const name = node("strong", "", debt.name);
      const apr = node("input");
      apr.type = "number"; apr.min = "0"; apr.max = "100"; apr.step = "0.01"; apr.value = Number(debt.apr || 0); apr.setAttribute("aria-label", `${debt.name} APR percent`);
      const priority = node("input");
      priority.type = "number"; priority.min = "1"; priority.max = "999"; priority.step = "1"; priority.value = Number(debt.priority || 50); priority.setAttribute("aria-label", `${debt.name} priority`);
      const save = node("button", "", "Save strategy data");
      const status = node("span", "money-row-status", "Lower priority number means more important.");
      save.addEventListener("click", async () => {
        save.disabled = true;
        try {
          await api(`/api/budget/debts/${debt.id}/strategy`, "POST", { apr: Number(apr.value), priority: Number(priority.value) });
          status.textContent = `${debt.name} strategy data saved.`;
          await load(true);
        } catch (error) {
          save.disabled = false;
          status.textContent = error.message;
        }
      });
      append(row, name, apr, priority, save, status);
      editor.appendChild(row);
    });
    card.appendChild(editor);
    card.appendChild(node("p", "money-policy-note", "Priority is the operating order used by Jarvis and windfall debt routing. Snowball and Avalanche remain comparisons only and never initiate a payment."));
    return card;
  }

  function auditCard(data) {
    const card = node("section", "money-card money-audit");
    append(card, heading("AUDIT / WHY", "Recent financial decisions", `${data.audit.length} EVENTS`));
    const list = node("div", "money-audit-list");
    if (!data.audit.length) list.appendChild(node("p", "money-empty", "No Money OS actions recorded yet."));
    data.audit.slice(0, 10).forEach((item) => {
      const row = node("div", "money-audit-row");
      append(row, node("strong", "", item.action.replace("finance.", "").replaceAll("_", " ")), node("span", "", `${localDate(item.created_at)} · ${item.risk.toUpperCase()} · ${item.confirmed ? "CONFIRMED" : "READ ONLY"}`), node("small", "", item.reason || item.subject));
      list.appendChild(row);
    });
    card.appendChild(list);
    return card;
  }

  function render(data) {
    const root = byId("money-command-center");
    if (!root) return;
    root.replaceChildren(summaryCard(data), transactionCard(data), accountsCard(data), missionsCard(data), forecastCard(data), debtCard(data), auditCard(data));
  }

  async function load(force = false) {
    if (state.loading) {
      if (force) state.reloadRequested = true;
      return;
    }
    if (state.loaded && !force) return;
    const root = byId("money-command-center");
    if (!root) return;
    state.loading = true;
    root.setAttribute("aria-busy", "true");
    if (!state.loaded) root.replaceChildren(node("div", "money-loading", "Jarvis is reconciling the Money OS operating picture…"));
    try {
      state.payload = await api("/api/money/command-center");
      state.loaded = true;
      render(state.payload);
    } catch (error) {
      root.replaceChildren(errorCard(error.message || "Money OS returned an unusable response."));
    } finally {
      state.loading = false;
      root.removeAttribute("aria-busy");
      if (state.reloadRequested) {
        state.reloadRequested = false;
        window.setTimeout(() => load(true), 0);
      }
    }
  }

  function initialize() {
    const grid = document.querySelector("#budget .finance-grid");
    if (!grid || byId("money-command-center")) return;
    const style = document.createElement("link");
    style.rel = "stylesheet";
    style.href = "money-command.css?v=5";
    document.head.appendChild(style);
    const root = node("div", "money-command-center");
    root.id = "money-command-center";
    root.setAttribute("aria-live", "polite");
    grid.insertBefore(root, grid.firstChild);
    const tab = document.querySelector('[data-tab="budget"]');
    if (tab) tab.addEventListener("click", () => load(true));
    document.querySelectorAll("#budget details.subordinate-tools").forEach((details) => {
      if (details.querySelector("summary")?.textContent.trim() === "Reconcile actual balance") details.hidden = true;
    });
    window.addEventListener("jarvis:data-changed", (event) => {
      const path = String(event.detail?.path || "");
      if (!path.startsWith("/api/money") && !path.startsWith("/api/budget") && !path.startsWith("/api/vault")) return;
      state.loaded = false;
      if (byId("budget")?.classList.contains("active")) load(true);
    });
    window.addEventListener("jarvis:refresh-active", (event) => {
      if (event.detail?.active === "budget") load(true);
    });
    if (byId("budget").classList.contains("active")) load();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", initialize);
  else initialize();
})();
