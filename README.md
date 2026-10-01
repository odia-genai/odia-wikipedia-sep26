---
pretty_name: Odia Wikipedia, cleaned for LLM training (2026-09-01 dump)
language:
- or
license: cc-by-sa-4.0
task_categories:
- text-generation
- translation
tags:
- wikipedia
- odia
size_categories:
- 10K<n<100K
configs:
- config_name: corpus
  default: true
  data_files:
  - split: train
    path: orwiki-20260901-trainingready.jsonl.gz
- config_name: translation_pairs
  data_files:
  - split: train
    path: translations/english-odia-pairs.jsonl
- config_name: translation_table
  data_files:
  - split: train
    path: translations/english-to-odia.jsonl
- config_name: excluded
  data_files:
  - split: train
    path: excluded.jsonl
- config_name: removed_blocks
  data_files:
  - split: train
    path: removed-blocks.jsonl
- config_name: topics
  data_files:
  - split: train
    path: annotations/topics.jsonl
- config_name: translation_flags
  data_files:
  - split: train
    path: annotations/translation.jsonl
- config_name: bpb
  data_files:
  - split: train
    path: annotations/bpb.jsonl
- config_name: bpb_paragraphs
  data_files:
  - split: train
    path: annotations/bpb.paragraphs.jsonl
---

# Odia Wikipedia, cleaned for LLM training

