// Browse: server-side filtered, sorted, paginated table. All state is in the URL.
import { h, append, api, dsApi, num, clear, value, snippet, verdictBadge, errorBox } from "../util.js";
import { filterPanel, activeChips, keepFocus, copyOpenState } from "../filters.js";

export async function render({ main, ds, params }) {
  let schema = await api(dsApi(ds, "schema", new URLSearchParams(params.get("v") ? { v: params.get("v") } : {})));
  let cur = new URLSearchParams(params);
  const layout = h("div", { class: "browse" });
  let panel = null;
  const right = h("section", { "aria-label": "Results" });
  layout.append(h("div"), right);
  main.append(h("h1", null, "Browse"), layout);

  const navigate = (p) => { location.hash = `/d/${encodeURIComponent(ds)}/browse${[...p].length ? "?" + p : ""}`; };

  // search bar
  const q = h("input", { type: "search", placeholder: "title contains…", "aria-label": "Title search", style: { maxWidth: "260px" } });
  const text = h("input", { type: "search", placeholder: "search the text (substring or RE2 regex)…", "aria-label": "Full-text search" });
  const mode = h("select", { "aria-label": "Search mode" }, h("option", { value: "substr" }, "substring"), h("option", { value: "regex" }, "regex"));
  const icase = h("input", { type: "checkbox" });
  const submit = () => {
    const p = new URLSearchParams(cur);
    for (const [k, el] of [["q", q], ["text", text]]) { if (el.value.trim()) p.set(k, el.value.trim()); else p.delete(k); }
    if (text.value.trim()) { p.set("mode", mode.value); if (icase.checked) p.set("icase", "1"); else p.delete("icase"); }
    else { p.delete("mode"); p.delete("icase"); }
    if (!text.value.trim() && p.get("sort") === "_matches") p.delete("sort");
    p.delete("page");
    navigate(p);
  };
  for (const el of [q, text]) el.addEventListener("keydown", (e) => { if (e.key === "Enter") submit(); });
  const bar = h("div", { class: "searchbar" }, q, text, mode, h("label", { class: "check small" }, icase, "ignore case"),
    h("button", { class: "primary", onclick: submit }, "Search"));
  const chips = h("div");
  const toolbar = h("div", { class: "row", style: { margin: "6px 0" } });
  const tableBox = h("div");
  const pager = h("div", { class: "pager" });
  right.append(bar, chips, toolbar, tableBox, pager);
  let picker = null; // the column menu closes on any click outside it
  const outside = (e) => { if (picker && !picker.contains(e.target)) picker.querySelector(".menu").hidden = true; };
  document.addEventListener("click", outside);

  function syncControls() {
    q.value = cur.get("q") || "";
    text.value = cur.get("text") || "";
    mode.value = cur.get("mode") || "substr";
    icase.checked = cur.get("icase") === "1";
    const np = filterPanel(schema, cur, navigate);
    copyOpenState(panel, np);
    const old = panel;
    layout.replaceChild(np, layout.firstChild);
    keepFocus(old, np);
    panel = np;
    clear(chips).append(activeChips(cur, navigate));
  }

  async function load() {
    tableBox.style.opacity = "0.55";
    let r;
    try {
      r = await api(dsApi(ds, "browse", cur));
    } catch (e) {
      clear(tableBox).append(errorBox(e));
      tableBox.style.opacity = "";
      clear(pager);
      return;
    }
    tableBox.style.opacity = "";
    drawToolbar(r);
    drawTable(r);
    drawPager(r);
  }

  function drawToolbar(r) {
    clear(toolbar);
    toolbar.append(h("strong", null, `${num(r.total)} articles`));
    if (r.total_matches !== null && r.total_matches !== undefined) toolbar.append(h("span", { class: "chip" }, `${num(r.total_matches)} matches`));
    toolbar.append(h("span", { class: "muted small" }, `${num(r.elapsed_ms)} ms`), h("span", { class: "spacer" }));
    // column picker
    const shown = new Set(r.columns);
    const menu = h("div", { class: "menu", hidden: true });
    const groups = {};
    for (const c of schema.columns) {
      if (c.key === "text" || c.key === "id" || c.key === "title") continue;
      (groups[c.source] = groups[c.source] || []).push(c);
    }
    for (const [g, cols] of Object.entries(groups)) {
      menu.append(h("div", { class: "muted small", style: { marginTop: "4px" } }, g));
      for (const c of cols) {
        menu.append(h("label", null, h("input", {
          type: "checkbox", checked: shown.has(c.key) ? true : null,
          onchange: (e) => {
            const cols = r.columns.filter((k) => k !== "id" && k !== "title" && k !== "review.verdict");
            const next = e.target.checked ? [...cols, c.key] : cols.filter((k) => k !== c.key);
            const p = new URLSearchParams(cur);
            p.set("cols", next.join(",") || "words");
            navigate(p);
          },
        }), c.key, h("span", { class: "muted" }, ` ${c.kind}`)));
      }
    }
    menu.append(h("div", { class: "row", style: { marginTop: "6px" } }, h("button", { class: "small", onclick: () => { const p = new URLSearchParams(cur); p.delete("cols"); navigate(p); } }, "default columns")));
    const pick = h("span", { class: "colpicker" }, h("button", { onclick: (e) => { e.stopPropagation(); menu.hidden = !menu.hidden; } }, "Columns ▾"), menu);
    picker = pick;
    const size = h("select", { "aria-label": "Rows per page", onchange: (e) => { const p = new URLSearchParams(cur); p.set("size", e.target.value); p.delete("page"); navigate(p); } },
      [25, 50, 100, 200, 500].map((n) => h("option", { value: n, selected: String(n) === String(r.size) ? true : null }, `${n} / page`)));
    const unrev = h("label", { class: "check small" }, h("input", { type: "checkbox", checked: cur.get("unrev") === "1" ? true : null,
      onchange: (e) => { const p = new URLSearchParams(cur); if (e.target.checked) p.set("unrev", "1"); else p.delete("unrev"); navigate(p); } }), "unreviewed first");
    toolbar.append(unrev, pick, size);
  }

  function drawTable(r) {
    const cols = r.columns.filter((k) => k !== "id" && k !== "title" && k !== "review.verdict");
    const sort = cur.get("sort") || (r.search && r.search.text ? "_matches" : "title");
    const dir = cur.get("dir") || (sort === "_matches" ? "desc" : "asc");
    const th = (key, label, cls = "") => {
      const on = sort === key;
      return h("th", { class: `sortable ${cls}`, title: `sort by ${key}`, onclick: () => {
        const p = new URLSearchParams(cur);
        p.set("sort", key);
        p.set("dir", on && dir === "asc" ? "desc" : on ? "asc" : key === "_matches" || isNum(key) ? "desc" : "asc");
        p.delete("page");
        navigate(p);
      } }, label, on ? h("span", { class: "arrow" }, dir === "asc" ? " ▲" : " ▼") : null);
    };
    const kinds = Object.fromEntries(schema.columns.map((c) => [c.key, c.kind]));
    function isNum(k) { return kinds[k] === "numeric"; }
    const head = h("tr", null, h("th", { class: "num" }, "#"), th("title", "title"),
      r.search && r.search.text ? th("_matches", "matches", "num") : null,
      cols.map((k) => th(k, k, isNum(k) ? "num" : "")), th("review.verdict", "verdict"));
    const tb = h("tbody");
    const start = (r.page - 1) * r.size;
    const linkParams = new URLSearchParams(cur);
    linkParams.delete("page"); linkParams.delete("size"); linkParams.delete("cols");
    linkParams.set("from", "browse");
    r.rows.forEach((row, i) => {
      const href = `#/d/${encodeURIComponent(ds)}/a/${row.id}?${linkParams}`;
      const titleCell = h("td", { class: "title" }, h("a", { href }, row.title || `(id ${row.id})`),
        row.snippets && row.snippets.length ? h("div", { class: "snips" }, row.snippets.map(snippet)) : null);
      tb.append(h("tr", null, h("td", { class: "num muted" }, num(start + i + 1)), titleCell,
        r.search && r.search.text ? h("td", { class: "num" }, num(row.__matches)) : null,
        cols.map((k) => h("td", { class: isNum(k) ? "num" : "small" }, value(row[k]))),
        h("td", null, verdictBadge(row["review.verdict"]))));
    });
    if (!r.rows.length) tb.append(h("tr", null, h("td", { colspan: cols.length + 4, class: "muted" }, "No articles match.")));
    clear(tableBox).append(h("div", { class: "table-wrap" }, h("table", { class: "data" }, h("thead", null, head), tb)));
  }

  function drawPager(r) {
    clear(pager);
    const pages = Math.max(1, Math.ceil(r.total / r.size));
    const goPage = (n) => { const p = new URLSearchParams(cur); p.set("page", n); navigate(p); };
    append(pager, [
      h("button", { disabled: r.page <= 1 ? true : null, onclick: () => goPage(1) }, "« first"),
      h("button", { disabled: r.page <= 1 ? true : null, onclick: () => goPage(r.page - 1) }, "‹ prev"),
      h("span", null, `page ${num(r.page)} of ${num(pages)}`),
      h("button", { disabled: r.page >= pages ? true : null, onclick: () => goPage(r.page + 1) }, "next ›"),
      h("button", { disabled: r.page >= pages ? true : null, onclick: () => goPage(pages) }, "last »"),
      r.rows.length ? h("a", { class: "btn", href: `#/d/${encodeURIComponent(ds)}/a/${r.rows[0].id}?${(() => { const p = new URLSearchParams(cur); p.delete("page"); p.delete("size"); p.delete("cols"); p.set("from", "browse"); return p; })()}` }, "Review these one by one ▶") : null]);
  }

  syncControls();
  await load();

  return {
    async update(p) {
      const vChanged = (p.get("v") || "") !== (cur.get("v") || "");
      cur = new URLSearchParams(p);
      if (vChanged) schema = await api(dsApi(ds, "schema", new URLSearchParams(p.get("v") ? { v: p.get("v") } : {})));
      syncControls();
      await load();
    },
    async onReviews() { await load(); },
    destroy() { document.removeEventListener("click", outside); },
  };
}
