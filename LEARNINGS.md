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
- **Tag articles by topic from `categorylinks` — done 2026-09-24, see Data** (2026-09-24). The dump's category table is 1.5
  MB. Topic tags would let the school mix up-weight science, history and Odisha geography over
  film and cricket bios, replacing the keyword `wiki-school` filter.
- **Flag Content Translation articles from `change_tag` — done 2026-09-24, see Data** (2026-09-24). The dump's tag table is
  1.6 MB. Machine-assisted translations carry translationese and English leftovers (the
  `data-cx` leaks came from them). Compare the flag with the 1,302 English-dominant paragraphs.
- **Score paragraphs with Sarvam-1 on the pod — done 2026-09-24, see Data** (kept for the record) (2026-09-24). This corpus is 13.3M Sarvam-1
  tokens (153.6 per kB), about 14 minutes on the A6000. Very high bits-per-byte flags garbled or wrong-script text
  that the pattern checks cannot see.
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
- **Down-weight bot-made pages in the CPT mix** (2026-09-24; updated 2026-09-25). The 1,495
  bot-created pages were all year pages and are now excluded. The formulaic stubs that remain
  (village, town and film pages) carry `templated_share`: 1,007 articles are at 0.5 or more.
  Use it in `cpt.py` as a down-weight or a repeat cap. The change is in `src/`.
- **A per-source `--min-chars` in `cpt.py`** (2026-09-25). Its global `--min-chars 200` drops
  498 clean one-line fact articles from this corpus (chemical elements, blocks, capitals). A
  per-source threshold would keep them without admitting short web documents.
- **Split frames inside paragraphs** (2026-09-25). `templated()` matches whole paragraphs, so
  a template sentence followed by one free sentence counts as free. Sentence-level frames
  (split on `।`) would measure `templated_share` more exactly.
- **Move "2"'s number content to 2 (ସଂଖ୍ୟା)?** (2026-09-25). The year page "2" holds 126
  words on the significance of the number 2, lost with the year pages. It is an upstream
  (Wikipedia) fix, or a one-off keep.

## Data (Odia)

- **Year pages were 9% of the articles and almost no content** (2026-09-25, owner's decision
  to drop them).
  - 1,864 year pages: 1,766 have no Odia word outside sentence frames repeated in 5 or more
    articles, and together they have only 2,415 free words. `0 (ମସିହା)` needed the title rule
    to accept a qualifier.
  - The same frame test split the date pages: 260 of 364 are empty, and 104 list real events.
    It also found 19 empty film-year lists.
  - All 1,495 pages with the bot-created template were year pages, so `bot_created` is now
    false throughout the corpus. It was never a good formulaic-text flag: the town stubs don't
    carry it.
  — Measure boilerplate by repeated frames (title and numbers masked), not by the bot
  template. — done: `frame_key()`, `templated()`, `boilerplate_reason()` and
  `templated_share` in `prepare.py`; `excluded.jsonl` lists every page with its reason.
- **A 200-character floor would cost only clean facts** (2026-09-25). After the boilerplate
  rules, 498 articles are under 200 characters: 7,856 words (0.17%). A sample showed
  chemical elements, block and village one-liners and a national anthem's adoption date, all
  clean. — A length floor is a proxy for boilerplate; once the boilerplate is out by rule, the
  proxy only removes good data. — done: `--min-chars` defaults to 0 (off).
- **Wikitext headings typed mid-line render as text** (2026-09-25). The residue scan missed
  `== ଗାଣିତିକ ଧର୍ମ ==` in 21 articles. MediaWiki makes a heading only at the start of a line, so
  "… ସଂଖ୍ୟା । '== ଗାଣିତିକ ଧର୍ମ ==" or "==ଭୂଗୋଳ==1947 …" stays literal. Headings written with
  the marks inside (`###### == ଆଧାର ==`) also escaped the reference-section drop. — Check for
  every markup family, not only the ones seen so far. — done: `split_wiki_headings()` makes
  them headings at a sentence or block break and strips the marks mid-sentence; `heading_key()`
  ignores the marks; `check.py` flags `==…==` outside math.
