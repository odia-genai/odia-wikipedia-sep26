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

- **The full corpus: 20,834 articles, 4.73M Odia words, 89 MB** (2026-09-24, dump
  2026-09-01). That is about 18% more than the ~4M-word estimate for the 2023 HF snapshot in
  `docs/landscape.md`, with no template holes. The median article has 143 Odia words. 3,424
  articles are under 50 words, and 25 are over 5,000 (the longest: ଓଡ଼ିଶାରେ କୋଭିଡ-୧୯ ମହାମାରୀ,
  15.6k). 400 articles (66k words) have `odia_ratio` under 0.6 because of English
  bibliographies and numeric tables. Left out: 112 disambiguation pages, 141 pages under 5
  words, 7 exact duplicates (disease "ସଂକ୍ଷିପ୍ତ" summary copies), and the main page. — done:
  `README.md` and `orwiki-20260901-build.json`.
- **The first chunks overstated the table share by 2×** (2026-09-24). The first 2,500 pages
  gave 9.2% as much Odia in tables as in prose; the full corpus has 4.4% of its words in tables
  (206k words in 1,748 articles). Chunks go in page-id order, so the oldest, biggest list
  articles (districts, chief ministers) come first. — Don't extrapolate corpus statistics from
  the first chunks of an id-ordered dump. Sample at random or wait for the full build. — done:
  the build computes these numbers (`table_words`, `articles_with_tables`) and the README uses
  them.
- **Some pages contain raw Parsoid HTML as text** (2026-09-24). Content Translation pasted
  `<span about="#mwt715" data-cx=… data-mw=…>` into the wikitext, which renders as literal
  markup, e.g. in a FIFA-ranking table. Editors also type bare URLs into prose (57 in 45 pages).
  — done: `HTML_RESIDUE` empties such blocks and cells, `LITERAL_TAG` strips stray tags, and
  `URL` removes bare URLs.
- **Data tables hold 9.2% as much Odia text as the prose** (2026-09-24). In the first 2,500
  articles: 336k Odia characters in `wikitable`s against 3.3M in the text. They are the list
  articles a school model needs: districts with area, population and literacy; chief
  ministers; constituencies; award winners. 54 pages had more Odia in tables than in prose.
  — Don't drop tables wholesale for a small wiki. — done: `table_markdown()` keeps data
  tables as Markdown (rowspans repeat, colspans fill the first column, image-only columns
  removed), with a `tables` count per record.
- **Keeping tables brings back residue that prose had lost** (2026-09-24). On 6,000 pages,
  table cells carried image sizes as text (`70px`, `100px`). Flag templates with missing
  country data showed "ଛାଞ୍ଚ:Country data ହଂକଂ" in a list of 36 territories. — Rerun the
  noise scan after every change in what is kept, not only after changes in what is dropped. —
  done: `PX_RESIDUE`, and red links to missing templates are dropped except the country name.
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

- **A response cut off mid-body crashed the render after 12 of 43 chunks** (2026-09-24). The
  server closed a chunked response early, and `r.read()` raised `http.client.IncompleteRead`.
  That is an `HTTPException`, not a `URLError`, so the retry clause missed it and the thread
  pool re-raised it. The finished chunks were safe because each one is written atomically.
  — Retry on every transient network error, not just the ones seen so far. A long run must
  outlive any single request. — done: `NETWORK_ERRORS` = `URLError`, `HTTPException`,
  `OSError` in `http_get()` and `fetch_html()`. A page that still fails is saved with status
  0, and the next `render` refetches it.
- **Wikimedia throttles bulk REST clients without contact details** (2026-09-24). With a
  user-agent that had no email, every request got HTTP 429 with `Retry-After` ~52 s. With
  contact details and 6 workers, `/w/rest.php/v1/revision/<id>/html` ran at 3.1–4.1 pages/s
  with an occasional 429 (`Retry-After` 5–29 s). All 21,095 pages rendered with no failures in
  about 2 h of fetching, plus one restart. The build then takes 34 s at 602 MB peak RSS. — Always
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
