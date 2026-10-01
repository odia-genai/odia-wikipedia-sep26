// Reports: quality/*.md plus the dataset's README.md and LEARNINGS.md, rendered by the server
// with linked references; headings have anchors and the open report's contents are listed.
import { h, api, dsApi, clear, when, bytes } from "../util.js";
import { renderDoc, tocNav, scrollToHeading, trackActive } from "../doc.js";

export async function render({ main, ds, params }) {
  let cur = new URLSearchParams(params);
  const v = cur.get("v");
  const base = `#/d/${encodeURIComponent(ds)}`;
  let list = await api(dsApi(ds, "reports"));
  const nav = h("nav", { class: "list card" });
  const view = h("div", { class: "card" });
  main.append(h("h1", null, "Reports"), h("div", { class: "reports" }, nav, view));
  let shown = null; // "group/name" on screen
  let untrack = () => {};

  const hrefOf = (name, group, id) => {
    const p = new URLSearchParams({ name, group });
    if (v) p.set("v", v);
    if (id) p.set("h", id);
    return `${base}/reports?${p}`;
  };

  async function draw() {
    const all = [...list.reports, ...list.docs];
    const name = cur.get("name") || (all[0] && all[0].name);
    const group = cur.get("group") || (list.reports.find((r) => r.name === name) ? "quality" : "docs");
    const key = `${group}/${name}`;
    if (key === shown) { // same document: just move to the heading
      scrollToHeading(view, cur.get("h"), true);
      return;
    }
    clear(nav);
    const link = (r) => h("a", { href: hrefOf(r.name, r.group), class: r.name === name && r.group === group ? "active" : "" }, r.name);
    nav.append(h("div", { class: "muted small" }, "quality/"));
    if (!list.reports.length) nav.append(h("div", { class: "muted small", style: { padding: "4px 8px" } }, "no reports yet"));
    for (const r of list.reports) {
      nav.append(link(r));
      if (r.name === list.review_first) nav.append(h("a", { href: `${base}/review-first`, class: "sub" }, "↳ as the Review first page"));
    }
    nav.append(h("div", { class: "muted small", style: { marginTop: "8px" } }, "dataset docs"));
    list.docs.forEach((r) => nav.append(link(r)));
    if (list.methodology) nav.append(h("a", { href: `${base}/methodology` }, "METHODOLOGY.md ↗"));
    const tocSlot = h("div");
    nav.append(tocSlot);
    clear(view);
    untrack();
    shown = null;
    if (!name) { view.append(h("p", { class: "muted" }, "Nothing to show.")); return; }
    const p = new URLSearchParams({ name, group });
    if (v) p.set("v", v);
    const r = await api(dsApi(ds, "report", p));
    const body = h("div", { class: "md" });
    view.append(h("div", { class: "muted small", style: { marginBottom: "8px" } }, `${group === "quality" ? "quality/" : ""}${r.name} · ${bytes(r.size)} · ${when(r.mtime)}`), body);
    renderDoc(body, r, (id) => hrefOf(name, group, id));
    const toc = tocNav(r.toc, (id) => hrefOf(name, group, id), { minLevel: 2, maxLevel: 3 });
    if (toc) tocSlot.append(h("div", { class: "muted small", style: { marginTop: "10px" } }, "contents"), toc);
    untrack = trackActive(body, toc);
    shown = key;
    if (cur.get("h")) setTimeout(() => scrollToHeading(view, cur.get("h")), 30);
  }
  await draw();
  return {
    async update(p) { cur = new URLSearchParams(p); await draw(); },
    async onData() { list = await api(dsApi(ds, "reports")); shown = null; const y = window.scrollY; await draw(); window.scrollTo(0, y); },
    destroy() { untrack(); },
  };
}