- **A source error looks like a digit bug** (2026-09-25). The chlorine article says atomic
  number 7. It looked like digit conversion had lost a digit, but the rendered HTML says `୭`.
  — Check the source HTML before blaming a text transform. — idea: a facts check against
  Wikidata for element numbers, capitals and dates.

- **Removing all English left skeletons; the fix was translate prose, keep data, drop
  citations** (2026-09-24, owner's decision).
  - The first English filter removed 6,622 blocks. List articles (lakes, mountains, rivers) kept
    only their headings, and 56 pages had to be dropped as mostly English.
  - The owner chose instead: translate the prose, keep tables, names and titles, and drop
    citations; headings were left to the maintainer, who translated them.
  - Result: 980 paragraphs and headings translated in place (396 articles), 5,754 lists and
    tables kept, 133 citations and 3 junk blocks removed. The corpus has 51 more articles.

  — Filter by what a block *is*, not only by its script. — done: `prepare.py` English
  handling, `translate.py`, `translations/english-to-odia.jsonl`, and the
  `translated_paragraphs` count per record.
- **Nine parallel translator agents: 21.6k English words in about 15 minutes, and they found
  junk** (2026-09-24).
  - Output: 449 paragraphs and 351 headings translated; 72 kept as they are (code, name lists,
    garbled OCR, verse in Latin script).
  - Junk they flagged: vandalism in the mouse article, leaked template instructions in the
    jaundice article, and a template error in the Latin article.
  - `translate.py merge` checks every item. Only 2 paragraph translations fail, both for Latin
    names kept on purpose. The length check (0.5–2.5) flags short headings ("Awards" → "ପୁରସ୍କାର
    ଓ ସମ୍ମାନ"), so read failures by kind.
  - A random sample of 8 read against the sources was accurate and natural.

  — Give translators a "not content" way out; check before merging. — done: `translate.py
  mark --drop`, and the build removes marked junk. idea: a length band per kind, and a
  back-translation spot check for a larger sample.
- **Citation detection must not rely on "(1962). "** (2026-09-24). A looser rule flagged prose
  that cites a film's year ("…Satyajit Ray's film Abhijan (1962). Following this…"), prose that
  says "journal", and an anthology title with "Press" in it. — done: paragraphs need structural
  signals (ISBN/DOI, page or volume numbers, "Retrieved", the "Surname, A." format). List items
  also count "(YYYY). " and publisher words, but only together with a year.

- **Boilerplate after the cleanups: 6.3% of paragraphs but only 2.1% of Odia words, and some
  of it wrong** (2026-09-24).
  - 151 sentence frames repeat in 5 or more articles, once names and numbers are masked:
    6,339 of 101,090 non-heading paragraphs. Only 1,422 of them are in bot-flagged pages.
  - Top frames:
    - the year-page sentence "N ଗ୍ରେଗୋରି ପାଞ୍ଜି ଅନୁସାରେ ଏକ ସାଧାରଣ ବର୍ଷ ଅଟେ ।" (1,774×), often the
      page's only sentence
    - a coordinates sentence (625×)
    - the 2001-census sentence (about 870× across its variants)
    - "ହିନ୍ଦୀ …ର ସରକାରୀ ଭାଷା ଅଟେ ।" (346×)
    - an election-table footnote (136×)
    - a village-school sentence (109×)
  - The census frame writes a signed difference into "less than": "ଏହା ଜାତୀୟ ହାରଠାରୁ -20.28%
    ପ୍ରତିଶତ କମ ଅଟେ" ("-20.28% less than the national rate", i.e. more). The data is also from
    2001. A model trained on it sees the same wrong phrasing hundreds of times.
  - — Repetition, not perplexity, is the signal (see bpb). — todo: cap each frame at a few
    copies in the training mix, or drop the one-sentence year pages; fix or drop the "-X% କମ"
    census sentences. The edaapp Patterns view lists these groups and can bulk-drop them.

- **After the cleanups the queue's English type is mostly bilingual text** (2026-09-24). The
  English candidates fell from 707 to 66, and they are now mostly Odia with English quotes or
  glosses. The removal also left some list articles as skeletons: ଭାରତୀୟ ହ୍ରଦ ସମୂହର ତାଲିକା is now
  English headings plus short items (bpb 2.82). An Odia category name still leaks as text
  (`Category:ଜୀବିତ ବ୍ୟକ୍ତି]`). — idea: drop pages the removal leaves mostly empty (e.g. under
  N Odia words after removal); extend `CATEGORY_TEXT` to `ଶ୍ରେଣୀ:`/`Category:` + any name.
  Both change text, so the affected paragraphs need a ~$0.02 re-score.

- **Judge English per list item, not per list, and give bilingual tables a floor**
  (2026-09-24). The first English filter judged a whole list at once. It removed mixed lists
  that had real Odia in them: Galileo's timeline (696 Odia letters) and a Kalpana Chawla list
  (648). It also removed a bilingual yoga-asana table with 1,207 Odia letters. — Pick the unit
  of a filter to match the unit of mixing; check which removed blocks hold the most Odia. —
  done: paragraph ≥ 30, list item ≥ 10, and table ≥ 30 Latin letters with under 200 Odia
  letters. The rule removes 6,622 blocks (513 paragraphs, 5,591 list items, 518 tables); 184
  of the list items hold any Odia at all.
- **Removing list items can turn a list into a code block** (2026-09-24). Taking a parent item
  out left its sublist indented 4+ spaces past any item above it, and CommonMark read that as an
  indented code block (rivers of India, dinosaur classification). The Markdown parse check
  caught it. — Re-run the Markdown check after every filter. — done: an item's indent is
  clamped to the previous item's content column (`LI_PREFIX`).
- **Tie a typo fix to its context** (2026-09-24). ପରୁଷ ("harsh") is a real word, so blindly
  replacing it with ପୁରୁଷ ("male") is wrong in general. All 919 uses here meant "male": 904 in a
  bot's census sentence, the rest in forms like ପରୁଷଙ୍କ, ପରୁଷମାନଙ୍କ and ପରୁଷୋତ୍ତମ. — done:
  `TYPO_FIXES` with context patterns; 917 fixed. The two left are other misspellings
  (ପରୁଷ୍କାର, ପରୁଷାମାନଙ୍କ).
- **Re-score by content hash, not by article** (2026-09-24). The cleanups changed 2,530
  articles, but only 1,379 paragraph texts (~137k tokens) had no score, because a paragraph's
  bpb depends only on its own text. — Key model scores by the text's hash so edits cost only
  what changed. — done: `score_bpb.py` carries scores over by `para_sha1`.

- **Topics: 97.7% of articles tagged, precision ~95–99% on held-out samples, weaker on small
  topics** (2026-09-24).
  - Signals: categories from the rendered HTML (including template-added ones), shallow category
    walks, rules on Odia category names, and Wikidata for the 2,767 articles with no direct
    evidence.
  - Share of words: health 15%, film 11.6%, politics 11.4%, literature 9.1%, religion 10.7%,
    geography 7.7%. School-relevant: 51.4% of articles (57.6% of words); Odisha: 32.5%.
  - Held-out random 100: 96/97 right. Stratified check of the small topics: 64/72 (science 5/8,
    society 5/8).
  - Category ancestry leaks: "geography" was reachable from 9,172 articles through "people of
    Odisha" → Odisha. Keyword rules collide: ଲୋକ matched ଲୋକ ସଭା; ପର୍ବ matched inside ପର୍ବତ.
  - The tuning sample read 100% where held-out gave 98% and small topics 89%.

  — Match on the category's own name, walk shallowly, anchor Odia keywords at word boundaries,
  always report held-out and stratified checks. — done: `annotate.py topics`,
  `quality/topics.md`, hand labels in `quality/topics-check.tsv`.
- **18.5% of articles are machine-assisted translations, and most health articles are**
  (2026-09-24).
  - 3,527 were created with Content Translation; 3,844 are `translated` if you add MDWiki's
    dashboard, which uses CX but leaves no tag and is found from edit summaries ending
    `#mdwikicx`. 1,936 of the 3,269 health articles are translations, 1,912 of them from MDWiki.
  - Translated articles hold 4× the English-dominant paragraphs (3.1% vs 0.8%), mostly untranslated
    table and list items, yet their `odia_ratio` is the same (0.900). Their bpb is the same too.
  - People made fewer later edits to them (median 3 vs 7). The dumps can't show MT pasted in from
    outside the tools, or how much machine output was kept.
  - With the contract's paragraph definition there are 1,856 English-dominant paragraphs, not
    the 1,302 counted earlier with a different split.

  — Filter English-dominant paragraphs at paragraph level; page-level ratios hide them. — done:
  `annotate.py translation`, `quality/translation.md`. idea: down-weight or review health by
  the `translated` flag.

- **Sarvam-1 bits per byte for every paragraph, and a review-first queue** (2026-09-24). An
  RTX A6000 Secure pod at $0.53/h ran for 0.495 h, costing $0.26; the image pull took 7 of the
  30 minutes. It scored 148,745 non-title paragraphs, 13.46M tokens at 16.5k tokens/s.
  - Corpus bpb 0.556; prose 0.523; headings 1.14; lists and tables 0.69.
  - Paragraph bpb looked worse than the held-out set's 0.4951 until matched for length. Prose
    paragraphs of 1 kB and over score 0.4948, because a paragraph scored from BOS has no
    context.
  - Content Translation articles score like the rest (0.554 vs 0.556).
  - Pages created after Sarvam-1's release score *lower* (median 0.519 vs 0.559), so there is no
    sign of memorisation (correlational).
  - Sanity checks: word-shuffled copies scored higher in 300 of 300 (0.518 → 0.883). The repo
    harness agrees to 0.04%. bf16 is unbiased in total but moves single paragraphs by up to 3.4%
    with batch shape, while fp32 is exact.

  — Compare bpb within length bands. Treat close per-paragraph ranks as noise. — done:
  `score_bpb.py`, `annotations/bpb*.parquet`, `quality/bpb.md`, `quality/review-first.md` (200
  flagged articles, seven failure types interleaved). idea: document-level bpb alongside, and
  fp32 when exact ranks matter.
- **bpb cannot see boilerplate repeated across articles** (2026-09-24). Each paragraph is scored
  alone, so sentence frames found in 5 or more articles sit at the 58th percentile of their
  length band. The low tail is formulaic but correct writing: election sentences, year lists,
  and an MLA career table whose header is in 220 articles. — Detect templates by repetition, not
  by perplexity. — done: a `repeats` column in `bpb.paragraphs`. todo: a per-article templated
  share and a repeat cap in the CPT mix (see above).
- **The residue filters miss escaped HTML and raw wikitable text** (2026-09-24). The review
  queue found 14 paragraphs in 13 articles: a leaked Content Translation link
  (`\<a href=… class="mw-redirect cx-link"…>`), raw `{| … |}` wikitable text, `style=` and
  `Category:` lines, and `&amp;amp;` in a table. — done 2026-09-24: `fix_residue()` in `prepare.py`
  (anchors, entities, raw wikitable, template parameters, attributes, category text: 67 fixes).
- **707 English-dominant and 375 other-script paragraphs sit inside Odia pages** (2026-09-24).
  They include Param Vir Chakra citations, English bios, OCR'd radio listings, and Latin-script
  filmographies. The page-level `--min-odia-ratio 0.6` misses them. — done 2026-09-24: removed per
  paragraph, list item and table (6,622 blocks), kept in `removed/english-paragraphs.parquet`.
  idea: translate them.

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

- **Scores lived only in annotations and scratch dirs** (2026-09-25). A text that left the corpus
  lost its score, and run 1's raw pod output was lost with a scratch dir. — Keep every score
  permanently, keyed by text. — done: `raw/bpb/scores.jsonl.gz` (116,318 texts, 4.9 MB, seeded
  from the Parquet after checking that no text had two different scores); `build` reads only the
  store, and `build --add` refuses a different model revision and skips a run already added.
  2,378 texts that left with the year pages stay in the store. todo: `raw/` is gitignored and
  `data/` untracked, so the store has no backup; decide whether to track it (4.9 MB).
- **Token estimates by script** (2026-09-25), measured from the store: Latin-script paragraphs
  run at 2.26 bytes per token, Odia prose at 7.12, all Odia paragraphs at 6.73, headings at
  6.03. Estimating per script came within 7% of run 3's actual tokens, where a bytes-only
  estimate was 2.4× low. — done: `build`'s stop message prints the per-script estimate.
- **`bpb.paragraphs.jsonl` repeats the store** (2026-09-25): 64 MB, against 6.6 MB as Parquet.
  About two-thirds is repeated key names, and its `bits`, `bytes`, `tokens`, `pieces` and
  `score_run` duplicate `raw/bpb/scores.jsonl.gz`. — idea: drop those fields from the paragraph
  file (edaapp would join the store), or gzip it. That changes the edaapp contract, so it's the
  owner's call.
- **The old re-check code could write `NaN` into `bpb.json`** (2026-09-25), which is not valid
  JSON. — done: missing values are written as null, and every JSONL write refuses `NaN`.
- **A migration ran while the corpus was being rebuilt** (2026-09-25). The score migration
  compared JSONL with Parquet exactly only because the agent had saved a copy of the old corpus
  first. — Snapshot an input before migrating it while others may rebuild it. — done.

- **54.5 MB of annotation inputs were 1.5 MB of facts** (2026-09-25). `annotate.py` needed from
  the 44 MB revision history only per-article facts (Wikidata item, revision counts, first and
  last revision, later editors) and 7,535 of 315,883 revisions (CX tags, "translat" summaries).
  Four SQL dumps (8.3 MB) reduce to a 52 KB category graph. Results are identical for all 18,694
  articles. — Cache facts, not decisions, so rules can still change offline. — done:
  `raw/orwiki-20260901-article-facts.jsonl.gz`, `category-graph.jsonl.gz`, provenance in
  `caches.json`; `download` rebuilds them when a source SHA-1 changes.
- **gzip writes a timestamp into its header** (2026-09-25), so rebuilding the same cache changed
  its SHA-1. — Write gzip with `mtime=0` and no file name when provenance records hashes. — done.
- **JSON lines cost 11–20× Parquet's disk** (2026-09-25): topics 7.5 MB against 0.66 MB,
  translation 8.7 MB against 0.43 MB, mostly repeated key names. — Readability has a disk price
  on a nearly full drive. — idea: accept it; gzip a file only if disk runs short.
- **The topic precision samples shrink with the corpus** (2026-09-25). Dropping year and date
  pages took the held-out check from 100 to 90 articles (86 of 87 tagged correct). — todo:
  decide whether to report precision on the full labelled set.
- **`sys.dont_write_bytecode` doesn't stop importlib's loader** (2026-09-25), which caches
  bytecode before the module runs. — Run verification scripts with `PYTHONDONTWRITEBYTECODE=1`.
  — done.

- **Exclusions recorded by title only couldn't be joined or shown** (2026-09-25). The build JSON
  listed dropped titles, with no page id or revision, and titles with Odia digits don't match
  the corpus's ASCII-digit titles. — Record every exclusion as data (`id`, `revid`, `title`,
  `reason`, `detail`), and check that corpus and exclusions partition the index. — done:
  `excluded.jsonl`, `check.py` consistency check (it catches a single missing page).
- **The dump was a second copy of the text** (2026-09-25). After rendering, the build read
  only ids, titles, revision ids, timestamps and two template flags from the 41 MB dump. —
  Reduce a raw input to the facts the build uses, with provenance to fetch it again. — done:
  `raw/orwiki-20260901-articles.jsonl` (3.3 MB) and `raw/orwiki-20260901-dump.json`;
  `download` deletes the dump after indexing (`--keep-dump` keeps it). The Parquet copy of
  the corpus (21 MB), `markdown/` (135 MB) and `removed/` went too.
- **Separate exports drifted; one command keeps them in step** (2026-09-25). Translations,
  annotations and scores were each re-run by hand after a build. — One pipeline, stopping at
  the first failing step. — done: `pipeline.py` (build, topics, translation, bpb carry-over,
  check).

- **Re-scoring after the translations: $0.03, and the translations read like native Odia**
  (2026-09-25).
  - The job: 2,445 new texts (497k tokens) on an RTX A6000, because the RTX 4000 Ada was out of
    stock; 3.6 min of pod time.
  - The translations score 0.519 bits per byte, the same as native prose, with no sign of
    unnatural text.
  - Other findings from the run:
    - Latin-script text tokenises at about 2.3 bytes per token against 7.1 for Odia, so a
      byte-based estimate (206k tokens) was 2.4× low.
    - `nohup … &` over ssh held the connection open until the job ended, because stdin wasn't
      redirected.
    - The image needs `pip install --break-system-packages` and numpy.
    - Fish shell doesn't split a variable holding several ssh options.
    - `translated_paragraphs` counted 50 headings whose sections were later dropped.
    - 9 translated reference lines had slipped past `is_citation()`.
    - `para_kind` read a Perl `#!/usr/bin/perl` block as a heading.
  - — Estimate tokens per script; detach remote jobs with `< /dev/null` (or `setsid`); count
    after the final pass; take whichever approved GPU is in stock.
  - — done: the count is fixed, the 9 lines are marked junk, and the heading rule (`#{1,6} `) is
    fixed in `score_bpb.py` and edaapp (SQL too). idea: the pod install line and detaching go in
    `score_bpb.py`'s docstring; keep pod score outputs under `raw/bpb/`.
- **Re-run `annotate.py` after every corpus rebuild, before `score_bpb.py build`** (2026-09-25).
  The topic and translation files lagged a rebuild, so 51 articles had no context row in the
  review queue. — done this time; todo: one `refresh` script that runs build → annotate →
  score build → check in order.

- **Reference headings come in many spellings; match them by pattern** (2026-09-24). After the
  first cleanup, about 200 sections still had "see also" and external-link headings spelled
  differently: "ବାହାର ଲିଙ୍କ୍" (64), "ଏହା ମଧ୍ୟ ଦେଖନ୍ତୁ" (62), "ବାହ୍ୟ ଆଧାର" (20), "… ଦେଖିବେ". —
  Re-scan the remaining headings for reference words after each change. — done:
  `DROP_SECTION_PATTERNS`.
- **A whole-block rule ran before the bracket stripping, so `[[ଶ୍ରେଣୀ:]]` survived**
  (2026-09-24). — Whole-block tests must allow the markup that a later step removes. — done:
  the category-block pattern accepts brackets.
- **English template errors hide in plain red spans** (2026-09-24). "Error: {{Lang}}: text has
  italic markup (help)" (59 in all) is printed in `<span style="color:#d33">` with no error
  class, so the class filter missed it. — done: `drop_templates()` also drops span or strong
  elements that start with "Error:" and are red or link to an error category; `check.py` scans
  for them.
- **A session restart wiped the scratch dir** (2026-09-24). It held the ad-hoc Markdown checker
  and the pod's raw scores. The checker came back as `check.py`, a permanent tool. The lost raw
  scores for since-removed texts mean restored blocks had to be re-scored. — Keep anything
  needed later (tools, raw model scores) in the repo's data folder, not in scratch. — done:
  `check.py`, `translate.py`. todo: keep pod score outputs under `raw/bpb/`.

- **The Mac's disk filled up, and a build died writing its temp file** (2026-09-24). 127 MB of
  228 GB were free, mostly used outside this work. The atomic writes kept the corpus intact, but
  a 20 MB partial `orwiki-20260901.tmp` was left behind. Freeing this session's scratch (browser
  profiles, test copies, a corpus copy: ~510 MB) let the rebuild finish, byte-identical. —
  Check `df` before long jobs, and clean scratch as you go, not at the end. — done:
  `atomic_write()` in `prepare.py` removes the temp file on any failure (tested with a
  simulated ENOSPC).
