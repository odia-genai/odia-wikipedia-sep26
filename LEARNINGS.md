# Learnings: Odia Wikipedia corpus

Learnings from building `data/odia-wikipedia/` (see `README.md` and `prepare.py`). This file is
separate from the repo's `LEARNINGS.md` so parallel sessions don't race on one file. Merge it
in when convenient. Same entry format: **what happened** — **lesson** — **action**, tagged
`done:` / `idea:` / `todo:`, newest first within each section.

---

## Ideas backlog

- **Point `odia-build-cpt`'s `wikipedia` source at this corpus** (2026-09-24). `cpt.py` still
  reads the 2023 Hugging Face snapshot (`wikimedia/wikipedia` `20231101.or`, ~4M words). It
  lacks three years of edits and articles and has the template holes described below.
  `--local <jsonl> --local-upsample 1` works today. A `SOURCES` entry is cleaner but has to be
  made outside `data/`.
- **Fold `|`→`।` and U+0B64→`।` into `normalize_odia` repo-wide?** (2026-09-24). Both are
  common in Odia Wikipedia (see Data). Measure how often they appear in Sangraha and FineWeb-2
  before changing a function every corpus goes through.
- **Translate the one-line state and city stubs** (2026-09-24). Articles like ଛତିଶଗଡ଼ are
  one sentence plus an infobox, and they are exactly the school topics we need. Add them to the
  translation list next to the 677 vital articles in `translations/`.
- **Render infoboxes as `key: value` lines?** (2026-09-24). Infoboxes hold dense facts
  (capital, population, dates), but the lead usually repeats the important ones, so they are
  dropped for now. Worth an ablation if knowledge QA is weak.
- **Down-weight bot-made pages in the CPT mix** (2026-09-24). 1,495 pages are bot-created
  (year pages, town stubs) and formulaic. They are flagged (`bot_created`), not dropped.

## Data (Odia)

- **Data tables hold 9.2% as much Odia text as the prose** (2026-09-24). In the first 2,500
  articles: 336k Odia characters in `wikitable`s against 3.3M in the text. They are the list
  articles a school model needs: districts with area, population and literacy; chief
  ministers; constituencies; award winners. 54 pages had more Odia in tables than in prose.
  — Don't drop tables wholesale for a small wiki. — done: `table_markdown()` keeps data
  tables as Markdown (rowspans repeat, colspans fill the first column, image-only columns
  removed), with a `tables` count per record.
- **Odia typists write the danda as `|` or as unassigned U+0B64** (2026-09-24). `|` appeared
  in 367 of the first 500 pages (mostly one year-page template sentence). U+0B64 (`୤`) appeared
  191 times in 48 of the first 2,000 pages. U+0B64/U+0B65 are reserved in Unicode, so any
  tokenizer sees junk. — Normalise both. — done: `PIPE_DANDA` (only after Odia text and
  before a space or line end, so real pipes survive) and `RESERVED_DANDA` in `prepare.py`.
  idea: repo-wide, see backlog.
- **Reference sections hide under many headings, and headings vary in nukta spelling**
  (2026-09-24). ଅଧିକ ତଥ୍ୟ ("more information") appeared 390 times in 2,500 pages and is always
  external links. ଦ୍ରଷ୍ଟବ୍ୟ, ବାହାର ସ୍ରୋତ and ଆଗକୁ ପଢିବେ are also link lists. ପଢ଼ is typed
  both with precomposed ଢ଼ and as ଢ + nukta. — Find them by measurement, not by guessing: rank
  headings by external links, list items and Odia characters per section. — done:
  `DROP_SECTIONS`, `heading_key()` folds nukta, and `link_only_item()` catches link and
  bibliography lists under any heading.
- **Stripping wikitext would break sentences across the wiki** (2026-09-24). Whole sentences
  are templates. `'''{{PAGENAME}}''' ଏକ ଭାରତୀୟ {{TownType|M}} ଅଟେ ।` appears in the 884
  TownType town stubs, and PAGENAME in 1,647 pages. Birth and death date templates are in
  4,176 pages. `convert` and `flag` are Lua modules. mwparserfromhell-style stripping (as in
  the HF `wikimedia/wikipedia` build) leaves "ଏକ ଭାରତୀୟ  ଅଟେ ।". — Use the rendered HTML for
  template-heavy wikis. — done: `prepare.py render` fetches Parsoid HTML for each article's
  exact dump revision.
