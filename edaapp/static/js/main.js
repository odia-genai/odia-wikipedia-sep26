// Router, top bar, change polling and the global keyboard handler.
import { h, clear, api, parseHash, hashFor, toast, isTyping, openDialog, closeDialog, errorBox } from "./util.js";
import * as home from "./views/home.js";
import * as overview from "./views/overview.js";
import * as browse from "./views/browse.js";
import * as article from "./views/article.js";
import * as queue from "./views/queue.js";
import * as patterns from "./views/patterns.js";
import * as decisions from "./views/decisions.js";
import * as reports from "./views/reports.js";
import * as files from "./views/files.js";
import * as methodology from "./views/methodology.js";
import * as reviewfirst from "./views/reviewfirst.js";
import * as excluded from "./views/excluded.js";

const main = document.getElementById("main");
const state = { home: null, view: null, kind: null, ds: null, lastChanges: null };
export const app = state;

// [route kind, label, shown only when the dataset has it]
const TABS = [
  ["overview", "Overview"], ["methodology", "Methodology", (d) => d.methodology], ["browse", "Browse"],
  ["review-first", "Review first", (d) => d.review_first], ["queue", "Review queue"], ["patterns", "Patterns"],
  ["decisions", "Decisions"], ["excluded", "Excluded", (d) => d.excluded || d.removed_blocks],
  ["reports", "Reports"], ["files", "Files"],
];

function route() {
  const { parts, params } = parseHash();
  if (parts[0] !== "d" || !parts[1]) return { kind: "home", params, parts };
  const ds = parts[1];
  const rest = parts.slice(2);
  if (!rest.length) return { kind: "overview", ds, params, parts };
  if (rest[0] === "a" && rest[1]) return { kind: "article", ds, id: Number(rest[1]), params, parts };
  if (rest[0] === "patterns" && rest[1] === "t" && rest[2]) return { kind: "patterns", ds, key: rest[2], params, parts };
  return { kind: rest[0], ds, params, parts };
}

const VIEWS = { home, overview, browse, article, queue, patterns, decisions, reports, files, methodology, "review-first": reviewfirst, excluded };

async function loadHome(force) {
  if (!state.home || force) state.home = await api("/api/datasets");
  return state.home;
}

function renderTopbar(r) {
  const picker = document.getElementById("ds-picker");
  const nav = document.getElementById("nav");
  clear(picker); clear(nav);
  const dsets = (state.home && state.home.datasets) || [];
  if (!r.ds) return;
  const v = r.params.get("v");
  const cur = dsets.find((d) => d.name === r.ds);
  if (dsets.length > 1) {
    const sel = h("select", { "aria-label": "Dataset", onchange: (e) => { location.hash = `/d/${encodeURIComponent(e.target.value)}`; } },
      dsets.map((d) => h("option", { value: d.name, selected: d.name === r.ds }, d.name)));
    picker.append(sel);
  } else {
    picker.append(h("a", { href: `#/d/${encodeURIComponent(r.ds)}`, class: "chip" }, r.ds));
  }
  if (cur && cur.versions.length > 1) {
    const sel = h("select", {
      "aria-label": "Version (newest first)", title: "Version (newest first)",
      onchange: (e) => {
        const p = new URLSearchParams(r.params);
        if (e.target.selectedIndex === 0) p.delete("v"); else p.set("v", e.target.value);
        location.hash = hashFor(r.parts, p).slice(1);
      },
    }, cur.versions.map((ver, i) => h("option", { value: ver.stem, selected: v ? ver.stem === v : i === 0 },
      ver.stem + (i === 0 ? " (newest)" : ""))));
    picker.append(" ", sel);
  }
  const vq = v ? `?v=${encodeURIComponent(v)}` : "";
  const from = r.kind === "article" ? r.params.get("from") || "browse" : null;
  for (const [kind, label, when] of TABS) {
    if (when && !(cur && when(cur))) continue;
    const href = kind === "overview" ? `#/d/${encodeURIComponent(r.ds)}${vq}` : `#/d/${encodeURIComponent(r.ds)}/${kind}${vq}`;
    const active = r.kind === kind || (from && (kind === from || (kind === "browse" && !TABS.some((t) => t[0] === from))));
    nav.append(h("a", { href, class: active ? "active" : "" }, label));
  }
}