- **A cached pod image makes small GPU jobs nearly free** (2026-09-24). The re-score pod
  (RTX 4000 Ada, $0.28/h) was ready in 13 s instead of 7 minutes and cost $0.02 for 25 s of
  scoring. The same texts re-scored on a different GPU came out bit-identical for 102 of 200,
  and within 2.2% for all, so bf16 noise comes from batch neighbours, not the GPU model. —
  done: a recheck sample goes with every partial re-score. idea: keep the same image tag for
  quick jobs.
- **Report prose that names examples goes stale when the data changes** (2026-09-24). Most
  examples named in `quality/bpb.md` (radio listings, PVC citations, CX markup) disappeared with
  the cleanup. — done: bpb.md now picks its examples from the current data, and METHODOLOGY.md
  names the queue as it stands after the re-score.

- **A pipe inside a code span still splits a GFM table cell** (2026-09-24). The methodology
  page's tables had `{{TownType|M}}`, `ଡାହାଣ|thumb` and `{| … |}` in code spans. GFM splits
  cells on those pipes, and markdown-it quietly pads or truncates the rows, so a cell-count
  check through the parser passes anyway. — Escape `|` as `\|` inside table cells, even in
  code; check by counting unescaped pipes per row against the header. — done for
  METHODOLOGY.md; `table_markdown()` already escaped cell pipes.

