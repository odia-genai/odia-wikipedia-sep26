# Learnings: Odia Wikipedia corpus

Learnings from building `data/odia-wikipedia/` (see `README.md` and `prepare.py`). This file is
separate from the repo's `LEARNINGS.md` so parallel sessions don't race on one file. Merge it
in when convenient. Same entry format: **what happened** — **lesson** — **action**, tagged
`done:` / `idea:` / `todo:`, newest first within each section.

---

## Ideas backlog

- **Guard the CPT held-out set before training on this corpus** (2026-09-24). `cpt.py` holds out
  300 documents from the 2023 Wikipedia snapshot for bits-per-byte. The same articles, in newer
  revisions, are in this corpus, so training on it as-is would leak the eval. Exclude those
  titles, or rebuild the held-out set from this corpus. The change is in `src/`.
- **Flag Content Translation articles from `change_tag` — done 2026-09-24, see Data** (2026-09-24). The dump's tag table is
  1.6 MB. Machine-assisted translations carry translationese and English leftovers (the
  `data-cx` leaks came from them). Compare the flag with the 1,302 English-dominant paragraphs.
- **Blind human review of ~50 random articles** (2026-09-24), with `odia-review`, for
  fluency, translationese and leftover noise. It is the only measure of what the automated
  checks miss.
- **Near-dedup against Sangraha and FineWeb-2** (2026-09-24). Both probably contain copies
  of Wikipedia pages, which would silently up-weight Wikipedia in the mix. MinHash on the pod.

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

- **Odia digits converted to ASCII, before escaping** (2026-09-24, user decision). 689,848
  digits were converted in text, headings, tables and math. Titles keep the page name. The
  order matters: converting after Markdown escaping would turn a paragraph "୧. ବିଧାୟିକା" into
  "1. ବିଧାୟିକା", which is a numbered list. The Odia word count fell from 4.73M to 4.53M because
  digit runs had counted as words. — Normalise text before escaping it; re-run the Markdown
  parse check after every text change. — done: `clean_block()` converts first, and
  `put_math_back()` and `md_heading()` convert too; the parse check and pandoc still agree.
  idea: Sangraha, FineWeb-2 and the eval sets still use Odia digits, and Odisha textbooks print
  them. So the model must still read them. Apply the same conversion in `cpt.py`'s `clean()` and
  to eval prompts, or the corpus will be inconsistent (outside `data/`, so it needs the owner's
  go-ahead).

- **6.8% of paragraphs are templated, and the bot flag catches only a fifth of them**
  (2026-09-24). Same text with numbers and the title masked, in 5 or more articles: 155
  patterns, 6,848 of 100,204 paragraphs. Top patterns:
  - "N ଗ୍ରେଗୋରି ପାଞ୍ଜି ଅନୁସାରେ ଏକ ସାଧାରଣ ବର୍ଷ ଅଟେ ।" (1,774×)
  - coordinates (625×)
  - census population (710×), with the bot typo ପରୁଷ for ପୁରୁଷ repeated in every copy
  - "ହିନ୍ଦୀ …ର ସରକାରୀ ଭାଷା ଅଟେ ।" (346×)

  Only 1,422 of those occurrences are in pages with `{{ବଟ୍ ତିଆରି}}`. — A template flag is not a
  measure of templated text. Measure repetition directly. — todo: a per-article templated
  share, a cap on repeats, and the ପରୁଷ typo fixed or down-weighted.
- **429 IMDb link lines survived the link filter** (2026-09-24). "- ଇଣ୍ଟରନେଟ ମୁଭି ଡାଟାବେସରେ
  <title>" is an external link written in Odia, so `link_only_item`'s odia_ratio < 0.5 test
  keeps it. — done 2026-09-24: dropped by template name (`imdb*`, `facebook*`, …); IMDb lines 608 → 1.
- **1,302 English-dominant paragraphs, and 2,751 articles mix ASCII and Odia digits**
  (2026-09-24). The English is untranslated leftovers, quotes and bibliographies. Mixed digits
  are real usage, so don't normalise them. `odia_llm.text.numbers` already reads both.

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

- **Review decisions flow from the web app back into the build** (2026-09-24). `build` reads
  `edaapp/state/reviews.jsonl`, taking the last event per page id. It drops articles marked
  *drop* and removes dropped paragraphs by **content sha1, not index**, so a decision survives
  rebuilds that shift paragraphs. It counts decisions whose paragraph no longer exists, and it
  skips a half-written last line. — done: `load_reviews()` and `apply_review()`, unit-tested in
  scratch; `--no-reviews`; the counts go into the build JSON and the README.
- **Backslashes in a plain f-string are a latent syntax error** (2026-09-24). The README template
  had `` `\*` `` in a non-raw f-string. Python 3.13 only warns (SyntaxWarning), ruff's rule set
  here (E, F, I, UP, B) doesn't include W605, and a future Python will reject it. — Check
  generated-text templates with `python -W error` or enable ruff `W605`. — done: doubled the
  backslashes.

- **"Markdown-shaped" text was not all valid Markdown** (2026-09-24). Parsing all 20,834
  articles with markdown-it (CommonMark + GFM tables) found about 100 articles that parsed
  differently from what was meant:
  - Page text read as markup: `*`, `_`, backticks used as minute marks (`୨୧°୩୦``),
    `<alt>+<F4>`, a MATLAB `>>` prompt, `[…](…)`, and leftover `<poem>`/`</right>` tags.
  - Taxonomy trees with text-less list levels jumped 6 spaces of indentation, which CommonMark
    reads as an indented code block.
  - Sublists under `1. ` need 3 spaces, not 2.

  The same reading exposed a real bug: the text cleaners also ran over TeX, so `\sqrt[3]{x}`,
  `x^{{2}}` and `f''` would have been mangled. — Validate generated Markdown with a real parser
  and compare it with the intended structure. Checking that it looks right is not enough. Keep
  math out of every text pass. — done: math waits behind placeholders until the end. Blocks
  carry their list prefix apart from the text. `md_escape()` runs last (2,489 escapes in 534
  articles, 71 per million characters). Empty list levels are skipped, and sublists are
  indented to the parent's content column. After the fixes, every article parses as intended,
  and pandoc's GFM reader agrees on 405 random articles. markdown-it's default `maxNesting` of
  20 flagged one 10-level sauropod tree that is valid Markdown.
- **Per-article Markdown files need names that are safe and unique on any file system**
  (2026-09-24). Titles contain `/` and `:`. APFS and NTFS compare names case- and
  normalisation-insensitively, so two titles that differ only in how ଡ଼ is encoded would
  overwrite each other. — done: `md_filename()` replaces unsafe characters, caps names at 180
  bytes, and checks collisions on an NFC-casefolded key (used only for the comparison; the text
  is never normalised). Build time is 41 s with the 20,834 files.

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
