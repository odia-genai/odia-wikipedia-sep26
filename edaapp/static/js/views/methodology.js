// Methodology: the dataset's METHODOLOGY.md, with a sticky table of contents, heading anchors,
// linked references, KaTeX math, "last updated" from the file, and live reload when it changes.
import { h, api, dsApi, clear, toast, ApiError, when, ago } from "../util.js";
import { renderDoc, tocNav, scrollToHeading, trackActive, topHeading, restoreHeading } from "../doc.js";

const hasMethodology = (app, ds) => {
  const d = app && app.home && app.home.datasets.find((x) => x.name === ds);
  return !d || d.methodology; // unknown: ask the server
};

export async function render({ main, ds, params, app }) {
  if (!hasMethodology(app, ds)) return gone(ds);
  let cur = new URLSearchParams(params);
  const v = cur.get("v");
  const vp = new URLSearchParams(v ? { v } : {});
  const hrefFor = (id) => {
    const p = new URLSearchParams(vp);
    p.set("h", id);
    return `#/d/${encodeURIComponent(ds)}/methodology?${p}`;
  };
  let doc;
  try {
    doc = await api(dsApi(ds, "methodology", vp));
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) return gone(ds);
    throw e;
  }
  const updated = h("span", { class: "muted small" });
  const head = h("div", { class: "row", style: { marginBottom: "10px" } }, h("h1", { style: { margin: 0 } }, "Methodology"),
    h("span", { class: "chip", title: doc.path }, doc.name), updated,
    h("span", { class: "spacer" }), h("span", { class: "muted small" }, "Reloads by itself when the file changes."));
  const body = h("article", { class: "md doc-body", lang: "en" });
  const tocBox = h("aside", { class: "toc-col" });
  main.append(head, h("div", { class: "docpage" }, tocBox, h("div", { class: "card" }, body)));
  let untrack = () => {};

  function paint() {
    renderDoc(body, doc, hrefFor);
    clear(tocBox);
    const nav = tocNav(doc.toc, hrefFor);
    if (nav) tocBox.append(h("div", { class: "muted small toc-title" }, "Contents"), nav);
    untrack();
    untrack = trackActive(body, nav);
    stamp();
  }
  function stamp() {
    updated.textContent = `last updated ${when(doc.mtime)} (${ago(new Date(doc.mtime * 1000).toISOString())})`;
    updated.title = new Date(doc.mtime * 1000).toString();
  }
  paint();
  if (cur.get("h")) setTimeout(() => scrollToHeading(body, cur.get("h")), 30);
  const clock = setInterval(stamp, 30000);

  return {
    async update(p) {
      cur = new URLSearchParams(p);
      if (cur.get("h")) scrollToHeading(body, cur.get("h"), true);
    },
    async onData() {
      if (!hasMethodology(app, ds)) { gone(ds); return; } // the file went away
      let next;
      try {
        next = await api(dsApi(ds, "methodology", vp));
      } catch (e) {
        if (e instanceof ApiError && e.status === 404) { gone(ds); return; }
        return;
      }
      if (next.html === doc.html && next.mtime === doc.mtime) return;
      const mark = topHeading(body);
      doc = next;
      paint();
      restoreHeading(body, mark);
      toast("Methodology updated");
    },
    destroy() { untrack(); clearInterval(clock); },
  };
}

function gone(ds) {
  // no METHODOLOGY.md (any more): don't show an empty page
  toast(`${ds} has no METHODOLOGY.md`);
  location.replace(`#/d/${encodeURIComponent(ds)}`);
  return {};
}