- **Wikimedia services were slow or lagging, so use mirrors and plain reads** (2026-09-24).
  - dumps.wikimedia.org served 2–3 kB/s. The official ACC Umeå mirror did ~400 kB/s, with SHA-1
    still checked against Wikimedia's `dumpstatus.json`.
  - The Wikidata query service was 5 hours behind. Because `maxlag` includes that lag, even
    `wbgetentities` reads with `maxlag` were refused.
  - Wikidata models concepts such as rice with only P279, and drugs look like chemicals.

  — done in `annotate.py`: mirror first; serial `wbgetentities` without `maxlag`, only for items
  that need it; P279 used when P31 is missing; P2175 marks drugs.
- **`uv run --with …` inside the repo creates a root `.venv`** (2026-09-24). The tagging agent did
  this once and deleted it at once. — Use `--script` or `--no-project`, and run from scratch. —
  done.
- **Lowercasing regex patterns while normalising them turned `\S` into `\s`** (2026-09-24). —
  Normalise the text, not the patterns. — done in `annotate.py`.

- **Short pod jobs: stock flickers, image pulls dominate, and nothing auto-terminates**
  (2026-09-24). A6000 Secure stock switched between Low and none, so the first create failed. A
  bounded retry loop created exactly one pod. The image pull took 7 of 30 minutes. runpodctl
  v2.14 `pod create` has no terminate-after option. — done: explicit termination, verified with
  `pod list`. idea: a lighter cached image, and a watchdog that terminates on a deadline.
