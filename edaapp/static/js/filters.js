// The schema-driven filter panel and the chips that list active filters.
// Every control writes URL parameters (is./min./max./in./any./has./null.); the server validates them.
import { h, num } from "./util.js";

const OPS = ["is", "min", "max", "in", "any", "has", "null"];
export const NULL = "__null__";

export function isFilterParam(k) {
  const i = k.indexOf(".");
  return i > 0 && OPS.includes(k.slice(0, i));
}

function groupOf(c) {
  if (c.source === "corpus" || c.source === "derived") return "Corpus";
  if (c.source === "review") return "Review";
  return c.source;
}

// schema: {columns}; params: URLSearchParams; onChange(newParams)
export function filterPanel(schema, params, onChange) {
  const panel = h("aside", { class: "filters", "aria-label": "Filters" });
  const groups = new Map();
  for (const c of schema.columns) {
    if (!c.filterable || c.key === "id" || c.key === "title") continue;
    const g = groupOf(c);
    if (!groups.has(g)) groups.set(g, []);
    groups.get(g).push(c);
  }
  const set = (changes) => {
    const p = new URLSearchParams(params);
    for (const [k, v] of Object.entries(changes)) {
      p.delete(k);
      if (Array.isArray(v)) v.forEach((x) => p.append(k, x));
      else if (v !== null && v !== undefined && v !== "") p.set(k, v);
    }
    p.delete("page");
    onChange(p);
  };
  for (const [g, cols] of groups) {
    const active = cols.some((c) => OPS.some((op) => params.has(`${op}.${c.key}`)));
    const det = h("details", { open: g === "Corpus" || g === "Review" || active || groups.size <= 3 ? true : null, "data-group": g },
      h("summary", null, g === "Corpus" || g === "Review" ? g : `annotation: ${g}`));
    for (const c of cols) det.append(control(c, params, set));
    panel.append(det);
  }
  if (!groups.size) panel.append(h("p", { class: "muted" }, "No filterable columns."));
  return panel;
}

function label(c) {
  const short = c.key.includes(".") ? c.key.slice(c.key.indexOf(".") + 1) : c.key;
  return h("div", { class: "fname", title: `${c.key} (${c.dtype})${c.description ? "\n" + c.description : ""}` },
    h("span", null, short), c.nulls ? h("span", { class: "desc" }, `${num(c.nulls)} missing`) : null);
}

function control(c, params, set) {
  const wrap = h("div", { class: "f", "data-key": c.key });
  wrap.append(label(c));
  if (c.kind === "bool") {
    const cur = params.get(`is.${c.key}`) || "";
    const counts = Object.fromEntries((c.options || []).map(([v, n]) => [String(v), n]));
    const seg = h("div", { class: "seg tri" });
    for (const [val, text] of [["", "any"], ["true", "true"], ["false", "false"]]) {
      const n = val ? counts[val] : null;
      seg.append(h("button", { class: cur === val ? "on small" : "small", "data-role": `is-${val}`,
        onclick: () => set({ [`is.${c.key}`]: val }) }, text, n !== undefined && n !== null ? h("span", { class: "muted" }, ` ${num(n)}`) : null));
    }
    wrap.append(seg);
  } else if (c.kind === "numeric" || c.kind === "date") {
    const type = c.kind === "numeric" ? "number" : "text";
    const mk = (op, ph) => h("input", {
      type, step: "any", placeholder: ph === null || ph === undefined ? op : String(ph), value: params.get(`${op}.${c.key}`) || "",
      "aria-label": `${c.key} ${op}`, "data-role": op,
      onchange: (e) => set({ [`${op}.${c.key}`]: e.target.value.trim() }),
    });
    wrap.append(h("div", { class: "range" }, mk("min", c.min), h("span", { class: "muted" }, "–"), mk("max", c.max)));
  } else if (c.kind === "category") {
    const sel = new Set(params.getAll(`in.${c.key}`));
    const box = h("div", { class: "opts" });
    for (const [v, n] of c.options || []) {
      const val = v === null ? NULL : String(v);
      box.append(h("label", null, h("input", {
        type: "checkbox", checked: sel.has(val) ? true : null, "data-role": `in-${val}`,
        onchange: (e) => {
          const s = new Set(params.getAll(`in.${c.key}`));
          if (e.target.checked) s.add(val); else s.delete(val);
          set({ [`in.${c.key}`]: [...s] });
        },
      }), h("span", { class: "odia" }, val === NULL ? "(none)" : val), h("span", { class: "n" }, num(n))));
    }
    wrap.append(box);
  } else if (c.kind === "list") {
    const sel = params.getAll(`any.${c.key}`);
    const listId = `dl-${c.key.replace(/[^a-zA-Z0-9_-]/g, "_")}`;
    const chips = h("div", { class: "row", style: { margin: "3px 0" } }, sel.map((v) => h("span", { class: "chip odia" }, v,
      h("button", { title: "remove", onclick: () => set({ [`any.${c.key}`]: sel.filter((x) => x !== v) }) }, "×"))));
    const input = h("input", { type: "text", list: listId, placeholder: "any of… (type, Enter)", style: { width: "100%" }, "data-role": "any",
      onkeydown: (e) => { if (e.key === "Enter" && e.target.value.trim()) set({ [`any.${c.key}`]: [...sel, e.target.value.trim()] }); } });
    const dl = h("datalist", { id: listId }, (c.options || []).map(([v, n]) => h("option", { value: v }, `${v} (${n})`)));
    const top = h("div", { class: "opts" });
    for (const [v, n] of (c.options || []).slice(0, 60)) {
      top.append(h("label", null, h("input", {
        type: "checkbox", checked: sel.includes(v) ? true : null,
        onchange: (e) => set({ [`any.${c.key}`]: e.target.checked ? [...sel, v] : sel.filter((x) => x !== v) }),
      }), h("span", { class: "odia" }, v), h("span", { class: "n" }, num(n))));
    }
    wrap.append(chips, input, dl, top);
  } else {
    wrap.append(h("input", { type: "text", placeholder: "contains…", value: params.get(`has.${c.key}`) || "", style: { width: "100%" },
      "data-role": "has", onchange: (e) => set({ [`has.${c.key}`]: e.target.value.trim() }) }));
  }
  return wrap;
}

