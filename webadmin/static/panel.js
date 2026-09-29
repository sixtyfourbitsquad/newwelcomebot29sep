const state = { csrf: "", role: "", section: "dashboard", mfa: "" };

const sections = [
  ["dashboard", "Home"],
  ["inbox", "Messages"],
  ["broadcasts", "Message everyone"],
  ["scheduled", "Send later"],
  ["welcome", "Welcome"],
  ["onboarding", "Follow-ups"],
  ["retention", "Come-back"],
  ["channel", "Channel"],
  ["people", "Members"],
  ["logs", "Activity"],
  ["queue", "Waiting to send"],
  ["health", "Is it working?"],
  ["setup", "Bot info"],
  ["admins", "Team"],
];

const groups = [
  ["Everyday", ["dashboard", "inbox", "broadcasts", "scheduled"]],
  ["Automatic messages", ["welcome", "onboarding", "retention", "channel"]],
  ["People and checks", ["people", "logs", "queue", "health", "setup", "admins"]],
];

const leads = {
  dashboard: "A quick look at your members and whether the bot is running.",
  inbox: "Messages from members. Everyone on the team can see them. A reply goes only to the person you choose.",
  broadcasts: "Send one message to everyone who started the bot and has not blocked it.",
  scheduled: "Write a message now and choose when it should go out. Use UTC time.",
  welcome: "The first messages a person gets after they press Start. Type {name} where you want their first name.",
  onboarding: "Extra messages after 1 hour, 1 day, and 3 days.",
  retention: "A message for someone who left the channel.",
  channel: "The channel this bot watches, join requests, and live alerts.",
  people: "Find a member by name, @username, or their number.",
  logs: "What the bot has been doing.",
  queue: "Messages that are still waiting to be sent.",
  health: "A simple check that the bot can store data and talk to Telegram.",
  setup: "The bot name and the addresses it uses. Passwords are hidden.",
  admins: "People who can use this page and the Telegram admin menu.",
};

const roleNames = { owner: "Owner", admin: "Admin", moderator: "Admin", support: "Admin" };

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
  const root = document.getElementById("view");
  root.innerHTML = html;
  labelTables(root);
}

function labelTables(root) {
  root.querySelectorAll("table").forEach((node) => {
    const labels = [...node.querySelectorAll("thead th")].map((cell) => cell.textContent.trim());
    node.querySelectorAll("tbody tr").forEach((row) => {
      [...row.children].forEach((cell, index) => {
        if (labels[index]) cell.dataset.label = labels[index];
      });
    });
  });
}

function closeMenu() {
  document.getElementById("app").classList.remove("nav-open");
}