- **pandas turns missing flags into NaN, which is truthy** (2026-09-24). That flagged all 2,782
  tables for review instead of 57. — Test `isinstance(x, str)`, not truthiness, on columns with
  nulls. — done in `score_bpb.py`.

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

## Web app (edaapp)

- **A side file looked like a second corpus** (2026-09-25). `removed-blocks.jsonl` has `id` and
  `text`, so the rule "any id + text table is a corpus version" listed it as one. — Structural
  discovery needs reserved names for side files. — done: `excluded.jsonl` and
  `removed-blocks.jsonl` are reserved, with a test.
- **JSON lines lose Parquet's schema** (2026-09-25). DuckDB's reader turned ISO strings in
  `translation.jsonl` into timestamps (dropping `T`/`Z`) and UUID-like strings into UUIDs. —
  Type JSONL explicitly. — done: types are inferred over every record, with dates and UUIDs kept
  as strings. Inference is 0.61 s of the 0.77 s spent loading five JSONL files. idea: declare
  column types in the sidecar `.json`.
- **A leftover Parquet would have been served instead of a newer JSONL** (2026-09-25). — When
  two copies exist, read the newer and say which. — done. Also `*.tmp` build writes are ignored,
  so they no longer trigger reloads.
- **Excluded view** (2026-09-25): counts per reason, a filter and search, and links to the dump
  revision and the current page. Duplicates link to the kept article. A second tab shows the
  removed blocks. Overview's "pages left out" reads `excluded.jsonl` and flags any disagreement
  with the build's counts. 160 tests pass (132 before).
