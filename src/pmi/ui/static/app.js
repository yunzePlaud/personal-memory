"use strict";
// PMI web UI. Everything from the server is inserted with textContent / createElement only:
// memories and logs contain other people's words and must never be parsed as HTML.

const TOKEN_KEY = "pmi-token";
const RETURN_KEY = "pmi-return-route";
const HIDE_KEY = "pmi-hide-idle";
const DEFAULT_ROUTE = "#/overview";
const KINDS = ["decision", "semantic", "episodic", "project", "person", "procedure", "preference"];
const STATUSES = ["active", "superseded", "disputed", "archived", "all"];
const PAGE_SIZE = 50;
const MAX_LOG_LINES = 5000;

let timers = [];
let renderSeq = 0;
let currentTab = null;
let memoriesView = null;
const memoryFilters = { q: "", status: "active", kind: "", slot: "" };

// ------------------------------------------------------------------ helpers

function h(tag, props, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") el.className = value;
    else if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
    else el.setAttribute(key, value === true ? "" : String(value));
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : String(child));
  }
  return el;
}

// replaceChildren() does not flatten arrays: pass section lists through this first.
function nodes(...items) {
  return items.flat(Infinity).filter((x) => x !== null && x !== undefined && x !== false);
}

function store(key, value) {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch (_) { /* storage may be unavailable; the page still works for this tab */ }
}

function load(key) {
  try { return localStorage.getItem(key); } catch (_) { return null; }
}

function every(ms, fn) { timers.push(setInterval(fn, ms)); }

function clearTimers() { timers.forEach(clearInterval); timers = []; }

function bytes(n) {
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = n;
  let i = 0;
  while (value >= 1024 && i < units.length - 1) { value /= 1024; i += 1; }
  return `${i === 0 ? value : value.toFixed(1)} ${units[i]}`;
}

function fmtTime(s) {
  if (!s) return "";
  const d = new Date(s);
  return Number.isNaN(d.getTime()) ? s : d.toLocaleString();
}

function safeUrl(u) {
  try {
    const url = new URL(u);
    return url.protocol === "http:" || url.protocol === "https:" ? url.href : null;
  } catch (_) {
    return null;
  }
}

function minutes(seconds) { return Math.max(0, Math.floor(seconds / 60)); }

function statusText(st) {
  if (st.state === "missing") return "No log file";
  if (st.state === "stale") return `No output for ${minutes(st.age_seconds)} min`;
  const m = minutes(st.age_seconds);
  return `Active · last output ${m === 0 ? "less than a minute" : `${m} min`} ago`;
}

function statusBadge(st) { return h("span", { class: `badge ${st.state}` }, statusText(st)); }

function chip(text, extra) { return h("span", { class: `chip ${extra || ""}` }, text); }

function debounce(fn, ms) {
  let t = null;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

function toast(message, kind) {
  const el = h("div", { class: `toast ${kind || ""}` }, message);
  document.getElementById("toasts").append(el);
  setTimeout(() => el.remove(), 4000);
}

// ------------------------------------------------------------------ API

class Unauthorized extends Error {}

function signedOut() {
  clearTimers();
  if (!load(RETURN_KEY) && location.hash && !location.hash.startsWith("#token=")) {
    store(RETURN_KEY, location.hash);
  }
  document.getElementById("view").replaceChildren(h("section", { class: "panel signin" },
    h("h2", {}, "Signed out"),
    h("p", {}, 'Open the link printed by "pmi ui" to sign in again.'),
    h("p", { class: "muted" }, "You will come back to this page afterwards.")));
}

async function api(path, options = {}) {
  const headers = { "X-PMI-Token": load(TOKEN_KEY) || "" };
  let body;
  if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(options.body);
  }
  const res = await fetch(path, { method: options.method || "GET", headers, body, cache: "no-store" });
  let data = null;
  try { data = await res.json(); } catch (_) { data = null; }
  if (res.status === 401) {
    signedOut();
    throw new Unauthorized("signed out");
  }
  if (!res.ok) throw new Error((data && data.error && data.error.message) || `HTTP ${res.status}`);
  return data;
}

function post(path, body) { return api(path, { method: "POST", body: body || {} }); }

function handleBackground(err) {
  if (!(err instanceof Unauthorized)) toast(err.message, "error");
}

