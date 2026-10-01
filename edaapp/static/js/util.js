// Small helpers shared by every view. All text goes into the DOM through textContent;
// the only HTML inserted as HTML is what the server rendered from Markdown with raw HTML disabled.

export function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  if (attrs) {
    for (const [k, v] of Object.entries(attrs)) {
      if (v === undefined || v === null || v === false) continue;
      if (k === "class") el.className = v;
      else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
      else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
      else if (k === "html") el.innerHTML = v; // server-rendered Markdown only
      else if (k === "dataset") Object.assign(el.dataset, v);
      else if (v === true) el.setAttribute(k, "");
      else el.setAttribute(k, v);
    }
  }
  append(el, children);
  return el;
}

export function append(el, children) {
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

export function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); return el; }

// ---- formatting ---------------------------------------------------------------------------
const nf = new Intl.NumberFormat("en-US");
export function num(x, digits) {
  if (x === null || x === undefined || x === "") return "–";
  if (typeof x !== "number") return String(x);
  if (digits !== undefined) return x.toLocaleString("en-US", { maximumFractionDigits: digits, minimumFractionDigits: 0 });
  if (Number.isInteger(x)) return nf.format(x);
  const a = Math.abs(x);
  return x.toLocaleString("en-US", { maximumFractionDigits: a >= 100 ? 1 : a >= 1 ? 3 : 4 });
}
export function compact(x) {
  if (x === null || x === undefined) return "–";
  const a = Math.abs(x);
  if (a >= 1e9) return (x / 1e9).toFixed(1).replace(/\.0$/, "") + "B";
  if (a >= 1e6) return (x / 1e6).toFixed(a >= 1e7 ? 0 : 1).replace(/\.0$/, "") + "M";
  if (a >= 1e4) return (x / 1e3).toFixed(0) + "K";
  return num(x);
}
export function bytes(n) {
  if (n === null || n === undefined) return "–";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return (i ? n.toFixed(n >= 100 ? 0 : 1) : n) + " " + u[i];
}
export function pct(a, b) { return b ? (100 * a / b).toFixed(a / b < 0.1 ? 1 : 0) + "%" : "–"; }
export function when(tsSeconds) {
  if (!tsSeconds) return "–";
  const d = new Date(tsSeconds * 1000);
  return d.toLocaleString(undefined, { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}
export function ago(iso) {
  if (!iso) return "";
  const t = new Date(iso).getTime();
  const s = Math.round((Date.now() - t) / 1000);
  if (s < 60) return `${Math.max(s, 0)}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return new Date(iso).toLocaleDateString();
}
export function value(v) {
  if (v === null || v === undefined) return h("span", { class: "muted" }, "–");
  if (Array.isArray(v)) return v.length ? v.join(", ") : h("span", { class: "muted" }, "[]");
  if (typeof v === "boolean") return v ? "true" : "false";
  if (typeof v === "number") return num(v);
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

// ---- URL state ------------------------------------------------------------------------------
export function parseHash() {
  const raw = location.hash.replace(/^#/, "") || "/";
  const qi = raw.indexOf("?");
  const path = qi >= 0 ? raw.slice(0, qi) : raw;
  const params = new URLSearchParams(qi >= 0 ? raw.slice(qi + 1) : "");
  const parts = path.split("/").filter(Boolean).map(decodeURIComponent);
  return { path, parts, params };
}
export function hashFor(parts, params) {
  const p = "#/" + parts.map(encodeURIComponent).join("/");
  const q = params && [...params].length ? "?" + params.toString() : "";
  return p + q;
}
export function go(parts, params, replace = false) {
  const url = hashFor(parts, params);
  if (replace) history.replaceState(null, "", url);
  else location.hash = url.slice(1);
  if (replace) window.dispatchEvent(new HashChangeEvent("hashchange"));
}
export function withParams(params, changes) {
  const p = new URLSearchParams(params);
  for (const [k, v] of Object.entries(changes)) {
    p.delete(k);
    if (v === null || v === undefined || v === "") continue;
    if (Array.isArray(v)) v.forEach((x) => p.append(k, x));
    else p.set(k, v);
  }
  return p;
}

// ---- API --------------------------------------------------------------------------------------
export class ApiError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}
export async function api(path, opts = {}) {
  const init = { headers: {} };
  if (opts.body !== undefined) {
    init.method = opts.method || "POST";
    init.headers["content-type"] = "application/json";
    init.body = JSON.stringify(opts.body);
  }
  const res = await fetch(path, init);
  let data = null;
  const text = await res.text();
  try { data = text ? JSON.parse(text) : null; } catch { data = { error: text.slice(0, 300) }; }
  if (!res.ok) throw new ApiError(res.status, (data && data.error) || res.statusText);
  return data;
}
export function dsApi(ds, rest, params) {
  const q = params && [...params].length ? "?" + params.toString() : "";
  return `/api/d/${encodeURIComponent(ds)}/${rest}${q}`;
}

// ---- toast, tooltip, dialog ------------------------------------------------------------------
let toastTimer;
export function toast(msg, kind = "") {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.className = "toast show " + kind;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.className = "toast " + kind; }, kind === "error" ? 6000 : 2600);
}
export function errorBox(e) {
  return h("div", { class: "card warnings" }, h("strong", null, "Error: "), String(e.message || e));
}

const tip = () => document.getElementById("tooltip");
export function showTip(ev, content) {
  const el = tip();
  clear(el);
  append(el, [content]);
  el.hidden = false;
  moveTip(ev);
}
export function moveTip(ev) {
  const el = tip();
  const pad = 14;
  let x = ev.clientX + pad, y = ev.clientY + pad;
  const r = el.getBoundingClientRect();
  if (x + r.width > innerWidth - 8) x = ev.clientX - r.width - pad;
  if (y + r.height > innerHeight - 8) y = ev.clientY - r.height - pad;
  el.style.left = x + "px";
  el.style.top = y + "px";
}
export function hideTip() { tip().hidden = true; }
export function tipOn(el, fn) {
  el.addEventListener("pointerenter", (e) => showTip(e, fn()));
  el.addEventListener("pointermove", moveTip);
  el.addEventListener("pointerleave", hideTip);
  el.addEventListener("focus", () => {
    const r = el.getBoundingClientRect();
    showTip({ clientX: r.right, clientY: r.bottom }, fn());
  });
  el.addEventListener("blur", hideTip);
  return el;
}

export function openDialog(title, body, buttons) {
  const d = document.getElementById("dialog");
  clear(d);
  const foot = h("div", { class: "dlg-foot" });
  d.append(h("div", { class: "dlg-head" }, h("strong", null, title), h("span", { class: "spacer" }),
    h("button", { class: "ghost", onclick: () => d.close(), "aria-label": "Close" }, "✕")),
    h("div", { class: "dlg-body" }, body), foot);
  for (const b of buttons || []) foot.append(b);
  if (!d.open) d.showModal();
  return d;
}
export function closeDialog() { const d = document.getElementById("dialog"); if (d.open) d.close(); }

// Mark matches of a KWIC snippet: {pre, match, post}
export function snippet(s) {
  return h("span", { class: "snip" }, s.pre, h("mark", null, s.match), s.post);
}

export function verdictBadge(v) {
  if (!v) return h("span", { class: "muted" }, "–");
  return h("span", { class: `verdict v-${v}` }, v);
}

export function isTyping(e) {
  const t = e.target;
  return !!(t && t.closest && t.closest("input, textarea, select, [contenteditable=''], [contenteditable=true]"));
}

// Typeset math elements (rendered by the server as .math.inline / .math.block holding TeX).
export function typeset(root) {
  const els = root.querySelectorAll(".math.inline, .math.block, .math.display");
  for (const el of els) {
    if (el.dataset.done) continue;
    const tex = el.textContent.trim();
    el.dataset.tex = tex;
    el.dataset.done = "1";
    if (window.katex) {
      try {
        window.katex.render(tex, el, { displayMode: !el.classList.contains("inline"), throwOnError: false, output: "html" });
        continue;
      } catch { /* fall through to raw TeX */ }
    }
    el.classList.add("raw-tex");
    el.textContent = tex;
  }
}