- **Smaller fixes** (2026-09-25). The chart grid's 420 px minimum overflowed phone widths (fixed;
  the Overview's build-statistics table is still 15 px too wide). A "²" search crashed
  `int()` after `isdigit()` (fixed). idea: natural sort for titles with digits; hide constant
  columns (`bot_created` is now all false) from Browse's defaults; compute Overview histograms
  in DuckDB (the app uses 591 MB with everything loaded).

- **Methodology and Review first pages, found by file name** (2026-09-24). `METHODOLOGY.md` at
  the dataset root → a Methodology page (contents, anchors, live reload). `quality/review-first.md`
  → a Review first page: items link to the flagged paragraph, show live verdict badges, filter by
  type, and have Keep/Drop/Fix and drop-¶ buttons in place. `id N`, `quality/x.md`,
  `annotations/x.parquet` and `README.md`/`LEARNINGS.md` in any rendered document become links,
  but only to targets that exist.
- **Follow a flagged paragraph by content across rebuilds** (2026-09-24). The 22:24 cleanup
  rebuild edited 16 of the 50 review-first paragraphs in place and removed 7. With a sha1 check
  alone, the edited ones would have looked "gone". — done: the page reports each paragraph as
  same, moved, changed or gone, from `para_sha1`, and counts staleness per paragraph (8,452
  stale paragraphs right after the rebuild). idea: have `review-first.md` carry `para_sha1`
  itself.
