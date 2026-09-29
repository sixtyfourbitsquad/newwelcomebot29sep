const state = { csrf: "", role: "", section: "dashboard", mfa: "" };

const sections = [
  ["dashboard", "Dashboard"],
  ["inbox", "Shared inbox"],
  ["broadcasts", "Broadcasts"],
  ["scheduled", "Scheduled"],
  ["welcome", "Welcome"],
  ["onboarding", "Onboarding"],
  ["retention", "Come-back"],
  ["channel", "Channel"],
  ["people", "People"],
  ["logs", "Logs"],
  ["queue", "Queue"],
  ["health", "Health"],
  ["setup", "Bot setup"],
  ["admins", "Admins"],
];

function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

async function api(path, options = {}) {
  const headers = Object.assign({ "Content-Type": "application/json" }, options.headers || {});
  if (options.method && options.method !== "GET" && state.csrf) {
    headers["X-CSRF-Token"] = state.csrf;
  }
  const response = await fetch(path, {
    credentials: "same-origin",
    headers,
    method: options.method || "GET",
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(data.detail || "Request failed");
    error.status = response.status;
    throw error;
  }
  return data;
}

function view(html) {
  document.getElementById("view").innerHTML = html;
}

function bind(id, event, handler) {
  const node = document.getElementById(id);
  if (node) node.addEventListener(event, handler);
}

window.onTelegramAuth = async function onTelegramAuth(user) {
  document.getElementById("login-error").textContent = "";
  try {
    const data = await api("/panel/api/login", { method: "POST", body: user });
    if (data.mfa_required) {
      state.mfa = data.mfa_token;
      document.getElementById("pin-form").hidden = false;
      return;
    }
    state.csrf = data.csrf_token;
    state.role = data.role;
    showApp();
  } catch (error) {
    document.getElementById("login-error").textContent = error.message;
  }
};

async function boot() {
  const config = await api("/panel/api/public-config");
  if (!config.enabled) {
    document.getElementById("login-error").textContent = "Web admin is disabled.";
    return;
  }
  if (config.bot_username) {
    const script = document.createElement("script");
    script.async = true;
    script.src = "https://telegram.org/js/telegram-widget.js?22";
    script.setAttribute("data-telegram-login", config.bot_username);
    script.setAttribute("data-size", "large");
    script.setAttribute("data-onauth", "onTelegramAuth(user)");
    script.setAttribute("data-request-access", "write");
    document.getElementById("tg-login").appendChild(script);
  }
  try {
    const me = await api("/panel/api/me");
    state.csrf = me.csrf_token;
    state.role = me.role;
    showApp();
  } catch (error) {
    if (error.status !== 401) {
      document.getElementById("login-error").textContent = error.message;
    }
  }
}

function showApp() {
  document.getElementById("login").hidden = true;
  document.getElementById("app").hidden = false;
  document.getElementById("who").textContent = state.role;
  const nav = document.getElementById("nav");
  nav.innerHTML = "";
  for (const [id, label] of sections) {
    if (id === "admins" && state.role !== "owner") continue;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "nav-btn";
    button.textContent = label;
    button.dataset.section = id;
    button.addEventListener("click", () => openSection(id));
    nav.appendChild(button);
  }
  openSection(state.section);
}

async function openSection(id) {
  state.section = id;
  document.querySelectorAll(".nav-btn").forEach((node) => {
    node.classList.toggle("active", node.dataset.section === id);
  });
  document.getElementById("title").textContent = sections.find((row) => row[0] === id)[1];
  const loaders = {
    dashboard: renderDashboard,
    inbox: renderInbox,
    broadcasts: renderBroadcasts,
    scheduled: renderScheduled,
    welcome: renderWelcome,
    onboarding: renderOnboarding,
    retention: renderRetention,
    channel: renderChannel,
    people: renderPeople,
    logs: renderLogs,
    queue: renderQueue,
    health: renderHealth,
    setup: renderSetup,
    admins: renderAdmins,
  };
  try {
    await loaders[id]();
  } catch (error) {
    view(`<p class="error">${esc(error.message)}</p>`);
  }
}

function cards(items) {
  return `<div class="grid">${items
    .map(([label, value]) => `<div class="card"><div class="muted">${esc(label)}</div><div class="stat">${esc(value)}</div></div>`)
    .join("")}</div>`;
}

async function renderDashboard() {
  const data = await api("/panel/api/dashboard");
  view(
    cards([
      ["Users", data.users.total],
      ["Active", data.users.active],
      ["Started", data.started_users],
      ["Blocked", data.users.blocked],
      ["Onboarding pending", data.onboarding.pending],
      ["Inbox messages", data.inbox.messages],
      ["Queue", data.broadcast_queue],
      ["Uptime (s)", data.uptime_seconds],
    ]) +
      `<div class="card">Postgres ${data.postgres_ok ? "ok" : "down"} · Redis ${data.redis_ok ? "ok" : "down"} · Webhook ${esc(data.webhook)}<br>Workers: ${esc(JSON.stringify(data.workers))}</div>`
  );
}

async function renderInbox() {
  const data = await api("/panel/api/inbox");
  const rows = data.conversations
    .map(
      (row) => `<tr>
        <td>${esc(row.first_name)} <span class="muted">@${esc(row.username)}</span><br><code>${esc(row.telegram_user_id)}</code></td>
        <td>${esc(row.message_type)}</td>
        <td>${esc(row.content_text)}</td>
        <td>${esc(row.received_at)}</td>
        <td>${esc(row.last_reply_status || "none")}</td>
        <td><button type="button" data-user="${esc(row.telegram_user_id)}">Open</button></td>
      </tr>`
    )
    .join("");
  view(
    `<div class="warn">Shared inbox. Every admin sees every message. A reply is sent only to the user you confirm.</div>
     <table><thead><tr><th>User</th><th>Type</th><th>Message</th><th>Received</th><th>Reply</th><th></th></tr></thead><tbody>${rows || ""}</tbody></table>
     <div id="thread"></div>`
  );
  document.querySelectorAll("[data-user]").forEach((button) => {
    button.addEventListener("click", () => openThread(button.dataset.user));
  });
}

async function openThread(userId) {
  const data = await api(`/panel/api/inbox/${userId}`);
  const name = data.user ? `${data.user.first_name || ""} (${userId})` : userId;
  const messages = (data.thread.messages || [])
    .map((row) => `<p><strong>${esc(row.message_type)}</strong> ${esc(row.content_text)} <span class="muted">${esc(row.received_at)}</span></p>`)
    .join("");
  const replies = (data.thread.replies || [])
    .map((row) => `<p>Reply ${esc(row.delivery_status)}: ${esc(row.content_text)} ${row.telegram_error ? esc(row.telegram_error) : ""}</p>`)
    .join("");
  document.getElementById("thread").innerHTML = `<div class="card">
      <h2>Reply only to ${esc(name)}</h2>
      ${messages}${replies}
      <textarea id="reply-text" rows="3" placeholder="Message"></textarea>
      <label><input id="reply-confirm" type="checkbox"> I am replying to user ${esc(userId)}</label>
      <p><button class="primary" id="send-reply" type="button">Send to this user</button></p>
      <p id="reply-result"></p>
    </div>`;
  document.getElementById("send-reply").addEventListener("click", async () => {
    const result = document.getElementById("reply-result");
    if (!document.getElementById("reply-confirm").checked) {
      result.textContent = "Confirm the user first.";
      return;
    }
    try {
      const sent = await api(`/panel/api/inbox/${userId}/reply`, {
        method: "POST",
        body: {
          confirm: true,
          user_id: Number(userId),
          kind: "text",
          text: document.getElementById("reply-text").value,
        },
      });
      result.textContent = sent.ok ? "Sent." : `Failed: ${sent.error}`;
    } catch (error) {
      result.textContent = error.message;
    }
  });
}

async function renderBroadcasts() {
  const data = await api("/panel/api/broadcasts");
  const rows = data.recent
    .map(
      (row) => `<tr><td>${esc(row.id)}</td><td>${esc(row.status)}</td><td>${esc(row.delivered_count)}/${esc(row.total_targets)}</td><td>${esc(row.failed_count)}</td><td>${esc(row.blocked_count)}</td>
        <td>
          <button type="button" data-act="test" data-id="${row.id}">Test</button>
          <button type="button" data-act="pause" data-id="${row.id}">Pause</button>
          <button type="button" data-act="resume" data-id="${row.id}">Resume</button>
          <button type="button" data-act="stop" data-id="${row.id}">Stop</button>
        </td></tr>`
    )
    .join("");
  view(`<div class="card"><p>${esc(data.recipients)} people can receive the next message.</p>
      <textarea id="bc-text" rows="3" placeholder="Broadcast text. {name} is replaced."></textarea>
      <p><button class="primary" id="bc-send" type="button">Send to everyone</button></p></div>
      <table><thead><tr><th>ID</th><th>Status</th><th>Delivered</th><th>Failed</th><th>Blocked</th><th></th></tr></thead><tbody>${rows}</tbody></table>`);
  bind("bc-send", "click", async () => {
    if (!window.confirm("Send this message to every active user?")) return;
    await api("/panel/api/broadcasts", { method: "POST", body: { kind: "text", text: document.getElementById("bc-text").value, start: true } });
    renderBroadcasts();
  });
  document.querySelectorAll("[data-act]").forEach((button) => {
    button.addEventListener("click", async () => {
      const action = button.dataset.act;
      if (action === "stop" && !window.confirm("Stop this broadcast?")) return;
      await api(`/panel/api/broadcasts/${button.dataset.id}/${action}`, {
        method: "POST",
        body: { confirm: action === "stop" },
      });
      renderBroadcasts();
    });
  });
}

async function renderScheduled() {
  const data = await api("/panel/api/scheduled");
  const rows = data.upcoming
    .map((row) => `<tr><td>${esc(row.id)}</td><td>${esc(row.run_at)} UTC</td><td>${esc(row.status)}</td><td><button type="button" data-cancel="${row.id}">Cancel</button></td></tr>`)
    .join("");
  view(`<div class="card"><p>Times are UTC.</p>
      <input id="sch-time" type="text" placeholder="2026-09-29T18:00:00Z">
      <textarea id="sch-text" rows="3" placeholder="Message"></textarea>
      <p><button class="primary" id="sch-save" type="button">Schedule</button></p></div>
      <table><tbody>${rows}</tbody></table>`);
  bind("sch-save", "click", async () => {
    await api("/panel/api/scheduled", {
      method: "POST",
      body: { run_at: document.getElementById("sch-time").value, kind: "text", text: document.getElementById("sch-text").value },
    });
    renderScheduled();
  });
  document.querySelectorAll("[data-cancel]").forEach((button) => {
    button.addEventListener("click", async () => {
      if (!window.confirm("Cancel this scheduled job?")) return;
      await api(`/panel/api/scheduled/${button.dataset.cancel}/cancel`, { method: "POST", body: { confirm: true } });
      renderScheduled();
    });
  });
}

async function renderWelcome() {
  const data = await api("/panel/api/welcome");
  const steps = data.steps
    .map((row, index) => {
      const previous = data.steps[index - 1];
      return `<li>${esc(row.step_order)}: ${esc(JSON.stringify(row.payload))}
        ${previous ? `<button type="button" data-swap="${previous.step_order},${row.step_order}">Move up</button>` : ""}
        <button type="button" data-del="${row.step_order}">Delete</button></li>`;
    })
    .join("");
  view(`<div class="card"><p>Use {name} for the person's first name.</p>
      <label><input id="welcome-on" type="checkbox" ${data.enabled ? "checked" : ""}> Welcome flow enabled</label>
      <textarea id="welcome-text" rows="3" placeholder="Welcome message"></textarea>
      <p><button class="primary" id="welcome-save" type="button">Add step</button></p>
      <ol>${steps}</ol></div>`);
  bind("welcome-save", "click", async () => {
    await api("/panel/api/welcome", {
      method: "POST",
      body: { enabled: document.getElementById("welcome-on").checked, kind: "text", text: document.getElementById("welcome-text").value },
    });
    renderWelcome();
  });
  document.querySelectorAll("[data-swap]").forEach((button) => {
    button.addEventListener("click", async () => {
      const pair = button.dataset.swap.split(",").map(Number);
      await api("/panel/api/welcome", { method: "POST", body: { swap: pair } });
      renderWelcome();
    });
  });
  document.querySelectorAll("[data-del]").forEach((button) => {
    button.addEventListener("click", async () => {
      if (!window.confirm("Delete this welcome step?")) return;
      await api(`/panel/api/welcome/${button.dataset.del}`, { method: "DELETE", body: { confirm: true } });
      renderWelcome();
    });
  });
}

async function renderOnboarding() {
  const data = await api("/panel/api/onboarding");
  const steps = data.steps
    .map((row) => `<p>Step ${esc(row.step_order)} after ${esc(row.delay_seconds)}s<br><textarea data-step="${row.step_order}" data-delay="${row.delay_seconds}" rows="2">${esc(row.payload && row.payload.text ? row.payload.text : "")}</textarea></p>`)
    .join("");
  view(`<div class="card"><label><input id="onb-on" type="checkbox" ${data.enabled ? "checked" : ""}> Onboarding enabled</label>
      ${steps}<p><button class="primary" id="onb-save" type="button">Save</button></p>
      <p class="muted">Pending ${esc(data.counts.pending)} · sent ${esc(data.counts.sent)}</p></div>`);
  bind("onb-save", "click", async () => {
    await api("/panel/api/onboarding", { method: "POST", body: { enabled: document.getElementById("onb-on").checked } });
    for (const box of document.querySelectorAll("[data-step]")) {
      if (!box.value.trim()) continue;
      await api("/panel/api/onboarding", {
        method: "POST",
        body: { step_order: Number(box.dataset.step), delay_seconds: Number(box.dataset.delay), kind: "text", text: box.value },
      });
    }
    renderOnboarding();
  });
}

async function renderRetention() {
  const data = await api("/panel/api/retention");
  const steps = data.steps
    .map((row, index) => {
      const previous = data.steps[index - 1];
      return `<li>${esc(row.step_order)} delay ${esc(row.delay_seconds)}s ${esc(JSON.stringify(row.payload))}
        ${previous ? `<button type="button" data-rswap="${previous.step_order},${row.step_order}">Move up</button>` : ""}
        <button type="button" data-rdel="${row.step_order}">Delete</button></li>`;
    })
    .join("");
  view(`<div class="card"><label><input id="ret-on" type="checkbox" ${data.enabled ? "checked" : ""}> Retention enabled</label>
      <input id="ret-delay" type="number" value="0" placeholder="Delay seconds. 0 is as soon as the worker runs.">
      <textarea id="ret-text" rows="3" placeholder="Message after someone leaves"></textarea>
      <p><button class="primary" id="ret-save" type="button">Add step</button></p><ol>${steps}</ol></div>`);
  bind("ret-save", "click", async () => {
    await api("/panel/api/retention", {
      method: "POST",
      body: {
        enabled: document.getElementById("ret-on").checked,
        delay_seconds: Number(document.getElementById("ret-delay").value || 0),
        kind: "text",
        text: document.getElementById("ret-text").value,
      },
    });
    renderRetention();
  });
  document.querySelectorAll("[data-rswap]").forEach((button) => {
    button.addEventListener("click", async () => {
      const pair = button.dataset.rswap.split(",").map(Number);
      await api("/panel/api/retention", { method: "POST", body: { swap: pair } });
      renderRetention();
    });
  });
  document.querySelectorAll("[data-rdel]").forEach((button) => {
    button.addEventListener("click", async () => {
      if (!window.confirm("Delete this come-back step?")) return;
      await api(`/panel/api/retention/${button.dataset.rdel}`, { method: "DELETE", body: { confirm: true } });
      renderRetention();
    });
  });
}

async function renderChannel() {
  const data = await api("/panel/api/channel");
  const channel = data.channel || {};
  const live = data.livestream || {};
  const owner = state.role === "owner";
  view(`<div class="card">
      <p>Bot status in the channel: ${esc(data.bot_status || "not checked")}</p>
      <label>Channel id <input id="ch-id" type="text" value="${esc(channel.monitored_chat_id || "")}" ${owner ? "" : "disabled"}></label>
      <label><input id="ch-approve" type="checkbox" ${channel.auto_approve_join_requests ? "checked" : ""} ${owner ? "" : "disabled"}> Auto-approve join requests</label>
      <label><input id="ch-ret" type="checkbox" ${channel.retention_enabled ? "checked" : ""} ${owner ? "" : "disabled"}> Retention</label>
      <label>Livestream text <textarea id="ch-live" rows="2" ${owner ? "" : "disabled"}>${esc(live.notification_template || "")}</textarea></label>
      <label>Private live link <input id="ch-link" type="text" value="${esc(live.manual_live_url || "")}" ${owner ? "" : "disabled"}></label>
      <label>Cooldown seconds <input id="ch-cd" type="number" value="${esc(live.cooldown_seconds || 0)}" ${owner ? "" : "disabled"}></label>
      ${owner ? '<p><button class="primary" id="ch-save" type="button">Save channel</button></p>' : '<p class="muted">Only an owner can change channel settings.</p>'}
    </div>`);
  if (owner) {
    bind("ch-save", "click", async () => {
      await api("/panel/api/channel", {
        method: "POST",
        body: {
          monitored_chat_id: document.getElementById("ch-id").value,
          auto_approve_join_requests: document.getElementById("ch-approve").checked,
          retention_enabled: document.getElementById("ch-ret").checked,
          notification_template: document.getElementById("ch-live").value,
          manual_live_url: document.getElementById("ch-link").value,
          cooldown_seconds: Number(document.getElementById("ch-cd").value || 0),
        },
      });
      renderChannel();
    });
  }
}

async function renderPeople() {
  view(`<div class="card"><input id="people-q" type="text" placeholder="Name, username, or Telegram id">
      <p><button id="people-go" type="button">Search</button></p><div id="people-out"></div></div>`);
  bind("people-go", "click", async () => {
    const data = await api(`/panel/api/users?q=${encodeURIComponent(document.getElementById("people-q").value)}`);
    document.getElementById("people-out").innerHTML = `<table>${data.users
      .map((row) => `<tr><td>${esc(row.user_id)}</td><td>${esc(row.first_name)}</td><td>@${esc(row.username)}</td><td>${esc(row.broadcast_status)}</td><td>${esc(row.last_seen)}</td></tr>`)
      .join("")}</table>`;
  });
}

async function renderLogs() {
  const data = await api("/panel/api/logs");
  let audit = "";
  if (state.role === "owner") {
    const extra = await api("/panel/api/audit");
    audit = `<h2>Audit</h2><pre>${esc(JSON.stringify(extra.events, null, 2))}</pre>`;
  }
  view(`<pre>${esc(JSON.stringify(data.system, null, 2))}</pre>${audit}`);
}

async function renderQueue() {
  const data = await api("/panel/api/queue");
  view(`<div class="card"><p>Broadcast jobs waiting: ${esc(data.broadcast_queue)}</p>
      ${state.role === "owner" ? '<button class="danger" id="q-clear" type="button">Clear waiting broadcast jobs</button>' : ""}
      <pre>${esc(JSON.stringify({ active: data.active_broadcasts, failed_replies: data.failed_replies }, null, 2))}</pre></div>`);
  bind("q-clear", "click", async () => {
    if (!window.confirm("Clear the broadcast queue? This cannot be undone.")) return;
    await api("/panel/api/queue/clear", { method: "POST", body: { confirm: true } });
    renderQueue();
  });
}

async function renderHealth() {
  const data = await api("/panel/api/health");
  view(`<pre>${esc(JSON.stringify(data, null, 2))}</pre>`);
}

async function renderSetup() {
  const data = await api("/panel/api/setup");
  view(`<pre>${esc(JSON.stringify(data, null, 2))}</pre>`);
}

async function renderAdmins() {
  const data = await api("/panel/api/admins");
  const rows = data.admins
    .map(
      (row) => `<tr><td>${esc(row.admin_id)}</td><td>${esc(row.role)}</td><td>${esc(row.is_active)}</td><td>${esc(row.last_login_at)}</td><td>${esc(row.updated_by)}</td>
        <td><button type="button" data-off="${row.admin_id}">Deactivate</button></td></tr>`
    )
    .join("");
  view(`<div class="card"><input id="new-admin" type="number" placeholder="Telegram user id">
      <select id="new-role"><option value="admin">admin</option><option value="owner">owner</option></select>
      <p><button class="primary" id="add-admin" type="button">Add admin</button></p></div>
      <table><tbody>${rows}</tbody></table>`);
  bind("add-admin", "click", async () => {
    await api("/panel/api/admins", {
      method: "POST",
      body: { admin_id: Number(document.getElementById("new-admin").value), role: document.getElementById("new-role").value },
    });
    renderAdmins();
  });
  document.querySelectorAll("[data-off]").forEach((button) => {
    button.addEventListener("click", async () => {
      if (!window.confirm("Deactivate this admin?")) return;
      await api(`/panel/api/admins/${button.dataset.off}/active`, { method: "POST", body: { confirm: true, active: false } });
      renderAdmins();
    });
  });
}

document.getElementById("pin-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const data = await api("/panel/api/login/pin", {
      method: "POST",
      body: { mfa_token: state.mfa, pin: document.getElementById("pin").value },
    });
    state.csrf = data.csrf_token;
    state.role = data.role;
    showApp();
  } catch (error) {
    document.getElementById("login-error").textContent = error.message;
  }
});

document.getElementById("logout").addEventListener("click", async () => {
  await api("/panel/api/logout", { method: "POST", body: {} });
  window.location.reload();
});

boot();