- **Many important articles are one-line stubs** (2026-09-24). States and capitals like
  ଛତିଶଗଡ଼ ("ଛତିଶଗଡ଼, ଭାରତର ଏକ ରାଜ୍ୟ ।") are one sentence plus an infobox and a navbox. —
  done: pages under 5 Odia words are dropped and listed in `*-build.json`. idea: translate
  them (backlog).
- **orwiki quirks** (2026-09-24). The main page (ପ୍ରଧାନ ପୃଷ୍ଠା) is in the article namespace.
  The dump has 21,095 articles and 21,239 redirects. Year pages have empty event sections, so
  only their one-sentence template lead survives. Some articles carry untranslated English
  paragraphs and quotes. — done: main page and disambiguation pages dropped; English prose
  kept (it is real content), link lists dropped.

## Tooling and automation

- **Wikimedia throttles bulk REST clients without contact details** (2026-09-24). With a
  user-agent that had no email, every request got HTTP 429 with `Retry-After` ~52 s. With
  contact details and 6 workers, `/w/rest.php/v1/revision/<id>/html` ran at 3.1–4.1 pages/s
  with an occasional 429 (`Retry-After` 5–26 s). All 21k articles take ~1.5–2 h. — Always
  include contact details for bulk Wikimedia work. — done: `ODIA_WIKI_CONTACT` env var (never
  stored in a file), and `Retry-After` is honoured.
- **The pre-rendered HTML dumps are gone** (2026-09-24). The Enterprise HTML dumps on
  dumps.wikimedia.org stop at 2025-03-20. Rendered HTML now means one REST call per page, or a
  Wikimedia Enterprise account. — done: the per-page REST route, cached in
  `raw/html/<date>/chunk-*.jsonl.gz` (~2.4–5 MB per 500 pages).
- **The dump server drops long transfers** (2026-09-24). `curl` failed with error 18 after 11
  of 41 MB. — done: `download()` resumes with Range requests and checks SHA-1 against
  `dumpstatus.json`.
- **Parsoid makes template removal exact** (2026-09-24). Every node a template produced
  shares one `about` id, and `data-mw` names the template. Webarchive notes, IPA, maintenance
  banners and hatnotes can be dropped by name. Error messages (ତୃଟି:) and missing-template red
  links (ଛାଞ୍ଚ:…) can be caught by their text. — done: `drop_templates()`.
- **All the rendered HTML is ~1.3 GB in memory** (2026-09-24). The first build loaded every
  chunk at once. — done: `build` streams page by page. Peak RSS was 209 MB on a 5-chunk build.
  lxml converts about 1 ms per page, so no worker processes are needed.
- **Writing only inside `data/` takes care** (2026-09-24). `uv run --script` with a PEP 723
  header keeps its environment in uv's cache. Importing `odia_llm.text` needs
  `sys.dont_write_bytecode` so no `.pyc` lands in `src/`. ruff needs `--no-cache`. — done: all
  three in `prepare.py` and the commands used.
- **`script | head -1` silently killed a test script** (2026-09-24). SIGPIPE ended it before
  it wrote its output file, so the next scan read stale results and looked like a failed fix.
  — Don't pipe a script that writes files after printing into `head`. Send its stdout to
  `/dev/null` and read the file.
- **Invisible characters written by the file tool** (2026-09-24). A regex typed as
  `"[­​…]"` was saved with the literal invisible characters. It worked but was
  unreadable and fragile. — Check invisible-character regexes with `od -c` after writing. —
  done: rewritten as escapes.

## Process

- **Parallel sessions race on the repo `LEARNINGS.md`** (2026-09-24). Another session was
  editing `LEARNINGS.md`, `README.md` and `translations/` during this work. — done: this file,
  at the user's request.
- **I sent the user's email in a request header before asking** (2026-09-24). Test requests
  to Wikimedia carried it in the user-agent. — Ask before putting personal contact details
  into any outbound request. — done: asked, and the email now comes from an env var at run
  time and is never written to a file.
