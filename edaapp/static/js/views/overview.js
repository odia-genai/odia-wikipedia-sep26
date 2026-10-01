import { h, api, dsApi, num, bytes, when, value, openDialog, toast } from "../util.js";
import { histogram, barList } from "../charts.js";

export async function render({ main, ds, params }) {
  const v = params.get("v");
  const vp = new URLSearchParams(v ? { v } : {});
  const o = await api(dsApi(ds, "overview", vp));
  const browse = (extra) => {
    const p = new URLSearchParams(vp);
    for (const [k, val] of extra) p.append(k, val);
    location.hash = `/d/${encodeURIComponent(ds)}/browse?${p}`;
  };

  main.append(h("div", { class: "row" }, h("h1", null, ds), h("span", { class: "chip" }, o.file),
    h("span", { class: "muted small" }, `${bytes(o.file_size)} · modified ${when(o.file_mtime)}`),
    o.file_not_read.length ? h("span", { class: "chip warn", title: "the same version in another format, older" }, `not read: ${o.file_not_read.join(", ")}`) : null));
  main.append(h("div", { class: "tiles", style: { margin: "8px 0 16px" } },
    tile("Articles", num(o.articles)), tile("Words", num(o.words)), tile("Characters", num(o.chars)),
    o.build && o.build.built ? tile("Built", o.build.built, o.build_file) : null,
    tile("Annotations", String(o.annotations.length), o.annotations.map((a) => a.key).join(", ") || "none")));

  if (o.warnings.length) {
    main.append(h("div", { class: "card warnings", style: { marginBottom: "16px" } }, h("strong", null, "Warnings"),
      h("ul", null, o.warnings.map((w) => h("li", null, w.annotation ? h("strong", null, w.annotation + ": ") : null, w.text)))));
  }

  // annotations
  if (o.annotations.length) {
    const t = h("table", { class: "data" }, h("thead", null, h("tr", null,
      ["Annotation", "Level", "Rows", "Articles matched", "Missing", "Stale", "Other ids", "Columns", "File", "Description"].map((x) => h("th", null, x)))));
    const tb = h("tbody");
    for (const a of o.annotations) {
      const stale = a.stale === null ? h("span", { class: "muted", title: "no text_sha1 column" }, "unknown")
        : a.stale ? h("span", { class: "chip bad" }, num(a.stale)) : h("span", { class: "chip ok" }, "0");
      tb.append(h("tr", { "data-ann": a.key, id: `ann-${a.key}` },
        h("td", null, h("strong", null, a.key)), h("td", null, a.level), h("td", { class: "num" }, num(a.rows)),
        h("td", { class: "num" }, num(a.matched)), h("td", { class: "num" }, num(a.missing)), h("td", { class: "num" }, stale),
        h("td", { class: "num" }, num(a.orphans)),
        h("td", { class: "small" }, a.columns.filter((c) => !c.from).map((c) => c.name).join(", "), joinNotes(a)),
        h("td", { class: "small nowrap" }, a.file, h("br"), h("span", { class: "muted" }, when(a.mtime)),
          a.not_read.length ? h("div", { class: "muted", title: "the same annotation in another format, older" }, `not read: ${a.not_read.join(", ")}`) : null),
        h("td", { class: "small" }, (a.meta && a.meta.description) || "", a.meta && a.meta.source ? h("div", { class: "muted" }, `source: ${a.meta.source}`) : null)));
    }
    t.append(tb);
    main.append(h("h2", null, "Annotations"), h("div", { class: "table-wrap", style: { marginBottom: "16px" } }, t));
  }

  // build stats and the pages left out
  const cards = h("div", { class: "grid cols-2", style: { marginBottom: "16px" } });
  if (o.build) {
    cards.append(h("div", { class: "card" }, h("h2", null, "Build statistics ", h("span", { class: "muted small" }, o.build_file)), kvTable(o.build)));
  }
  const ex = o.excluded;
  const exHref = (extra) => {
    const q = new URLSearchParams([...vp, ...extra]).toString();
    return `#/d/${encodeURIComponent(ds)}/excluded${q ? "?" + q : ""}`;
  };
  if (ex && ex.pages) {
    // excluded.jsonl lists every page left out; the Excluded view filters and links them
    const pages = ex.pages;
    cards.append(h("div", { class: "card" }, h("h2", null, `Pages left out (${num(pages.rows)})`),
      h("p", { class: "muted small" }, "From ", h("code", null, pages.file), ". Click a reason to see its pages in the Excluded view."),
      barList(pages.reasons, { total: pages.rows, limit: 30, onPick: (reason) => { location.hash = exHref([["reason", reason === null ? "__null__" : reason]]).slice(1); } }),
      ex.mismatch.length ? h("p", { class: "muted small" }, `The build file's counts differ: ${ex.mismatch.map(([k, a, b]) => `${k}: ${num(a)} in ${o.build_file}, ${num(b)} in ${pages.file}`).join("; ")}.`) : null,
      h("div", { class: "row", style: { marginTop: "8px" } },
        h("a", { class: "btn", href: exHref([]) }, "All excluded pages →"),
        ex.blocks ? h("a", { class: "btn", href: exHref([["tab", "blocks"]]) }, `Removed blocks (${num(ex.blocks.rows)}) →`) : null)));
  } else if (o.dropped) {
    // no excluded.jsonl: the build file's counts (older builds also listed the titles)
    const opts = Object.entries(o.dropped).sort((a, b) => b[1] - a[1]);
    const total = opts.reduce((s, x) => s + x[1], 0);
    cards.append(h("div", { class: "card" }, h("h2", null, `Pages left out (${num(total)})`),
      h("p", { class: "muted small" }, o.dropped_titles ? "Click a reason to see its titles." : `Counts from ${o.build_file}; excluded.jsonl, which would list the pages, is not there.`),
      barList(opts, { total, onPick: o.dropped_titles ? (reason) => showDropped(ds, vp, reason) : null }),
      ex && ex.blocks ? h("div", { class: "row", style: { marginTop: "8px" } }, h("a", { class: "btn", href: exHref([["tab", "blocks"]]) }, `Removed blocks (${num(ex.blocks.rows)}) →`)) : null));
  }
  if (cards.children.length) main.append(cards);

  // numeric distributions
  if (o.distributions.length) {
    main.append(h("h2", null, "Distributions"),
      h("p", { class: "muted small" }, "Click a bar to browse the articles in that bin. Log-spaced bins are marked."));
    const grid = h("div", { class: "grid cols-3", style: { marginBottom: "16px" } });
    for (const d of o.distributions) {
      grid.append(distCard(d, (lo, hi, last) => browse([[`min.${d.key}`, trim(lo, d.integer)], [`max.${d.key}`, trim(last ? hi : prevVal(hi, d.integer), d.integer)]])));
    }
    main.append(grid);
  }
  if (o.para_distributions.length) {
    main.append(h("h2", null, "Paragraph-level distributions"));
    const grid = h("div", { class: "grid cols-3", style: { marginBottom: "16px" } });
    for (const d of o.para_distributions) grid.append(distCard(d, null));
    main.append(grid);
  }

  // categorical, boolean and list columns
  if (o.categories.length) {
    main.append(h("h2", null, "Categories, flags and lists"));
    const grid = h("div", { class: "grid cols-3" });
    for (const c of o.categories) {
      const total = c.options.reduce((s, x) => s + x[1], 0);
      const pick = c.key.includes(".paragraphs.") ? null : (val) => {
        if (c.kind === "bool") browse(val === null || val === "__null__" ? [[`null.${c.key}`, "1"]] : [[`is.${c.key}`, String(val)]]);
        else if (c.kind === "list") browse([[`any.${c.key}`, val]]);
        else browse([[`in.${c.key}`, val === null ? "__null__" : val]]);
      };
      grid.append(h("div", { class: "card chart-card" },
        h("h3", null, c.key, " ", h("span", { class: "chip" }, c.kind)),
        h("div", { class: "stats" }, c.kind === "list" ? `${num(c.values)} values, ${num(c.distinct)} distinct` : `${num(c.distinct)} distinct`,
          c.nulls ? ` · ${num(c.nulls)} missing` : "", c.description ? ` · ${c.description}` : ""),
        barList(c.options, { total: c.kind === "list" ? null : total, onPick: pick, limit: 15 })));
    }
    main.append(grid);
  }
  main.append(h("p", { class: "muted small" }, `computed in ${num(o.elapsed_ms)} ms`));
  // ?ann=<key> (links from documents to annotations/<key>.parquet): show that annotation's row
  const ann = params.get("ann");
  if (ann) {
    const row = main.querySelector(`tr[data-ann="${CSS.escape(ann)}"]`);
    if (row) {
      row.classList.add("target");
      requestAnimationFrame(() => row.scrollIntoView({ block: "center" }));
    } else {
      toast(`No annotation ${ann} in this dataset (yet)`);
    }
  }
}

