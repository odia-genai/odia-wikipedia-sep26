// Excluded: the pages the build left out of the corpus (excluded.jsonl) and the blocks it cut out
// of articles (removed-blocks.jsonl). All state is in the URL: tab, reason, kind, kept, q, sort,
// dir, page (and v, the corpus version the rows are checked against).
import { h, api, dsApi, num, clear, when, errorBox } from "../util.js";
import { barList } from "../charts.js";

const TAB_PARAMS = ["reason", "kind", "kept", "q", "sort", "dir", "page", "size", "id"];
const ID_REF = /(?<![\p{L}\p{N}_])id (\d+)/gu; // as the server matches it: "same text as id 1234 (...)"

export async function render({ main, ds, params, app }) {
  let cur = new URLSearchParams(params);
  const enc = encodeURIComponent(ds);
  const info = () => ((app.home && app.home.datasets) || []).find((d) => d.name === ds) || {};
  const box = h("div");
  main.append(h("h1", null, "Excluded"), box);

  const nav = (p) => { location.hash = `/d/${enc}/excluded${[...p].length ? "?" + p : ""}`; };
  const set = (changes) => {
    const p = new URLSearchParams(cur);
    for (const [k, val] of Object.entries(changes)) { if (val === null || val === undefined || val === "") p.delete(k); else p.set(k, val); }
    if (!("page" in changes)) p.delete("page");
    nav(p);
  };
  const tabOf = () => cur.get("tab") || (info().excluded === false && info().removed_blocks ? "blocks" : "pages");
  const vq = () => (cur.get("v") ? `v=${encodeURIComponent(cur.get("v"))}&` : "");

  async function draw() {
    const tab = tabOf();
    const d = info();
    const tabBtn = (key, label, n) => h("button", { class: tab === key ? "on" : "", onclick: () => {
      const p = new URLSearchParams(cur);
      for (const k of TAB_PARAMS) p.delete(k);
      p.set("tab", key);
      nav(p);
    } }, label, n !== null && n !== undefined ? h("span", { class: "muted" }, ` ${num(n)}`) : null);
    const tabs = h("div", { class: "seg", style: { marginBottom: "12px" } },
      d.excluded !== false ? tabBtn("pages", "Excluded pages", d.excluded_rows) : null,
      d.removed_blocks ? tabBtn("blocks", "Removed blocks", d.removed_block_rows) : null);
    const body = h("div");
    clear(box).append(tabs, body);
    try {
      if (tab === "blocks") await drawBlocks(body);
      else await drawPages(body);
    } catch (e) {
      clear(body).append(errorBox(e));
    }
  }

  // ---- excluded pages --------------------------------------------------------------------------
  async function drawPages(body) {
    const r = await api(dsApi(ds, "excluded", apiParams()));
    const s = r.summary.pages;
    const reason = cur.get("reason") || "";
    body.append(h("p", { class: "muted" }, "Every page of the dump that is not in the corpus, with the reason, from ",
      h("code", null, r.file), ` (${num(s.rows)} pages, written ${when(r.mtime)}). `,
      "“Dump revision” opens the exact revision the build read; “current page” opens the page as it is on Wikipedia now."));
    const notes = notesFor(r);
    if (notes) body.append(notes);
    body.append(h("div", { class: "grid cols-2", style: { marginBottom: "12px" } },
      h("div", { class: "card" }, h("h2", null, "By reason"),
        barList(s.reasons, { total: s.rows, limit: 30, onPick: (val) => set({ reason: val === null ? "__null__" : val }) }))));

    const search = searchBox("title, detail or an id…");
    const reasonSel = h("select", { "aria-label": "Reason", onchange: (e) => set({ reason: e.target.value }) },
      h("option", { value: "" }, `all reasons (${num(s.rows)})`),
      s.reasons.map(([val, n]) => h("option", { value: val === null ? "__null__" : val, selected: (val === null ? "__null__" : val) === reason ? true : null }, `${val === null ? "(missing)" : val} (${num(n)})`)));
    body.append(controls([reasonSel, search], r.total, ["page", "pages"], r.elapsed_ms));

    const th = sortHeader(r, "reason");
    const t = h("table", { class: "data excluded" }, h("thead", null, h("tr", null,
      h("th", { class: "num" }, "#"), th("title", "title"), th("reason", "reason"), h("th", null, "detail"),
      th("id", "id", "num"), th("revid", "revid", "num"), h("th", null, "links"))));
    const tb = h("tbody");
    const start = (r.page - 1) * r.size;
    r.rows.forEach((row, i) => {
      tb.append(h("tr", null,
        h("td", { class: "num muted" }, num(start + i + 1)),
        h("td", { class: "title" }, row.title || h("span", { class: "muted" }, "(no title)"),
          row.in_corpus ? h("a", { class: "chip bad", href: `#/d/${enc}/a/${row.id}?${vq()}from=excluded`, title: "this id is also in the corpus version shown" }, "in corpus") : null),
        h("td", null, h("a", { class: "chip", href: "javascript:void 0", title: "show only this reason", onclick: (e) => { e.preventDefault(); set({ reason: row.reason ?? "__null__" }); } }, row.reason ?? "(missing)")),
        h("td", { class: "small odia detail" }, detailNode(row)),
        h("td", { class: "num" }, row.id ?? "–"),
        h("td", { class: "num muted" }, row.revid ?? "–"),
        h("td", { class: "small nowrap" },
          row.links.revision ? h("a", { href: row.links.revision, target: "_blank", rel: "noopener", title: `revision ${row.revid} (the one in the dump)` }, "dump revision ↗") : null,
          row.links.revision && row.links.current ? " · " : null,
          row.links.current ? h("a", { href: row.links.current, target: "_blank", rel: "noopener", title: `page id ${row.id}, as it is now` }, "current page ↗") : null)));
    });
    if (!r.rows.length) tb.append(h("tr", null, h("td", { colspan: 7, class: "muted" }, "No excluded pages match.")));
    t.append(tb);
    body.append(h("div", { class: "table-wrap" }, t), pager(r));
  }

  // "same text as id 1234 (title)": the id links to that article when it is in the corpus
  function detailNode(row) {
    const text = row.detail || "";
    if (!text) return h("span", { class: "muted" }, "–");
    const known = Object.fromEntries((row.detail_ids || []).map((x) => [String(x.id), x]));
    const out = h("span");
    let last = 0;
    for (const m of text.matchAll(ID_REF)) {
      const x = known[m[1]];
      out.append(text.slice(last, m.index));
      if (x && x.in_corpus) out.append(h("a", { href: `#/d/${enc}/a/${x.id}?${vq()}from=excluded`, title: `open ${x.title || "the article"} (kept)` }, m[0]));
      else out.append(h("span", { title: "not in this corpus version" }, m[0]), h("span", { class: "muted" }, " (not in the corpus)"));
      last = m.index + m[0].length;
    }
    out.append(text.slice(last));
    return out;
  }

  // ---- removed blocks ----------------------------------------------------------------------------
  async function drawBlocks(body) {
    const r = await api(dsApi(ds, "removed-blocks", apiParams()));
    const b = r.summary.blocks;
    body.append(h("p", { class: "muted" }, "Blocks the build cut out of articles, from ", h("code", null, r.file),
      ` (${num(b.rows)} blocks from ${num(b.articles)} articles, written ${when(r.mtime)}). `,
      "A block of an article that was then left out as a whole links to that page's entry."));
    const notes = notesFor(r);
    if (notes) body.append(notes);
    body.append(h("div", { class: "grid cols-2", style: { marginBottom: "12px" } },
      h("div", { class: "card" }, h("h2", null, "By reason"),
        barList(b.reasons, { total: b.rows, onPick: (val) => set({ reason: val === null ? "__null__" : val }) })),
      h("div", { class: "card" }, h("h2", null, "By kind"),
        barList(b.kinds, { total: b.rows, onPick: (val) => set({ kind: val === null ? "__null__" : val }) }))));

    const opt = (list, key, label) => h("select", { "aria-label": label, onchange: (e) => set({ [key]: e.target.value }) },
      h("option", { value: "" }, `all ${label}s`),
      list.map(([val, n]) => { const x = val === null ? "__null__" : val; return h("option", { value: x, selected: x === (cur.get(key) || "") ? true : null }, `${val === null ? "(missing)" : val} (${num(n)})`); }));
    const ctl = [opt(b.reasons, "reason", "reason"), opt(b.kinds, "kind", "kind")];
    if (r.has_kept) {
      ctl.push(h("select", { "aria-label": "Article", onchange: (e) => set({ kept: e.target.value }) },
        [["", "kept and left-out articles"], ["1", "articles in the corpus"], ["0", "articles left out"]].map(([val, label]) =>
          h("option", { value: val, selected: val === (cur.get("kept") || "") ? true : null }, label))));
    }
    if (cur.get("id")) ctl.push(h("span", { class: "chip" }, `article id ${cur.get("id")}`, h("button", { title: "remove", onclick: () => set({ id: null }) }, "✕")));
    ctl.push(searchBox("title or text contains…"));
    body.append(controls(ctl, r.total, ["block", "blocks"], r.elapsed_ms));

    const th = sortHeader(r, "id");
    const t = h("table", { class: "data blocks" }, h("thead", null, h("tr", null,
      h("th", { class: "num" }, "#"), th("title", "article"), th("kind", "kind"), th("reason", "reason"),
      h("th", null, "text"), th("chars", "chars", "num"))));
    const tb = h("tbody");
    const start = (r.page - 1) * r.size;
    r.rows.forEach((row, i) => {
      const art = row.in_corpus
        ? h("a", { href: `#/d/${enc}/a/${row.id}?${vq()}from=excluded`, title: `open the article (id ${row.id})` }, row.title || `id ${row.id}`)
        : h("span", null, row.title || `id ${row.id}`);
      const status = row.in_corpus ? null
        : row.excluded_reason ? h("a", { class: "chip warn", href: `#/d/${enc}/excluded?${vq()}tab=pages&q=${row.id}`, title: "the page was left out; see why" }, `left out: ${row.excluded_reason}`)
        : h("span", { class: "chip bad", title: "the article is not in this corpus version" }, "not in corpus");
      tb.append(h("tr", null,
        h("td", { class: "num muted" }, num(start + i + 1)),
        h("td", { class: "title" }, art, h("div", { class: "row small" }, h("span", { class: "muted" }, `id ${row.id}`), status,
          h("a", { href: "javascript:void 0", class: "muted", title: "only this article's blocks", onclick: (e) => { e.preventDefault(); set({ id: String(row.id) }); } }, "its blocks"))),
        h("td", { class: "small nowrap" }, row.kind ?? "–"),
        h("td", { class: "small nowrap" }, row.reason ?? "–"),
        h("td", null, h("div", { class: "block-text" }, row.text ?? "")),
        h("td", { class: "num" }, num(row.chars))));
    });
    if (!r.rows.length) tb.append(h("tr", null, h("td", { colspan: 6, class: "muted" }, "No removed blocks match.")));
    t.append(tb);
    body.append(h("div", { class: "table-wrap" }, t), pager(r));
  }

  // ---- shared pieces ---------------------------------------------------------------------------
  function apiParams() {
    const p = new URLSearchParams();
    for (const k of [...TAB_PARAMS, "v"]) if (cur.get(k)) p.set(k, cur.get(k));
    return p;
  }

  function notesFor(r) {
    const s = r.summary;
    const items = [...(s.problems || [])];
    if (r.summary.mismatch && r.summary.mismatch.length && tabOf() === "pages") {
      items.push(`Counts differ from the build file's “dropped”: ${r.summary.mismatch.map(([k, a, b]) => `${k}: build ${num(a)}, ${r.file} ${num(b)}`).join("; ")}`);
    }
    if (!items.length) return null;
    return h("div", { class: "card warnings", style: { marginBottom: "12px" } }, h("ul", null, items.map((x) => h("li", null, x))));
  }

  function searchBox(placeholder) {
    const input = h("input", { type: "search", placeholder, value: cur.get("q") || "", "aria-label": "Search", class: "odia", style: { minWidth: "260px" },
      onkeydown: (e) => { if (e.key === "Enter") set({ q: e.target.value.trim() }); } });
    return h("span", { class: "row" }, input, h("button", { class: "primary", onclick: () => set({ q: input.value.trim() }) }, "Search"));
  }

  function controls(items, total, [one, many], ms) {
    const active = TAB_PARAMS.some((k) => !["sort", "dir", "page", "size"].includes(k) && cur.get(k));
    return h("div", { class: "card row", style: { marginBottom: "8px" } }, ...items, h("span", { class: "spacer" }),
      active ? h("button", { class: "ghost", onclick: () => { const p = new URLSearchParams(cur); for (const k of ["reason", "kind", "kept", "q", "id", "page"]) p.delete(k); nav(p); } }, "clear filters") : null,
      h("strong", null, `${num(total)} ${total === 1 ? one : many}`), h("span", { class: "muted small" }, `${num(ms)} ms`));
  }

  function sortHeader(r, def) {
    const sort = cur.get("sort") || def;
    const dir = cur.get("dir") || "asc";
    return (key, label, cls = "") => {
      const on = sort === key;
      return h("th", { class: `sortable ${cls}`, title: `sort by ${key}`, onclick: () => set({ sort: key, dir: on && dir === "asc" ? "desc" : "asc" }) },
        label, on ? h("span", { class: "arrow" }, dir === "asc" ? " ▲" : " ▼") : null);
    };
  }

  function pager(r) {
    const pages = Math.max(1, Math.ceil(r.total / r.size));
    const go = (n) => set({ page: String(n) });
    return h("div", { class: "pager" },
      h("button", { disabled: r.page <= 1 ? true : null, onclick: () => go(1) }, "« first"),
      h("button", { disabled: r.page <= 1 ? true : null, onclick: () => go(r.page - 1) }, "‹ prev"),
      h("span", null, `page ${num(r.page)} of ${num(pages)}`),
      h("button", { disabled: r.page >= pages ? true : null, onclick: () => go(r.page + 1) }, "next ›"),
      h("button", { disabled: r.page >= pages ? true : null, onclick: () => go(pages) }, "last »"),
      h("select", { "aria-label": "Rows per page", onchange: (e) => set({ size: e.target.value }) },
        [50, 100, 200, 500].map((n) => h("option", { value: n, selected: n === r.size ? true : null }, `${n} / page`))));
  }

  await draw();
  return {
    async update(p) { cur = new URLSearchParams(p); await draw(); },
    async onData() { const y = window.scrollY; await draw(); window.scrollTo(0, y); },
  };
}