function friendlyTime(seconds) {
  const total = Number(seconds) || 0;
  if (total < 60) return "just started";
  const days = Math.floor(total / 86400);
  const hours = Math.floor((total % 86400) / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  if (days) return `${days}d ${hours}h`;
  if (hours) return `${hours}h ${minutes}m`;
  return `${minutes} min`;
}

function waitLabel(seconds) {
  const total = Number(seconds) || 0;
  if (total <= 0) return "right away";
  if (total % 86400 === 0) {
    const days = total / 86400;
    return days === 1 ? "1 day later" : `${days} days later`;
  }
  if (total % 3600 === 0) {
    const hours = total / 3600;
    return hours === 1 ? "1 hour later" : `${hours} hours later`;
  }
  if (total % 60 === 0) return `${total / 60} minutes later`;
  return `${total} seconds later`;
}

function yesNo(value) {
  return value ? "Yes" : "No";
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
    document.getElementById("login-error").textContent = "This page is turned off.";
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
  document.getElementById("who").textContent = roleNames[state.role] || "Admin";
  const nav = document.getElementById("nav");
  nav.innerHTML = "";
  const labels = Object.fromEntries(sections);
  for (const [group, ids] of groups) {
    const title = document.createElement("p");
    title.className = "nav-label";
    title.textContent = group;
    nav.appendChild(title);
    for (const id of ids) {
      if (id === "admins" && state.role !== "owner") continue;
      const button = document.createElement("button");
      button.type = "button";
      button.className = "nav-btn";
      button.textContent = labels[id];
      button.dataset.section = id;
      button.addEventListener("click", () => openSection(id));
      nav.appendChild(button);
    }
  }
  openSection(state.section);
}

async function openSection(id) {
  state.section = id;
  document.querySelectorAll(".nav-btn").forEach((node) => {
    node.classList.toggle("active", node.dataset.section === id);
  });
  document.getElementById("title").textContent = sections.find((row) => row[0] === id)[1];
  document.getElementById("lead").textContent = leads[id] || "";
  closeMenu();
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

function payloadText(payload) {
  if (!payload) return "";
  if (typeof payload === "string") return payload;
  return payload.text || payload.caption || "";
}

function table(head, rows) {
  if (!rows) return `<div class="card empty">Nothing here yet.</div>`;
  return `<div class="table-wrap"><table><thead><tr>${head}</tr></thead><tbody>${rows}</tbody></table></div>`;
}

async function renderDashboard() {
  const data = await api("/panel/api/dashboard");
  view(
    cards([
      ["People", data.users.total],
      ["Can get messages", data.users.active],
      ["Pressed Start", data.started_users],
      ["Blocked the bot", data.users.blocked],
      ["Follow-ups waiting", data.onboarding.pending],
      ["Messages received", data.inbox.messages],
      ["Still sending", data.broadcast_queue],
      ["Running for", friendlyTime(data.uptime_seconds)],
    ]) +
      `<div class="card">Saved information: ${data.postgres_ok ? "working" : "not working"}. Fast storage: ${data.redis_ok ? "working" : "not working"}. Telegram connection: ${esc(data.webhook)}</div>`
  );
}

async function renderInbox() {
  const data = await api("/panel/api/inbox");
  const rows = data.conversations
    .map(
      (row) => `<tr>
        <td>${esc(row.first_name)} <span class="muted">${row.username ? "@" + esc(row.username) : ""}</span></td>
        <td>${esc(row.message_type)}</td>
        <td>${esc(row.content_text)}</td>
        <td>${esc(row.received_at)}</td>
        <td>${esc(row.last_reply_status || "No reply yet")}</td>
        <td><button type="button" data-user="${esc(row.telegram_user_id)}">Reply</button></td>
      </tr>`
    )
    .join("");
  view(
    table("<th>Person</th><th>Kind</th><th>Message</th><th>When</th><th>Last reply</th><th></th>", rows) +
      `<div id="thread"></div>`
  );
  document.querySelectorAll("[data-user]").forEach((button) => {
    button.addEventListener("click", () => openThread(button.dataset.user));
  });
}

async function openThread(userId) {
  const data = await api(`/panel/api/inbox/${userId}`);
  const name = data.user ? (data.user.first_name || "this person") : "this person";
  const messages = (data.thread.messages || [])
    .map((row) => `<p><strong>${esc(row.message_type)}</strong> ${esc(row.content_text)} <span class="muted">${esc(row.received_at)}</span></p>`)
    .join("");
  const replies = (data.thread.replies || [])
    .map((row) => `<p>Your reply (${esc(row.delivery_status)}): ${esc(row.content_text)} ${row.telegram_error ? esc(row.telegram_error) : ""}</p>`)
    .join("");
  document.getElementById("thread").innerHTML = `<div class="card">
      <h2>Reply to ${esc(name)}</h2>
      ${messages || "<p class=\"muted\">No saved messages.</p>"}${replies}
      <label>Your reply
        <textarea id="reply-text" rows="3" placeholder="Write the message they will receive"></textarea>
      </label>
      <label><input id="reply-confirm" type="checkbox"> Send this only to ${esc(name)}</label>
      <p><button class="primary" id="send-reply" type="button">Send</button></p>
      <p id="reply-result"></p>
    </div>`;
  document.getElementById("send-reply").addEventListener("click", async () => {
    const result = document.getElementById("reply-result");
    if (!document.getElementById("reply-confirm").checked) {
      result.textContent = "Tick the box so this goes to the right person.";
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
      result.textContent = sent.ok ? "Sent." : `Not sent: ${sent.error}`;
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
          <button type="button" data-act="test" data-id="${row.id}">Send me a test</button>
          <button type="button" data-act="pause" data-id="${row.id}">Pause</button>
          <button type="button" data-act="resume" data-id="${row.id}">Continue</button>
          <button type="button" data-act="stop" data-id="${row.id}">Stop</button>
        </td></tr>`
    )
    .join("");
  view(`<div class="card"><p>${esc(data.recipients)} people will receive this.</p>
      <label>Message
        <textarea id="bc-text" rows="4" placeholder="Write the message. {name} becomes their first name."></textarea>
      </label>
      <p><button class="primary" id="bc-send" type="button">Send to everyone</button></p></div>
      ${table("<th>Number</th><th>Status</th><th>Delivered</th><th>Could not send</th><th>Blocked</th><th></th>", rows)}`);
  bind("bc-send", "click", async () => {
    if (!window.confirm("Send this message to everyone who can receive it?")) return;
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
  view(`<div class="card">
      <label>When to send (UTC)
        <input id="sch-time" type="text" placeholder="Example: 2026-09-29 18:00 UTC">
      </label>
      <label>Message
        <textarea id="sch-text" rows="4" placeholder="What should they receive?"></textarea>
      </label>
      <p><button class="primary" id="sch-save" type="button">Save for later</button></p></div>
      ${table("<th>Number</th><th>When</th><th>Status</th><th></th>", rows)}`);
  bind("sch-save", "click", async () => {
    await api("/panel/api/scheduled", {
      method: "POST",
      body: { run_at: document.getElementById("sch-time").value, kind: "text", text: document.getElementById("sch-text").value },
    });
    renderScheduled();
  });
  document.querySelectorAll("[data-cancel]").forEach((button) => {
    button.addEventListener("click", async () => {
      if (!window.confirm("Cancel this message?")) return;
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
      return `<li>${esc(row.step_order)}. ${esc(payloadText(row.payload)) || "No text yet"}
        ${previous ? `<button type="button" data-swap="${previous.step_order},${row.step_order}">Move up</button>` : ""}
        <button type="button" data-del="${row.step_order}">Delete</button></li>`;
    })
    .join("");
  view(`<div class="card">
      <label><input id="welcome-on" type="checkbox" ${data.enabled ? "checked" : ""}> Send welcome messages</label>
      <label>New welcome message
        <textarea id="welcome-text" rows="4" placeholder="Hello {name}, welcome."></textarea>
      </label>
      <p><button class="primary" id="welcome-save" type="button">Add this message</button></p>
      <ol>${steps || "<li>No welcome messages yet.</li>"}</ol></div>`);
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
    .map((row) => `<p><strong>Message ${esc(row.step_order)}</strong> <span class="muted">${esc(waitLabel(row.delay_seconds))}</span><br><textarea data-step="${row.step_order}" data-delay="${row.delay_seconds}" rows="3">${esc(row.payload && row.payload.text ? row.payload.text : "")}</textarea></p>`)
    .join("");
  view(`<div class="card"><label><input id="onb-on" type="checkbox" ${data.enabled ? "checked" : ""}> Send these follow-ups</label>
      ${steps}<p><button class="primary" id="onb-save" type="button">Save</button></p>
      <p class="muted">${esc(data.counts.pending)} waiting · ${esc(data.counts.sent)} already sent</p></div>`);
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
      return `<li>Message ${esc(row.step_order)}, ${esc(waitLabel(row.delay_seconds))}. ${esc(payloadText(row.payload)) || "No text yet"}
        ${previous ? `<button type="button" data-rswap="${previous.step_order},${row.step_order}">Move up</button>` : ""}
        <button type="button" data-rdel="${row.step_order}">Delete</button></li>`;
    })
    .join("");
  view(`<div class="card"><label><input id="ret-on" type="checkbox" ${data.enabled ? "checked" : ""}> Send come-back messages</label>
      <label>How long to wait, in seconds
        <input id="ret-delay" type="number" value="0" placeholder="0 means as soon as possible">
      </label>
      <label>Message
        <textarea id="ret-text" rows="4" placeholder="We miss you. Tap to come back."></textarea>
      </label>
      <p><button class="primary" id="ret-save" type="button">Add this message</button></p><ol>${steps || "<li>No come-back messages yet.</li>"}</ol></div>`);
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
      <p>The bot in this channel is ${esc(data.bot_status || "not checked yet")}.</p>
      <label>Channel number
        <input id="ch-id" type="text" value="${esc(channel.monitored_chat_id || "")}" ${owner ? "" : "disabled"}>
      </label>
      <label><input id="ch-approve" type="checkbox" ${channel.auto_approve_join_requests ? "checked" : ""} ${owner ? "" : "disabled"}> Approve join requests automatically</label>
      <label><input id="ch-ret" type="checkbox" ${channel.retention_enabled ? "checked" : ""} ${owner ? "" : "disabled"}> Message people who leave</label>
      <label>Live alert text
        <textarea id="ch-live" rows="3" ${owner ? "" : "disabled"}>${esc(live.notification_template || "")}</textarea>
      </label>
      <label>Private live link
        <input id="ch-link" type="text" value="${esc(live.manual_live_url || "")}" ${owner ? "" : "disabled"}>
      </label>
      <label>Wait between live alerts (seconds)
        <input id="ch-cd" type="number" value="${esc(live.cooldown_seconds || 0)}" ${owner ? "" : "disabled"}>
      </label>
      ${owner ? '<p><button class="primary" id="ch-save" type="button">Save</button></p>' : '<p class="muted">Only the owner can change these.</p>'}
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
  view(`<div class="card"><label>Find a member
      <input id="people-q" type="text" placeholder="Name, @username, or number">
      </label>
      <p><button class="primary" id="people-go" type="button">Search</button></p><div id="people-out"></div></div>`);
  bind("people-go", "click", async () => {
    const data = await api(`/panel/api/users?q=${encodeURIComponent(document.getElementById("people-q").value)}`);
    const rows = data.users
      .map((row) => `<tr><td>${esc(row.user_id)}</td><td>${esc(row.first_name)}</td><td>@${esc(row.username)}</td><td>${esc(row.broadcast_status)}</td><td>${esc(row.last_seen)}</td></tr>`)
      .join("");
    document.getElementById("people-out").innerHTML = table("<th>Number</th><th>Name</th><th>Username</th><th>Can get messages</th><th>Last seen</th>", rows);
    labelTables(document.getElementById("people-out"));
  });
}

async function renderLogs() {
  const data = await api("/panel/api/logs");
  let audit = "";
  if (state.role === "owner") {
    const extra = await api("/panel/api/audit");
    const events = extra.events || [];
    audit = events.length
      ? `<h2>Team actions</h2>${facts(events.slice(0, 20))}`
      : `<div class="card empty">No team actions yet.</div>`;
  }
  const system = data.system;
  view(`${Array.isArray(system) ? facts(system.slice(0, 20)) : facts(system)}${audit}`);
}

function facts(value) {
  if (Array.isArray(value)) {
    return value.map((item) => facts(item)).join("");
  }
  if (!value || typeof value !== "object") {
    return `<div class="card">${esc(value)}</div>`;
  }
  const rows = Object.entries(value)
    .map(([key, item]) => {
      const shown = item && typeof item === "object" ? JSON.stringify(item) : item;
      return `<div class="fact"><div class="muted">${esc(String(key).replaceAll("_", " "))}</div><div>${esc(shown ?? "")}</div></div>`;
    })
    .join("");
  return `<div class="card facts">${rows}</div>`;
}

async function renderQueue() {
  const data = await api("/panel/api/queue");
  view(`<div class="card"><p><strong>${esc(data.broadcast_queue)}</strong> messages are still waiting to go out.</p>
      ${state.role === "owner" ? '<button class="danger" id="q-clear" type="button">Clear the waiting list</button>' : ""}
      <p class="muted">Messages already being sent are left alone.</p></div>`);
  bind("q-clear", "click", async () => {
    if (!window.confirm("Clear messages that have not been sent yet? This cannot be undone.")) return;
    await api("/panel/api/queue/clear", { method: "POST", body: { confirm: true } });
    renderQueue();
  });
}

async function renderHealth() {
  const data = await api("/panel/api/health");
  view(cards([
    ["Saved information", data.postgres_ok ? "Working" : "Not working"],
    ["Fast storage", data.redis_ok ? "Working" : "Not working"],
    ["Telegram connection", data.webhook || "Unknown"],
    ["Waiting to send", data.broadcast_queue],
    ["Running for", friendlyTime(data.uptime_seconds)],
  ]));
}

async function renderSetup() {
  const data = await api("/panel/api/setup");
  view(cards([
    ["Bot", data.bot_username ? "@" + data.bot_username : "Unknown"],
    ["Running for", friendlyTime(data.uptime_seconds)],
    ["Channel", data.channel_id || "Not set"],
    ["Join requests", data.join_requests_total || 0],
    ["Telegram connection", data.webhook_status || "Unknown"],
    ["This page", data.admin_panel_url || "This site"],
  ]));
}

async function renderAdmins() {
  const data = await api("/panel/api/admins");
  const rows = data.admins
    .map(
      (row) => `<tr><td>${esc(row.admin_id)}</td><td>${esc(roleNames[row.role] || row.role)}</td><td>${yesNo(row.is_active)}</td><td>${esc(row.last_login_at || "Not yet")}</td>
        <td><button type="button" data-off="${row.admin_id}">Remove access</button></td></tr>`
    )
    .join("");
  view(`<div class="card">
      <label>Their Telegram number
        <input id="new-admin" type="number" placeholder="Example: 123456789">
      </label>
      <label>What they can do
        <select id="new-role"><option value="admin">Help with messages</option><option value="owner">Full control</option></select>
      </label>
      <p><button class="primary" id="add-admin" type="button">Add to the team</button></p></div>
      ${table("<th>Number</th><th>Role</th><th>Can sign in</th><th>Last visit</th><th></th>", rows)}`);
  bind("add-admin", "click", async () => {
    await api("/panel/api/admins", {
      method: "POST",
      body: { admin_id: Number(document.getElementById("new-admin").value), role: document.getElementById("new-role").value },
    });
    renderAdmins();
  });
  document.querySelectorAll("[data-off]").forEach((button) => {
    button.addEventListener("click", async () => {
      if (!window.confirm("Remove this person's access?")) return;
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

document.getElementById("menu").addEventListener("click", () => {
  document.getElementById("app").classList.toggle("nav-open");
});
document.getElementById("scrim").addEventListener("click", closeMenu);

document.getElementById("logout").addEventListener("click", async () => {
  await api("/panel/api/logout", { method: "POST", body: {} });
  window.location.reload();
});

boot();