// columns an annotation's sidecar joins from another file ("joins"): which, from where, how many rows matched
function joinNotes(a) {
  return (a.joins || []).map((j) => h("div", { class: "muted", title: j.description || "" },
    j.columns.length ? `+ ${j.columns.join(", ")} from ` : `not joined: ${j.declared.join(", ")} from `, h("code", null, j.path), ` on ${j.on}`,
    j.matched !== undefined ? ` (${num(j.matched)} of ${num(j.matched + j.unmatched)} rows found${j.unmatched ? `, ${num(j.unmatched)} not` : ""})` : ""));
}

function trim(x, integer) {
  if (integer) return String(Math.round(x));
  return String(Number(x.toPrecision(6)));
}
function prevVal(hi, integer) { return integer ? Math.ceil(hi) - 1 : hi; }

function distCard(d, onPick) {
  return h("div", { class: "card chart-card" },
    h("h3", null, d.key, d.log ? h("span", { class: "chip", style: { marginLeft: "6px" } }, "log bins") : null),
    h("div", { class: "stats" },
      `n ${num(d.count)} · median ${num(d.median)} · p10 ${num(d.p10)} · p90 ${num(d.p90)} · min ${num(d.min)} · max ${num(d.max)}`,
      d.nulls ? ` · ${num(d.nulls)} missing` : ""),
    histogram(d, { onPick }),
    d.description ? h("div", { class: "muted small" }, d.description) : null);
}

