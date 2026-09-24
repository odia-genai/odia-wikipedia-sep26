# Methodology: Odia Wikipedia for LLM training

This page records every step and rule that turns Odia Wikipedia into the training corpus in this folder. Each rule says what it does, why it exists (the evidence that led to it) and what it changed (counts), and names the code and the report behind it. When a step is added or changed, this page is updated in the same change (see [How to keep this page current](#how-to-keep-this-page-current)).

## Summary

The corpus is every article of the **2026-09-01 Odia Wikipedia dump**, rendered by Wikipedia itself, converted to GitHub-flavoured Markdown, cleaned, checked and annotated.

| | |
|---|---|
| Articles | 18,695 (of 21,095 main-namespace pages that are not redirects) |
| Odia words | 4,512,644 (runs of Odia letters; digits are ASCII) |
| Characters / UTF-8 bytes | 34,810,662 / 87,307,918 |
| Sarvam-1 tokens | 12.82M, without the BOS token of each scored paragraph (150.0 per kB of scored text) |
| Paragraphs | 162,052, of which 143,357 are not the title |
| Data tables | 2,962 in 1,727 articles, 168,947 Odia words |
| Pages left out | 137 under 5 Odia words, 112 disambiguation, 7 exact duplicates, the main page |
| English inside articles | 899 paragraphs and headings translated into Odia (in 368 articles), 5,754 lists, tables and names kept as they are, 133 citations and 15 junk or reference blocks removed |
| Annotations | topics, machine-assisted translation, Sarvam-1 bits per byte (article and paragraph), a review-first queue |

The steps are scripts of their own, all in this folder:

1. `prepare.py`: `download` (the dump), `render` (Wikipedia's HTML of each article), `build` (HTML to Markdown, cleaning, translation insertion, filtering, outputs, `README.md`).
2. `check.py`: checks the built corpus. Every article must parse as intended Markdown, pandoc must agree on a sample, and no cleaned-away residue may be left.
3. `translate.py`: translation rounds for the English prose left in articles. It makes work batches, checks and merges the translations, and marks junk.
4. `annotate.py`: topics and translation flags from the dump's metadata tables.
5. `score_bpb.py`: Sarvam-1 bits per byte on a GPU pod, and the review-first ranking.

The outputs are `orwiki-20260901.jsonl` / `.parquet` (one record per article), `markdown/<title>.md`, `annotations/*.parquet`, and the reports in `quality/`. The fields are described in `README.md`, and what each step taught us is in `LEARNINGS.md`.

Reading this page: sections 2 to 6 are the build (the order in which text passes through `prepare.py`), section 7 the annotations, section 8 the human review loop, and the changelog at the end lists every change by date.

## Source and snapshot

**Rule.** Use the latest complete `pages-articles` dump, `orwiki-20260901-pages-articles.xml.bz2` (41,403,955 bytes, SHA-1 `4ace67b6c5412a78565e463066f9450bfff6df6b`, checked against the dump's `dumpstatus.json`). Every main-namespace page that is not a redirect (21,095 pages) is taken at **the exact revision in the dump**.

**Why a dump.** It is a fixed, citable snapshot: each record keeps its `revid`, so `https://or.wikipedia.org/w/index.php?oldid=<revid>` shows exactly the source text, and a rebuild gives the same corpus.

**Why rendered HTML, not wikitext.** Odia articles build whole sentences out of templates. Stripping the wikitext, as the Hugging Face `wikimedia/wikipedia` build does, leaves holes in them. In the dump's articles:

| Template | Pages | What stripping loses |
|---|---:|---|
| `{{PAGENAME}}` | 1,647 | the subject of the sentence: `'''{{PAGENAME}}''' ଏକ ଭାରତୀୟ {{TownType\|M}}` becomes "ଏକ ଭାରତୀୟ  ।" |
| `{{TownType}}` | 884 town stubs | the kind of town |
| birth and death date templates | 4,176 | the dates in biography leads |
| `{{flag}}` | 2,657 | country names |
| `{{convert}}` | 360 | quantities and units |

Many of these are Lua modules, which only MediaWiki can run. So `prepare.py render` fetches Wikipedia's own rendering (Parsoid HTML) of each dump revision from `https://or.wikipedia.org/w/rest.php/v1/revision/<revid>/html`: what a reader sees, with every template expanded.

**How it ran.** 21,095 of 21,095 revisions rendered (all HTTP 200), 6 parallel requests, about 3 pages/s, about 2 hours, cached in `raw/html/20260901/` in 43 chunks of 500 pages (150 MB compressed). Wikimedia throttles bulk clients without contact details to about one request a minute, so `render` requires `ODIA_WIKI_CONTACT` in the user-agent (never written to a file) and honours `Retry-After`. Transient network errors (including a response cut off mid-body, which crashed one run) are retried, and a page that still fails is saved as failed and refetched on the next run (`NETWORK_ERRORS`, `FINAL`).

## Extraction: HTML to Markdown

`html_to_text()` in `prepare.py` walks the Parsoid DOM once and emits blocks (heading, paragraph, list item, table). The rules below apply in this order: whole sections, then templates, then elements.

### Sections dropped

**Rule.** A section whose heading is a reference or link heading is dropped with all its subsections (`DROP_SECTIONS`, matched through `heading_key()`).

| Group | Headings |
|---|---|
| References and notes | ଆଧାର, ଆଧାରସୂତ୍ର, ଆଧାର ସୂତ୍ର, ଆଧାର ଗ୍ରନ୍ଥ, ଆଧାରଗ୍ରନ୍ଥ, ଆଧାର ଓ ଟୀକା, ଟୀକା, ଟୀକା ଓ ଆଧାର, ପାଦଟୀକା, ଟିପ୍ପଣୀ, ଉତ୍ସ, ତଥ୍ୟସୂତ୍ର, ତଥ୍ୟ ସୂତ୍ର, ସୂତ୍ର, ଗ୍ରନ୍ଥସୂଚୀ |
| See also | ଆହୁରି ଦେଖନ୍ତୁ, ଆହୁରି ଦେଖିବେ, ଅଧିକ ଦେଖନ୍ତୁ, ଏହା ବି ଦେଖନ୍ତୁ, ଦ୍ରଷ୍ଟବ୍ୟ |
| Further reading | ଆହୁରି ପଢ଼ନ୍ତୁ, ଅଧିକ ପଢ଼ନ୍ତୁ, ଅଧିକ ପଠନ, ଆଗକୁ ପଢ଼ିବେ |
| External links | ବାହାର ଲିଙ୍କ, ବାହାରର ଲିଙ୍କ, ବାହାର ଲିଂକ, ବାହ୍ୟ ଲିଙ୍କ, ବାହ୍ୟ ସଂଯୋଗ, ବାହାର ସଂଯୋଗ, ବାହାର ଯୋଗସୂତ୍ର, ବାହ୍ୟ ଯୋଗସୂତ୍ର, ବାହାର ଆଧାର, ବାହାର ତଥ୍ୟ, ବାହାର ସ୍ରୋତ, ଅଧିକ ତଥ୍ୟ |
| Galleries | ଗ୍ୟାଲେରୀ, ଗ୍ୟାଲେରି, ଚିତ୍ର ଗ୍ୟାଲେରୀ, ଚିତ୍ରଶାଳା, ଚିତ୍ରାବଳୀ, ଛବି |
| English, left untranslated | see also, notes, note, references, reference, further reading, external links, external link, bibliography, sources, citations, footnotes, works cited, notes and references, references and notes, gallery |

**Spelling variants, by pattern** (`DROP_SECTION_PATTERNS`, `reference_heading()`). The same headings are typed with a virama more or less and in different words: "ବାହାର ଲିଙ୍କ୍" (64 sections), "ଏହା ମଧ୍ୟ ଦେଖନ୍ତୁ" (62), "ବାହ୍ୟ ଆଧାର" (20), "ଆଉରି ଦେଖନ୍ତୁ" (20), "ଏହା ମଧ୍ୟ ଦେଖିବେ", "ଅଧିକ ଜାଣିବା ପାଇଁ ପଢ଼ନ୍ତୁ", "ପଠନ ତାଲିକା". They are matched on `heading_key()`:
- a heading ending in ଦେଖନ୍ତୁ or ଦେଖିବେ ("see …")
- a heading ending in ପଢ଼ନ୍ତୁ ("read …")
- ବାହ୍ୟ / ବାହାର / ବାହର / ଅନ୍ୟାନ୍ୟ followed by ଲିଙ୍କ, ଲିଂକ, ଆଧାର, ସଂଯୋଗ, ଯୋଗସୂତ୍ର, ସ୍ରୋତ, ତଥ୍ୟ or ଉତ୍ସ
- a bare ଲିଙ୍କ / ଲିଂକ
- ପଠନ ତାଲିକା

Content headings that share a word stay, e.g. ଉତ୍ସବ ("festival"), ଖାଦ୍ୟ ଉତ୍ସ ("food sources") and ଆର୍କିମିଡିସଙ୍କ ସୂତ୍ର ("Archimedes' principle"). Found on 2026-09-24 by scanning the remaining headings for reference words.

**How the list was found.** By measurement, not guessing: every section heading in the first 2,500 rendered pages was ranked by external links, list items and Odia characters per section. ଅଧିକ ତଥ୍ୟ ("more information") appeared 390 times and was always external links; ଦ୍ରଷ୍ଟବ୍ୟ (80 sections, ~51 list items each) was "see also" lists. Headings are compared after `heading_key()`: lower-cased, spaces collapsed, a trailing colon removed and **nukta letters folded**, because the same heading is typed with precomposed ଡ଼/ଢ଼ (U+0B5C/U+0B5D) or with ଡ/ଢ + nukta (ପଢ଼ନ୍ତୁ both ways).

### Templates dropped

**Rule.** Parsoid marks every node a template produced with one `about` id and names the template in `data-mw`. `drop_templates()` removes everything a template produced when:

- its name is in `DROP_TEMPLATES` (lower-cased; `*` matches a prefix or suffix): archive and dead-link notes (`webarchive`, `wayback`, `dead link`), citation-needed (`citation needed`, `cn`, `fact`), maintenance banners (`wikify`, `cleanup*`, `unreferenced*`, `refimprove*`, `more citations needed*`, `pov*`), pronunciation (`ipa*`, `ipac-*`, `respell`, `audio*`, `pronunciation*`), coordinates (`coord`), sister projects (`commons*`, `wiktionary*`, `wikiquote*`, `wikisource*`, `wikivoyage*`, `sister project*`), `authority control`, `portal*`, stubs (`ମୁଣ୍ଡିଆ*`, `ଅଧାଗଢ଼ା`, `ଅଧାଗଢା`, `stub*`, `*-stub`), `ବଟ୍ ତିଆରି` ("bot-created"), reference lists (`ଆଧାର`, `reflist`, `notelist`), date-format markers and hatnotes (`about`, `other uses*`, `redirect*`, `distinguish`, `main`, `see also`, `further`, `for`);
- its output is only an error message (starts with ତୃଟି, "error"), e.g. "ତୃଟି: the {{Wikify}} template is deprecated";
- it is a red link to a missing template (text starting `ଛାଞ୍ଚ:` or `Template:`). One exception keeps content: `{{flag}}` without its country data shows "ଛାଞ୍ଚ:Country data ହଂକଂ"; the country name (ହଂକଂ) is kept and the prefix dropped (36 territories in one list article).

**Why by template name.** It is exact: the whole output goes, including sibling nodes, and nothing else. The Wayback notes ("Archived 2015-03-17 at the Wayback Machine."), for example, were 264 hits in 53 of the first 500 pages before this rule and 0 after.

### Elements dropped

`droppable()` removes an element (keeping its tail text) when any of these match:

| Test | Values | Removes |
|---|---|---|
| tag (`DROP_TAGS`) | style, script, link, meta, figure, figcaption, img, table, audio, video, map, noscript, caption | images, captions, layout tables (data tables are handled first, see below) |
| class (`DROP_CLASSES`) | reference, mw-ref, references, reflist, navbox, infobox, metadata, ambox and other message boxes, hatnote, noprint, mw-empty-elt, gallery, thumb, coordinates, geo, asbox, stub, authority-control, citation, IPA, side-box, NavFrame, sidebar, succession-box, … (62 classes) | citations, infoboxes, navboxes, banners, hatnotes, sister-project boxes, collapsible navigation |
| typeof (`DROP_TYPEOF`) | mw:Extension/ref, references, gallery, templatestyles, graph, timeline, mapframe, maplink, imagemap, score, inputbox; mw:File; mw:Error | reference markers, galleries, maps, embedded media |
| role (`DROP_ROLES`) | note, navigation, presentation, figure | hatnotes, navigation, layout |
| style | `display:none` | hidden text |

### Link-only list items

**Rule.** A list item is dropped when it holds an external link, a `<cite>` or an ISBN **and** less than half of its characters are Odia (`link_only_item()`).

**Why.** External-link and bibliography lists also sit under headings the section rules don't know. In the first 500 pages, lists like "India profile from the BBC News" survived under ଅଧିକ ତଥ୍ୟ and ବାହାର ତଥ୍ୟ before this rule.

**Known gap, fixed.** A link written in Odia passes this test: "- ଇଣ୍ଟରନେଟ ମୁଭି ଡାଟାବେସରେ …" (IMDb) lines survived it. They are now dropped by template name; see [Text cleanups](#text-cleanups-2026-09-24).

### Data tables kept as Markdown

**Rule.** A table with class `wikitable` (and none of the dropped classes, not nested in another table) becomes a GitHub Markdown table (`is_data_table()`, `table_markdown()`). Infoboxes, navboxes and layout tables are still dropped.

- A cell spanning rows repeats its text in each row it covers; a cell spanning columns fills only its first column (repeating it across columns duplicated whole notes, e.g. in the chief-ministers list).
- Columns empty in every data row (image columns) are removed.
- Cells are cleaned like paragraphs, and `|` inside a cell is escaped as `\|` (also inside math).
- A table needs at least two non-empty rows.

**Why.** Dropping all tables lost real knowledge: in the first 2,500 articles, tables held 9% as much Odia text as the prose, almost all of it in list articles a school model needs (the 30 districts with area, population and literacy, chief ministers, constituencies, award winners). In the full corpus: 2,983 tables in 1,748 articles, 169,233 Odia words (3.7% of the corpus; the first chunks overstated the share because the oldest, biggest list articles come first in page-id order). For prose only, drop the lines that start with `|`.

### Math

**Rule.** Math elements become a placeholder (`<n>`) while the text is cleaned and escaped; the TeX source (from Parsoid's `data-mw`) goes back at the very end as `$…$`, or `$$…$$` when the formula is display math or is a paragraph on its own (`tex_of()`, `put_math_back()`).

**Why.** Every cleaning pass would otherwise run over TeX: `x^{{2}}` and `f''` would lose braces and primes to the wikitext-residue rule, and bracketed numbers to the citation rule. The dump has 414 formulas in 43 pages; the corpus has 108 math paragraphs.

### Lists and nesting

**Rule.** Items get `- ` or `1. `; a sublist is indented to its parent's content column (2 spaces under `- `, 3 under `1. `), and continuation lines line up with the item's text (`Writer.walk_list()`). An item that holds only a sublist is skipped as a level, so the indentation never jumps.

**Why.** CommonMark reads a sublist under `1. ` indented by 2 as a new top-level list, and an indentation jump of 6 spaces (taxonomy trees with text-less levels, e.g. the dinosaur classification) as an indented code block.

## Cleaning and normalisation

Each block's text is cleaned by `clean_block()` in this order: drop the block if it is residue; convert digits; strip residue inside it; fix danda typing; tidy parentheses and spaces; escape for Markdown (see [Markdown correctness](#markdown-correctness)). Page-wide normalisation runs last.

### Blocks dropped as residue

A block is dropped entirely when it contains:

| Pattern | Constant | Example |
|---|---|---|
| a file name or image option followed by `\|` | `FILE_RESIDUE` | `[[{{}}]] Yatra Puri 07-11027.jpg\|thumb\|right\|250px\|…` |
| wikitable row syntax: `\|\|…\|\|`, or a line starting with `\|`, `{\|`, `\|-`, `\|}` | `TABLE_RESIDUE` | a poets list typed as broken table markup |
| raw Parsoid HTML pasted as text: `data-mw=`, `data-cx=`, `about="#mwt`, `typeof="mw:` | `HTML_RESIDUE` | a Content Translation bug in a FIFA-ranking table |
| no letter or digit at all | | a lone `।` or `,` |
| only a template name | | `Template:Infobox medical intervention` |

**Also dropped** (2026-09-24):
- A block that is only a category link: `Category:…`, `[[ଶ୍ରେଣୀ:]]` or `ଶ୍ରେଣୀ:<name>`, brackets or not. A leftover like this sat at the end of 2 articles. "ଶ୍ରେଣୀ: ସ୍ତନ୍ୟପାୟୀ" (Odia for "class: mammals") has a space after the colon and stays.
- English template error messages, e.g. "Error: {{Lang}}: text has italic markup (help)" and "Error: This is not a valid number …". There were 59 of them, in the red span MediaWiki prints them in, or a `strong.error`, linking to an error category. They are removed in `drop_templates()`. A `print("Error:")` inside a code example stays.

### Residue stripped inside a block

| What | Constant | Example |
|---|---|---|
| magic words | `MAGIC_WORD` | `__LEAD_SECTION__`, `__NOTOC__` |
| literal wiki/HTML tags | `LITERAL_TAG` | `<poem>`, `</right>`, `<meta />` (span, div, br, small, center, font, ref, sup, sub, nowiki, gallery, …) |
| image options | `THUMB_TOKEN` | `ଡାହାଣ\|thumb` (ଡାହାଣ is "right") |
| image sizes | `PX_RESIDUE` | `70px` in a table cell |
| bare URLs typed in prose | `URL` | `(https://sites.google.com/…)`; 57 in 45 pages |
| hand-typed reference markers | `MANUAL_CITE` | `[2]`, `[୪]` (not `a[1]` in code: not after a Latin letter or digit) |
| broken wikitext | `WIKI_RESIDUE` | `[[`, `]]`, `{{`, `}}`, `''` |
| lines with no letter or digit | | a `।` left on its own line |
| parentheses emptied by removed pronunciations | | `ବଙ୍ଗଳା ଭାଷା (),` → `ବଙ୍ଗଳା ଭାଷା,`; `(; বাংলা)` → `(বাংলা)` |

### Digits to ASCII

**Rule.** Odia digits `୦–୯` become `0–9` in text, headings, tables and math (`ODIA_DIGITS` from `odia_text.py`, applied in `clean_block()`, `md_heading()` and `put_math_back()`). The `title` field keeps the page name as it is on Wikipedia (`୨୦୦୬`), while the heading in `text` reads `# 2006`. 689,743 digits were converted in the text.

**Why.** The owner's decision (2026-09-24): numbers should look the same everywhere in training. Wikipedia mixes both systems: 2,751 articles used ASCII and Odia digits side by side.

**Order matters.** Conversion runs before escaping. After it, a paragraph "୧. ବିଧାୟିକା" is `1\. ବିଧାୟିକା` (a paragraph); converting after escaping would have made it a numbered list.

**Side effect.** Odia word counts fell from 4.73M to 4.53M because runs of Odia digits no longer count as words.

### Danda typed as `|` or U+0B64

**Rule.** A `|` after Odia text (or a closing quote or bracket after it) and before a space or line end becomes the danda `।`, and `||` becomes `॥` (`PIPE_DANDA`). The unassigned code points U+0B64 and U+0B65 become `।` (U+0964) and `॥` (U+0965) (`RESERVED_DANDA`).

**Why.** Typists use both. `|` as danda was in 367 of the first 500 pages (mostly the year-page template sentence "… ଏକ ସାଧାରଣ ବର୍ଷ ଅଟେ |"); U+0B64 was used 191 times in 48 of the first 2,000 pages. Unicode reserves U+0B64/65 and says to use U+0964/65, so any tokenizer sees junk. The context rule leaves real pipes (math `|x|`, table syntax) alone.

### Characters and spacing

- `normalize_odia()` from `odia_text.py`: ୟ written as ଯ + nukta (U+0B2F U+0B3C) becomes U+0B5F, the spelling native Odia text uses (Sarvam-1 spends 4 tokens instead of 1 on ବ୍ୟକ୍ତି spelled the other way).
- Removed: soft hyphen, zero-width space, word joiner, BOM (`INVISIBLE`) and C0/C1 control characters (`CONTROL`).
- Runs of spaces, tabs and no-break spaces collapse to one space (`SPACES`). **ZWJ and ZWNJ stay**: Odia spelling uses them (କମନୱେଲ୍‍ଥ).
- **No NFC or other Unicode normalisation**, by repo rule: NFC decomposes ଡ଼/ଢ଼ (composition exclusions) and shifts text away from what tokenizers saw.
- Line breaks from `<br>` are kept inside a block (verse, Sanskrit ślokas).

## Markdown correctness

`text` is GitHub-flavoured Markdown: `# title`, the lead, `##`/`###` headings, paragraphs separated by a blank line, lists, GFM tables and `$…$` math. Links and emphasis are reduced to their text.

### Escaping

Text from the page must never turn into markup, so `md_escape()` runs last on every block (not on list prefixes or math):

| Where | Rule | Example |
|---|---|---|
| anywhere | `\` before ASCII punctuation → `\\` | |
| anywhere | `*`, `` ` ``, `$` → `\*`, `` \` ``, `\$` | `3\*4`, minute marks `୨୧°୩୦\``, `US\$` |
| anywhere | `_` not between word characters → `\_` | `snake_case` untouched |
| anywhere | `<` before a letter, `/`, `!` or `?` → `\<` | `\<alt>+\<F4>` |
| anywhere | `](` and `][` → `\](`, `\][` | `[ସଙ୍ଗିତ : ରିତୁରାଜ୍](2005)` |
| anywhere | `~~` → `\~~` | |
| line start | `#`, `>`, `\|`, `-`/`+` before a space, `1.`/`1)` before a space, lines of `=` or `-` | the MATLAB prompt `\>> x = 17` |
| headings | a trailing `#` run is escaped | |
| table cells | inline rules only; pipes inside a cell are backslash-escaped | |

The corpus has 2,489 escapes in 534 articles (71 per million characters), mostly `1\.` at the start of numbered-looking paragraphs and literal `*`, `_` and `$`.

### How validity was checked

Every article is parsed with markdown-it (CommonMark plus GFM tables and strikethrough), and the parsed structure is compared with what the text is meant to hold: the same number of headings, list items and tables, and no emphasis, links, code, HTML, block quotes, rules or setext headings. Math is masked first, as a math-aware renderer would treat it.

- Before the escaping and list fixes about 100 articles failed. Since then **all 20,834 parse exactly as intended**.
- pandoc's GFM reader (`gfm+tex_math_dollars`) agrees on a random sample of 405 articles, including the math, taxonomy and table articles.
- markdown-it's default nesting limit (20) flags one valid 10-level sauropod tree; the check raises the limit.

## Filtering

`build()` leaves out:

| Reason | Pages | Rule | Examples |
|---|---:|---|---|
| under 5 Odia words | 137 | fewer than 5 runs of Odia letters in the body after cleaning (`--min-words`) | one-line stubs: ଛତିଶଗଡ଼ ("ଛତିଶଗଡ଼, ଭାରତର ଏକ ରାଜ୍ୟ ।"), ତ୍ରିପୁରା, ଲାକ୍ଷାଦ୍ୱୀପ |
| disambiguation | 112 | Parsoid's `mw:PageProp/disambiguation` | ଓଡ଼ିଆ, ବୌଦ୍ଧ, ସମାଜ, ସମୟ |
| exact duplicate | 7 | the same body text as an earlier article (sha1) | ଏକିନୋକୋକୋସିସ, a copy of ଏକିନୋକୋକୋସିସ ସଂକ୍ଷିପ୍ତ |
| mostly English | 2 | what is left after taking out citations is under 25 Odia words (`gutted()`) | ଆବ୍ରୋସରସ and ଈଲୋସରସ, whose English is all citations |
| main page | 1 | ପ୍ରଧାନ ପୃଷ୍ଠା is in the article namespace | |

Every left-out title and its reason is in `orwiki-20260901-build.json`. Stubs (1,910) and bot-created pages (1,495) are kept and flagged (`stub`, `bot_created`), not dropped. Articles whose `odia_ratio` is under 0.6 (332, 56,627 Odia words) are kept; `odia-build-cpt`'s default `--min-odia-ratio 0.6` skips them.

## Annotations

Extra fields per article live in `annotations/`, one Parquet file each, joined on `id`; `*.paragraphs.parquet` files have one row per paragraph (`id`, `para`), where paragraph `i` is `text.split("\n\n")[i]` and paragraph 0 is the title. Annotations that depend on the text carry `text_sha1` or `para_sha1`, so a rebuild that changes text makes them visibly stale. Each file has a JSON sidecar describing its columns.

### Topics

`annotations/topics.parquet`, built by `annotate.py topics`; report `quality/topics.md`, hand labels `quality/topics-check.tsv`.

**Purpose.** Let the school training mix favour science, history, geography and Odisha over film and sports biographies (experiment E04 found random Odia Wikipedia paragraphs are mostly film-star and athlete bios).

**Vocabulary (17 topics).** calendar, film, sports, health, biology, mathematics, science, technology, economy, education, religion, literature, arts, history, politics, society, geography.

**Evidence, strongest first.**

1. The article's categories, from the rendered HTML (including template-added ones), minus hidden (`hiddencat`) and maintenance categories. A category name is matched against ordered keyword rules after folding (nukta, vowel length, ଵ/ୱ, ୟ/ଯ, ZWJ/ZWNJ, digits; for matching only, never NFC). Specific rules come before general ones, and words match at the start of an Odia word (ପର୍ବ "festival" does not match ପର୍ବତ "mountain"). People categories (births, living people, "people of X") carry no topic, so a biography gets the topic of its occupation. A bare district is weak geography evidence; a category with no match walks up to 3 parent levels at half weight.
2. The title: year and date pages are `calendar`; a qualifier such as `(ଓଡ଼ିଆ କଥାଚିତ୍ର)` is read like a category.
3. Wikidata, only for the 2,767 articles with no direct evidence: P31 (or P279), P106 (occupation) and P2175 (a drug).

**Flags.**

- `is_person`: a biography.
- `odisha`: a category or the title names Odisha, Odia, Ollywood, Utkal, Kalinga, Jagannath, a district or a main town.
- `school_relevant`: the primary topic is a school subject (science, biology, health, mathematics, technology, geography, history, politics/civics, economy, literature and language, society, education), or arts/religion when `odisha`. For a biography only science, biology, health, mathematics, technology, history and literature count.

**Results.**

- Coverage: 18,211 articles (97.4%, 96.4% of words) have a topic; 482 have none.
- Largest topics by words: health 15.0%, film 11.6%, politics 11.4%, religion 10.8%, literature 9.2%, arts 7.9%, geography 7.7%.
- `school_relevant`: 10,704 articles (57.3%, 57.9% of words). `odisha`: 6,744 (36.1%). Biographies: 6,668 (35.7%); film and sports biographies are 9.6% of words.

**Precision** (title and lead read by hand; errors listed in `quality/topics.md`):

| Sample | Correct |
|---|---:|
| random 100, held out (labelled after the rules were frozen) | 96 of 97 tagged (99.0%; 95 of 97 at first scoring) |
| random 120 used to tune the rules | 118 of 118 (optimistic) |
| stratified, 8 per small topic | 65 of 72 (90.3%): science 5/8, society 5/8, education 7/8 |

**Lessons built in.** Category ancestry leaks: `geography` was reachable from 9,172 articles through "people of Odisha" → Odisha, so walks are shallow and bare places give no evidence. Keyword rules collide (ଲୋକ "people" inside ଲୋକ ସଭା; ସ୍ୱାଧୀନତା "independence" tagging pre-independence MLAs as history), so rules are anchored and checked on held-out labels.

### Machine-assisted translation

`annotations/translation.parquet`, built by `annotate.py translation`; report `quality/translation.md`.

**Signals.**

- Content Translation tags on revisions (`change_tag` dump): `contenttranslation` 5,834 revisions, `contenttranslation-v2` 4,166, `sectiontranslation` 137, `contenttranslation-high-unmodified-mt-text` 2.
- Revisions mapped to pages with `stub-meta-history` (every revision's page, time, contributor, English edit summary naming the source page and revision).
- **MDWiki**: WikiProject Medicine's dashboard runs Content Translation on mdwiki.org and publishes here without a tag; its edit summaries end `#mdwikicx`, and they are flagged too.
- `translated` (the recommended flag) = created by Content Translation or MDWiki, or the tools wrote at least half the page's bytes.

**Results.**

| Flag | Articles | Share of words |
|---|---:|---:|
| `ct_created` | 3,528 (18.9%) | 14.8% |
| `translated` | 3,845 (20.6%) | 16.2% |

- 1,936 of the 3,269 `health` articles are translations, 1,912 of them from MDWiki sources.
- Peaks: 2016 (859), 2024 (851), 2023 (599). Sources: English 3,505, MDWiki 300, Simple English 21, others 19.
- Translated articles hold **4× the English-dominant paragraphs** (3.1% vs 0.8% for comparable articles written in Odia), mostly untranslated table and list items, yet their mean `odia_ratio` is the same (0.900 vs 0.900–0.908). A page-level ratio filter cannot catch them.
- People edited them less afterwards: median 3 edits against 7.

**What the dumps cannot show.** Machine translation pasted in from outside the tools; anything before June 2015; how much machine output the translator kept.

### Sarvam-1 bits per byte

`annotations/bpb.parquet` and `annotations/bpb.paragraphs.parquet`, built by `score_bpb.py`; report `quality/bpb.md`.

**Method** (matches odia-llm-trainer's harness, `src/odia_llm/evaluation/harness.py`, so numbers compare with the experiments):

- Model `sarvamai/sarvam-1` at revision `e9607337`, bf16, on one RTX A6000 (RunPod Secure, $0.53/h, 0.495 h, **$0.26**), 13.46M tokens at 16,487 tokens/s.
- Every paragraph except the title is scored from BOS. A paragraph over 1,000 characters is cut at whitespace (`split_text`) and the pieces' bits are summed. bpb = bits / UTF-8 bytes; article bpb = Σ bits / Σ bytes.
- Each paragraph gets a `kind` (text, heading, list, table, math), a percentile and a robust z of log bpb within its kind and length band, script shares, markup and verse flags, and `repeats` (how many articles share its sentence frame with names and numbers masked).

**Sanity checks.**

- Word-shuffled copies scored higher for 300 of 300 paragraphs (pooled 0.518 → 0.883).
- The repo harness gives the same pooled bpb to 0.04%, and `split_text` matches on all 148,745 paragraphs.
- bf16 is unbiased in total (0.5550 vs fp32 0.5551) but moves single paragraphs by a median 0.3% (max 3.4%); fp32 is exact. Flags are stable; ranks a few percent apart are not.
- Every paragraph and article has a row, and every `text_sha1`/`para_sha1` matches the corpus.

**Results.**

- Corpus bpb 0.5564; prose 0.5234; headings 1.14; lists and tables 0.69.
- Paragraphs are scored without the text before them, so short ones cost more. Matched for length, prose paragraphs of 1 kB and more score 0.4948, the same as base Sarvam-1 on odia-llm-trainer's held-out set (0.4951).
- Machine-assisted translations score like the rest (0.554 vs 0.556).
- Pages created after Sarvam-1's release score lower (median 0.519 vs 0.559): no sign of memorisation (correlational).
- **After the text cleanups** (re-scored, see [Re-scoring](#re-scoring)): corpus bpb 0.5500, prose 0.5219, prose paragraphs of 1 kB and over 0.4945. The high tail is now Odia text: 98% of the top 1% of prose is in Odia script, up from 81%, because the English blocks are gone.

**What bpb cannot see.** Boilerplate repeated across articles: each paragraph is scored alone, so a sentence frame found in 5 or more articles sits at the 58th percentile of its band. Repetition is detected by `repeats`, not by perplexity.

### Review-first queue

`review_rank`, `review_type`, `review_para` and `review_reasons` in `annotations/bpb.parquet`; the readable list is `quality/review-first.md`, and edaapp shows it as its Review first page and Review queue.

**Signals.** A paragraph is extreme when it is in the top or bottom 1% of its kind and length band (only text, list and math paragraphs of at least 100 B and tables of at least 200 B are compared: 96,838 paragraphs, 1,940 extreme). An article is extreme when its bpb is in the top or bottom 1% of articles of at least 500 B, or when most of its bytes are in extreme paragraphs. Script shares, common-English-word shares, markup leftovers, verse shape, near-copies and repeated table headers give each flag its type and its reasons.

**Types** (interleaved, so the top of the list shows every kind):

| Type | Meaning | In queue | Candidates |
|---|---|---:|---:|
| `garbled` | Odia the model finds very unlikely: OCR or typing damage (also verse and Sanskrit, ranked after prose) | 32 | 725 |
| `english` | a paragraph that is mostly English | 32 | 707 |
| `templated` | very predictable text (bottom 1%): formulaic sentences, lists, near-copies | 31 | 931 |
| `table` | a table in the top or bottom 1% of tables | 30 | 57 |
| `markup` | conversion leftovers (braces, tags, `class=`, namespace prefixes, URLs), any bpb | 13 | 14 |
| `script` | mostly another script, or Latin letters that are not English | 31 | 375 |
| `article` | the article as a whole | 31 | 455 |

200 articles are queued. The table shows the first run. The queue was rebuilt on the cleaned text (see [Re-scoring](#re-scoring)). By then 85 of its 200 flagged paragraphs had gone with the cleanups: 31 English, 22 other-script, 21 table, 11 markup.

The queue after the cleanups: garbled 35, english 35, templated 35, script 35, article 34, table 24, markup 2.

- The `english` pool shrank from 707 candidates to 66, and they are now mostly bilingual paragraphs: Odia with English quotes or glosses.
- Other script fell from 375 to 178, and markup from 14 to 2.

Its first items:
- OCR-garbled text (ବିଶାଳାକ୍ଷୀ ମନ୍ଦିର, id 49282, para 2)
- a mostly-English paragraph (id 97980)
- a repetitive list of volumes (ସ୍ୱାମୀ ବିବେକାନନ୍ଦ, id 18187)
- a repetitive table (ଲୋକ ସଭା, id 33455)
- a hidden category shown as text, `Category:ଜୀବିତ ବ୍ୟକ୍ତି]` (id 66231)
- a Latin-script film list (id 56180)
- a list article left as English headings and short items after the English removal (ଭାରତୀୟ ହ୍ରଦ ସମୂହର ତାଲିକା, id 56546, bpb 2.82)

## Human review loop

edaapp, the web app of odia-llm-trainer for browsing and reviewing datasets, shows this corpus with its annotations and records decisions as append-only JSON events, one per line, with the page id, a verdict (`keep`, `drop`, `fix` or none), a note, dropped paragraphs (index and sha1 of their text) and the sha1 of the text reviewed. They are kept here in `reviews/reviews.jsonl`; events appended by hand in the same format work too.

`prepare.py build` reads that file (`load_reviews()`, `apply_review()`):

- **The latest event per article wins**, so every event carries the article's complete decision. edaapp carries earlier paragraph drops forward, or a later event would bring those paragraphs back.
- An article marked *drop* is left out (reason "reviewer: drop" in the build JSON).
- Dropped paragraphs are removed **by the sha1 of their text, not by position**, so a decision survives rebuilds that shift paragraphs. Paragraph 0 (the title) is never dropped. A decision whose paragraph is no longer in the text is counted, not applied.
- *fix* verdicts are counted for follow-up; they change nothing by themselves.
- A half-written last line is skipped. `--no-reviews` builds without any decisions.

The counts are in the build JSON and `README.md`. So far no reviews have been applied.

## Text cleanups, 2026-09-24

Four problems found by the measurements and the review queue, fixed in `prepare.py`. The rebuild changed 2,530 articles and dropped 7 more pages that were left with under 5 Odia words (all near-entirely English, `odia_ratio` 0.02–0.21, e.g. ସାମରୋଜ ଆଜମି ଆଲଭୀ).

### External-link lines written in Odia

**Rule.** The output of external-link templates is dropped by template name, like the other templates in `DROP_TEMPLATES`: `imdb*`, `facebook*`, `instagram*`, `twitter*`, `youtube*`, `bollywood hungama*`, `official website`, `official`, `dmoz`, `curlie`, `cia world factbook link`, `allmusic*`, `discogs*`, `rotten tomatoes*`, `spotify*`, `linkedin*`. A list item left empty disappears.

**Why.** These templates write their line in Odia ("ଇଣ୍ଟରନେଟ ମୁଭି ଡାଟାବେସରେ ନନ୍ଦିତା ଦାସ", "ଫେସବୁକରେ ଶ୍ରେୟା ଘୋଷାଲ"), so `link_only_item()`, which looks for mostly non-Odia link items, kept them. A scan of all list items with an external link and at most 6 Odia words besides it showed where they come from: `imdb name` 1,162 and `imdb title` 954 items, then `facebook`, `instagram`, `twitter`, `bollywood hungama`, `official website`. Almost all other such items are citations, already dropped with the reference sections.

**Effect.** IMDb lines 608 → 1, Instagram 69 → 1, Facebook 57 → 13 and Twitter 29 → 4 (the rest are mentions in prose).

### Typos copied by bots

**Rule.** `TYPO_FIXES` in `prepare.py`: each entry is a pattern tied to the context the mistake appears in, so a genuine use of the same letters survives. Applied in `fix_residue()`, before escaping.

| Fix | Pattern | Fixed |
|---|---|---:|
| ପରୁଷ → ପୁରୁଷ ("male") | ପରୁଷ followed by ୋତ୍ତମ, ଙ୍କ, ମାନ, or a space and ହୋଇଥିବା / ଓ | 917 |

**Why.** A bot's census sentence ("… % ଜଣ ପରୁଷ ହୋଇଥିବା ବେଳେ …") has the typo in every copy (904), and the same slip appears in a few hand-written forms (ପରୁଷଙ୍କ, ପରୁଷମାନଙ୍କ, ପରୁଷୋତ୍ତମ). ପରୁଷ is also a real word ("harsh"), so the fix is not a blind replacement. The corpus has no genuine use of it.

**Not covered.** Two other misspellings with the same letters: ପରୁଷ୍କାର (for ପୁରସ୍କାର "award", ମହେଶ ବାବୁ) and ପରୁଷାମାନଙ୍କ (ହେରେଡିଟାରି ଆଞ୍ଜିଓଇଡିମା).

### Conversion leftovers

**Rule.** `fix_residue()` in `prepare.py`, applied to every block and table cell after the digit conversion and before escaping:

| Leftover | Rule | Fixed |
|---|---|---:|
| Content Translation anchors `<a href=… class="cx-link" data-linkid=…>Dryvax</a>` pasted as text | `ANCHOR_TAG`: strip `<a …>` with attributes and `</a>`, keep the link text; a bare `<a>` (the URL article discusses it) stays | 24 |
| HTML entities shown as text | `&amp;` → `&` ("Channapatna Toys &amp; Dolls"), `&#13;` removed; other numeric entities stay (the article on ୡ lists them on purpose) | 26 |
| Raw wikitable syntax `{\| … \|- … \|}` | `WIKITABLE_MARK`: a block with 3 or more ` \| ` cell separators is a table printed as text and is dropped; otherwise the stray marks are stripped | 1 block, 8 marks |
| Template parameters shown as text (`Quote box\|width=\|bgcolor=#ACE1AF\|…`) | `TEMPLATE_PARAMS`, only where `\|width=`, `\|bgcolor=`, `\|align=`, `\|style=`, `\|class=`, `\|quote=` or `\|border=` appears | 2 |
| HTML attributes shown as text | `HTML_ATTR`: `style="…"`, `class="…"`, `align=…` and similar | 3 |
| Hidden-category text | `CATEGORY_TEXT`: "Category:Articles containing potentially dated statements from" | 3 |

In total the rules removed 8,046 characters of leftovers.

### English-dominant blocks: translate prose, keep data, drop citations

**Which blocks.** A block is English-dominant when it has more than twice as many Latin letters as Odia letters. Only letters count (digits are ASCII by then) and math is ignored (`english_dominant()` in `prepare.py`). The unit and its minimum:

| Unit | Also needs |
|---|---|
| paragraph | at least 30 Latin letters |
| heading | at least 5 Latin letters ("Early life") |
| list item | at least 10 Latin letters |
| table | at least 30 Latin letters and fewer than 200 Odia letters |

**What happens to them** (the owner's decision, 2026-09-24):

| Block | Action | Count |
|---|---|---:|
| citation (bibliography entry), any unit | removed | 133 |
| paragraph or heading with a translation | replaced in place by its Odia translation | 908 in the text (374 articles) |
| paragraph the translator judged not prose (code, names, garbled OCR, verse in Latin script) | kept as it is | 72 |
| paragraph marked junk: vandalism, leaked template instructions, and reference lines the citation rule missed ("Source: …", numbered news references, a Gazette notification) | removed | 15 blocks (12 table entries) |
| list item, table | kept as it is: names, titles, data | the rest of the 5,754 kept |

A paragraph or heading with no translation yet is taken out and kept aside in `removed/` (reason "awaiting translation"), where `translate.py batches` finds it for the next translation round. None are waiting now. The citations (133) and junk blocks (15) that were cut out are listed there too, each with the article's `id` and `title`.

**Citations** (`is_citation()`). Structural signals count for any block:
- ISBN, ISSN, DOI or OCLC
- page or volume numbers
- "Retrieved"
- the "Surname, A. B." author format

For list items, "(1997). Title" also counts, as do publisher words (Press, Publishers, Journal, …) together with a year. Prose that mentions a film "(1962)." or a "journal" is not a citation. A first, looser version flagged such prose: Waheeda Rehman's career, and a Param Vir Chakra citation. So did an anthology title in a poet's list of works ("The Notion Press Book of Modern Odia Poetry"). Taxonomic authorities ("Gu et al. 2008") are names, not citations.

**Why not remove it all, as at first.** The first version of this rule removed every English-dominant block (6,622). It left skeleton articles: the lists of lakes, mountains and rivers kept only their headings. So the owner decided:
- translate the prose
- keep tables, names and titles, which are useful even in Latin script
- drop citations
- headings were left to the maintainer, who translated them

**Translations.**
- **Source table.** They live in `translations/english-to-odia.jsonl` (in git), keyed by the sha1 of the English block as the build produces it. Each entry records the source, the Odia text, the translator, notes and the automatic checks.
- **Who translated.** Nine parallel Claude Opus translator agents wrote them on 2026-09-24: 524 unique paragraphs (21,617 English words) in 8 batches, and 352 unique headings with one shared glossary (Early life → ପ୍ରାରମ୍ଭିକ ଜୀବନ, Filmography → ଚଳଚ୍ଚିତ୍ର ତାଲିକା, Awards → ପୁରସ୍କାର ଓ ସମ୍ମାନ, Personal life → ବ୍ୟକ୍ତିଗତ ଜୀବନ).
- **Guidelines.**
  - formal Odia as on Odia Wikipedia, faithful, nothing added or dropped
  - ASCII digits exactly as in the source, and Odia month names
  - established Odia spellings of names (checked against the corpus)
  - scientific names and common Latin acronyms stay in Latin
  - ୟ written precomposed, and " ।" at the end of a sentence
- **Checks.** `translate.py merge` checks every item:
  - no digit sequence lost
  - no Bengali or Devanagari letters
  - Odia letters at least half of the letters
  - length ratio 0.5–2.5
  - no ଯ + nukta

  Only two paragraphs fail, both for kept Latin names (frog family names, a Commons link target). The flagged headings are short ones and Latin binomials.
- **Review.** The maintainer read a random sample of 8 against their sources: accurate, natural Odia word order, names and numbers kept.
- **Provenance.** Each record has `translated_paragraphs`, the number of machine-translated paragraphs and headings in its final text. It is counted after empty sections are dropped; a first version counted 980, because it included 50 headings whose sections were later dropped. The count lets the text be separated by origin, as in E06-style ablations. Translated text is about 18k Odia words, 0.4% of the corpus.
- **How natural the translations are.** Sarvam-1 scores translated paragraphs at 0.519 bits per byte pooled, the same as native Odia prose (0.519). Within length bands, their median sits at the 44th percentile, and 1.2% / 2.1% fall in the bottom / top 1%. Long translations are about 5% more predictable than native text of the same length. There is no sign of unnatural or formulaic Odia (`quality/bpb.md`).
- **Missed citations.** The re-scoring's review queue found 9 translated reference lines that `is_citation()` had missed: "Source: …" lines, numbered news references, a Gazette notification, and a "ଆଧାର: 1) …" source list. They are marked junk with `translate.py mark --drop`. Quote attributions ("— Maj A. H. Amin …") and a quoted court judgment stay.

**Articles that come back.** The corpus has 51 more articles than under the first rule (20,785 → 20,836): pages that had been dropped as mostly English are back, translated or with their lists and tables. The 2 still left out as mostly English (ଆବ୍ରୋସରସ, ଈଲୋସରସ) had only citations in English. `gutted()` still drops a page whose removed English (now only citations and untranslated prose) leaves under 25 Odia words outside headings.

**How a later round works.** `translate.py batches` (items awaiting translation) → translators → `translate.py merge` → `translate.py mark --drop` for junk → `prepare.py build` → `check.py` → re-scoring of the new paragraphs.

### List nesting after removals

**Rule.** When the output is assembled, a list item may be indented at most to the content column of the list item before it (`LI_PREFIX` in `html_to_text()`).

**Why.** Removing an item can leave its sublist stranded deeper than any item above it. CommonMark reads that as an indented code block: the English filter broke the rivers-of-India list and the dinosaur classification this way, and the Markdown check caught both. After the fix all 20,827 articles parse as intended again, and pandoc agrees on the 405-article sample.

### Re-scoring

**Rule.** A paragraph's bits per byte depends only on its own text, since it is scored from BOS, so scores carry over by `para_sha1`. `score_bpb.py score --only-missing` scores only texts without a score, and `build` stops while any paragraph has no score. `annotations/bpb.json` keeps every run's record.

**Effect.** After the cleanups, 143,357 of 144,751 paragraphs kept their scores.
- 1,379 new texts (1,394 rows in 1,304 articles, 183,357 tokens) were scored on an RTX 4000 Ada, Secure, at $0.28/h: 25 s of GPU time and 5 minutes of pod time. The image was cached, so the pod was ready in 13 s.
- It cost **$0.02**; both scoring runs together cost $0.29.
- Same model revision, library versions, precision and code path as the first run.

**Consistency.** 200 already-scored texts were scored again on the new GPU. 102 came out bit-identical, and the median change was 0.00% (p95 1.04%, max 2.20%, on short headings). Pooled bpb was 0.5360 against 0.5359. That is within the first run's own bf16 noise, so flags don't move.

**Queue.** The review queue was rebuilt on the cleaned text, and every `review_para` points at a current paragraph (see [Review-first queue](#review-first-queue)).

**Run 3, after the translations (2026-09-25).**
- **What was scored.** 2,445 new texts in 1,064 articles: translated paragraphs and headings, restored English lists and tables, and paragraphs changed by the error-message rule. That is 497,209 tokens, well over the 206k estimate: Latin-script lists and tables run at about 2.3 bytes per token, against about 7.1 for Odia.
- **Pod.** The RTX 4000 Ada was out of stock, so it ran on an RTX A6000 Secure at $0.53/h: 34.5 s of GPU time and 3 min 38 s of pod time, **$0.03**. All three runs together cost $0.32.
- **Recheck.** 200 already-scored texts came out 126 identical, with a median change of 0.00% and a maximum of 1.79%.
- **Scores.** Corpus bpb 0.5500 → 0.5544, as Latin-script lists and tables returned. Prose 0.5221; translated paragraphs 0.519; restored lists and tables 0.96.
- **Afterwards.** Removing the 9 missed reference lines needed no GPU: removals leave no new text to score, and `score_bpb.py build` carries every other score over.

## Known limitations and open issues

- **Boilerplate.** 6.8% of paragraphs repeat, with names and numbers masked, in 5 or more articles (year-page sentences, coordinates, census sentences, the Hindi-official-language sentence); `bot_created` covers only about a fifth of them. A per-article templated share and a repeat cap for the training mix are still to do.
- **Paragraph granularity.** A list is one paragraph, so a paragraph drop removes the whole list block.
- **Translations are LLM output.** They passed automatic checks and a sample review, not a full human review. They are marked per article (`translated_paragraphs`) and listed with their sources in `translations/english-to-odia.jsonl`.
- **English lists and tables stay.** 5,754 blocks of names, titles and data remain in Latin script, by decision. `odia-build-cpt`'s `--min-odia-ratio` filter sees them.
- **Topics.** The small topics have thin evidence (science and society 5/8 in the stratified check); `odisha` misses articles without categories; Wikidata was read live on 2026-09-24, not from a dump.
- **Translation.** Untagged machine translation cannot be detected from the dumps.
- **bpb.** Per-paragraph ranks carry bf16 noise; short paragraphs (under 100 B) are only flagged through their article.
- **Digits.** This corpus uses ASCII digits, but Sangraha, FineWeb-2, the eval sets and Odisha's textbooks use Odia digits; the same conversion in `cpt.py` and the eval prompts needs the owner's go-ahead.
- **Held-out overlap.** `cpt.py` holds out 300 documents from the 2023 Wikipedia snapshot; newer revisions of the same articles are in this corpus. Exclude them before training on it.

Details and the history of each issue are in `LEARNINGS.md`.

## Changelog

Newest first.

- **2026-09-25: re-scoring run 3 and follow-ups.**
  - 2,445 new texts scored ($0.03); translations score like native Odia.
  - 9 missed reference lines marked junk.
  - `translated_paragraphs` counted after empty sections are dropped (908 blocks, 374 articles).
  - Topic and translation annotations recomputed for the current articles.
  - A code block starting `#!/usr/bin/perl` is no longer classified as a heading: headings need `#` to `######` and a space, in `score_bpb.py` and edaapp.
- **2026-09-24: English policy changed from "remove" to "translate prose, keep data, drop citations".**
  - 980 paragraphs and headings translated in place; 5,754 lists and tables restored; 133 citations and 3 junk blocks removed.
  - 51 articles are back (20,785 → 20,836).
  - More reference headings dropped by pattern.
  - Category-only blocks and 59 English template error messages removed.
  - New `check.py` (Markdown, pandoc, residue) and `translate.py` (translation rounds).
- **2026-09-24: re-scoring after the cleanups.**
  - Scores carried over by `para_sha1`; only 1,379 new texts scored.
  - Cost: $0.02 on an RTX 4000 Ada.
  - Corpus bpb 0.5564 → 0.5500.
  - Review queue rebuilt on the cleaned text.
- **2026-09-24: build writes survive a full disk.** A rebuild on a full disk left a partial temp file; `atomic_write()` now removes it and leaves the old files untouched.
- **2026-09-24: text cleanups.**
  - External-link templates dropped (IMDb lines 608 → 1).
  - ପରୁଷ → ପୁରୁଷ in context (917).
  - Conversion leftovers fixed (67).
  - 6,622 English-dominant blocks removed and kept aside.
  - List nesting clamped.
  - 2,530 articles changed; 20,827 articles remain.
- **2026-09-24: methodology page.** This document, shown as edaapp's Methodology page.
- **2026-09-24: human review loop.** `build` applies edaapp decisions (article drops, paragraph drops by sha1; `--no-reviews`). No decisions applied yet.
- **2026-09-24: Sarvam-1 bits per byte and review-first queue.** Every paragraph scored ($0.26 of GPU time); 200 articles queued in seven types (`annotations/bpb.parquet`, `quality/bpb.md`, `quality/review-first.md`).
- **2026-09-24: topic and translation annotations.** 17 topics with 97.7% coverage; 3,844 machine-assisted translations flagged (`annotations/topics.parquet`, `annotations/translation.parquet`).
- **2026-09-24: Odia digits to ASCII.** 689,848 digits converted before escaping; titles unchanged; Odia word count 4.73M → 4.53M.
- **2026-09-24: Markdown correctness.** Escaping as the last step, math placeholders (fixing TeX damage), CommonMark list indentation and empty-level skipping, per-article `markdown/<title>.md` files; all articles validated with markdown-it, pandoc on a sample.
- **2026-09-24: residue rules from the full-corpus scan.** Literal tags, magic words, raw HTML pasted by Content Translation, bare URLs, image sizes and options, missing-template red links (country names kept), punctuation-only lines.
- **2026-09-24: data tables kept as Markdown tables** instead of dropped.
- **2026-09-24: first build.** Dump 2026-09-01 downloaded and verified; all 21,095 articles rendered from their dump revisions; sections, templates and elements dropped; `normalize_odia`, danda fixes, invisible characters; filtering of disambiguation, near-empty and duplicate pages; JSONL and Parquet outputs.

## How to keep this page current

Every step that changes the Wikipedia data, or adds knowledge about its quality, updates this page in the same change:

1. Add a section, or update the one it belongs to: the rule, why (the evidence), its measured effect (counts), and the code and report names.
2. Add a dated line to the [Changelog](#changelog).
3. Update the Summary numbers if they changed, and the [Known limitations](#known-limitations-and-open-issues) if an issue was fixed or found.
4. Record what the step taught us in `LEARNINGS.md`.

Coming next: scoring the corpus with the project's own model.