function setBadge(n) {
  const el = document.getElementById("review-count");
  el.hidden = !n;
  el.textContent = n ? ` ${n}` : "";
}

async function refreshBadge() {
  try { setBadge((await api("/api/overview")).pending_proposals); } catch (err) { handleBackground(err); }
}

// ------------------------------------------------------------------ dialogs

function modal({ title, message, reason, requireText, confirmLabel, danger }) {
  return new Promise((resolve) => {
    const root = document.getElementById("modal-root");
    const reasonInput = reason !== undefined ? h("input", { type: "text", value: reason }) : null;
    const typed = requireText ? h("input", { type: "text", autocomplete: "off" }) : null;
    const ok = h("button", { class: `btn ${danger ? "danger" : "primary"}` }, confirmLabel);
    const cancel = h("button", { class: "btn" }, "Cancel");
    const update = () => {
      ok.disabled = Boolean((reasonInput && !reasonInput.value.trim()) || (typed && typed.value !== requireText));
    };
    const close = (value) => { root.replaceChildren(); resolve(value); };
    ok.addEventListener("click", () => close({ reason: reasonInput ? reasonInput.value.trim() : null }));
    cancel.addEventListener("click", () => close(null));
    [reasonInput, typed].forEach((el) => el && el.addEventListener("input", update));
    root.replaceChildren(h("div", { class: "overlay" }, h("div", { class: "modal panel", role: "dialog" },
      h("h2", {}, title),
      h("p", {}, message),
      reasonInput && h("label", {}, "Reason", reasonInput),
      typed && h("label", {}, `Type ${requireText} to confirm`, typed),
      h("div", { class: "actions" }, cancel, ok))));
    update();
    (typed || reasonInput || ok).focus();
  });
}

// ------------------------------------------------------------------ shared pieces

function memoryRow(m, selected) {
  return h("li", { class: selected ? "selected" : null },
    h("a", { href: `#/memories/${encodeURIComponent(m.id)}` },
      chip(m.kind),
      h("span", { class: "title" }, m.title || m.content),
      m.status !== "active" ? chip(m.status, m.status) : null,
      m.updated_at ? h("span", { class: "muted small" }, fmtTime(m.updated_at)) : null));
}

function configHelp() {
  return h("section", { class: "panel" },
    h("h2", {}, "No logs configured"),
    h("p", {}, "Add a log to config/settings.toml in your PMI folder, then restart pmi ui:"),
    h("pre", { class: "snippet" },
      '[[ui.logs]]\nname = "my-agent"\npath = "~/.my-agent/logs/*.log"\nstale_minutes = 10'));
}

function citation(c, url) {
  const link = safeUrl(url);
  const titleEl = link
    ? h("a", { href: link, target: "_blank", rel: "noopener noreferrer" }, c.title || link)
    : h("span", {}, c.title || "source");
  return h("div", { class: "citation" },
    h("blockquote", {}, c.quote),
    h("div", { class: "muted small" }, titleEl,
      c.version ? ` · version ${c.version}${c.is_latest === false ? " (not the latest)" : ""}` : "",
      c.trust ? ` · ${c.trust}` : ""));
}

// ------------------------------------------------------------------ overview

function card(title, value, sub, href) {
  return h("a", { class: "card", href }, h("div", { class: "muted small" }, title),
    h("div", { class: "value" }, value), h("div", { class: "muted small" }, sub));
}