Every article on [Odia Wikipedia](https://or.wikipedia.org) in the **2026-09-01 dump**
(`orwiki-20260901-pages-articles.xml.bz2`, the latest complete dump when built on 2026-10-02), as clean
GitHub-flavoured Markdown, one article per record: **18,683 articles, 4,514,702
Odia words, 87 MB of UTF-8 text**.

## Download

Two files are ready to take and use as they are:

- **[`orwiki-20260901-trainingready.jsonl.gz`](orwiki-20260901-trainingready.jsonl.gz)**: **the corpus**. 18,683 Odia Wikipedia articles as clean
  Markdown, one JSON object per line, 4,514,702 Odia words (17 MB gzipped,
  87 MB unpacked). This is the file to train on.
- **[`translations/english-odia-pairs.jsonl`](translations/english-odia-pairs.jsonl)**: **English-to-Odia translation pairs**, if you want them separately, ready
  for training: 726 pairs, one per line, `{"english": …, "odia": …}`. They are English
  paragraphs and headings found in these articles, with their Odia translations, which the corpus has
  in place of the English.

```python
import gzip, json
articles = [json.loads(line) for line in gzip.open("orwiki-20260901-trainingready.jsonl.gz", "rt", encoding="utf-8")]
pairs = [json.loads(line) for line in open("translations/english-odia-pairs.jsonl", encoding="utf-8")]
```

The pairs come from the translation table, [`translations/english-to-odia.jsonl`](translations/english-to-odia.jsonl), which also keeps each translation's
article, kind, checks and notes. Left out of the pairs: junk, or names and titles kept in English (85); the English side has Odia in it (39); failed an automatic check (25); template residue in the English (1).

Everything else in this repository is how they were made, and what it takes to make them again.

## What is here

| File | What it is |
|---|---|
| **`orwiki-20260901-trainingready.jsonl.gz`** | **the training-ready corpus**, one JSON object per line (fields below), gzipped; `build` also writes it unpacked, as `orwiki-20260901-trainingready.jsonl` |
| **`translations/english-odia-pairs.jsonl`** | **English-Odia translation pairs for training**, `english` and `odia` only |
| `translations/english-to-odia.jsonl` | the translation table the pairs come from: each translation with its article, kind, checks and notes |
| `excluded.jsonl` | every page of the dump that is not in the corpus: `id`, `revid`, `title`, `reason`, `detail` |
| `removed-blocks.jsonl` | blocks taken out of articles: citations, junk, prose awaiting translation |
| `orwiki-20260901-trainingready-build.json` | build statistics (counts per exclusion reason; the pages are in `excluded.jsonl`) |
| `annotations/*.jsonl` | topics, translation flags, Sarvam-1 scores and the review queue, joined on `id` |
| `pipeline.py` | **one command that rebuilds everything** from the cached inputs, in order, and checks it |
| `prepare.py`, `check.py`, `translate.py`, `annotate.py`, `score_bpb.py` | the steps |
| `METHODOLOGY.md` | every step and rule applied to the data, with the evidence and counts |
| `LEARNINGS.md` | what building this corpus taught us, and ideas for next steps |
| `curation/junk-paragraphs.jsonl` | paragraphs judged by hand not to be content, with the reason; `build` drops them |
| `reviews/reviews.jsonl` | review decisions (keep, drop, fix, paragraphs to drop); `build` applies them |
| `odia_text.py` | the Odia text rules the steps share: normalisation, Odia words, digits |
| `edaapp/` | the review web app: browse the dataset, read the methodology, record review decisions |
| `publish_hub.py` | uploads the dataset to Hugging Face: the files git tracks, without `edaapp/` |
| `LICENSE` | CC BY-SA 4.0, for the data and the code |
| `raw/` | rebuild inputs: article index, dump provenance, rendered HTML, annotation inputs, model scores |

All outputs are JSON, JSON lines or Markdown, to read with any editor or `jq`. Every file here is
tracked in git, all under 50 MB and none through Git LFS: the corpus as `orwiki-20260901-trainingready.jsonl.gz`, while the
unpacked `orwiki-20260901-trainingready.jsonl` is left out and rebuilt by `pipeline.py` from the inputs in `raw/`,
`translations/`, `curation/` and `reviews/`.

## Record format

| Field | Meaning |
|---|---|
| `id` | page id on or.wikipedia.org |
| `title` | article title |
| `url` | article URL |
| `revid` | revision in the dump; `https://or.wikipedia.org/w/index.php?oldid=<revid>` is exactly this text |
| `timestamp` | when that revision was saved |
| `text` | the article (format below) |
| `words` | Odia words in `text`: runs of Odia letters, title included; numbers and Latin words don't count |
| `chars` | characters in `text` (Unicode code points), title line and Markdown included |
| `odia_ratio` | share of non-space characters in the Odia block (`odia_text.odia_ratio`) |
| `tables` | data tables in `text`, as Markdown tables |
| `translated_paragraphs` | paragraphs and headings machine-translated from English (0 = all native Odia) |
| `bot_created` | the page carries `{{ବଟ୍ ତିଆରି}}`, made by a bot (in 2026-09 only year pages, all excluded) |
| `stub` | the page carries a stub template (`{{ମୁଣ୍ଡିଆ}}`, `{{ଅଧାଗଢ଼ା}}`) |
| `templated_share` | share of the article's Odia words in template sentences (found in 5+ articles); high = formulaic |

`text` is GitHub-flavoured Markdown: `# title`, the lead, `##`/`###` section headings,
paragraphs separated by a blank line, `- ` / `1. ` lists (sublists indented to their parent's
content column), data tables as GFM tables (`| a | b |`), and math as `$...$` (`$$...$$` on its
own line). Links and emphasis are reduced to their text.

It is checked as Markdown, not just shaped like it. Every article parses (markdown-it,
CommonMark + GFM tables) into exactly the headings, list items and tables it is meant to have,
with no accidental emphasis, links, code, HTML, block quotes or rules. pandoc's GFM reader agrees
on a random sample of 405 articles. Text from the page that would read as markup is
backslash-escaped: `\*`, `\_`, `\$`, `\<alt>`, `1\.` or `\-` at the start of a line.
There are 2,189 escapes in 519 articles, about
63 per million characters.

A short example record:

```json
{
  "id": 3008,
  "title": "କର୍ଣ୍ଣାଟକ",
  "url": "https://or.wikipedia.org/wiki/%E0%AC%95%E0%AC%B0%E0%AD%8D%E0%AC%A3%E0%AD%8D%E0%AC%A3%E0%AC%BE%E0%AC%9F%E0%AC%95",
  "revid": 498992,
  "timestamp": "2023-08-27T17:20:28Z",
  "words": 77,
  "chars": 576,
  "odia_ratio": 0.9052,
  "tables": 0,
  "translated_paragraphs": 0,
  "bot_created": false,
  "stub": true,
  "templated_share": 0.0,
  "text": "# କର୍ଣ୍ଣାଟକ\n\nକର୍ଣ୍ଣାଟକ (କନ୍ନଡ: ಕರ್ನಾಟಕ) ଦକ୍ଷିଣ-ପଶ୍ଚିମ ଭାରତର ଏକ ରାଜ୍ୟ । 1956 ମସିହା ନଭେମ୍ବର 1 ତାରିଖରେ ରାଜ୍ୟ ପୁନର୍ଗଠନ ଆଇନ ବଳରେ ଏହି ରାଜ୍ୟ ସ୍ଥାପିତ ହୋଇଥିଲା । ଏହାର ପୂର୍ବ ନାମ ମହୀଶୂର ଥିଲା । 1973 ମସିହାରେ ଏହାର ନୂତନ ନାମକରଣ ହେଲା କର୍ଣ୍ଣାଟକ । କର୍ଣ୍ଣାଟକର ପଶ୍ଚିମରେ ଆରବ ସାଗର, ଉତ୍ତର-ପଶ୍ଚିମରେ ଗୋଆ, ଉତ୍ତରରେ ମହାରାଷ୍ଟ୍ର, ଉତ୍ତର-ପୂର୍ବରେ ତେଲେଙ୍ଗାନା, ପୂର୍ବରେ ଆନ୍ଧ୍ର ପ୍ରଦେଶ, ଦକ୍ଷିଣ-ପୂର୍ବରେ ତାମିଲନାଡ଼ୁ ଓ ଦକ୍ଷିଣ-ପଶ୍ଚିମରେ କେରଳ ଅବସ୍ଥିତ । ରାଜ୍ୟର ମୋଟ ଆୟତନ 1,91,791 ବର୍ଗକିଲୋମିଟର ଓ ଭାରତର ମୋଟ ଆୟତନର 5.83 ପ୍ରତିଶତ । ଏହି ରାଜ୍ୟର ଜିଲ୍ଲା ସଂଖ୍ୟା 30 ଓ ସରକାରୀ ଭାଷା କନ୍ନଡ଼ ।\n\n## ଭାଷା ଓ ସାହିତ୍ୟ\n\nକନ୍ନଡ଼ ହେଉଛି ଏହାର ମାତୃଭାଷା ।"
}
```

## How it was made

1. **Download.** `orwiki-20260901-pages-articles.xml.bz2` from dumps.wikimedia.org, with its SHA-1
   (`4ace67b6c5412a78565e463066f9450bfff6df6b`) checked against the dump's `dumpstatus.json`. The build needs only
   each article's id, title, revision id, timestamp and bot/stub flags, so the dump is reduced to
   that index (`raw/orwiki-20260901-articles.jsonl`, with its provenance in `raw/orwiki-20260901-dump.json`) and
   deleted; `download` fetches it again.
2. **Render.** Every main-namespace page that is not a redirect (21,095 pages)
   was fetched as Wikipedia's own rendering (Parsoid HTML) **of the exact revision in the
   dump**, from `/w/rest.php/v1/revision/<revid>/html`. Odia articles build whole sentences out
   of templates: `'''{{PAGENAME}}''' ଏକ ଭାରତୀୟ {{TownType|M}}` in over 1,600 town stubs, and
   `{{Birth date}}`, `{{convert}}`, `{{flag}}` and other Lua-module templates across
   the wiki. Stripping the wikitext would leave holes in those sentences. The rendered HTML has
   what a reader sees.
3. **Clean** (HTML to text). Prose, headings, lists and math are kept. Dropped:
   - citations, reference lists, and reference-type sections (ଆଧାର, ଟୀକା, ଆହୁରି ଦେଖନ୍ତୁ,
     ବାହାର ଲିଙ୍କ, ଅଧିକ ପଢ଼ନ୍ତୁ, ଗ୍ୟାଲେରୀ, … and their English equivalents)
   - infoboxes, navboxes, layout tables, images, galleries, captions, maps. Data tables
     (`wikitable`) are kept as Markdown tables. A cell spanning rows repeats in each row, a cell
     spanning columns fills the first one, and image-only columns are removed. Tables hold
     169,433 Odia words (3.8%) in
     1,721 articles: lists of districts, constituencies, award winners
     and office holders. For prose only, drop the lines starting with `|`.
   - hatnotes, maintenance and stub banners, coordinates, pronunciation (IPA), sister-project
     boxes, archive notes ("Archived … at the Wayback Machine"), template error messages
   - list items that are only an external link or a book citation, under any heading
   - hand-typed reference markers (`[2]`, `[୪]`) and broken wikitext that renders as text
     (`[[`, `{{`, `''`, stray `File:…|thumb|` lines)
   - bare URLs typed into the prose, and raw HTML pasted into the wikitext (a Content
     Translation bug) that renders as text
   - empty sections, and parentheses emptied by the removed pronunciations
   - external-link lines written in Odia (IMDb, Facebook, Instagram, Twitter, official website, …),
     dropped by template name
   - conversion leftovers found by the review queue: Content Translation `<a href=… cx-link>` tags,
     raw `{| … |}` wikitable text, template parameters shown as text (`Quote box|width=…`), HTML
     attributes, `Category:` text, and the entities `&amp;` / `&#13;`
     (69 fixes)
   - wikitext headings typed mid-line (`… । == ଇତିହାସ ==`) become real headings
     (19)
   - numeric superscripts are written as LaTeX math, like the rest of the math: `10<sup>26</sup>`
     becomes `$10^{26}$` and `km<sup>2</sup>` becomes `km$^{2}$`
     (283). Flattened, they would read 1026 and
     km2. Powers of ten typed without the superscript upstream, `6.1 x 108`, become
     `$6.1 \times 10^{8}$` (13). Short
     subscripts too: `H<sub>2</sub>O` becomes `H$_2$O`
     (303); chemistry markup is `$\ce{…}$`
   - paragraphs judged by hand not to be content: test edits, colour legends of tables whose colours
     are gone, a leaked timeline template, pasted search-result snippets
     (189; each with its reason in `curation/junk-paragraphs.jsonl`)
   - paragraphs a reviewer marked *fix*, replaced by their fixed text: English lists, tables and
     passages translated into Odia with the names transliterated, and a wrong name corrected
     (26; each with its source in `curation/paragraph-fixes.jsonl`)
   - **English inside articles** (blocks with more than twice as many Latin as Odia letters):
     citations are removed (104), paragraphs and
     headings are replaced by their Odia translation (861,
     see `translations/english-to-odia.jsonl` and `METHODOLOGY.md`), and lists, tables and names stay
     as they are (4,847)
4. **Fix known typos.** Bots copied some misspellings into hundreds of articles; each fix is tied to
   its context: ପରୁଷ→ପୁରୁଷ (917).
5. **Normalise.** `normalize_odia` from `odia_text.py` (ୟ written as ଯ + nukta becomes
   U+0B5F). **Odia digits become ASCII** (`୧୯୪୭` → `1947`; 689,041 digits), in
   text, headings, tables and math, so numbers look the same everywhere, titles included (`url` keeps the page's
   real name). A `|` typed for the danda after Odia text becomes `।` (and `||` becomes `॥`). Soft
   hyphens, zero-width spaces, word joiners and BOMs are removed, and runs of spaces are
   collapsed. ZWJ and ZWNJ stay, because Odia spelling uses them. There is **no** NFC or other
   Unicode normalisation (by design).
6. **Filter.** 2,412 pages were left out, each listed in `excluded.jsonl`
   with its id, revision and reason. Boilerplate pages (year pages, date pages without events,
   empty film-year lists) say nothing beyond their title. Fact-bearing stubs stay, with
   `templated_share` for down-weighting.

| Reason | Pages |
|---|---:|
| year page | 1,864 |
| date page without events | 260 |
| under 5 Odia words | 137 |
| disambiguation | 112 |
| empty list page | 19 |
| reviewer: drop | 12 |
| duplicate text | 7 |
| main page | 1 |

## Size

The median article has 151 Odia words (10th percentile 58, 90th 428).
1,006 articles have more than half of their words in template
sentences (`templated_share` > 0.5: bot-made villages, towns and film pages),
and 1,910 are marked as stubs.
310 articles
(54,816 Odia words) have `odia_ratio` under 0.6, mostly from English
bibliographies and numeric tables; a threshold of 0.6 (the default of odia-llm-trainer's
`odia-build-cpt --min-odia-ratio`) skips them.

| Odia words per article | Articles | Words |
|---|---:|---:|
| 0–49 | 1,392 | 41,522 |
| 50–199 | 10,637 | 1,274,479 |
| 200–999 | 6,200 | 2,228,752 |
| 1,000–4,999 | 428 | 766,474 |
| 5,000+ | 26 | 203,475 |

## Annotations

Extra fields per article live next to the corpus, one JSON-lines file each (with a `.json`
description), joined on `id`. A file named `*.paragraphs.jsonl` has one row per paragraph (`id`,
`para`). Paragraph `i` of an article
is `text.split("\n\n")[i]`, and paragraph 0 is the `# title` heading. Annotations that depend on
the text carry `text_sha1` (sha1 of the scored `text`), so a rebuild that changes an article makes
them visibly stale.

- **`annotations/bpb.jsonl`**: Sarvam-1 bits per byte of every article, and a review queue of likely data problems (garbled or wrong-script text, untranslated English, boilerplate, odd tables, conversion leftovers) from paragraph and article bpb extremes. Columns: `id`, `text_sha1`, `bpb`, `bpb_text`, `bpb_pct`, `bytes`, `tokens`, `bits`, `paragraphs`, `unscored_paragraphs`, `extreme_high`, `extreme_low`, `extreme_share`, `review_rank`, `review_type`, `review_para`, `review_reasons`. Depends on the text: rows carry `text_sha1`.
- **`annotations/bpb.paragraphs.jsonl`**: Sarvam-1 bits per byte of every paragraph except the title (paragraph i = text.split('\n\n')[i]), with its percentile within its kind and length band. Each row's bits, bytes, tokens, pieces and scoring run are in the score store, joined on para_sha1 (see 'joins'). Columns: `id`, `para`, `para_sha1`, `kind`, `bpb`, `bpb_group`, `bpb_pct`, `bpb_z`, `flag`, `latin_share`, `other_script_share`, `english_words`, `markup`, `verse`, `repeats`, `self_repeat`, `near_copies`. More columns, `score_run, bytes, tokens, bits, pieces`, are read from `raw/bpb/scores.jsonl.gz` on `para_sha1` (kept there once per key). Depends on the text: rows carry `para_sha1`.
- **`annotations/topics.jsonl`**: Subject topics of each article from its categories, its Wikidata item and its title, for weighting a school-oriented training mix. One JSON object per line (topics.jsonl), one per article of the corpus, in corpus order. See quality/topics.md. Columns: `id`, `title`, `categories`, `topics`, `primary_topic`, `is_person`, `odisha`, `school_relevant`, `topic_source`, `wikidata`, `topic_scores`.
- **`annotations/translation.jsonl`**: Articles created or largely written with Content Translation (CX) or MDWiki's CX-based translation dashboard: machine-assisted translations, mostly from English. One JSON object per line (translation.jsonl), one per article of the corpus, in corpus order. See quality/translation.md. Columns: `id`, `ct_created`, `ct_any`, `ct_tags`, `mdwiki_created`, `mdwiki_any`, `tool_bytes_share`, `translated`, `translated_at`, `source_lang`, `source_title`, `source_revid`, `source_section`, `created`, `creator_is_bot`, `revisions`, `edits_after_creation`, `bot_edits_after_creation`, `editors_after_creation`, `bytes`.

Reports: [`quality/bpb.md`](quality/bpb.md), [`quality/review-first.md`](quality/review-first.md), [`quality/topics.md`](quality/topics.md), [`quality/translation.md`](quality/translation.md).

## Review decisions

Review decisions are kept in `reviews/reviews.jsonl`, one append-only JSON event per line: `ts`,
`dataset` (`odia-wikipedia`), `id`, `title`, `verdict` (`keep`, `drop`, `fix` or null), `note`,
`drop_paragraphs` (a list of `{"para": i, "sha1": …}`) and `text_sha1` (the text reviewed). They were
recorded with edaapp, the review web app in `edaapp/` (`cd edaapp && uv run edaapp`), which writes
them to this file; appending events by hand works as well.
`build` applies the **latest event per article**, so every event carries the article's complete
decision: articles marked *drop* are left out, and dropped paragraphs are removed, matched by the
sha1 of their text (not by position), so they survive rebuilds. Paragraph 0 (the title) is never
dropped. This
build applied 217 reviews: 12 articles dropped,
74 paragraphs dropped, 0 still marked *fix*, and
0 paragraph decisions whose text is no longer in the article.
26 paragraphs of articles marked *fix* have been fixed
(`curation/paragraph-fixes.jsonl`, made by `translate.py fixes` and `merge-fixes`).
Use `--no-reviews` to build without the decisions.

## Using it

```python
import gzip, json
docs = [r["text"] for r in map(json.loads, gzip.open("orwiki-20260901-trainingready.jsonl.gz", "rt", encoding="utf-8"))
        if r["templated_share"] < 0.8]  # e.g. down-weight or skip formulaic stubs
```

```bash
jq -r 'select(.reason == "year page") | .title' excluded.jsonl | head   # why a page is missing
gzip -dc orwiki-20260901-trainingready.jsonl.gz | jq -c 'select(.chars >= 500 and .chars < 600) | {id, title, words, chars}' | head
```

- In odia-llm-trainer, `odia-build-cpt --local orwiki-20260901-trainingready.jsonl --local-upsample 1` adds all of it
  to a continued-pretraining build. `--local` upsamples 3× by default, which is meant for
  textbooks. The builder's own `wikipedia` source still reads the older Hugging Face snapshot
  (`wikimedia/wikipedia`, `20231101.or`).
- The text is already normalised with `normalize_odia`, so a pipeline that applies it again
  (and line dedup) barely touches it.

## Origin

Built from 2026-09-24 to 2026-10-01 inside odia-llm-trainer, a project on Odia language models, as
its `data/odia-wikipedia/` folder, then moved here with everything needed to rebuild it: the
rendered HTML, the annotation inputs, every Sarvam-1 score, the translations, the curated junk
paragraphs and the review decisions. edaapp, the web app that recorded those decisions, followed on
2026-10-02. `METHODOLOGY.md` and `LEARNINGS.md` keep that project's other names: `src/`, `cpt.py`
and `odia-build-cpt` (its training-data builder), the eval harness and experiments (E01, E03, …).

## License

Everything in this repository, the data and the code, is licensed
[CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/) (the full text is in `LICENSE`).
The text is by Odia Wikipedia contributors: anything derived from it must keep that license and
credit Wikipedia. Each record's `revid` names its exact source revision, whose history lists the
authors.

## Rebuild

```bash
uv run pipeline.py           # build, annotate, scores, checks, from raw/ (a few minutes)
ODIA_WIKI_CONTACT=you@example.org uv run pipeline.py --fetch   # from scratch (~2 h)
```

The steps one by one:

```bash
uv run prepare.py download   # newest complete dump, or --dump YYYYMMDD
ODIA_WIKI_CONTACT=you@example.org uv run prepare.py render  # ~2 h, resumable
uv run prepare.py build      # about a minute; --min-chars N, --markdown
uv run check.py              # exit 1 if any check fails
```

`render` needs contact details in the user-agent (`ODIA_WIKI_CONTACT`). Wikimedia throttles
anonymous bulk clients to about one request a minute per connection. With contact details and 6
parallel requests it ran at about 3 pages/s (21,095 pages in about 2 hours), backing off on the
occasional 429 as `Retry-After` asks. Rendered chunks are cached in `raw/html/<date>/`, so a rerun fetches only what is
missing. The script writes only inside this directory. uv keeps its environment in its own cache.

## Publishing

GitHub (https://github.com/odia-genai/odia-wikipedia-sep26) holds this repository with its history. The Hugging Face dataset is the same files
without `edaapp/`, one commit per publish: commit and push first, then `uv run publish_hub.py`
(`--dry-run` lists what would change). It deletes on the Hub what git no longer tracks and checks every
file afterwards.
