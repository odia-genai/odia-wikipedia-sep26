// Article: rendered Markdown by paragraph, paragraph shading, metadata, and the review panel.
import { h, append, api, dsApi, num, clear, value, toast, typeset, tipOn, ago, verdictBadge } from "../util.js";
import { itemPasses, itemHref } from "./reviewfirst.js";

const ARTICLE_PARAMS = ["raw", "from", "auto", "shade", "para", "rank"];

export async function render({ main, ds, id, params }) {
  const v = params.get("v");
  const vp = new URLSearchParams(v ? { v } : {});
  const a = await api(dsApi(ds, `article/${id}`, vp));
  const ctx = new URLSearchParams(params);
  for (const k of ARTICLE_PARAMS) ctx.delete(k);
  const from = params.get("from");
  const auto = params.get("auto") === "1" || from === "queue";
  let pos = null;
  let raw = params.get("raw") === "1";
  const targetPara = params.get("para") !== null && params.get("para") !== "" ? Number(params.get("para")) : null;

  // review state (client copy of the latest decision)
  const st = {
    verdict: a.review.event ? a.review.event.verdict : null,
    note: a.review.event ? a.review.event.note : "",
    drops: new Set((a.review.drops || []).map((d) => d.para)),
    saving: false,
    lastSaved: a.review.event ? a.review.event.ts : null,
  };

  document.title = `${a.title} · ${ds} · edaapp`;
  const listHref = from === "queue" ? `#/d/${encodeURIComponent(ds)}/queue?${listParams(ctx)}`
    : from === "review-first" ? `#/d/${encodeURIComponent(ds)}/review-first?${ctx}`
    : from === "patterns" ? `#/d/${encodeURIComponent(ds)}/patterns?${ctx}`
    : from === "decisions" ? `#/d/${encodeURIComponent(ds)}/decisions?${ctx}`
    : from === "excluded" ? `#/d/${encodeURIComponent(ds)}/excluded?${ctx}`
    : `#/d/${encodeURIComponent(ds)}/browse?${ctx}`;

  // ---- header -------------------------------------------------------------------------------
  const posText = h("span", { class: "muted small" });
  const prevBtn = h("button", { title: "previous (k)", disabled: true, onclick: () => goTo(pos && pos.prev) }, "‹ prev");
  const nextBtn = h("button", { title: "next (j)", disabled: true, onclick: () => goTo(pos && pos.next) }, "next ›");
  const head = h("div", { class: "article-head" },
    h("a", { href: listHref, class: "btn", title: "back to the list (b)" }, "← list"), prevBtn, nextBtn, posText,
    auto ? h("span", { class: "chip warn", title: "a verdict moves on to the next unreviewed article" }, "review mode: auto-advance") : null);
  const titleRow = h("div", { class: "article-head" }, h("h1", null, a.title),
    h("span", { class: "chip" }, `id ${a.id}`), verdictSlot());
  function verdictSlot() { const s = h("span", { id: "vbadge" }); s.append(verdictBadge(st.verdict)); return s; }

  // ---- shading ------------------------------------------------------------------------------
  const shadeOpts = [];
  for (const [name, pa] of Object.entries(a.para_annotations)) {
    for (const c of pa.columns) if (c.numeric && pa.scales[c.name]) shadeOpts.push({ name, col: c.name, key: `${name}.${c.name}` });
  }
  shadeOpts.sort((x, y) => (y.col === y.name) - (x.col === x.name));
  let shade = params.get("shade") || (shadeOpts[0] && shadeOpts[0].key) || "";
  const legend = h("span", { class: "legend" });
  const shadeSel = shadeOpts.length ? h("select", { "aria-label": "Shade paragraphs by", onchange: (e) => { shade = e.target.value; paint(); drawLegend(); } },
    h("option", { value: "" }, "no shading"), shadeOpts.map((o) => h("option", { value: o.key, selected: o.key === shade ? true : null }, `shade: ${o.key}`))) : null;
  const rawBtn = h("button", { title: "raw Markdown (r)", onclick: () => { raw = !raw; drawDoc(); } }, "raw");

  const docTools = h("div", { class: "row", style: { margin: "0 0 8px" } }, rawBtn, shadeSel, legend,
    h("span", { class: "spacer" }), h("span", { class: "muted small" }, `${a.paragraphs.length} paragraphs · ${num(a.text.length)} chars`));
  const doc = h("article", { class: "doc", lang: "or" });
  const rawParas = a.text.split("\n\n"); // the same split as the server's paragraphs

  function scaleFor(key) {
    const o = shadeOpts.find((x) => x.key === key);
    if (!o) return null;
    return { o, sc: a.para_annotations[o.name].scales[o.col], rows: a.para_annotations[o.name].rows };
  }
  // a paragraph annotation's primary column (numeric, named like it: bpb.paragraphs -> bpb) is null when the
  // paragraph's text has no score yet; shown as "not scored", never as a low value
  function unscored(name, i) {
    const pa = a.para_annotations[name];
    const row = pa && pa.primary ? pa.rows[String(i)] : null;
    return !!(row && !row._stale && row[pa.primary] === null);
  }
  function tOf(x, sc) {
    const lo = sc.q[0], hi = sc.q[4];
    if (x === null || x === undefined || hi === lo) return null;
    return Math.max(0, Math.min(1, (x - lo) / (hi - lo)));
  }
  function paint() {
    const s = scaleFor(shade);
    for (const el of doc.querySelectorAll(".para")) {
      const g = el.querySelector(".gutter");
      g.style.borderRightColor = "transparent";
      el.style.backgroundColor = "";
      el.classList.toggle("unscored", !!s && unscored(s.o.name, el.dataset.i));
      if (!s) continue;
      const row = s.rows[el.dataset.i];
      if (row && row._stale) { g.style.borderRightColor = "var(--axis)"; continue; } // scored on other text
      if (el.classList.contains("unscored")) { g.style.borderRightColor = "var(--axis)"; continue; } // dashed, "not scored"
      const t = row ? tOf(row[s.o.col], s.sc) : null;
      if (t === null) continue;
      g.style.borderRightColor = `rgba(var(--heat), ${0.12 + 0.88 * t})`;
      if (!el.classList.contains("dropped")) el.style.backgroundColor = `rgba(var(--heat), ${(0.16 * t).toFixed(3)})`;
    }
  }
  function drawLegend() {
    clear(legend);
    const s = scaleFor(shade);
    if (!s) return;
    const pa = a.para_annotations[s.o.name];
    append(legend, [h("span", null, num(s.sc.q[0])), h("span", { class: "ramp", style: { background: "linear-gradient(to right, rgba(var(--heat),.12), rgba(var(--heat),1))" } }),
      h("span", null, num(s.sc.q[4])), h("span", { class: "muted" }, `(2nd–98th percentile over the corpus; median ${num(s.sc.q[2])})`),
      pa.stale ? h("span", { class: "chip bad", title: "the paragraph scores were computed on a different text" }, "stale") : null,
      pa.unscored ? h("span", { class: "chip warn", title: `${pa.unscored} paragraph(s) of this article have no ${s.o.name} score yet (${pa.primary} null): marked "not scored", not shaded` }, `${pa.unscored} not scored`) : null]);
  }

  function paraTip(i) {
    const p = a.paragraphs[i];
    const box = h("div", null, h("strong", null, `paragraph ${i}`), ` · ${p.kind} · ${num(p.chars)} chars`);
    for (const [name, pa] of Object.entries(a.para_annotations)) {
      const row = pa.rows[String(i)];
      if (!row) { box.append(h("div", { class: "muted" }, `${name}: no row`)); continue; }
      if (row._stale) box.append(h("div", { class: "muted" }, `${name}: scored on a different text of this paragraph (not shaded)`));
      if (unscored(name, i)) box.append(h("div", { class: "muted" }, `${name}: not scored (this paragraph's text has no score yet)`));
      for (const [k, val] of Object.entries(row)) {
        if (["text_sha1", "para_sha1", "_stale"].includes(k)) continue;
        const ns = k === pa.primary && val === null && !row._stale;
        box.append(h("div", null, `${name}.${k}: `, ns ? h("strong", { class: "muted" }, "not scored") : h("strong", null, value(val))));
      }
    }
    if (st.drops.has(i)) box.append(h("div", null, "marked to drop"));
    box.append(h("div", { class: "muted" }, `sha1 ${p.sha1.slice(0, 12)}…`));
    return box;
  }

  function drawDoc() {
    clear(doc);
    rawBtn.classList.toggle("primary", raw);
    a.paragraphs.forEach((p, i) => {
      // paragraph 0 is the title heading: the build never drops it (drop the article instead)
      const toggle = i === 0 ? null : h("button", { title: st.drops.has(i) ? "keep this paragraph" : "drop this paragraph", "aria-pressed": st.drops.has(i) ? "true" : "false",
        onclick: () => toggleDrop(i) }, st.drops.has(i) ? "undrop" : "drop");
      const idx = h("span", { class: "pi" }, String(i));
      tipOn(idx, () => paraTip(i));
      const body = h("div", { class: "body" });
      if (raw) body.append(h("pre", { class: "raw" }, rawParas[i]));
      else body.innerHTML = p.html; // server-rendered Markdown, raw HTML disabled
      doc.append(h("div", { class: `para${st.drops.has(i) ? " dropped" : ""}${i === targetPara ? " target" : ""}`, id: `p${i}`, "data-i": String(i) },
        h("div", { class: "gutter" }, idx, toggle), body));
    });
    if (!raw) typeset(doc);
    paint();
  }

  // ---- side panel ---------------------------------------------------------------------------
  const saved = h("div", { class: "saved" });
  const note = h("textarea", { rows: 3, placeholder: "note (n): what is wrong, what to fix…", "aria-label": "Review note" });
  note.value = st.note || "";
  note.addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); saveNote(); note.blur(); } });
  note.addEventListener("blur", saveNote);
  const vbtn = (val, label, keys) => h("button", { class: val, "data-v": val, title: `${label} (${keys})`, onclick: () => setVerdict(st.verdict === val ? null : val) }, label);
  const verdicts = h("div", { class: "verdicts" }, vbtn("keep", "Keep", "1 / Shift+K"), vbtn("drop", "Drop", "2 / d"), vbtn("fix", "Fix", "3 / f"));
  const dropsInfo = h("div", { class: "small" });
  const staleBox = h("div");
  const hist = h("div");
  const panel = h("div", { class: "card review-panel" },
    h("h2", null, "Review"), verdicts, note, saved, dropsInfo, staleBox,
    h("div", { class: "row", style: { marginTop: "8px" } },
      h("button", { onclick: undo, title: "undo the last change (u)" }, "Undo last change"),
      h("button", { class: "ghost", onclick: () => setVerdict(null), title: "clear the verdict (0)" }, "Clear verdict")),
    hist);

  function drawPanel() {
    for (const b of verdicts.querySelectorAll("button")) b.classList.toggle("on", b.dataset.v === st.verdict);
    const badge = document.getElementById("vbadge");
    if (badge) clear(badge).append(verdictBadge(st.verdict));
    clear(saved).append(st.saving ? "saving…" : st.lastSaved ? `saved ${ago(st.lastSaved)} · ${a.review.events} event${a.review.events === 1 ? "" : "s"} for this article` : "not reviewed yet");
    clear(dropsInfo);
    if (st.drops.size) {
      dropsInfo.append(`Paragraphs to drop: `, ...[...st.drops].sort((x, y) => x - y).map((i) => h("a", { href: "javascript:void 0", style: { marginRight: "6px" }, onclick: () => flash(i) }, `¶${i}`)));
    } else dropsInfo.append(h("span", { class: "muted" }, "Hover a paragraph and press its drop button to drop just that paragraph."));
    clear(staleBox);
    if (a.review.stale) staleBox.append(h("div", { class: "card warnings small", style: { marginTop: "8px" } }, "The latest decision was made on a different version of this text."));
    const moved = (a.review.drops || []).filter((d) => d.moved_from !== undefined);
    if (moved.length) staleBox.append(h("div", { class: "card warnings small", style: { marginTop: "8px" } }, `${moved.length} dropped paragraph(s) moved and were matched by their sha1: `, moved.map((d) => `¶${d.moved_from}→¶${d.para}`).join(", ")));
    if (a.review.missing && a.review.missing.length) {
      staleBox.append(h("div", { class: "card warnings small", style: { marginTop: "8px" } },
        `${a.review.missing.length} dropped paragraph(s) are not in this text: a rebuild already removed them, or the text changed. They stay in the decision, so the next build keeps dropping them. `,
        h("button", { class: "small", onclick: discardMissing, title: "save the decision without them" }, "forget them")));
    }
    clear(hist);
    if (a.review.history && a.review.history.length) {
      const ul = h("ul", { class: "history" });
      for (const ev of [...a.review.history].reverse()) {
        ul.append(h("li", null, verdictBadge(ev.verdict), " ", h("span", { class: "muted" }, ago(ev.ts)),
          ev.drop_paragraphs.length ? ` · ${ev.drop_paragraphs.length} ¶ dropped` : "", ev.note ? ` · “${ev.note.slice(0, 60)}”` : ""));
      }
      hist.append(h("details", { style: { marginTop: "8px" } }, h("summary", { class: "small" }, `History (${a.review.events} events, newest first)`), ul));
    }
  }

  function applyResult(res) {
    a.review = res.review;
    const ev = res.review.event;
    st.verdict = ev ? ev.verdict : null;
    st.note = ev ? ev.note : "";
    st.drops = new Set((res.review.drops || []).map((d) => d.para));
    st.lastSaved = ev ? ev.ts : null;
    if (document.activeElement !== note) note.value = st.note || "";
  }

  // Saves run one at a time, and each sends the full decision as it is when the save starts,
  // so quick successive changes (a note, then a verdict) can never land out of order.
  let queue = Promise.resolve();
  let pending = 0;
  function persist(request) {
    pending++;
    st.saving = true; drawPanel();
    const run = queue.then(async () => {
      try {
        const res = await request();
        a.review = res.review;
        if (pending === 1) { applyResult(res); drawDoc(); }
        return res;
      } catch (e) {
        toast(`Not saved: ${e.message}`, "error");
        return null;
      } finally {
        pending--;
        st.saving = pending > 0;
        drawPanel();
      }
    });
    queue = run;
    return run;
  }
  const saveReview = () => persist(() => api(dsApi(ds, "review"), { body: {
    v, id: a.id, text_sha1: a.text_sha1, verdict: st.verdict, note: note.value, drop_paras: [...st.drops] } }));
  async function setVerdict(val) {
    st.verdict = val; drawPanel();
    const res = await saveReview();
    if (!res) return;
    toast(val ? `Saved: ${val}` : "Verdict cleared");
    if (auto && val) advance();
  }
  async function saveNote() {
    if ((note.value || "") === (st.note || "")) return;
    st.note = note.value;
    if (await saveReview()) toast("Note saved");
  }
  async function toggleDrop(i) {
    if (st.drops.has(i)) st.drops.delete(i); else st.drops.add(i);
    drawDoc(); drawPanel();
    await saveReview();
  }
  async function discardMissing() {
    const res = await persist(() => api(dsApi(ds, "review"), { body: {
      v, id: a.id, text_sha1: a.text_sha1, verdict: st.verdict, note: note.value, drop_paras: [...st.drops], discard_missing: true } }));
    if (res) toast("Saved without the paragraphs that are not in this text");
  }
  async function undo() {
    const res = await persist(() => api(dsApi(ds, "undo"), { body: { v, id: a.id } }));
    if (res) toast("Undone: a new event restores the previous decision");
  }
  function flash(i, smooth = true) {
    const el = doc.querySelector(`#p${i}`);
    if (!el) return false;
    el.scrollIntoView({ behavior: smooth ? "smooth" : "auto", block: "center" });
    el.classList.add("flash");
    setTimeout(() => el.classList.remove("flash"), 1200);
    return true;
  }
  // ?para=N: the linked paragraph stays marked, and the page opens scrolled to it
  function target(i) {
    for (const el of doc.querySelectorAll(".para.target")) el.classList.remove("target");
    const el = doc.querySelector(`#p${i}`);
    if (!el) { toast(`This article has no paragraph ${i} (it has ${a.paragraphs.length})`, "error"); return; }
    el.classList.add("target");
    flash(i, false);
  }

  // metadata + annotations
  const metaCard = h("div", { class: "card" }, h("h2", null, "Metadata"), fieldsTable(a.meta, ["text"]));
  const links = h("div", { class: "small stack" });
  if (a.links.revision) links.append(h("div", null, h("a", { href: a.links.revision, target: "_blank", rel: "noopener" }, "This revision on Wikipedia ↗")));
  if (a.links.url) links.append(h("div", null, h("a", { href: a.links.url, target: "_blank", rel: "noopener" }, "Current page ↗")));
  const rb = a.removed_blocks; // blocks the build cut out of this article (removed-blocks.jsonl)
  if (rb && rb.total) {
    const vq = v ? `v=${encodeURIComponent(v)}&` : "";
    links.append(h("div", null, h("a", { href: `#/d/${encodeURIComponent(ds)}/excluded?${vq}tab=blocks&id=${a.id}` },
      `${rb.total} block${rb.total === 1 ? "" : "s"} cut out by the build`), h("span", { class: "muted" }, ` (${rb.by_reason.map(([r, n]) => `${r} ${n}`).join(", ")})`)));
  }
  if (a.links.file_abs) {
    links.append(h("div", null, h("span", { class: "muted" }, "Markdown file: "), h("code", { style: { wordBreak: "break-all" } }, a.links.file),
      a.links.file_exists ? null : h("span", { class: "chip bad" }, "missing"), " ",
      h("button", { class: "small", onclick: () => navigator.clipboard.writeText(a.links.file_abs).then(() => toast("Path copied")) }, "copy path")));
  }
  metaCard.append(links);
  const annUnscored = a.ann_unscored || [];
  const annCards = Object.entries(a.annotations).map(([name, fields]) => h("div", { class: "card" },
    h("h2", null, name, a.ann_stale[name] ? h("span", { class: "chip bad", title: "text_sha1 differs from this text" }, "stale") : null),
    fieldsTable(fields, [], annUnscored.includes(name) ? name : null)));
  const paraSummary = Object.entries(a.para_annotations).map(([name, pa]) => h("div", { class: "card small" },
    h("h2", null, `${name} (paragraphs)`, pa.stale ? h("span", { class: "chip bad" }, "stale") : null),
    h("div", null, `${Object.keys(pa.rows).length} of ${a.paragraphs.length} paragraphs have a row.`),
    pa.unscored ? h("div", null, h("span", { class: "chip warn" }, `${pa.unscored} not scored`), ` ${pa.primary} is null: the text is new since the last scoring run.`) : null,
    pa.columns.some((c) => c.from) ? h("div", { class: "muted" }, `Joined from ${[...new Set(pa.columns.filter((c) => c.from).map((c) => c.from))].join(", ")}: `,
      pa.columns.filter((c) => c.from).map((c) => c.name).join(", "), ".") : null,
    pa.description ? h("div", { class: "muted" }, pa.description) : null));

  const side = h("aside", { class: "side" }, panel, ...annCards, ...paraSummary, metaCard);
  const left = h("section", null, docTools, doc);
  main.append(head, titleRow, h("div", { class: "article-layout" }, left, side));
  drawDoc(); drawPanel(); drawLegend();
  if (targetPara !== null) requestAnimationFrame(() => target(targetPara));

  // ---- position in the list -----------------------------------------------------------------
  async function loadPos() {
    if (!from || from === "excluded") { posText.textContent = ""; return; } // not a list of articles
    if (from === "review-first") return loadReviewFirstPos();
    const p = new URLSearchParams(ctx);
    p.set("id", a.id);
    try {
      pos = await api(dsApi(ds, "position", p));
    } catch (e) { posText.textContent = `list: ${e.message}`; return; }
    prevBtn.disabled = !pos.prev; nextBtn.disabled = !pos.next;
    posText.textContent = pos.index === null ? `not in this list (${num(pos.total)} articles)` : `${num(pos.index + 1)} of ${num(pos.total)} · ${num(pos.reviewed)} with a verdict`;
  }
  // Review first: prev/next walk that list (with its filters); each item opens at its paragraph
  async function loadReviewFirstPos() {
    let data;
    try {
      data = await api(dsApi(ds, "review-first", new URLSearchParams(v ? { v } : {})));
    } catch (e) { posText.textContent = `list: ${e.message}`; return; }
    const items = (data.segments || []).filter((x) => x.kind === "item");
    const rank = Number(params.get("rank"));
    const shown = items.filter((it) => it.rank === rank || itemPasses(it, ctx));
    const k = shown.findIndex((it) => it.rank === rank);
    const href = (it) => itemHref(ds, it, ctx);
    const unrev = shown.filter((it, i) => i > k && !it.reviewed && it.rank !== rank);
    pos = {
      prev: k > 0 ? href(shown[k - 1]) : null,
      next: k >= 0 ? (k + 1 < shown.length ? href(shown[k + 1]) : null) : (shown[0] ? href(shown[0]) : null),
      next_unreviewed: unrev.length ? href(unrev[0]) : null,
    };
    prevBtn.disabled = !pos.prev; nextBtn.disabled = !pos.next;
    const it = shown[k];
    posText.textContent = k < 0 ? `not in Review first (${shown.length} items)`
      : `Review first #${rank} (${it.type || "item"}) · ${k + 1} of ${shown.length} · ${shown.filter((x) => x.reviewed).length} reviewed`;
  }
  // target: an article id (same list parameters) or a full "#/..." link
  function goTo(targetId) {
    if (!targetId) { toast("End of the list"); return; }
    if (typeof targetId === "string" && targetId.startsWith("#")) { location.hash = targetId.slice(1); return; }
    const p = new URLSearchParams(params);
    p.delete("raw");
    p.delete("para");
    location.hash = `/d/${encodeURIComponent(ds)}/a/${targetId}?${p}`;
  }
  async function advance() {
    await loadPos();
    if (pos && pos.next_unreviewed) goTo(pos.next_unreviewed);
    else toast("Nothing left to review in this list");
  }
  const posReady = loadPos();

  return {
    keys(e) {
      const k = e.key;
      // j/k wait for the list position if it is still loading
      if (k === "j") { posReady.then(() => goTo(pos && pos.next)); return true; }
      if (k === "k") { posReady.then(() => goTo(pos && pos.prev)); return true; }
      if (k === "1" || k === "K") { setVerdict("keep"); return true; }
      if (k === "2" || k === "d") { setVerdict("drop"); return true; }
      if (k === "3" || k === "f") { setVerdict("fix"); return true; }
      if (k === "0") { setVerdict(null); return true; }
      if (k === "u") { undo(); return true; }
      if (k === "n") { note.focus(); return true; }
      if (k === "r") { raw = !raw; drawDoc(); return true; }
      if (k === "b") { location.hash = listHref.slice(1); return true; }
      return false;
    },
    destroy() { if (document.activeElement === note) saveNote(); },
  };
}

function listParams(ctx) { return ctx.toString(); }
function safeDecode(u) { try { return decodeURI(u); } catch { return u; } }

// unscoredKey: the primary column of an annotation with no score for this article (shown as "not scored")
function fieldsTable(obj, skip = [], unscoredKey = null) {
  const t = h("table", { class: "kv fields" });
  for (const [k, v] of Object.entries(obj)) {
    if (skip.includes(k)) continue;
    const isLink = typeof v === "string" && /^https?:\/\//.test(v);
    const cell = k === unscoredKey && v === null ? h("span", { class: "muted" }, "not scored")
      : isLink ? h("a", { href: v, target: "_blank", rel: "noopener" }, safeDecode(v).slice(0, 80)) : value(v);
    t.append(h("tr", null, h("td", null, k), h("td", { class: "v" }, cell)));
  }
  return t;
}