async function overviewPage(view, arg, alive) {
  const draw = async () => {
    const o = await api("/api/overview");
    if (!alive()) return;
    setBadge(o.pending_proposals);
    const m = o.memories;
    const cards = h("div", { class: "cards" },
      card("Memories", String(m.active), `${m.archived} archived · ${m.superseded} superseded · ${m.disputed} disputed`,
        "#/memories"),
      card("Review", String(o.pending_proposals), o.pending_proposals ? "waiting for you" : "nothing pending",
        "#/review"),
      card("Disk", bytes(o.disk.total_bytes), o.disk.top.map((i) => `${i.label} ${bytes(i.bytes)}`).join(" · "),
        "#/storage"),
      o.logs.map((l) => h("a", { class: "card", href: `#/logs/${encodeURIComponent(l.name)}` },
        h("div", { class: "muted small" }, l.name), h("div", { class: "value small-value" }, statusBadge(l.status)))));
    const recent = h("section", { class: "panel" }, h("h2", {}, "Recent memories"),
      o.recent_memories.length ? h("ul", { class: "list" }, o.recent_memories.map((r) => memoryRow(r)))
        : h("p", { class: "muted" }, "No memories yet."));
    let latest;
    if (!o.logs.length) latest = h("p", { class: "muted" }, "No logs configured. See the Logs tab.");
    else if (!o.latest_log) latest = h("p", { class: "muted" }, "No log file yet.");
    else latest = h("pre", { class: "log short" }, o.latest_log.lines.join("\n"));
    const logs = h("section", { class: "panel" },
      h("h2", {}, o.latest_log ? `Latest log lines · ${o.latest_log.name}` : "Latest log lines"), latest);
    view.replaceChildren(cards, h("div", { class: "two-col" }, recent, logs));
  };
  await draw();
  every(10000, () => draw().catch(handleBackground));
}

// ------------------------------------------------------------------ memories

function select(options, value, emptyLabel) {
  const el = h("select", {}, options.map((o) => h("option", { value: o }, o || emptyLabel || "all")));
  el.value = value;
  return el;
}

async function memoriesPage(view, arg, alive) {
  const f = memoryFilters;
  const list = h("ul", { class: "list" });
  const more = h("button", { class: "btn" }, "Load more");
  const detail = h("section", { class: "panel detail" });
  let offset = 0;

  async function loadList(reset) {
    if (reset) offset = 0;
    const params = new URLSearchParams({ status: f.status, limit: String(PAGE_SIZE), offset: String(offset) });
    if (f.q) params.set("q", f.q);
    if (f.kind) params.set("kind", f.kind);
    if (f.slot) params.set("slot", f.slot);
    const data = await api(`/api/memories?${params}`);
    if (!alive()) return;
    if (reset) list.replaceChildren();
    const selected = memoriesView && memoriesView.selected;
    data.items.forEach((m) => list.append(memoryRow(m, m.id === selected)));
    offset += data.items.length;
    more.hidden = Boolean(f.q) || data.items.length < PAGE_SIZE;
    if (reset && !data.items.length) list.append(h("li", { class: "muted" }, "No memories match."));
  }

  const reload = () => loadList(true).catch(handleBackground);
  const search = h("input", { type: "search", placeholder: "Search memories (Chinese or English)", value: f.q });
  search.addEventListener("input", debounce(() => { f.q = search.value.trim(); reload(); }, 300));
  const status = select(STATUSES, f.status);
  status.addEventListener("change", () => { f.status = status.value; reload(); });
  const kind = select(["", ...KINDS], f.kind, "all kinds");
  kind.addEventListener("change", () => { f.kind = kind.value; reload(); });
  const slot = h("input", { type: "text", placeholder: "Slot", value: f.slot });
  slot.addEventListener("input", debounce(() => { f.slot = slot.value.trim(); reload(); }, 300));
  more.addEventListener("click", () => loadList(false).catch(handleBackground));

  view.replaceChildren(h("div", { class: "split" },
    h("aside", { class: "panel" },
      h("div", { class: "filters" }, search, h("div", { class: "row" }, status, kind, slot)), list, more),
    detail));
  memoriesView = { detail, list, reload, selected: arg || null };
  await loadList(true);
  await showDetail(arg, alive);
}

function actionButton(label, cls, onclick) { return h("button", { class: `btn ${cls || ""}`, onclick }, label); }

async function runAction(promise, message) {
  await promise;
  toast(message);
  refreshBadge();
}

function markSelected(id) {
  memoriesView.list.querySelectorAll("li").forEach((li) => {
    const a = li.querySelector("a");
    li.classList.toggle("selected", Boolean(a && id && a.getAttribute("href") === `#/memories/${encodeURIComponent(id)}`));
  });
}

