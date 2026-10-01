// Review first: quality/review-first.md as an actionable list. Each item links to its article,
// scrolled to the flagged paragraph (followed by content across rebuilds), shows the article's
// current review, and can be decided in place. Filters: failure type, hide reviewed.
import { h, api, dsApi, clear, num, toast, verdictBadge, when, ago, ApiError } from "../util.js";
import { progressBar } from "../charts.js";
import { renderDoc } from "../doc.js";

// Filters travel with article links, so prev/next in the article view walks the same list.
export const RF_PARAMS = ["type", "hide"];

export function itemPasses(it, params) {
  const types = params.getAll("type");
  if (types.length && !types.includes(it.type)) return false;
  if (params.get("hide") === "1" && it.reviewed) return false;
  return true;
}

export function itemHref(ds, it, params) {
  const p = new URLSearchParams();
  if (params.get("v")) p.set("v", params.get("v"));
  if (it.para_now !== null && it.para_now !== undefined) p.set("para", it.para_now);
  p.set("from", "review-first");
  p.set("rank", it.rank);
  for (const k of RF_PARAMS) for (const val of params.getAll(k)) p.append(k, val);
  return `#/d/${encodeURIComponent(ds)}/a/${it.id}?${p}`;
}

const STATUS = {
  moved: (it) => h("span", { class: "chip warn", title: `the flagged paragraph was paragraph ${it.para} when scored; it is now paragraph ${it.para_now}` }, `now ¶${it.para_now}`),
  changed: (it) => h("span", { class: "chip warn", title: "the scored text of this paragraph is no longer in the article: it was edited, or paragraphs before it were removed. The link opens the same index." }, `¶${it.para} changed since scoring`),
  gone: (it) => h("span", { class: "chip bad", title: "the scored paragraph is no longer in the article's text (removed by a rebuild)" }, `¶${it.para} no longer in the text`),
  "article not in this version": () => h("span", { class: "chip bad" }, "article not in this version"),
};

