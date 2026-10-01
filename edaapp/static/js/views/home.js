import { h, num, bytes, when, pct } from "../util.js";
import { progressBar } from "../charts.js";

export async function render({ main, reloadHome }) {
  const data = await reloadHome();
  main.append(h("h1", null, "Datasets"),
    h("p", { class: "muted" }, "Data root ", h("code", null, data.data_root),
      ". A dataset is any folder here with a JSONL (or gzipped JSONL) or Parquet table that has id and text columns."));
  // where the datasets come from: this repository (the default), or its Hugging Face copy (--hub);
  // nothing for a data root given with --data
  for (const s of data.sources || []) {
    if (s.kind === "repository") {
      main.append(h("p", { class: "muted small" }, h("code", null, s.name), " is this repository, ",
        h("code", null, s.path), s.commit ? ` (commit ${s.commit.slice(0, 7)}, plus any changes not committed).` : "."));
      continue;
    }
    main.append(h("p", { class: s.offline ? "card warnings small" : "muted small" },
      h("code", null, s.name), " is the Hugging Face dataset ",
      h("a", { href: s.url, target: "_blank", rel: "noopener" }, `${s.repo_id} ↗`),
      ` at ${s.revision} (commit ${s.commit.slice(0, 7)})`,
      s.offline ? `. The Hub could not be reached (${s.offline}), so this is the cached snapshot, which may be out of date.` : "."));
  }
  if (!data.datasets.length) {
    main.append(h("div", { class: "card warnings" }, "No datasets found under this data root."));
  }
  const grid = h("div", { class: "grid cols-2" });
  for (const d of data.datasets) grid.append(card(d));
  main.append(grid);
  if (data.other_dirs.length) {
    main.append(h("p", { class: "muted small" }, "Not datasets (no top-level table with id and text): ",
      data.other_dirs.join(", ")));
  }
  const rf = data.reviews_file;
  main.append(h("p", { class: "muted small" }, "Review decisions are appended to ", h("code", null, rf.path),
    ` (${num(rf.events)} events${rf.bad_lines ? `, ${rf.bad_lines} unreadable lines skipped` : ""}).`));
}

function card(d) {
  const base = `#/d/${encodeURIComponent(d.name)}`;
  const el = h("div", { class: "card stack" });
  el.append(h("h2", null, h("a", { href: base }, d.name),
    h("span", { class: "chip" }, d.versions.length === 1 ? "1 version" : `${d.versions.length} versions`)));
  if (d.error) {
    el.append(h("div", { class: "warnings card" }, d.error));
    return el;
  }
  const v = d.versions[0];
  el.append(h("div", { class: "tiles" },
    tile("Articles", num(d.articles)),
    tile("Words", num(d.words)),
    tile("Newest version", v.stem, `${v.fmt}${v.not_read.length ? ` (${v.not_read.join(", ")} not read)` : ""} · ${bytes(v.size)}`),
    tile("Built", d.built || "–", `corpus file ${when(d.corpus_mtime)}`)));
  // annotations
  const anns = h("div", { class: "row" }, h("span", { class: "muted small" }, "Annotations:"));
  if (!d.annotations.length) anns.append(h("span", { class: "chip" }, "none yet"));
  for (const a of d.annotations) {
    const cls = a.stale ? "chip bad" : a.warnings ? "chip warn" : "chip ok";
    const tip = a.stale ? `${num(a.stale)} stale` : a.stale === 0 ? "fresh" : a.warnings ? `${a.warnings} warning(s)` : "ok";
    anns.append(h("span", { class: cls, title: `${num(a.matched)} articles matched, ${num(a.missing)} missing` }, `${a.key}: ${tip}`));
  }
  el.append(anns);
  if (d.notes && d.notes.length) el.append(h("div", { class: "card warnings small" }, d.notes.join("; ")));
  // reviews
  const r = d.reviews;
  el.append(h("div", null,
    h("div", { class: "row small" }, h("strong", null, "Review progress"), h("span", { class: "muted" },
      `${num(r.reviewed)} of ${num(d.articles)} articles have a verdict (${pct(r.reviewed, d.articles)}) · keep ${num(r.keep)} · drop ${num(r.drop)} · fix ${num(r.fix)} · ${num(r.with_paragraphs)} with dropped paragraphs`)),
    progressBar([{ n: r.keep, cls: "p-keep", label: "keep" }, { n: r.drop, cls: "p-drop", label: "drop" },
      { n: r.fix, cls: "p-fix", label: "fix" }], d.articles)));
  el.append(h("div", { class: "row" },
    h("a", { class: "btn", href: base }, "Overview"),
    d.methodology ? h("a", { class: "btn", href: `${base}/methodology` }, "Methodology") : null,
    d.review_first ? h("a", { class: "btn", href: `${base}/review-first` }, "Review first") : null,
    h("a", { class: "btn", href: `${base}/browse` }, "Browse"),
    h("a", { class: "btn", href: `${base}/queue` }, "Review queue"),
    h("a", { class: "btn", href: `${base}/patterns` }, "Patterns"),
    d.excluded || d.removed_blocks ? h("a", { class: "btn", href: `${base}/excluded${d.excluded ? "" : "?tab=blocks"}` },
      d.excluded ? `Excluded (${num(d.excluded_rows)})` : "Removed blocks") : null,
    d.reports.length ? h("a", { class: "btn", href: `${base}/reports` }, `Reports (${d.reports.length})`) : null));
  return el;
}

function tile(label, value, sub) {
  return h("div", { class: "tile" }, h("div", { class: "label" }, label), h("div", { class: "value" }, value),
    sub ? h("div", { class: "sub" }, sub) : null);
}
