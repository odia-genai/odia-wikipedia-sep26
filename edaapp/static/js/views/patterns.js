// Patterns: repeated (templated) paragraphs, and a regex search over paragraphs, with bulk drops.
import { h, api, dsApi, num, clear, snippet, pct, errorBox } from "../util.js";
import { bulkDialog } from "../bulk.js";

const KINDS = ["para", "list", "table", "heading", "math"];

function normNode(text) {
  // highlight the masks: ⟨title⟩ and digit runs (#)
  const el = h("span", { class: "norm" });
  const re = /(⟨title⟩|#)/g;
  let last = 0, m;
  while ((m = re.exec(text))) {
    if (m.index > last) el.append(text.slice(last, m.index));
    el.append(h("span", { class: "tok", title: m[0] === "#" ? "digits" : "the article's title" }, m[0] === "#" ? "N" : "TITLE"));
    last = m.index + m[0].length;
  }
  el.append(text.slice(last));
  return el;
}

export async function render({ main, ds, params, key }) {
  let cur = new URLSearchParams(params);
  const v = params.get("v");
  const base = `#/d/${encodeURIComponent(ds)}/patterns`;
  const box = h("div");
  main.append(box);

  async function draw() {
    clear(box);
    if (key) return drawGroup();
    const tab = cur.get("tab") || "repeated";
    const vq = v ? `v=${encodeURIComponent(v)}&` : "";
    box.append(h("h1", null, "Patterns"), h("div", { class: "seg", style: { marginBottom: "12px" } },
      h("button", { class: tab === "repeated" ? "on" : "", onclick: () => { location.hash = `${base.slice(1)}?${vq}tab=repeated`; } }, "Repeated paragraphs"),
      h("button", { class: tab === "search" ? "on" : "", onclick: () => { location.hash = `${base.slice(1)}?${vq}tab=search`; } }, "Regex search over paragraphs")));
    if (tab === "search") return drawSearch();
    return drawRepeated();
  }

  function nav(p) { location.hash = `${base.slice(1)}?${p}`; }

  function kindBoxes(selected, onChange) {
    return h("span", { class: "row" }, KINDS.map((k) => h("label", { class: "check small" }, h("input", {
      type: "checkbox", checked: selected.includes(k) ? true : null,
      onchange: (e) => onChange(e.target.checked ? [...selected, k] : selected.filter((x) => x !== k)),
    }), k)));
  }

  async function drawRepeated() {
    const kinds = (cur.get("kinds") || "para,list,table,math").split(",").filter(Boolean);
    const min = cur.get("min") || "5";
    const set = (ch) => { const p = new URLSearchParams(cur); p.set("tab", "repeated"); for (const [k, val] of Object.entries(ch)) { if (val === "" || val === null) p.delete(k); else p.set(k, val); } p.delete("page"); nav(p); };
    const minIn = h("input", { type: "number", min: 2, value: min, "aria-label": "minimum articles", onchange: (e) => set({ min: e.target.value }) });
    const qIn = h("input", { type: "search", placeholder: "normalised text contains…", value: cur.get("q") || "", style: { minWidth: "260px" },
      onkeydown: (e) => { if (e.key === "Enter") set({ q: e.target.value.trim() }); } });
    box.append(h("p", { class: "muted" }, "Paragraphs are normalised by masking digit runs (N) and the article's own title (TITLE; also the title without a trailing “(…)”), then grouped across articles. Computed once per corpus file and cached until it changes."),
      h("div", { class: "row card", style: { marginBottom: "10px" } }, h("span", null, "in at least"), minIn, h("span", null, "articles ·"),
        kindBoxes(kinds, (ks) => set({ kinds: ks.join(",") })), qIn));
    const status = h("p", { class: "muted" }, "Grouping paragraphs… (the first time takes a second or two)");
    box.append(status);
    const p = new URLSearchParams(cur);
    p.set("kinds", kinds.join(",")); p.set("min", min);
    let r;
    try { r = await api(dsApi(ds, "patterns/templates", p)); } catch (e) { status.replaceWith(errorBox(e)); return; }
    status.replaceWith(h("p", null, h("strong", null, `${num(r.total)} groups`),
      ` cover ${num(r.paragraphs_in_groups)} of ${num(r.paragraphs_total)} paragraphs of these kinds (${pct(r.paragraphs_in_groups, r.paragraphs_total)}).`,
      h("span", { class: "muted small" }, ` ${num(r.elapsed_ms)} ms`)));
    const t = h("table", { class: "data" }, h("thead", null, h("tr", null, ["articles", "paragraphs", "variants", "kind", "normalised text", ""].map((x, i) => h("th", { class: i < 3 ? "num" : "" }, x)))));
    const tb = h("tbody");
    for (const g of r.rows) {
      const vq = v ? `v=${encodeURIComponent(v)}&` : "";
      tb.append(h("tr", null, h("td", { class: "num" }, num(g.articles)), h("td", { class: "num" }, num(g.paragraphs)), h("td", { class: "num" }, num(g.variants)),
        h("td", null, g.kind), h("td", null, normNode(g.norm.length > 400 ? g.norm.slice(0, 400) + "…" : g.norm)),
        h("td", { class: "nowrap" },
          h("a", { class: "btn", href: `${base}/t/${g.nkey}?${vq}` }, "examples"), " ",
          h("a", { class: "btn", href: `#/d/${encodeURIComponent(ds)}/browse?${vq}pat=${g.nkey}` }, "articles"), " ",
          h("button", { class: "danger", onclick: () => bulkDialog(ds, v, { action: "drop_paragraphs", match: { type: "template", key: g.nkey }, note: `bulk: repeated paragraph ${g.nkey}` }, { onDone: draw }) }, "drop all…"))));
    }
    t.append(tb);
    box.append(h("div", { class: "table-wrap" }, t), pagerFor(r, (n) => { const q = new URLSearchParams(cur); q.set("page", n); nav(q); }));
  }

  async function drawGroup() {
    const p = new URLSearchParams(cur);
    const r = await api(dsApi(ds, `patterns/template/${encodeURIComponent(key)}`, p));
    const g = r.group;
    const vq = v ? `v=${encodeURIComponent(v)}&` : "";
    const note = h("input", { type: "text", value: `bulk: repeated paragraph ${g.nkey}`, style: { minWidth: "320px" }, "aria-label": "note for the bulk action" });
    box.append(h("div", { class: "row" }, h("a", { class: "btn", href: `${base}?${vq}tab=repeated` }, "← all groups"), h("h1", { style: { margin: 0 } }, "Repeated paragraph")),
      h("div", { class: "card stack", style: { margin: "10px 0" } }, normNode(g.norm),
        h("div", { class: "muted" }, `${num(g.articles)} articles · ${num(g.paragraphs)} paragraphs · ${num(g.variants)} distinct texts · kind ${g.kind} · ~${num(g.chars)} chars`),
        h("div", { class: "row" }, h("span", null, "Note:"), note,
          h("button", { class: "danger", onclick: () => bulkDialog(ds, v, { action: "drop_paragraphs", match: { type: "template", key: g.nkey }, note: note.value }, { onDone: draw }) }, `Drop these ${num(g.paragraphs)} paragraphs…`),
          h("button", { class: "danger", onclick: () => bulkDialog(ds, v, { action: "drop_articles", match: { type: "template", key: g.nkey }, note: note.value }, { onDone: draw }) }, `Drop the ${num(g.articles)} articles…`),
          h("a", { class: "btn", href: `#/d/${encodeURIComponent(ds)}/browse?${vq}pat=${g.nkey}` }, "Browse these articles"))));
    if (r.top_variants.length > 1) {
      box.append(h("h2", null, "Most common exact texts"), h("ul", { class: "odia" }, r.top_variants.map((x) => h("li", null, `${num(x.n)}× `, x.text.slice(0, 300)))));
    }
    const ol = h("div", { class: "stack" });
    for (const ex of r.examples) {
      ol.append(h("div", { class: "card" }, h("div", { class: "row" },
        h("a", { class: "odia", href: `#/d/${encodeURIComponent(ds)}/a/${ex.id}?${vq}para=${ex.para}` }, ex.title), h("span", { class: "muted small" }, `¶${ex.para}`)),
        h("div", { class: "odia", style: { lineHeight: "1.7" } }, ex.text.length > 600 ? ex.text.slice(0, 600) + "…" : ex.text)));
    }
    box.append(h("h2", null, `Examples (${num((r.page - 1) * r.size + 1)}–${num((r.page - 1) * r.size + r.examples.length)} of ${num(g.paragraphs)})`), ol,
      pagerFor({ page: r.page, size: r.size, total: g.paragraphs }, (n) => { const q = new URLSearchParams(cur); q.set("page", n); location.hash = `${base.slice(1)}/t/${key}?${q}`; }));
  }

  async function drawSearch() {
    const kinds = (cur.get("kinds") || KINDS.join(",")).split(",").filter(Boolean);
    const re = cur.get("re") || "";
    const set = (ch) => { const p = new URLSearchParams(cur); p.set("tab", "search"); for (const [k, val] of Object.entries(ch)) { if (val === "" || val === null) p.delete(k); else p.set(k, val); } p.delete("page"); nav(p); };
    const input = h("input", { type: "search", value: re, placeholder: "RE2 regular expression, e.g. ପରୁଷ or ^- ଇଣ୍ଟରନେଟ ମୁଭି", style: { minWidth: "380px", fontFamily: "var(--odia)" },
      onkeydown: (e) => { if (e.key === "Enter") set({ re: e.target.value }); } });
    const icase = h("input", { type: "checkbox", checked: cur.get("icase") === "1" ? true : null, onchange: (e) => set({ icase: e.target.checked ? "1" : "" }) });
    box.append(h("div", { class: "row card", style: { marginBottom: "10px" } }, input, h("button", { class: "primary", onclick: () => set({ re: input.value }) }, "Search"),
      h("label", { class: "check small" }, icase, "ignore case"), kindBoxes(kinds, (ks) => set({ kinds: ks.join(",") }))));
    if (!re) { box.append(h("p", { class: "muted" }, "Search every paragraph (RE2 syntax; the same engine as the server's filter). Results show hit counts and context; you can then mark the matching paragraphs or articles as dropped.")); return; }
    const p = new URLSearchParams(cur);
    p.set("kinds", kinds.join(","));
    let r;
    try { r = await api(dsApi(ds, "patterns/search", p)); } catch (e) { box.append(errorBox(e)); return; }
    const note = h("input", { type: "text", value: `bulk: regex /${re}/`, style: { minWidth: "260px" }, "aria-label": "note for the bulk action" });
    const spec = (action) => ({ action, match: { type: "regex", pattern: re, icase: cur.get("icase") === "1", kinds }, note: note.value });
    box.append(h("div", { class: "card stack", style: { marginBottom: "10px" } },
      h("div", { class: "row" }, h("strong", null, `${num(r.matches)} matches in ${num(r.paragraphs)} paragraphs of ${num(r.articles)} articles`),
        h("span", { class: "muted small" }, r.by_kind.map(([k, n]) => `${k} ${num(n)}`).join(" · ")), h("span", { class: "muted small" }, `${num(r.elapsed_ms)} ms`)),
      r.paragraphs ? h("div", { class: "row" }, h("span", null, "Note:"), note,
        h("button", { class: "danger", onclick: () => bulkDialog(ds, v, spec("drop_paragraphs"), { onDone: draw }) }, `Drop the ${num(r.paragraphs)} matching paragraphs…`),
        h("button", { class: "danger", onclick: () => bulkDialog(ds, v, spec("drop_articles"), { onDone: draw }) }, `Drop the ${num(r.articles)} articles…`),
        h("a", { class: "btn", href: `#/d/${encodeURIComponent(ds)}/browse?${new URLSearchParams({ ...(v ? { v } : {}), text: re, mode: "regex", ...(cur.get("icase") === "1" ? { icase: "1" } : {}) })}` }, "Open in Browse")) : null));
    const t = h("table", { class: "data" }, h("thead", null, h("tr", null, h("th", null, "article"), h("th", { class: "num" }, "¶"), h("th", null, "kind"), h("th", { class: "num" }, "hits"), h("th", null, "context"))));
    const tb = h("tbody");
    const vq = v ? `v=${encodeURIComponent(v)}&` : "";
    for (const row of r.rows) {
      tb.append(h("tr", null, h("td", { class: "title" }, h("a", { href: `#/d/${encodeURIComponent(ds)}/a/${row.id}?${vq}para=${row.para}` }, row.title)),
        h("td", { class: "num" }, row.para), h("td", null, row.kind), h("td", { class: "num" }, num(row.matches)),
        h("td", { class: "snips" }, row.snippets.map(snippet))));
    }
    t.append(tb);
    box.append(h("div", { class: "table-wrap" }, t), pagerFor({ page: r.page, size: r.size, total: r.paragraphs }, (n) => { const q = new URLSearchParams(cur); q.set("page", n); nav(q); }));
  }

  await draw();
  return { async update(p) { cur = new URLSearchParams(p); await draw(); } };
}

function pagerFor(r, go) {
  const pages = Math.max(1, Math.ceil(r.total / r.size));
  if (pages <= 1) return h("div");
  return h("div", { class: "pager" },
    h("button", { disabled: r.page <= 1 ? true : null, onclick: () => go(r.page - 1) }, "‹ prev"),
    h("span", null, `page ${num(r.page)} of ${num(pages)}`),
    h("button", { disabled: r.page >= pages ? true : null, onclick: () => go(r.page + 1) }, "next ›"));
}