async function showDropped(ds, vp, reason) {
  const p = new URLSearchParams(vp);
  p.set("reason", reason);
  const r = await api(dsApi(ds, "dropped", p));
  const list = h("ol", { class: "odia", style: { columns: "2", fontSize: "14px" } });
  for (const t of r.titles) {
    list.append(h("li", null, h("a", { href: `https://or.wikipedia.org/wiki/${encodeURIComponent(t.split(" = ")[0].replace(/ /g, "_"))}`, target: "_blank", rel: "noopener" }, t)));
  }
  openDialog(`Left out: ${reason} (${r.titles.length})`, h("div", null,
    h("p", { class: "muted small" }, "Titles as recorded in the build file; links open Odia Wikipedia."), list));
}

// key/value rows; nested objects become nested tables
function kvTable(obj) {
  const t = h("table", { class: "kv small" });
  for (const [k, val] of Object.entries(obj)) {
    const cell = val && typeof val === "object" && !Array.isArray(val) ? kvTable(val) : value(val);
    t.append(h("tr", null, h("td", null, k), h("td", null, cell)));
  }
  return t;
}

function tile(label, val, sub) {
  return h("div", { class: "tile" }, h("div", { class: "label" }, label), h("div", { class: "value" }, val),
    sub ? h("div", { class: "sub" }, sub) : null);
}

