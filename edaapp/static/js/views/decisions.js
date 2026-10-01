// Decisions: the current decision per article (the latest event), counts, filter, undo, export.
import { h, api, dsApi, num, clear, verdictBadge, ago, toast } from "../util.js";
import { bulkDialog } from "../bulk.js";

const FILTERS = [["", "all decided"], ["keep", "keep"], ["drop", "drop"], ["fix", "fix"], ["none", "no verdict (paragraphs / note only)"],
  ["paras", "with dropped paragraphs"], ["stale", "made on another text"], ["cleared", "cleared"]];

export async function render({ main, ds, params }) {
  let cur = new URLSearchParams(params);
  const v = params.get("v");
  const box = h("div");
  main.append(h("h1", null, "Decisions"), box);
  const nav = (p) => { location.hash = `/d/${encodeURIComponent(ds)}/decisions?${p}`; };

  async function draw() {
    const r = await api(dsApi(ds, "decisions", cur));
    clear(box);
    const c = r.counts;
    const verdict = cur.get("verdict") || "";
    const countOf = { "": c.total - c.cleared, keep: c.keep, drop: c.drop, fix: c.fix, none: c.none, paras: c.with_paragraphs, stale: c.stale, cleared: c.cleared };
    const seg = h("div", { class: "seg" }, FILTERS.map(([k, label]) => h("button", { class: verdict === k ? "on" : "", onclick: () => {
      const p = new URLSearchParams(cur); if (k) p.set("verdict", k); else p.delete("verdict"); p.delete("page"); nav(p);
    } }, `${label} `, h("span", { class: "muted" }, num(countOf[k])))));
    const q = h("input", { type: "search", placeholder: "title or note contains…", value: cur.get("q") || "",
      onkeydown: (e) => { if (e.key === "Enter") { const p = new URLSearchParams(cur); if (e.target.value.trim()) p.set("q", e.target.value.trim()); else p.delete("q"); p.delete("page"); nav(p); } } });
    const exp = (fmt) => `/api/d/${encodeURIComponent(ds)}/decisions/export?format=${fmt}`;
    box.append(h("div", { class: "card stack" },
      h("div", { class: "row" }, seg),
      h("div", { class: "row" }, q, h("span", { class: "spacer" }),
        h("a", { class: "btn", href: exp("jsonl"), download: "" }, "Export JSONL"),
        h("a", { class: "btn", href: exp("json"), download: "" }, "Export JSON"),
        h("a", { class: "btn", href: "/api/reviews/log", download: "reviews.jsonl", title: "every event, all datasets" }, "Raw log"),
        r.all_ids.length ? h("button", { class: "danger", onclick: () => bulkDialog(ds, v, { action: "undo", match: { type: "ids", ids: r.all_ids } }, { onDone: draw }) }, `Undo all ${num(r.all_ids.length)} shown…`) : null),
      h("div", { class: "muted small" }, `${num(c.total)} articles have events in `, h("code", null, r.log.path),
        ` (${num(r.log.events)} events in the file${r.log.bad_lines ? `, ${r.log.bad_lines} unreadable lines skipped` : ""}). The latest event per article wins; undo appends an event that restores the previous decision. Exports hold the current decisions, cleared ones left out.`)));

    const t = h("table", { class: "data", style: { marginTop: "12px" } }, h("thead", null, h("tr", null,
      ["article", "verdict", "¶ dropped", "note", "when", "events", ""].map((x) => h("th", null, x)))));
    const tb = h("tbody");
    const vq = v ? `v=${encodeURIComponent(v)}&` : "";
    const ctx = new URLSearchParams(cur); ctx.delete("page");
    for (const row of r.rows) {
      const undo = h("button", { class: "small", onclick: async () => {
        undo.disabled = true;
        try { await api(dsApi(ds, "undo"), { body: { v, id: row.id } }); toast(`Undone: ${row.title}`); await draw(); }
        catch (e) { toast(e.message, "error"); undo.disabled = false; }
      } }, "undo");
      tb.append(h("tr", null,
        h("td", { class: "title" }, h("a", { href: `#/d/${encodeURIComponent(ds)}/a/${row.id}?${vq}` }, row.title),
          row.stale ? h("span", { class: "chip warn", title: "the text changed since this decision" }, "text changed") : null,
          row.missing ? h("span", { class: "chip bad" }, "not in this version") : null),
        h("td", null, row.cleared ? h("span", { class: "muted" }, "cleared") : verdictBadge(row.verdict)),
        h("td", { class: "num" }, row.dropped ? num(row.dropped) : ""),
        h("td", { class: "small odia" }, row.note),
        h("td", { class: "small nowrap", title: row.ts }, ago(row.ts)),
        h("td", { class: "num" }, num(row.events)),
        h("td", null, undo)));
    }
    if (!r.rows.length) tb.append(h("tr", null, h("td", { colspan: 7, class: "muted" }, "No decisions here yet.")));
    t.append(tb);
    box.append(h("div", { class: "table-wrap" }, t));
    const pages = Math.max(1, Math.ceil(r.total / r.size));
    if (pages > 1) {
      const go = (n) => { const p = new URLSearchParams(cur); p.set("page", n); nav(p); };
      box.append(h("div", { class: "pager" }, h("button", { disabled: r.page <= 1 ? true : null, onclick: () => go(r.page - 1) }, "‹ prev"),
        h("span", null, `page ${r.page} of ${pages}`), h("button", { disabled: r.page >= pages ? true : null, onclick: () => go(r.page + 1) }, "next ›")));
    }
  }
  await draw();
  return {
    async update(p) { cur = new URLSearchParams(p); await draw(); },
    async onReviews() { await draw(); },
  };
}