const OP_TEXT = { is: "=", min: "≥", max: "≤", in: "∈", any: "has any", has: "contains", null: "missing" };

// chips for active filters and searches; onChange(newParams)
export function activeChips(params, onChange, extraKeys = {}) {
  const box = h("div", { class: "active-filters" });
  const remove = (k, v) => {
    const p = new URLSearchParams(params);
    const all = p.getAll(k).filter((x) => v === undefined || x !== v);
    p.delete(k);
    if (v !== undefined) all.forEach((x) => p.append(k, x));
    p.delete("page");
    onChange(p);
  };
  for (const [k, v] of params) {
    let text = null;
    if (isFilterParam(k)) {
      const op = k.slice(0, k.indexOf("."));
      const col = k.slice(k.indexOf(".") + 1);
      if (op === "null") text = `${col} ${v === "1" ? "is missing" : "is present"}`;
      else text = `${col} ${OP_TEXT[op]} ${v === NULL ? "(none)" : v}`;
    } else if (k === "q") text = `title contains “${v}”`;
    else if (k === "text") text = `text ${params.get("mode") === "regex" ? "matches /" + v + "/" : "contains “" + v + "”"}${params.get("icase") === "1" ? " (ignore case)" : ""}`;
    else if (k === "pat") text = `has repeated paragraph ${v}`;
    else if (k === "unrev") text = "unreviewed first";
    else if (extraKeys[k]) text = extraKeys[k](v);
    if (text === null) continue;
    box.append(h("span", { class: "chip odia" }, text, h("button", { title: "remove", onclick: () => remove(k, isFilterParam(k) && ["in", "any"].includes(k.split(".")[0]) ? v : undefined) }, "×")));
  }
  if (box.childNodes.length > 1) {
    const p = new URLSearchParams();
    for (const k of ["v", "cols", "size", "sort", "dir"]) if (params.has(k)) p.set(k, params.get(k));
    box.append(h("button", { class: "small ghost", onclick: () => onChange(p) }, "clear all"));
  }
  return box;
}

// Re-focus the control that had focus before a panel was rebuilt.
export function keepFocus(oldPanel, newPanel) {
  const a = document.activeElement;
  if (!oldPanel || !a || !oldPanel.contains(a)) return;
  const f = a.closest(".f");
  if (!f) return;
  const sel = `.f[data-key="${CSS.escape(f.dataset.key)}"] [data-role="${CSS.escape(a.dataset.role || "")}"]`;
  const target = newPanel.querySelector(sel);
  if (target) target.focus();
}

export function copyOpenState(oldPanel, newPanel) {
  if (!oldPanel) return;
  const open = new Map([...oldPanel.querySelectorAll("details")].map((d) => [d.dataset.group, d.open]));
  for (const d of newPanel.querySelectorAll("details")) if (open.has(d.dataset.group)) d.open = open.get(d.dataset.group);
  newPanel.scrollTop = oldPanel.scrollTop;
}

