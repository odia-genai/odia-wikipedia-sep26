# edaapp

The review web app of this dataset, the Odia Wikipedia corpus (`odia-wikipedia`): browse and search
the articles, look at the topic, machine-translation and bits-per-byte annotations, see which pages
and blocks the build left out and why, and record which articles to keep, drop or fix and which
paragraphs to drop. It lives in the dataset's repository, in `edaapp/`, and by default serves the
repository itself, the folder above it. Decisions go to `../reviews/reviews.jsonl`, the review log
the build reads.

## Running it

In a clone. Nothing needs building first: the corpus is committed as
`orwiki-20260901-trainingready.jsonl.gz`.

```bash
git clone https://github.com/odia-genai/odia-wikipedia-sep26
cd odia-wikipedia-sep26/edaapp
uv run edaapp                     # this repository: http://127.0.0.1:8765/
uv run edaapp --hub               # the published copy on Hugging Face, at main
uv run edaapp --hub-revision 52a4f35   # a branch, tag or commit of it (implies --hub)
uv run edaapp --data ~/other      # any other data root: one folder per dataset
uv run edaapp --port 8766 --state .cache/try-state   # a trial: decisions go to .cache/try-state/
```

`uv run` sets up the environment (`edaapp/.venv`) on first use. The app serves on 127.0.0.1 only.

`--state DIR` sets the folder of `reviews.jsonl`. The default is the repository's `reviews/`, in
every mode: with `--hub` too, because the Hub copy is the same dataset and its decisions belong in
the same log. Any other folder must be inside `edaapp/` (use one under `.cache/`, which is
git-ignored, for trials and demos); anything else is refused.

The first request loads the corpus and its annotations into memory (1.5 s, measured 2026-10-01:
0.41 s of it is the gzipped corpus, 0.17 s the score store that `bpb.paragraphs` joins in, see
*Sidecar joins* below, and most of the rest is DuckDB inferring the types of the JSON lines files),
and the Overview's first computation takes another 0.75 s. Everything after that is interactive:
Browse and Excluded queries take a few milliseconds, and a search over the full text of all 18,683
articles takes 0.03–0.25 s.

## Data sources

### This repository (the default)

The folder above `edaapp/` is served as one dataset named `odia-wikipedia`: `REPO_DATASET` in
`src/edaapp/paths.py`, the same as `DATASET` in `../prepare.py` and the `dataset` of every review
event. It is never named after the clone's folder, which is `odia-wikipedia-sep26` or whatever the
clone was called. The app makes `.cache/repo-root/odia-wikipedia` (git-ignored) a symlink to the
repository and serves `.cache/repo-root/` as its data root. The terminal and the Home page name the
repository's path and its checked-out commit; the files shown are the working tree, changes not
committed included.

The corpus is committed gzipped, and `uv run pipeline.py` writes the plain `orwiki-*.jsonl` next to
it (git-ignored). After a rebuild both exist: the newer file is read, and the Overview names the
other as not read (see *One table in several formats*). The app reloads what changed while it runs,
so a rebuild shows up without a restart.

Inside the repository the app leaves out what is not data. `edaapp/` is never a dataset, and the
Files view hides it along with `.git/` and every other dot-folder. Discovery only looks at the top
level, `annotations/`, `quality/` and the files that sidecars join, so nothing in `edaapp/` or
`reviews/` is read as a table or an annotation, and appending a decision is not a change of the data.

### The published copy on Hugging Face (`--hub`)

