// Review queue: articles ordered by an annotation's review_rank (or any numeric column),
// unreviewed first, with progress and a one-at-a-time review mode.
import { h, api, dsApi, num, clear, value, verdictBadge, pct } from "../util.js";
import { progressBar } from "../charts.js";
import { activeChips, isFilterParam } from "../filters.js";

function defaultSort(cols) {
  const numeric = cols.filter((c) => c.kind === "numeric" && c.key !== "id" && c.source !== "review");
  const rank = numeric.find((c) => /(^|\.)review_rank$/.test(c.key));
  if (rank) return [rank.key, "asc"];
  const ann = numeric.find((c) => c.source !== "corpus" && c.source !== "derived");
  if (ann) return [ann.key, "desc"];
  if (numeric.find((c) => c.key === "odia_ratio")) return ["odia_ratio", "asc"];
  return [numeric[0] ? numeric[0].key : "id", "asc"];
}

export async function render({ main, ds, params }) {
  const v = params.get("v");
  const schema = await api(dsApi(ds, "schema", new URLSearchParams(v ? { v } : {})));
  const cols = schema.columns;
  let cur = new URLSearchParams(params);
  const box = h("div");
  main.append(h("h1", null, "Review queue"),
    h("p", { class: "muted" }, "Articles ordered by the column you pick, unreviewed first. Columns named review_rank (1 = review first) are offered first. Reviewing moves on to the next unreviewed article after each verdict."),
    box);

  const nav = (p) => { location.hash = `/d/${encodeURIComponent(ds)}/queue?${p}`; };

  async function draw() {
    let [sort, dir] = [cur.get("sort"), cur.get("dir")];
    if (!sort) [sort, dir] = defaultSort(cols);
    dir = dir || "asc";
    const all = cur.get("all") === "1";
    // the queue is a Browse query: same filters, plus ordering and "unreviewed first"
    const q = new URLSearchParams();
    for (const [k, val] of cur) if (isFilterParam(k) || ["q", "text", "mode", "icase", "pat", "v"].includes(k)) q.append(k, val);
    q.set("sort", sort); q.set("dir", dir); q.set("unrev", "1");
    if (!all && !q.has(`null.${sort}`)) q.set(`null.${sort}`, "0");
    const listQ = new URLSearchParams(q);
    listQ.set("size", "100");
    listQ.set("cols", [sort, "words"].filter((k, i, arr) => arr.indexOf(k) === i && cols.some((c) => c.key === k)).join(","));
    const posQ = new URLSearchParams(q); posQ.set("id", "-1");
    const [list, pos] = await Promise.all([api(dsApi(ds, "browse", listQ)), api(dsApi(ds, "position", posQ))]);
    const counts = {};
    await Promise.all(["keep", "drop", "fix"].map(async (vv) => {
      const cq = new URLSearchParams(q); cq.set("in.review.verdict", vv); cq.set("size", "1");
      counts[vv] = (await api(dsApi(ds, "browse", cq))).total;
    }));

    clear(box);
    // controls
    const numeric = cols.filter((c) => c.kind === "numeric" && c.source !== "review");
    numeric.sort((a, b) => (/review_rank$/.test(b.key) - /review_rank$/.test(a.key)) || (a.source === "corpus") - (b.source === "corpus"));
    const sel = h("select", { "aria-label": "Order by", onchange: (e) => { const p = new URLSearchParams(cur); p.set("sort", e.target.value); p.delete(`null.${sort}`); nav(p); } },
      numeric.map((c) => h("option", { value: c.key, selected: c.key === sort ? true : null }, `${c.key}${c.description ? " — " + c.description.slice(0, 50) : ""}`)));
    const dirSel = h("select", { "aria-label": "Direction", onchange: (e) => { const p = new URLSearchParams(cur); p.set("sort", sort); p.set("dir", e.target.value); nav(p); } },
      h("option", { value: "asc", selected: dir === "asc" ? true : null }, "lowest first"), h("option", { value: "desc", selected: dir === "desc" ? true : null }, "highest first"));
    const allBox = h("label", { class: "check" }, h("input", { type: "checkbox", checked: all ? null : true,
      onchange: (e) => { const p = new URLSearchParams(cur); p.set("sort", sort); if (e.target.checked) p.delete("all"); else p.set("all", "1"); nav(p); } }),
    `only articles with a ${sort} value`);
    const artParams = new URLSearchParams(q); artParams.set("from", "queue");
    const start = pos.next_unreviewed || pos.next;
    box.append(h("div", { class: "card stack" },
      h("div", { class: "row" }, h("span", null, "Order by"), sel, dirSel, allBox),
      activeChips((() => { const p = new URLSearchParams(cur); p.delete("unrev"); return p; })(), (p) => nav(p)),
      h("div", { class: "row" }, h("strong", null, `${num(pos.reviewed)} of ${num(pos.total)} reviewed (${pct(pos.reviewed, pos.total)})`),
        h("span", { class: "muted" }, `keep ${num(counts.keep)} · drop ${num(counts.drop)} · fix ${num(counts.fix)}`),
        h("span", { class: "spacer" }),
        start ? h("a", { class: "btn primary", href: `#/d/${encodeURIComponent(ds)}/a/${start}?${artParams}` }, pos.next_unreviewed ? "Start reviewing ▶" : "Walk through again ▶") : h("span", { class: "chip ok" }, "queue empty")),
      progressBar([{ n: counts.keep, cls: "p-keep", label: "keep" }, { n: counts.drop, cls: "p-drop", label: "drop" }, { n: counts.fix, cls: "p-fix", label: "fix" }], pos.total),
      h("p", { class: "muted small" }, "In review mode: ", h("kbd", null, "1"), "/", h("kbd", null, "K"), " keep, ", h("kbd", null, "2"), "/", h("kbd", null, "d"), " drop, ",
        h("kbd", null, "3"), "/", h("kbd", null, "f"), " fix, then it opens the next unreviewed article; ", h("kbd", null, "j"), "/", h("kbd", null, "k"), " move without deciding, ",
        h("kbd", null, "n"), " note, ", h("kbd", null, "u"), " undo.")));

    // list
    const t = h("table", { class: "data" }, h("thead", null, h("tr", null, h("th", { class: "num" }, "#"), h("th", null, "title"),
      h("th", { class: "num" }, sort), h("th", { class: "num" }, "words"), h("th", null, "verdict"), h("th", null, "reasons"))));
    const tb = h("tbody");
    const reasonsKey = sort.includes(".") ? sort.slice(0, sort.indexOf(".")) + ".review_reasons" : null;
    list.rows.forEach((r, i) => {
      tb.append(h("tr", null, h("td", { class: "num muted" }, num(i + 1)),
        h("td", { class: "title" }, h("a", { href: `#/d/${encodeURIComponent(ds)}/a/${r.id}?${artParams}` }, r.title)),
        h("td", { class: "num" }, value(r[sort])), h("td", { class: "num" }, value(r.words)),
        h("td", null, verdictBadge(r["review.verdict"])),
        h("td", { class: "small" }, reasonsKey && r[reasonsKey] !== undefined ? value(r[reasonsKey]) : "")));
    });
    if (!list.rows.length) tb.append(h("tr", null, h("td", { colspan: 6, class: "muted" }, "Nothing in this queue.")));
    box.append(h("h2", { style: { marginTop: "16px" } }, `Next ${Math.min(100, list.total)} of ${num(list.total)}`),
      h("div", { class: "table-wrap" }, t.appendChild(tb) && t));
    // fetch reasons column if it exists
    if (reasonsKey && cols.some((c) => c.key === reasonsKey) && list.rows.length) {
      const rq = new URLSearchParams(listQ); rq.set("cols", reasonsKey);
      const rr = await api(dsApi(ds, "browse", rq));
      const byId = Object.fromEntries(rr.rows.map((x) => [x.id, x[reasonsKey]]));
      [...tb.querySelectorAll("tr")].forEach((tr, i) => { const r = list.rows[i]; if (r) tr.lastChild.textContent = byId[r.id] ? [].concat(byId[r.id]).join(", ") : ""; });
    }
  }
  await draw();
  return {
    async update(p) { cur = new URLSearchParams(p); await draw(); },
    async onReviews() { await draw(); },
  };
}