async function showDetail(id, alive) {
  const { detail, reload } = memoriesView;
  memoriesView.selected = id || null;
  markSelected(id);
  if (!id) {
    detail.replaceChildren(h("p", { class: "muted" }, "Select a memory to see its details."));
    return;
  }
  const m = await api(`/api/memories/${encodeURIComponent(id)}`);
  if (!alive()) return;
  const base = `/api/memories/${encodeURIComponent(m.id)}`;
  const refresh = () => { reload(); showDetail(m.id, alive).catch(handleBackground); };
  const act = (promise, message) => runAction(promise, message).then(refresh).catch(handleBackground);
  const a = m.actions;
  const buttons = [
    a.confirm && actionButton("Confirm", "primary", () => act(post(`${base}/confirm`), "Confirmed.")),
    a.resolve && actionButton("Resolve in Review", "primary", () => {
      location.hash = `#/review/${encodeURIComponent(m.pending_proposals[0] || "")}`;
    }),
    a.archive && actionButton("Archive", "", () => {
      if (window.confirm("Archive this memory? It stops appearing in answers, and you can restore it later.")) {
        act(post(`${base}/archive`), "Archived.");
      }
    }),
    a.restore && actionButton("Restore", "", () => act(post(`${base}/restore`), "Restored.")),
    a.forget && actionButton("Delete permanently", "danger", async () => {
      const answer = await modal({
        title: "Delete permanently", message: "This removes the memory for good. It cannot be restored.",
        reason: "deleted in web UI", requireText: "DELETE", confirmLabel: "Delete", danger: true });
      if (!answer) return;
      try {
        await runAction(post(`${base}/forget`, { reason: answer.reason }), "Deleted.");
        reload();
        location.hash = "#/memories";
      } catch (err) { handleBackground(err); }
    }),
  ];
  const history = (m.history || []).map((v) => h("li", {},
    `v${v.version} ${v.change_kind} by ${v.actor} · ${fmtTime(v.changed_at)}${v.reason ? ` — ${v.reason}` : ""}`));
  const valid = `${m.valid_from ? fmtTime(m.valid_from) : "—"} → ${m.valid_until ? fmtTime(m.valid_until) : "now"}`;
  detail.replaceChildren(...nodes(
    h("h2", {}, m.title),
    h("div", { class: "meta" }, chip(m.kind), chip(m.status, m.status), chip(m.verification),
      m.slot ? chip(`slot ${m.slot}`) : null),
    h("div", { class: "content" }, m.content),
    h("div", { class: "actions" }, buttons),
    h("dl", { class: "facts" },
      h("dt", {}, "Written by"), h("dd", {}, m.created_by),
      h("dt", {}, "Valid"), h("dd", {}, valid),
      h("dt", {}, "Created"), h("dd", {}, fmtTime(m.created_at)),
      h("dt", {}, "Updated"), h("dd", {}, fmtTime(m.updated_at))),
    h("h3", {}, "Evidence"),
    m.evidence.length ? m.evidence.map((e) => citation(e, e.url)) : h("p", { class: "muted" }, "No source quotes."),
    m.entities.length ? [h("h3", {}, "Entities"), h("div", { class: "meta" },
      m.entities.map((e) => chip(`${e.name} · ${e.kind}${e.role ? ` · ${e.role}` : ""}`)))] : null,
    (m.superseded_by || m.supersedes.length) ? [h("h3", {}, "Related"), h("ul", { class: "plain" },
      m.superseded_by ? h("li", {}, "Replaced by ",
        h("a", { href: `#/memories/${encodeURIComponent(m.superseded_by)}` }, m.superseded_by)) : null,
      m.supersedes.map((s) => h("li", {}, "Replaces ",
        h("a", { href: `#/memories/${encodeURIComponent(s.id)}` }, s.title || s.id))))] : null,
    history.length ? [h("h3", {}, "History"), h("ul", { class: "plain small" }, history)] : null));
}

// ------------------------------------------------------------------ review

function previewBlock(label, mem, cls) {
  if (!mem) return null;
  return h("div", { class: `preview ${cls || ""}` },
    h("div", { class: "muted small" }, label, mem.status ? ` · ${mem.status}` : "", mem.kind ? ` · ${mem.kind}` : ""),
    h("div", { class: "content" }, mem.content || ""),
    (mem.citations || []).map((c) => citation(c, null)));
}