export async function render({ main, ds, params, app }) {
  const listed = () => {
    const d = app && app.home && app.home.datasets.find((x) => x.name === ds);
    return !d || d.review_first;
  };
  if (!listed()) {
    toast(`${ds} has no quality/review-first.md`);
    location.replace(`#/d/${encodeURIComponent(ds)}`);
    return {};
  }
  let cur = new URLSearchParams(params);
  const v = cur.get("v");
  const vp = new URLSearchParams(v ? { v } : {});
  let data;
  try {
    data = await api(dsApi(ds, "review-first", vp));
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) {
      toast(`${ds} has no quality/review-first.md`);
      location.replace(`#/d/${encodeURIComponent(ds)}`);
      return {};
    }
    throw e;
  }
  const base = `#/d/${encodeURIComponent(ds)}`;
  const nav = (p) => { location.hash = `/d/${encodeURIComponent(ds)}/review-first${[...p].length ? "?" + p : ""}`; };
  const header = h("div");
  const controls = h("div", { class: "card stack", style: { margin: "10px 0 14px" } });
  const list = h("div", { class: "rf-list" });
  main.append(header, controls, list);

  function drawHeader() {
    clear(header);
    const vq = v ? `&v=${encodeURIComponent(v)}` : "";
    header.append(h("div", { class: "row" }, h("h1", { style: { margin: 0 } }, "Review first"),
      h("span", { class: "chip", title: data.path }, `quality/${data.name}`),
      h("span", { class: "muted small", title: new Date(data.mtime * 1000).toString() }, `updated ${when(data.mtime)} (${ago(new Date(data.mtime * 1000).toISOString())})`),
      h("span", { class: "spacer" }),
      data.queue_sort ? h("a", { class: "btn", href: `${base}/queue?sort=${encodeURIComponent(data.queue_sort)}&dir=asc${vq}`, title: `every flagged article, ordered by ${data.queue_sort}, with one-at-a-time review` }, `Review queue (${data.queue_sort}) →`) : null,
      h("a", { class: "btn", href: `${base}/reports?name=${encodeURIComponent(data.name)}&group=quality${vq}` }, "Plain report")));
  }

  function drawControls() {
    clear(controls);
    if (!data.parsed) {
      controls.append(h("div", { class: "warnings small" }, "The items in this file are not in the expected shape (", h("code", null, "1. **title** (id N, para P, `type`)"), "), so it is shown as a document; ids are still links."));
      return;
    }
    const items = data.segments.filter((s) => s.kind === "item");
    const types = cur.getAll("type");
    const counts = { keep: 0, drop: 0, fix: 0, para: 0 };
    for (const it of items) {
      if (it.verdict) counts[it.verdict]++;
      else if (it.flagged_dropped) counts.para++;
    }
    const seg = h("div", { class: "seg" }, h("button", { class: types.length ? "" : "on", onclick: () => { const p = new URLSearchParams(cur); p.delete("type"); nav(p); } }, "all types"),
      data.types.map(([t, n]) => h("button", { class: types.includes(t) ? "on" : "", "aria-pressed": types.includes(t) ? "true" : "false", onclick: () => {
        const p = new URLSearchParams(cur);
        const now = new Set(p.getAll("type"));
        if (now.has(t)) now.delete(t); else now.add(t);
        p.delete("type");
        for (const x of now) p.append("type", x);
        nav(p);
      } }, t, h("span", { class: "muted" }, ` ${n}`))));
    const hide = h("label", { class: "check" }, h("input", { type: "checkbox", checked: cur.get("hide") === "1" ? true : null,
      onchange: (e) => { const p = new URLSearchParams(cur); if (e.target.checked) p.set("hide", "1"); else p.delete("hide"); nav(p); } }), "hide reviewed");
    const shown = items.filter((it) => itemPasses(it, cur)).length;
    controls.append(h("div", { class: "row" }, seg, hide),
      h("div", { class: "row small" }, h("strong", null, `${num(data.reviewed)} of ${num(data.items)} reviewed`),
        h("span", { class: "muted" }, `keep ${counts.keep} · drop ${counts.drop} · fix ${counts.fix} · flagged paragraph dropped ${counts.para}`),
        h("span", { class: "spacer" }), h("span", { class: "muted" }, `showing ${num(shown)}`)),
      progressBar([{ n: counts.keep, cls: "p-keep", label: "keep" }, { n: counts.drop, cls: "p-drop", label: "drop" },
        { n: counts.fix, cls: "p-fix", label: "fix" }, { n: counts.para, cls: "p-other", label: "flagged paragraph dropped" }], data.items),
      h("p", { class: "muted small", style: { margin: 0 } }, "Reviewed = the article has a verdict, or its flagged paragraph is marked to drop. Titles open the article at the flagged paragraph; prev/next there (j/k) walks this list with these filters."));
  }

  async function decide(it, change) {
    const drops = new Set(it.drops);
    let verdict = it.verdict;
    if (change.verdict !== undefined) verdict = change.verdict;
    if (change.togglePara) { if (drops.has(it.para_now)) drops.delete(it.para_now); else drops.add(it.para_now); }
    try {
      await api(dsApi(ds, "review"), { body: { v, id: it.id, text_sha1: it.text_sha1, verdict, note: it.note, drop_paras: [...drops] } });
      toast(change.togglePara ? (drops.has(it.para_now) ? `¶${it.para_now} marked to drop` : `¶${it.para_now} kept`) : verdict ? `${it.title}: ${verdict}` : "Verdict cleared");
      await reload();
    } catch (e) {
      toast(`Not saved: ${e.message}`, "error");
    }
  }

  function itemCard(it) {
    const href = itemHref(ds, it, cur);
    const para = it.para === null || it.para === undefined ? h("span", { class: "chip" }, "whole article") : h("span", { class: "chip" }, `¶${it.para}`);
    const state = it.verdict ? verdictBadge(it.verdict) : it.flagged_dropped ? h("span", { class: "verdict v-drop", title: "the flagged paragraph is marked to drop" }, "¶ dropped") : h("span", { class: "verdict none" }, "none");
    const act = (label, val, cls) => h("button", { class: `small ${it.verdict === val ? "on " + cls : ""}`, title: it.verdict === val ? "clear this verdict" : `set the verdict to ${val}`,
      disabled: it.in_corpus ? null : true, onclick: () => decide(it, { verdict: it.verdict === val ? null : val }) }, label);
    const actions = h("div", { class: "rf-actions" }, act("Keep", "keep", "keep"), act("Drop", "drop", "drop"), act("Fix", "fix", "fix"),
      it.para_now !== null && it.para_now !== undefined ? h("button", { class: `small ${it.flagged_dropped ? "on drop" : ""}`, title: it.flagged_dropped ? "keep the flagged paragraph" : "mark just the flagged paragraph to drop", onclick: () => decide(it, { togglePara: true }) }, it.flagged_dropped ? `Undrop ¶${it.para_now}` : `Drop ¶${it.para_now}`) : null);
    const body = h("div", { class: "rf-body md" });
    if (it.body_html) renderDoc(body, { html: it.body_html }, () => href);
    return h("div", { class: `card rf-item${it.reviewed ? " reviewed" : ""}`, "data-rank": it.rank, "data-type": it.type || "" },
      h("div", { class: "rf-head" },
        h("span", { class: "rf-rank" }, `${it.rank}`),
        h("a", { class: "rf-title odia", href }, it.title),
        h("span", { class: "muted small" }, `id ${it.id}`),
        it.type ? h("span", { class: `chip type-${it.type}` }, it.type) : null,
        para, STATUS[it.para_status] ? STATUS[it.para_status](it) : null,
        it.stale && it.stale.length ? h("span", { class: "chip warn", title: `the article's text changed since ${it.stale.join(", ")} was computed` }, "text changed since scoring") : null,
        it.title_now && it.title_now !== it.title ? h("span", { class: "muted small odia" }, `now titled ${it.title_now}`) : null,
        h("span", { class: "spacer" }), state, actions),
      body);
  }

  function drawList() {
    clear(list);
    if (!data.parsed) {
      const body = h("article", { class: "md" });
      renderDoc(body, { html: data.html }, (id) => `${base}/reports?name=${encodeURIComponent(data.name)}&group=quality&h=${encodeURIComponent(id)}`);
      list.append(h("div", { class: "card" }, body));
      return;
    }
    let first = true;
    for (const s of data.segments) {
      if (s.kind === "md") {
        const md = h("div", { class: "md rf-md" });
        renderDoc(md, { html: s.html }, (id) => `${base}/reports?name=${encodeURIComponent(data.name)}&group=quality&h=${encodeURIComponent(id)}`);
        if (first) {
          // the intro and legend: useful, but long; folded by default
          list.append(h("details", { class: "card rf-intro" }, h("summary", null, "About this list: how it is ranked, and the failure types"), md));
        } else {
          list.append(md);
        }
      } else if (itemPasses(s, cur)) {
        list.append(itemCard(s));
      }
      first = false;
    }
    if (!list.querySelector(".rf-item")) list.append(h("p", { class: "muted" }, "No items match these filters."));
  }

  async function reload() {
    if (!listed()) { toast(`${ds} has no quality/review-first.md any more`); location.replace(`#/d/${encodeURIComponent(ds)}`); return; }
    try {
      data = await api(dsApi(ds, "review-first", vp));
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) { toast(`${ds} has no quality/review-first.md any more`); location.replace(`#/d/${encodeURIComponent(ds)}`); }
      return;
    }
    const y = window.scrollY;
    const open = list.querySelector(".rf-intro")?.open;
    drawHeader(); drawControls(); drawList();
    const intro = list.querySelector(".rf-intro");
    if (intro && open) intro.open = true;
    window.scrollTo(0, y);
  }

  drawHeader(); drawControls(); drawList();
  return {
    async update(p) { cur = new URLSearchParams(p); drawControls(); drawList(); },
    onReviews: reload,
    onData: reload,
  };
}