`--hub` serves the dataset as published on Hugging Face,
[`fastpixels/odia-wikipedia-sep26`](https://huggingface.co/datasets/fastpixels/odia-wikipedia-sep26),
named in `src/edaapp/hub.py`:

```python
HUB_DATASETS = {REPO_DATASET: "fastpixels/odia-wikipedia-sep26"}
```

At start, for each one:

1. The Hub is asked for the revision (`--hub-revision`, default `main`), and `snapshot_download`
   brings the Hugging Face cache up to date with it. The cache is the normal one
   (`~/.cache/huggingface/hub`, or wherever `HF_HOME` or `HF_HUB_CACHE` point), outside the
   repository. Only changed files are downloaded: the first start fetches about 100 MB (46 files,
   6 s), later ones only check (0.7 s).
2. `raw/html/` is skipped (149 MB of rendered HTML, the build's input). Nothing in the app reads
   it: discovery looks at the top level, `annotations/` and `quality/`, and the one file a sidecar
   joins is `raw/bpb/scores.jsonl.gz`. The Files view therefore doesn't list it.
3. `.cache/hub-root/odia-wikipedia` (git-ignored) is made a symlink to the snapshot folder, and
   `.cache/hub-root/` is the data root. The app never writes through the link: the path guard
   resolves it, and it leads out of the folders the app may write in.

The terminal and the Home page say which revision is shown, with its commit. When the Hub can't be
reached (no network, a timeout, `HF_HUB_OFFLINE=1`), the cached snapshot of the revision is used
instead (`local_files_only`), and both say so, since it may be out of date. With no cached copy the
app exits with a message saying so. A revision or repository that the Hub says doesn't exist is an
error, not a fallback. The dataset is public, so no token is needed (huggingface_hub warns that
unauthenticated requests have lower rate limits; that is harmless here). A newer revision on the
Hub is picked up at the next start, not while the app runs.

### Any other data root (`--data PATH`)

A folder holding one folder per dataset, each named after its dataset (see *Discovery conventions*).
Decisions still go to the repository's `reviews/reviews.jsonl` unless `--state` says otherwise.
Events carry their dataset's name and the build applies only those of `odia-wikipedia`, but a log
of another dataset's decisions belongs elsewhere: give it a `--state` folder of its own. To serve
another checkout of this dataset this way, name its folder `odia-wikipedia`.

`--hub` and `--data` exclude each other, and so do `--hub-revision` and `--data`.

## The review loop

1. **Decide in the app**: keep, drop or fix an article, drop paragraphs, or act in bulk from
   Patterns. Each decision is appended to `reviews/reviews.jsonl` at once.
2. **Rebuild** at the repository's root: `uv run pipeline.py` (build, annotate, scores, checks; a
   few minutes). The build applies the latest event per article (see *The review log* below). The
   running app picks up the rebuilt files.
3. **Commit and push** the review log and what the rebuild changed, to
   <https://github.com/odia-genai/odia-wikipedia-sep26>.
4. **Upload the dataset to Hugging Face, without `edaapp/`**: `uv run publish_hub.py` at the root
   uploads the files git tracks at HEAD and leaves `edaapp/` out. By hand, the same is roughly:

   ```bash
   hf upload fastpixels/odia-wikipedia-sep26 . . --repo-type dataset \
     --exclude "edaapp/*" --exclude "orwiki-*.jsonl"
   ```

   The second pattern leaves out the plain corpus a rebuild writes, which git ignores; other
   untracked files would go up too, which `publish_hub.py` avoids.

The app is published on GitHub only; the Hugging Face copy is the data.

## Views

| View | What it does |
|---|---|
| **Home** | Where the data comes from (this repository and its commit, or the Hugging Face dataset and commit, with a warning when the Hub could not be reached and the cached snapshot is used). One card per dataset: articles, words, newest version (and which file is read if it exists in several formats), build date, annotations (fresh, stale or with warnings), review progress, and links to its pages. |
| **Methodology** | The dataset's `METHODOLOGY.md`, shown only when the file exists. It has a sticky table of contents that marks the section in view, an anchor on every heading (`?h=<heading id>`), GFM tables, KaTeX math and linked references (see below). "Last updated" comes from the file's mtime, and the page reloads in place, keeping your section, when the file changes. |
| **Review first** | `quality/review-first.md` as an actionable list, shown only when the file exists. Each item (`` 1. **title** (id N, para P, `type`) ``) links to its article, opened at the flagged paragraph. It shows the article's current verdict (keep/drop/fix, "¶ dropped" when just the flagged paragraph is marked, or none), live, with Keep/Drop/Fix and "Drop ¶P" buttons. Filter by failure type and hide reviewed; the filters are in the URL, and j/k in the article view walks the filtered list. It flags a paragraph that moved, changed or is gone since scoring, and articles whose text changed. It links to the Review queue (the `*.review_rank` column). If the items can't be parsed, the file is shown as a document instead. |
| **Overview** | `?ann=<key>` scrolls to that annotation's row (links to `annotations/<key>.jsonl`, `.jsonl.gz` or `.parquet` land here). Build statistics from `<stem>-build.json`. Pages left out, by reason, from `excluded.jsonl`: a reason opens the Excluded view filtered to it, and counts that differ from the build file's `dropped` are listed. (Without `excluded.jsonl` the build file's `dropped` counts are shown; an older build file's `dropped_titles` still opens a list of titles.) A histogram for every numeric column, including annotation columns (log bins when skewed); click a bar to browse that bin. Value counts for boolean, categorical and list columns (click to filter). Paragraph-level distributions, with the count of null values (e.g. paragraphs not scored yet). Annotation status: rows, matched, missing, stale, ids not in the corpus, the columns (joined ones with their file and how many rows found a match), and the file read (with the older copy that is not read, during a conversion). Warnings. |
| **Browse** | A server-side filtered, sorted and paginated table. Filters come from the schema: boolean → any/true/false; numeric and ISO date → min/max; string with few values → multi-select; list of strings → any-of; other strings → contains. Title search and full-text search (substring or RE2 regex, optional ignore-case) with match counts and keyword-in-context snippets. You can pick the columns, sort by any column, and put unreviewed articles first. All state is in the URL. "Review these one by one" walks the list in the article view. |
| **Article** | `?para=N` opens the article scrolled to paragraph N and keeps it marked. Server-rendered Markdown (CommonMark + GFM tables, raw HTML disabled) with KaTeX math. Raw Markdown toggle. Every metadata and annotation field, a stale flag per annotation, links to the exact Wikipedia revision and the current page, the blocks the build cut out of it (a link to the Excluded view's Removed blocks tab), and the `.md` path (copy button) when the corpus has a `file` column (the Markdown export is optional). Paragraph shading from a paragraph annotation (e.g. bits per byte), scaled from the corpus-wide 2nd to 98th percentile, with a legend and values on hover (joined columns included). A paragraph whose primary value is null (see *Sidecar joins*) is marked **not scored** (dashed gutter, not shaded), and so is an article-level one. Review panel: keep/drop/fix, a note, per-paragraph drop toggles, undo, history. Previous/next within the list you came from. |
| **Review queue** | Articles ordered by any numeric column; `…review_rank` columns are offered first (1 = review first). Unreviewed articles come first, with a progress bar. "Start reviewing" opens a one-at-a-time mode: each verdict saves and moves on to the next unreviewed article. |
| **Patterns** | *Repeated paragraphs*: every paragraph normalised (digit runs → `N`, the article's own title → `TITLE`, also the title without a trailing "(…)"), grouped across articles, with counts, variants and examples. Computed on first use and cached until the corpus file changes. *Regex search over paragraphs*: hit counts by paragraph kind, with context. Both have **bulk actions** (drop the matching paragraphs, or drop the matching articles) behind a confirmation that lists every event and the exact JSON lines to be appended. |
| **Excluded** | Shown when `excluded.jsonl` or `removed-blocks.jsonl` exists. *Excluded pages*: counts per reason (click to filter), then a table filtered by reason, searched by title or detail (or an exact id or revid), sortable and paged. Each page links to the revision in the dump (`…/w/index.php?oldid=<revid>`) and to the current page (`…/?curid=<id>`); a detail that names an id ("same text as id 1234 (…)", a duplicate) links to that article when it is in the corpus. A page that is also in the corpus (the two files come from different builds) is flagged. *Removed blocks*: counts per reason and kind, filters for reason, kind, article (`id=`) and whether the article stayed in the corpus, a title/text search, the block's text, and a link to the article (or, for an article left out as a whole, to its excluded entry). The state is in the URL (`?tab=blocks&reason=…&q=…&page=…`). |
| **Decisions** | The current decision per article (the latest event), counts by verdict, filters (keep, drop, fix, paragraphs only, made on another text, cleared), a search, per-article undo, bulk undo of what is shown, and export as JSONL or JSON (current decisions) or the raw log. |
| **Reports** | `quality/*.md` plus the dataset's `README.md` and `LEARNINGS.md`, rendered with linked references, heading anchors (`?h=`) and the open report's contents. `review-first.md` also appears here as a plain document. |
| **Files** | The dataset folder as a tree with sizes. Folders with more than 300 entries, and folders over 100 MB below the top level, show counts and totals instead of their contents. Symlinked files (every file of a Hugging Face snapshot is one, into the cache's `blobs/`) show their target's size and mtime. The app's own folder (`edaapp/`) and dot-folders (`.git/`, ...) are left out. |

The app polls the server every 4 s. When tables or annotations in the data folder change, list
views re-render; the article view shows a banner instead, so it doesn't lose a note you are typing.
An edit to a Markdown file (`METHODOLOGY.md`, `quality/*.md`) only updates the nav and the views
that show documents, so a document edited often doesn't keep reloading Browse.

### Keyboard (article view and review mode)

| Key | Action |
|---|---|
| `j` / `k` | next / previous article in the list |
| `1` or `Shift+K` | keep |
| `2` or `d` | drop |
| `3` or `f` | fix |
| `0` | clear the verdict |
| `u` | undo the last change to this article |
| `n` | write a note. `Ctrl/⌘+Enter`, or leaving the box, saves it; `Esc` leaves it. |
| `r` | raw Markdown on/off |
| `b` | back to the list |
| `/` | focus the search box (any view) |
| `?` | shortcut help (any view) |

`k` is taken by navigation (vim style), so *keep* is `1` or `Shift+K`. Shortcuts never fire while
the focus is in an input, a textarea or a select, or when a modifier key is held.

## Discovery conventions

Nothing in discovery is hard-coded to a dataset (only `paths.py` names this repository's, and
`hub.py` the Hugging Face ones). The app looks at the data root's structure: the repository root
by default, the Hugging Face root with `--hub` (see *Data sources*), or the folder given with
`--data`:

- A **dataset** is an immediate subfolder (or a symlink to one) with a JSONL (`.jsonl`, or gzipped
  `.jsonl.gz`) or Parquet table at its top level that has `id` and `text` columns. Each such table
  is a **version**, named by its file stem (`orwiki-20260901-trainingready` for
  `orwiki-20260901-trainingready.jsonl.gz`). Versions are ordered newest first by the first 8-digit
  date in the stem, then by modification time. The top bar has a version selector when there is
  more than one.
- **One table in several formats** (`x.jsonl`, `x.jsonl.gz` and `x.parquet`, or
  `annotations/x.jsonl` next to `annotations/x.parquet`, as while a dataset is being converted):
  the newer file by mtime is read; on a tie JSONL, then JSONL.gz. The others are named as not read,
  in a warning on the Overview and the Home card, and in the Overview's header or annotation row.
  Parquet keeps working; nothing requires it.
- **Gzipped JSON lines** (`.jsonl.gz`, as the corpus is committed and published: 17 MB, 96 MB
  unpacked) are read by DuckDB directly, never unpacked to disk. Reading and typing the corpus takes
  0.41–0.43 s instead of about 0.2 s for the plain file. Annotations may be gzipped too.
- **Symlinks are followed** for files, so a Hugging Face cache snapshot, where every file is a
  symlink into the cache's `blobs/` folder, reads like a plain folder.
- **Not data**: dot-folders of the data root (and of a dataset) are never looked at, and the app's
  own folder is never a dataset, even when `--data` names the repository itself. Within a dataset
  only the top level, `annotations/`, `quality/` and joined files are read.
- **JSON lines are typed explicitly.** DuckDB infers each column's type from every record, then
  the app keeps strings that DuckDB would turn into dates, timestamps or UUIDs as strings (as in the
  Parquet files; `"2026-07-17T18:50:18Z"` would otherwise lose its `T` and `Z`), and reads
  all-null or mixed-type (`JSON`) columns as strings.
- `<stem>-build.json` next to a version holds its build statistics (`x-build.json` next to
  `x.jsonl.gz`). `dropped` (reason → count)
  feeds "Pages left out" when there is no `excluded.jsonl`; an older build file's
  `dropped_titles` (reason → titles) still works.
- `excluded.jsonl` at the dataset's top level: every page of the dump that is not in the corpus,
  one `{"id", "revid", "title", "reason", "detail"}` per line. It adds the **Excluded** view.
- `removed-blocks.jsonl` at the top level: blocks cut out of kept articles,
  `{"id", "title", "kind", "reason", "text"}` (and optionally `article_kept`). It adds the
  Excluded view's Removed blocks tab. Both names are reserved: neither is ever a version, in any
  table format, although `removed-blocks.jsonl` has `id` and `text` columns. A missing field is
  read as null and listed as a warning.
- The wiki links (dump revision, current page) use the origin of the corpus's `url` column
  (`https://or.wikipedia.org`), else the wiki named by the build file's `dump`
  (`orwiki-…` → `https://or.wikipedia.org`).
- `annotations/<name>.jsonl` (or `.jsonl.gz`, `.parquet`) holds one row per article, keyed by
  `id`. Its columns show up as `<name>.<column>`, plus `<name>._present` (the article has a row).
  If the file has a `text_sha1` column, there is also `<name>._stale`: that sha1 differs from the
  current text's.
- `annotations/<name>.paragraphs.jsonl` (or `.jsonl.gz`, `.parquet`) holds rows keyed by (`id`,
  `para`); numeric columns can shade paragraphs.
- Optional sidecar: `annotations/<name>.json`, or `<name>.paragraphs.json` for a paragraph file
  (falling back to `<name>.json`), shaped like `{"name", "description", "source", "created",
  "columns": {col: description}, "depends_on_text", "joins"}`. Descriptions appear in filters and
  charts. `depends_on_text` without a `text_sha1` column gives a warning, because staleness can't
  be checked.
- **Sidecar joins**: columns that belong to every row of an annotation but are kept once in another
  file, not copied into it. `"joins": [{"path": "raw/bpb/scores.jsonl.gz", "on": "para_sha1",
  "description": "…", "columns": {"bits": "…", "tokens": "…"}}]` says that each row's `bits` and
  `tokens` are those of the row of that file whose `para_sha1` equals its own. `path` is relative to
  the dataset folder and must stay inside it: not absolute, and no `..` or symlinked folder leading
  out (the file itself may be a symlink, as in the Hugging Face cache). The file is JSON lines,
  gzipped JSON lines or Parquet, with one row per key. `columns` may also be a list of names. It
  works for article and paragraph annotations alike, on any key column the two files share.
  - The joined file is loaded once per file version into its own table, shared by every annotation
    that names it, and the named columns are joined into the annotation's table when it loads. From
    there on they are the annotation's own columns: filters, sorting, shading, the article view's
    values on hover, the Overview's distributions. Its (size, mtime, inode) is part of the change
    detection, so rewriting it reloads the annotation.
  - A row with no match gets nulls. The Overview's annotation row lists the joined columns with their
    file and how many rows found their key.
  - Problems are warnings, never crashes: a missing file (the annotation is shown without those
    columns), a path outside the dataset folder, an unsupported format, a key column missing on
    either side, a named column the file doesn't have, a column the annotation already has (its own
    value wins), repeated keys (the first row of each is used). A joined file that is replaced but
    can't be read keeps its previous copy.
- **Not scored**: an annotation's *primary* column is the numeric one named like the annotation
  (`bpb` in `bpb.jsonl` and `bpb.paragraphs.jsonl`). A null there means no score yet (for bpb: a
  paragraph text new since the last scoring run), shown as "not scored", never shaded as a value;
  sorting puts nulls last in both directions.
- Files the app can't use (no `id`, a paragraph file without `para`, unreadable) are listed as
  warnings, not errors. Duplicate keys are counted, and one row per key is used.
- Files ending in `.tmp` are not looked at: a build writes `x.jsonl.tmp` and renames it over
  `x.jsonl`, and watching the growing temp file would reload the dataset every half second.
- `quality/*.md` are reports. `README.md` and `LEARNINGS.md` are the dataset's docs.
- `METHODOLOGY.md` at the dataset's top level → a **Methodology** nav item and page (hidden when absent).
- `quality/review-first.md` → a **Review first** nav item and page (it also stays in Reports).
- A paragraph annotation with a `para_sha1` column (sha1 of the scored paragraph's text) makes
  paragraph links follow content across rebuilds, and gives per-paragraph staleness: the Overview
  counts stale paragraphs, and the article view doesn't shade stale scores.
- **References in rendered documents become links**, but only to targets that exist:
  `id 12345` / `page id 12345` → the article; `quality/<x>.md` → that report (`review-first.md`
  → the Review first page); `annotations/<x>.jsonl` (also `.jsonl.gz`, `.parquet`, `.json`) → the
  Overview row of that annotation; `excluded.jsonl` → the Excluded view, `removed-blocks.jsonl` → its
  Removed blocks tab; `README.md` / `LEARNINGS.md` → the doc view; `METHODOLOGY.md` → the
  Methodology page. Code spans that are exactly a reference are linked too. Documents use
  pandoc's `$` rules (no space inside, no digit after the closing `$`), so "$0.53/h … $0.26"
  stays text.
- The Markdown export is optional: when the corpus has a `file` column, it is the article's
  Markdown file, relative to the dataset folder, and the article view shows its path.

**Paragraphs** follow the data contract everywhere: paragraph *i* is `text.split("\n\n")[i]`.
Index 0 is the `# title` heading, and a heading, a list block or a table is one paragraph. A
paragraph is a heading when it starts with 1 to 6 `#` and a space (`#{1,6} `, as CommonMark reads
it), so `#!/usr/bin/perl` or `#hashtag` is text; Python and the SQL of the paragraph table agree. DuckDB's
`string_split` gives identical pieces; this was checked on all 169,579 paragraphs of the corpus.

**Change detection**: before a request, the server stats the files discovery looks at (the
dataset's top level, `annotations/` and `quality/`, and the files that sidecars join; at most every
0.5 s). A file whose size,
mtime or inode changed is read again; new files are picked up and vanished ones dropped. A file
that is replaced but can't be read keeps its previous copy, and the problem is shown as a warning.

## The review log: `../reviews/reviews.jsonl`

The repository's `reviews/reviews.jsonl` (or `reviews.jsonl` in the `--state` folder). Append-only,
one JSON object per line, UTF-8 (Odia is written as-is, not `\u` escaped):

```json
{"ts": "2026-09-24T14:10:51.528Z", "dataset": "odia-wikipedia", "id": 3008, "title": "କର୍ଣ୍ଣାଟକ", "verdict": "fix", "note": "…", "drop_paragraphs": [{"para": 3, "sha1": "88ec39305e07028aecd0a168ec2f10ed5d033ec6"}], "text_sha1": "bac525810f6c8e6f26c814a7744775178a29819c"}
```

| Field | Meaning |
|---|---|
| `ts` | when the event was written, ISO-8601 UTC |
| `dataset` | the dataset's name (its folder's name in the data root): `odia-wikipedia` for this repository, in every mode |
| `id` | page id (the join key) |
| `title` | the article title, for people reading the file |
| `verdict` | `"keep"`, `"drop"`, `"fix"` or `null` (no verdict) |
| `note` | free text, may be empty |
| `drop_paragraphs` | paragraphs to remove: `para` is the index when it was recorded, `sha1` the sha1 hex of that paragraph's UTF-8 text |
| `text_sha1` | sha1 hex of the article's UTF-8 `text` when the decision was saved |

Rules:

- Each event is the **whole decision** for (dataset, id) at that moment. The **latest event in
  file order wins**. Nothing is rewritten or deleted. Undo appends an event that restores the
  previous decision, and "clear" appends one with `verdict: null`, an empty note and no
  paragraphs, which means *no decision*.
- Paragraphs are identified by **content** (`sha1`), because the build matches them by content.
  So identical paragraphs in one article are dropped together, the list has no sha1 twice, and
  paragraph 0 (the title) is never dropped (drop the article instead).
- A recorded drop whose paragraph is not in the current text (a rebuild already removed it, or the
  text changed) is **carried into later events**. The build re-renders from source and applies
  only the latest event, so leaving it out would bring the paragraph back. The article view lists
  such drops and offers "forget them".
- A decision whose `text_sha1` differs from the current text is shown as *made on another text*.
  A rebuild that applied its paragraph drops also changes the text.
- Saving the same decision again appends nothing. The article view saves one change at a time, in
  order. Bulk actions write all their events with one timestamp, in a single append.
- The build reads this file (`load_reviews`/`apply_review` in `../prepare.py`, run by `uv run
  pipeline.py`) and applies the latest event per article whose `dataset` is `odia-wikipedia`:
  `drop` removes the article, dropped paragraphs are removed by sha1, and `fix` is counted as
  pending (paragraph fixes come from `curation/paragraph-fixes.jsonl`, made by `translate.py fixes`
  and `merge-fixes`).

The log is human work, committed with the dataset. Tests never touch it.

## Architecture

```
edaapp/ (in the dataset's repository; ../ is the dataset)
src/edaapp/
  cli.py         entry point (uv run edaapp): argparse, the data source, uvicorn on 127.0.0.1
  roots.py       data roots of symlinks; the default one, this repository (.cache/repo-root/)
  hub.py         the Hugging Face copy (--hub): snapshot_download into the cache, the offline
                 fallback, the data root of symlinks (.cache/hub-root/)
  server.py      FastAPI routes under /api, static page, host/origin guard
  service.py     browse, article, reviews, patterns, bulk actions, overview, excluded pages and
                 removed blocks (no HTTP)
  store.py       DuckDB catalog: tables (JSON lines typed explicitly), sidecar joins, joined view,
                 change detection, paragraph tables, the excluded/removed-blocks tables
  discovery.py   finds datasets/versions/annotations/reports/side files; the Files tree
  filters.py     schema-driven filters -> parameterised SQL
  reviews.py     the review log: validate, append, incremental replay, undo
  paragraphs.py  the paragraph definition, sha1, block kinds
  render.py      markdown-it-py (CommonMark + tables + dollarmath), raw HTML off; documents get
                 heading ids, a table of contents and reference links (core rules)
  reviewfirst.py parses quality/review-first.md into items
  paths.py       the repository's paths and dataset name; the only module that writes files or
                 makes links (SafeWriter, path guard)
static/          index.html, app.css, js/ (ES modules, hash routing, no build step)
static/vendor/katex/   KaTeX 0.18.9 (MIT), woff2 fonts only
.cache/repo-root/      the default data root: odia-wikipedia -> .. (git-ignored)
.cache/hub-root/       the Hugging Face data root: one symlink per dataset (git-ignored)
tests/           pytest; fixtures are built in .pytest-tmp/, never in the dataset
../reviews/reviews.jsonl   the review log (the app appends; the build reads)
```

- **DuckDB, in memory.** Each corpus version, annotation file, `excluded.jsonl` and
  `removed-blocks.jsonl` is loaded into a DuckDB table once per file version (size, mtime,
  inode). Reading the 96 MB JSONL corpus takes about 0.1 s and inferring its types another 0.1 s
  (the Parquet copy took 0.3 s), and both together take 0.41–0.43 s from the 17 MB gzipped copy
  that is committed and published (measured 2026-10-01; DuckDB unpacks it twice, in one thread).
  The 47 MB paragraph annotation, 143,357 small records, takes 0.06 s to read but 0.30 s to infer,
  and the 4.9 MB gzipped score store it joins (116,318 rows) 0.05 s to read and 0.15 s to infer
  (measured 2026-09-25). Inference reads every record, because a column seen only late in a file
  would otherwise be dropped silently. A view per dataset version joins the corpus to every
  article-level annotation and to the current review decisions, by `id`. This deviates from
  querying the files directly, for two reasons. Queries on the table are 3–10× faster
  (the regex search runs in 0.03–0.25 s instead of 0.3–1 s). And a table is a consistent
  snapshot while another process replaces the file: queries that are running finish on the old
  tables (MVCC), and new ones use the reloaded tables.
- **Paragraph table**, built on first use of Patterns: every paragraph with its kind and
  normalised form (0.7 s), plus the repeated-paragraph groups (0.1 s). Both are cached with the
  corpus table and dropped when the corpus file changes.
- **SQL safety.** Column names come only from the discovered schema (a whitelist) and are always
  quoted. Every user value is a bound parameter. Regexes run in DuckDB's RE2, which has no
  catastrophic backtracking. Snippets locate the RE2 matches in Python, so highlighting agrees
  with filtering.
- **Writes.** Everything the app writes goes through `paths.SafeWriter`. It resolves the path
  (following symlinks and `..`) and refuses anything outside its two roots: `edaapp/` (DuckDB's
  spill directory `.cache/duckdb-tmp`, the links in `.cache/repo-root/` and `.cache/hub-root/`, a
  `--state` trial) and the repository's `reviews/` folder (the review log). The rest of the
  repository, the dataset, is never written. A link may point out of the roots (to the
  repository, into the Hugging Face cache), but a write through it is refused unless it lands in a
  root. DuckDB extension auto-install is off. A test scans the source to check that no other
  module writes files or makes links.
- **Local only.** The app binds 127.0.0.1. Requests whose `Host` is not `127.0.0.1:<port>` or
  `localhost:<port>` are refused (DNS rebinding). Writes must be JSON with a same-origin (or no)
  `Origin`, so another website can't post to it.

### API (all JSON; `v=<version stem>` selects a version)

`GET /api/datasets`, `/api/changes`, `/api/health`; per dataset `GET /api/d/{ds}/…`:
`schema`, `overview`, `dropped?reason=` (titles, from `excluded.jsonl` when present),
`excluded?reason=&q=&sort=reason|title|id|revid&dir=&page=&size=`,
`removed-blocks?reason=&kind=&id=&kept=1|0&q=&sort=id|title|reason|kind|chars&dir=&page=&size=`
(both 404 without their file), `browse?<filters>`, `position?id=&<filters>`,
`article/{id}`, `decisions`, `decisions/export?format=jsonl|json`, `patterns/templates`,
`patterns/template/{key}`, `patterns/search?re=`, `reports`, `report?name=&group=`, `files`;
`methodology` (404 without the file), `review-first`;
`POST /api/d/{ds}/review` `{id, verdict, note, drop_paras, text_sha1, discard_missing?}`,
`POST …/undo` `{id}`, `POST …/bulk/preview` `{spec}` → events and a token, and
`POST …/bulk/apply` `{spec, token}`. Apply recomputes the plan and refuses (409) if it no longer
matches the preview. `GET /api/reviews/log` returns the raw log. Interactive docs are at `/api/docs`.

Browse parameters: `is.<col>=true|false`, `min.<col>`, `max.<col>`, `in.<col>` (repeatable;
`__null__` = missing), `any.<col>` (repeatable), `has.<col>`, `null.<col>=1|0`, `q` (title),
`text` + `mode=substr|regex` + `icase=1`, `pat=<repeated-paragraph key>`, `sort`, `dir`,
`unrev=1`, `cols=a,b`, `page`, `size` (≤ 500).

## Development

```bash
cd edaapp
uv run pytest -q                     # 205 tests: discovery, filters (incl. injection), paragraphs,
                                     # reviews (write/replay/undo), path guard, change detection, API,
                                     # documents and links, Review first, --state, the JSON-lines
                                     # layout (JSONL-only, mixed Parquet/JSONL, excluded pages,
                                     # removed blocks), sidecar joins (gz store, missing or
                                     # unreadable store, null bpb, join problems), gzipped tables,
                                     # the Hugging Face source (snapshot_download mocked, a fake
                                     # cache of symlinks, the offline fallback), the repository mode
                                     # (the name, the review log, hidden folders) and the command line
uv run ruff check --no-cache src tests
uv run ruff format --no-cache src tests
for f in static/js/*.js static/js/views/*.js; do node --input-type=module --check < $f; done
```

`node --check file.js` does not check a `.js` ES module: it exits 0 even on a syntax error. Hence
`--input-type=module` and stdin.

The environment (`.venv`), `uv.lock`, the ruff cache (`cache-dir` in `pyproject.toml`) and pytest's
cache and temporary folders all live in `edaapp/`. Run the commands from here.

Tests never touch the review log (`../reviews/reviews.jsonl`) or the network. Every test builds
its own data and state in pytest's temporary folder (`.pytest-tmp/`), and a session fixture fails
the run if anything in `../reviews/` changed (size, mtime or content). The Hugging Face tests
replace `snapshot_download` and `dataset_info` with fakes. Two tests read the real repository
(its dataset name in `prepare.py`, and discovery of the clone, read only). For manual checks that
save decisions, run the server with `--state .cache/<something>`.

## Known limitations

- The whole corpus is held in memory: with this dataset, its four annotations (143,357 paragraph
  rows among them), the joined score store and the paragraph table loaded, the server's macOS
  footprint is about 615–635 MB (700–820 MB RSS; measured 2026-09-25). The score store's table adds
  about 13 MB of DuckDB memory, and the same data with the store's columns copied into
  `bpb.paragraphs.jsonl` measured 575–600 MB. DuckDB's tables are about 120 MB of that for the corpus;
  loading it from JSONL takes less memory than from Parquet did. That is fine for corpora of this
  size (tens to hundreds of MB), not for multi-GB ones.
- Dropping a paragraph drops a whole block. A list block or a table is one paragraph, so a
  pattern that matches one list item drops the whole list. The confirmation dialog shows each block.
- Paragraph shading uses the corpus-wide 2nd–98th percentile of the chosen column, with higher
  values shown darker. There is no invert switch for columns where low is bad.
- Regex search uses RE2 syntax: no look-around or back-references.
- The Files view follows symlinked files, not symlinked folders (those are left out).
- With `--hub`, a start needs the Hub, or a cached snapshot of the revision. A newer revision on
  the Hub is only seen at the next start.
- One review log serves every dataset the app shows; with `--data` and other datasets, give them a
  `--state` folder of their own.
- `excluded.jsonl` and `removed-blocks.jsonl` belong to the dataset, not to a version: with an
  older version selected, the Excluded view checks them against that version's corpus (so pages
  excluded later show as "in corpus").
- At phone width (390 px) the Overview's build-statistics table is about 15 px wider than the
  window (long keys and a 40-character sha1). The other views fit.