- **Documents need stricter math rules than the corpus** (2026-09-24). Rendered with the
  articles' dollar rules, "$0.53/h … $0.26" in METHODOLOGY.md became a formula. The corpus
  escapes its dollars; hand-written documents don't. — done: `render_doc` uses stricter rules.
- **Separate document edits from data changes in live reload** (2026-09-24). Every save of
  METHODOLOGY.md made Browse, Overview and the queue reload. — done: a separate `tables` counter
  in `/api/changes`.
- **Checks run with their own review state** (2026-09-24). — done: `--state PATH` (must be inside
  `edaapp/`), plus a pytest guard that fails the run if the real `state/` changes. todo: make
  "checks use `--state .cache/…`" a rule for agents working on edaapp.

- **The review file needs full-state events** (2026-09-24). `build` applies only the latest
  event per article and matches dropped paragraphs by sha1. So an event that leaves out an
  earlier paragraph drop brings that paragraph back on the next build. — Each event must carry
  the article's complete current decision. — done: edaapp carries earlier drops forward (with a
  "forget them" button), keys drops by sha1, and never drops paragraph 0; the dataset README
  states the rule.
- **Verification writes polluted the real review log** (2026-09-24). The app agent's
  end-to-end checks left 215 fake decisions in `edaapp/state/reviews.jsonl`, and the build would
  have applied them. It archived them to scratch and deleted the file. — Test runs need their
  own state dir. — idea: a `--state` option for edaapp.