let rendering = 0;
async function render() {
  const r = route();
  const token = ++rendering;
  try {
    await loadHome(false);
  } catch (e) {
    clear(main).append(errorBox(e));
    return;
  }
  if (token !== rendering) return;
  renderTopbar(r);
  closeDialog(); // a dialog left open would swallow the keyboard shortcuts of the next view
  document.getElementById("banner").hidden = true;
  const sameView = state.view && state.kind === r.kind && state.ds === r.ds && state.key === r.key && state.id === r.id;
  if (sameView && state.view.update) {
    try { await state.view.update(r.params); } catch (e) { toast(String(e.message || e), "error"); }
    return;
  }
  if (state.view && state.view.destroy) state.view.destroy();
  state.view = null;
  state.kind = r.kind; state.ds = r.ds; state.key = r.key; state.id = r.id;
  const mod = VIEWS[r.kind];
  clear(main);
  if (!mod) { main.append(errorBox(new Error(`unknown page ${r.kind}`))); return; }
  document.title = r.ds ? `${r.kind === "overview" ? "" : r.kind + " · "}${r.ds} · edaapp` : "edaapp";
  try {
    const view = await mod.render({ main, ...r, app: state, reloadHome: () => loadHome(true) });
    if (token === rendering) state.view = view || {};
  } catch (e) {
    if (token === rendering) { clear(main).append(errorBox(e)); state.view = {}; }
  }
}

window.addEventListener("hashchange", render);

// ---- watch the data folder: the server notices changed files; we re-render when it does ------
async function poll() {
  try {
    const c = await api("/api/changes");
    const status = document.getElementById("status");
    status.textContent = "";
    if (state.lastChanges && c.data !== state.lastChanges.data) {
      await loadHome(true);
      renderTopbar(route()); // nav items come and go with files (Methodology, Review first)
      const typing = document.activeElement && isTyping({ target: document.activeElement });
      const tables = c.tables !== state.lastChanges.tables; // not just a Markdown document
      if (state.view && state.view.onData) {
        await state.view.onData(); // the view refreshes itself and keeps the reader's place
      } else if (!tables) {
        // only documents changed (e.g. METHODOLOGY.md): the nav above is enough for other views
      } else if (state.kind === "article" || typing) {
        const b = document.getElementById("banner");
        clear(b).append("Files in the data folder changed. ",
          h("button", { class: "small", onclick: () => { state.view = null; render(); } }, "Reload this view"));
        b.hidden = false;
      } else {
        state.view = null;
        toast("Data folder changed: view refreshed");
        render();
      }
    } else if (state.lastChanges && c.reviews !== state.lastChanges.reviews && state.view && state.view.onReviews) {
      state.view.onReviews();
    }
    state.lastChanges = c;
  } catch {
    document.getElementById("status").textContent = "server not reachable";
  }
}
setInterval(poll, 4000);
poll();

// ---- keyboard ---------------------------------------------------------------------------------
const HELP = [
  ["Article view", ""],
  ["j / k", "next / previous article in the current list"],
  ["1 or Shift+K", "verdict: keep"],
  ["2 or d", "verdict: drop"],
  ["3 or f", "verdict: fix"],
  ["0", "clear the verdict"],
  ["u", "undo the last change to this article"],
  ["n", "write a note (Ctrl/⌘+Enter or leaving the box saves it; Esc leaves)"],
  ["r", "raw Markdown on / off"],
  ["b", "back to the list"],
  ["Anywhere", ""],
  ["/", "focus the search box"],
  ["?", "this help"],
];
function help() {
  const t = h("table", { class: "shortcuts" });
  for (const [k, d] of HELP) {
    t.append(d ? h("tr", null, h("td", null, ...k.split(" ").map((x) => (["/", "or", "+"].includes(x) ? ` ${x} ` : h("kbd", null, x)))), h("td", null, d))
      : h("tr", null, h("td", { colspan: 2 }, h("strong", null, k))));
  }
  openDialog("Keyboard shortcuts", h("div", null, t,
    h("p", { class: "muted small" }, "k is navigation (vim style), so keep is 1 or Shift+K. Shortcuts never fire while you are typing in a box; press Esc to leave it.")));
}
document.getElementById("help-btn").addEventListener("click", help);
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && isTyping(e)) { e.target.blur(); return; }
  if (isTyping(e) || e.ctrlKey || e.metaKey || e.altKey) return;
  if (document.getElementById("dialog").open) return;
  if (state.view && state.view.keys && state.view.keys(e)) { e.preventDefault(); return; }
  if (e.key === "?") { e.preventDefault(); help(); }
  else if (e.key === "/") {
    const box = main.querySelector("input[type=search]");
    if (box) { e.preventDefault(); box.focus(); box.select(); }
  }
});

render();