function proposalCard(p, onDone) {
  const base = `/api/proposals/${encodeURIComponent(p.id)}`;
  const approve = actionButton("Approve", "primary", () =>
    runAction(post(`${base}/approve`), "Approved.").then(onDone).catch(handleBackground));
  const reject = actionButton("Reject", "", async () => {
    const answer = await modal({ title: "Reject this change", message: "The proposed change will not be applied.",
      reason: "rejected in web UI", confirmLabel: "Reject" });
    if (answer) {
      runAction(post(`${base}/reject`, { reason: answer.reason }), "Rejected.").then(onDone).catch(handleBackground);
    }
  });
  const items = (p.preview || []).map((item) => h("div", { class: "op" },
    h("div", { class: "small" }, chip(item.op)),
    previewBlock("New", item.new_memory, "new"),
    previewBlock("Would become current", item.activates, "new"),
    (item.replaces || []).map((r) => previewBlock("Would replace", r, "old")),
    previewBlock("Target", item.target, "old"),
    item.changes ? h("pre", { class: "snippet" }, JSON.stringify(item.changes, null, 2)) : null));
  return h("article", { class: "panel proposal", id: `p-${p.id}` },
    h("div", { class: "muted small" }, `Proposed by ${p.actor} · ${fmtTime(p.created_at)}`),
    p.risk ? h("p", { class: "risk" }, `Needs your review: ${p.risk}`) : null,
    p.reason ? h("p", {}, `Reason given: ${p.reason}`) : null,
    items,
    h("div", { class: "actions" }, approve, reject));
}

async function reviewPage(view, arg, alive) {
  const draw = async () => {
    const data = await api("/api/proposals");
    if (!alive()) return;
    setBadge(data.items.length);
    if (!data.items.length) {
      view.replaceChildren(h("section", { class: "panel" }, h("p", { class: "muted" }, "Nothing to review.")));
      return;
    }
    view.replaceChildren(...data.items.map((p) => proposalCard(p, () => draw().catch(handleBackground))));
  };
  await draw();
  if (arg) {
    const el = document.getElementById(`p-${arg}`);
    if (el) { el.classList.add("focus"); el.scrollIntoView({ block: "start" }); }
  }
}

// ------------------------------------------------------------------ logs

function splitLines(text) { return text ? text.replace(/\n$/, "").split("\n") : []; }

async function logsPage(view, arg, alive) {
  const { logs } = await api("/api/logs");
  if (!alive()) return;
  if (!logs.length) { view.replaceChildren(configHelp()); return; }
  const spec = logs.find((l) => l.name === arg) || logs[0];
  const enc = encodeURIComponent;
  const tabs = logs.length > 1 ? h("div", { class: "subtabs" }, logs.map((l) =>
    h("a", { href: `#/logs/${enc(l.name)}`, class: l.name === spec.name ? "active" : null }, l.name))) : null;
  const badgeSlot = h("span", {}, statusBadge(spec.status));
  if (!spec.files.length) {
    view.replaceChildren(tabs || "", h("section", { class: "panel" }, h("h2", {}, spec.name), badgeSlot,
      h("p", { class: "muted" }, `No file matches ${spec.path} yet.`)));
    return;
  }
  let file = spec.files[0];
  let following = true;
  let offset = 0;
  let lines = [];
  let stick = true;
  let hide = load(HIDE_KEY) !== "0";
  const pre = h("pre", { class: "log" });
  const fileSelect = select(spec.files, file);
  pre.addEventListener("scroll", () => { stick = pre.scrollHeight - pre.scrollTop - pre.clientHeight < 40; });

  const paint = () => {
    const shown = hide && spec.hide_pattern ? lines.filter((l) => !l.includes(spec.hide_pattern)) : lines;
    pre.textContent = shown.join("\n");
    if (stick) pre.scrollTop = pre.scrollHeight;
  };
  const read = async (reset) => {
    const q = reset ? "" : `?since=${offset}`;
    const r = await api(`/api/logs/${enc(spec.name)}/${enc(file)}${q}`);
    if (!alive()) return;
    lines = reset || r.reset ? splitLines(r.text) : lines.concat(splitLines(r.text));
    if (lines.length > MAX_LOG_LINES) lines = lines.slice(-MAX_LOG_LINES);
    offset = r.offset;
    paint();
  };
  const switchTo = async (name) => {
    file = name;
    fileSelect.value = name;
    stick = true;
    await read(true);
  };
  fileSelect.addEventListener("change", () => {
    following = fileSelect.value === spec.files[0];
    switchTo(fileSelect.value).catch(handleBackground);
  });
  const hideBox = spec.hide_pattern ? h("label", { class: "check" },
    h("input", { type: "checkbox", checked: hide }), "Hide idle lines") : null;
  if (hideBox) {
    hideBox.firstChild.addEventListener("change", (e) => {
      hide = e.target.checked;
      store(HIDE_KEY, hide ? "1" : "0");
      paint();
    });
  }
  view.replaceChildren(tabs || "", h("section", { class: "panel" },
    h("div", { class: "log-head" }, h("h2", {}, spec.name), badgeSlot, fileSelect, hideBox), pre));
  await read(true);

  const poll = async () => {
    const fresh = (await api("/api/logs")).logs.find((l) => l.name === spec.name);
    if (!alive() || !fresh) return;
    badgeSlot.replaceChildren(statusBadge(fresh.status));
    if (fresh.files.join("\n") !== spec.files.join("\n")) {
      spec.files = fresh.files;
      const keep = fileSelect.value;
      fileSelect.replaceChildren(...fresh.files.map((n) => h("option", { value: n }, n)));
      fileSelect.value = fresh.files.includes(keep) ? keep : fresh.files[0];
    }
    if (following && fresh.files[0] && fresh.files[0] !== file) {
      await switchTo(fresh.files[0]);
      return;
    }
    if (fresh.files.includes(file)) await read(false);
  };
  every(3000, () => poll().catch(handleBackground));
}