- **`node --check file.js` passes ES modules with syntax errors** (2026-09-24, Node 22.20). It
  doesn't parse `.js` as a module without `"type": "module"`. — done: use
  `node --input-type=module --check < file` (in edaapp's README).
- **`sandbox-exec` with a deny-writes-outside-edaapp profile is a cheap, strong check that a
  local app writes nowhere else** (2026-09-24). — done for edaapp; idea: reuse it for
  `odia-review` and other local tools.
- **Loading the corpus into in-memory DuckDB beat querying Parquet directly** (2026-09-24).
  Regex search ran 3–10× faster (e.g. 0.16 → 0.03 s), and queries stay consistent while an agent
  replaces a file atomically. The cost is ~230 MB of RAM. — done: `store.py` reloads on on-disk
  changes only.
- **Dropping a "paragraph" drops a whole list block** (2026-09-24). A list counts as one
  paragraph, so an IMDb-link regex matched 589 list blocks where only 442 were one-line. —
  idea: support dropping single list items, or split list items into their own paragraphs in
  the contract.

## Process

- **Parallel sessions race on the repo `LEARNINGS.md`** (2026-09-24). Another session was
  editing `LEARNINGS.md`, `README.md` and `translations/` during this work. — done: this file,
  at the user's request.
- **I sent the user's email in a request header before asking** (2026-09-24). Test requests
  to Wikimedia carried it in the user-agent. — Ask before putting personal contact details
  into any outbound request. — done: asked, and the email now comes from an env var at run
  time and is never written to a file.