// ------------------------------------------------------------------ storage

async function storagePage(view, arg, alive) {
  const s = await api("/api/storage");
  if (!alive()) return;
  view.replaceChildren(h("section", { class: "panel" },
    h("h2", {}, "Storage"),
    h("table", { class: "table" },
      h("thead", {}, h("tr", {}, h("th", {}, "Part"), h("th", {}, "Path"), h("th", { class: "num" }, "Size"))),
      h("tbody", {}, s.items.map((i) => h("tr", {}, h("td", {}, i.label), h("td", { class: "mono" }, i.path),
        h("td", { class: "num" }, bytes(i.bytes))))),
      h("tfoot", {}, h("tr", {}, h("td", {}, "Total"), h("td", {}), h("td", { class: "num" }, bytes(s.total_bytes))))),
    h("p", { class: "muted small" }, "PMI home: ", h("span", { class: "mono" }, s.home))));
}

// ------------------------------------------------------------------ router

const PAGES = { overview: overviewPage, memories: memoriesPage, review: reviewPage, logs: logsPage, storage: storagePage };

function parseRoute() {
  const parts = location.hash.replace(/^#\/?/, "").split("/").map((p) => {
    try { return decodeURIComponent(p); } catch (_) { return p; }
  });
  const tab = PAGES[parts[0]] ? parts[0] : "overview";
  return { tab, arg: parts[1] || null };
}

async function render() {
  const { tab, arg } = parseRoute();
  document.querySelectorAll("#tabs a").forEach((a) => a.classList.toggle("active", a.dataset.tab === tab));
  if (tab === "memories" && currentTab === "memories" && memoriesView) {
    const seq = renderSeq;
    await showDetail(arg, () => seq === renderSeq).catch(handleBackground);
    return;
  }
  clearTimers();
  currentTab = tab;
  memoriesView = null;
  const seq = ++renderSeq;
  const alive = () => seq === renderSeq;
  const view = document.getElementById("view");
  view.replaceChildren(h("p", { class: "muted" }, "Loading…"));
  try {
    await PAGES[tab](view, arg, alive);
  } catch (err) {
    if (err instanceof Unauthorized || !alive()) return;
    view.replaceChildren(h("section", { class: "panel error" }, h("h2", {}, "Something went wrong"),
      h("p", {}, err.message)));
  }
}

function boot() {
  const match = location.hash.match(/^#token=([^&]+)/);
  if (match) {
    store(TOKEN_KEY, decodeURIComponent(match[1]));
    const back = load(RETURN_KEY);
    store(RETURN_KEY, null);
    history.replaceState(null, "", location.pathname + (back || DEFAULT_ROUTE));
  } else if (!location.hash || location.hash === "#") {
    history.replaceState(null, "", location.pathname + DEFAULT_ROUTE);
  }
  window.addEventListener("hashchange", () => render());
  render();
  setInterval(() => { if (currentTab !== "overview" && currentTab !== "review") refreshBadge(); }, 10000);
}

boot();
