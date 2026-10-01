# Machine-assisted translations in Odia Wikipedia

Built by `annotate.py translation` on 2026-10-01 from the `orwiki-20260901` dumps, for the 18,683 articles of the corpus (`orwiki-20260901-trainingready.jsonl`). Output: `annotations/translation.jsonl` (one JSON object per article, in corpus order; columns in `annotations/translation.json`). Titles are written with ASCII digits.

## Method

- **Tags.** `change_tag` links tags to revision ids. Content Translation (CX) marks every revision it publishes with `contenttranslation`, plus `contenttranslation-v2` (CX's second version), `sectiontranslation` (section translation, mostly a section added to an existing page) and, twice, `contenttranslation-high-unmodified-mt-text`. Revisions carrying these tags in the whole wiki (`change_tag_def` counts): `contenttranslation` 5,834, `contenttranslation-v2` 4,166, `sectiontranslation` 137, `contenttranslation-high-unmodified-mt-text` 2.
- **Revisions to pages.** `stub-meta-history` (44 MB) gives every revision's page, timestamp, contributor, edit summary and size. `annotate.py download` reduces it, with the tags, to per-article facts (`raw/orwiki-20260901-article-facts.jsonl.gz`, 1.3 MB): the first and last revision, the revision count, each later editor's revision count, and every revision with a CX tag or an edit summary about translating. The last revision of each article is exactly the corpus revision (checked for all 21,095 dump articles).
- **Edit summaries are English, not localised**: `Created by translating the page "[[:en:Special:Redirect/revision/663243963|Devdutt Pattanaik]]"`, `... the section "History" from the page "[[...]]"`, or `... the opening section from the page ...`. Source language, title and revision come from the link.
- **MDWiki.** 2024–2025 revisions with the summary `Created by translating the page [[:mdwiki:Special:Redirect/revision/N|Title]] to:or #mdwikicx` carry no CX tag, only `OAuth CID: 9394`. They come from WikiProject Medicine's translation dashboard, which runs CX on mdwiki.org and publishes here. They are machine-assisted in the same way, so they are flagged too (`mdwiki_created`, `mdwiki_any`).
- **Created vs. largely translated.** 25 articles got CX text after their first revision: a placeholder (`{{ତିଆରି ଚାଲିଛି}}`, "under construction") or a redirect left by a move came first, or a section translation added most of the page (ଚୀନ, ଓଡ଼ିଆ ଭାଷା, ବରାହଗିରି ଭେଙ୍କଟଗିରି, କ୍ଷୀର ସାଗର). Others only added a section to an existing article. `tool_bytes_share` is the bytes the CX/MDWiki revisions added over the dump revision's size, and `translated` = created by CX or MDWiki, or `tool_bytes_share` ≥ 0.5. **Use `translated`.**
- **Bots** (`creator_is_bot`, and excluded from `edits_after_creation`): accounts in this wiki's `bot` group now or before (`user_groups`, `user_former_groups`), plus global interwiki bots, which are not in the local tables, by name (ending in *bot*, e.g. InternetArchiveBot, EmausBot).

## How many

| Flag | Articles | Share | Odia words | Share of words |
|---|---:|---:|---:|---:|
| `ct_created` | 3,523 | 18.9% | 667,720 | 14.8% |
| `ct_any` | 3,548 | 19.0% | 685,212 | 15.2% |
| `mdwiki_created` | 298 | 1.6% | 52,907 | 1.2% |
| `mdwiki_any` | 309 | 1.7% | 54,237 | 1.2% |
| **`translated`** | 3,840 | 20.6% | 732,946 | 16.2% |

All 18,683 articles hold 4,514,702 Odia words.

By year of the translation (`translated_at`), for `translated` articles:

| Year | Articles | Odia words | CX | MDWiki | Median words |
|---|---:|---:|---:|---:|---:|
| 2015 | 23 | 3,399 | 23 | 0 | 119 |
| 2016 | 854 | 138,583 | 854 | 0 | 136 |
| 2017 | 263 | 51,233 | 263 | 0 | 143 |
| 2018 | 61 | 23,669 | 61 | 0 | 259 |
| 2019 | 95 | 28,618 | 95 | 0 | 181 |
| 2020 | 108 | 44,478 | 108 | 0 | 312 |
| 2021 | 304 | 71,855 | 304 | 0 | 182 |
| 2022 | 514 | 91,121 | 514 | 0 | 144 |
| 2023 | 599 | 112,758 | 599 | 2 | 131 |
| 2024 | 851 | 131,960 | 670 | 188 | 120 |
| 2025 | 149 | 30,521 | 30 | 119 | 181 |
| 2026 | 19 | 4,751 | 19 | 0 | 214 |

Source wikis of `translated` articles: `en` 3,500, `mdwiki` 300, `simple` 21, `hi` 8, `as` 3, `ro` 3, `bn` 2, `km` 1, `de` 1, `te` 1. (`simple` is Simple English; `mdwiki` is mdwiki.org, English. 1,618 `en` sources are medical drafts in `User:Mr. Ibrahem/`, the English Wikipedia user space of the MDWiki translation organiser.)

## Translated articles vs. the rest

`odia_ratio` is the corpus field (share of non-space characters in the Odia block). A paragraph (`text.split("\n\n")`) is English-dominant when it has over 30 Latin letters and more than twice as many Latin as Odia letters.

| Group | Articles | Mean `odia_ratio` | Median `odia_ratio` | Articles < 0.6 | English-dominant paragraphs | of prose paragraphs | Articles with any | Median words |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| CX-created (`ct_created`) | 3,523 | 0.912 | 0.946 | 3.1% | 454 of 25,477 (1.8%) | 0.1% | 6.2% | 137 |
| MDWiki-created (`mdwiki_created`) | 298 | 0.953 | 0.956 | 0.0% | 0 of 1,363 (0.0%) | 0.0% | 0.0% | 180 |
| `translated` (all machine-assisted) | 3,840 | 0.915 | 0.947 | 2.9% | 456 of 27,178 (1.7%) | 0.1% | 5.7% | 140 |
| not `translated` | 14,843 | 0.907 | 0.938 | 1.3% | 560 of 134,548 (0.4%) | 0.1% | 2.1% | 156 |
| not `translated`, created 2015 or later | 10,964 | 0.904 | 0.936 | 1.4% | 430 of 98,273 (0.4%) | 0.1% | 2.3% | 162 |
| not `translated`, human-created, 2015 or later | 10,075 | 0.912 | 0.941 | 0.9% | 425 of 91,472 (0.5%) | 0.1% | 2.4% | 174 |

`translated` articles hold 456 of the corpus's 1,016 English-dominant paragraphs (44.9%), against 16.8% of all paragraphs. Blocks are the contract's paragraphs, so tables and lists count: 925 of the 1,016 are table, list or heading blocks (untranslated names in lists of rivers, lakes, records). All English-dominant paragraphs together contain 2,960 Odia words.

Post-editing of `translated` articles (non-bot revisions after the first):

- median 3 edits; 5.3% never edited again by a person; 25.4% never touched by anyone but the translator.
- For comparison, human-created articles that are not `translated`: median 7 edits (created 2015 or later).

## Examples

- **କ୍ୱାଣ୍ଟମ କମ୍ପ୍ୟୁଟିଙ୍ଗ** (id 72578), from `en` "Quantum computing", 2020-02-04; 5 of 56 prose paragraphs English-dominant, e.g. "$$\binom{\frac{1}{\sqrt{2}}}{\frac{1}{\sqrt{2}}}, \binom{\frac{1}{\sqrt{3}}}{\frac{\sqrt{2}}{\sqrt{3}}}, \binom{0}{-1}, \binom{-\frac{1}{\sqrt{5}}}{\frac{{2}}{\sqrt{5}}}$$…"
- **ଗସ୍ - ସିଡ଼ାଲ୍ ପଦ୍ଧତି** (id 46156), from `en` "Gauss–Seidel method", 2015-08-28; 4 of 16 prose paragraphs English-dominant, e.g. "$A= \begin{bmatrix} 16 & 3 \\ 7 & -11 \\ \end{bmatrix}$ ଏବଂ $b= \begin{bmatrix} 11 \\ 13 \end{bmatrix}.$…"
- **ବୁଧି କୁନ୍ଦେରନ** (id 56226), from `en` "Budhi Kunderan", 2016-10-20; 1 of 15 prose paragraphs English-dominant, e.g. "Anshuman Pandey, 209\*, Madhya Pradesh v Uttar Pradesh, 1995-96…"
- **ସ୍କ୍ରୋଡିଙ୍ଗରଙ୍କ ବିରାଡ଼ି** (id 75283), from `en` "Schrödinger's cat", 2020-08-23; 1 of 30 prose paragraphs English-dominant, e.g. "$$\|\psi \rangle ={\frac {1} {\sqrt {2}}} {\bigg (}\|00\ldots 0\rangle +\|11\ldots 1\rangle {\bigg )}$$…"
- ସୋଫୋସବୁଭିର/ଭେଲପାଟାସଭିର/ଭୋକ୍ସିଲାପ୍ରେଭିର (id 93860): from `en` "User:Mr. Ibrahem/Sofosbuvir/velpatasvir/voxilaprevir", 2024-08-02, 103 Odia words, odia_ratio 0.95, 1 later human edits
- ଯୋନୀ ସ୍ରାବ (id 79462): from `en` "User:Mr. Ibrahem/Vaginal discharge", 2021-09-30, 206 Odia words, odia_ratio 0.98, 3 later human edits
- ପେଗସେଟାକୋପ୍ଲାନ (id 91883): from `en` "User:Mr. Ibrahem/Pegcetacoplan", 2024-04-01, 119 Odia words, odia_ratio 0.90, 2 later human edits
- ଏପକୋରିଟାମାବ (id 95206): from `mdwiki` "Epcoritamab", 2024-12-17, 106 Odia words, odia_ratio 0.90, 0 later human edits
- ଶିରାଧମନୀ କୁସଂଯୋଗ (id 80294): from `en` "User:Mr. Ibrahem/Arteriovenous malformation", 2022-01-07, 135 Odia words, odia_ratio 0.94, 2 later human edits
- ଶାଲିମାର ଏକ୍ସପ୍ରେସ (id 56232): from `en` "Shalimar Express", 2016-10-20, 153 Odia words, odia_ratio 0.95, 4 later human edits

## What the dumps cannot tell

- **Translations made outside the tools.** Text machine-translated elsewhere (Google Translate in a browser) and pasted in carries no tag. In the corpus, 4 articles use a translation template (`{{Translation}}`, `{{translation}}`, `{{ଅନୁବାଦ ଚାଲିଛି}}`), too few to flag anything. User-defined tags: `ଉଇକିପିଡ଼ିଆ:ଗୁଗୁଲ ଟ୍ରାନ୍ସଲେଟ ସାହାଯ୍ୟରେ ଅନୁବାଦ` (applied 0 times).
- **Before CX.** The first CX-tagged revision here is from 2015-06-09; earlier translations cannot carry a tag.
- **How much machine output survived.** The tag says the tool was used, not how much of its machine translation the translator kept. CX keeps that measure in its own tables, which the public dumps do not include. The `contenttranslation-high-unmodified-mt-text` tag (2 revisions) is the only direct signal here.
- **Deleted and re-created pages** keep only the new history. Merged histories can put an older revision first: 2 articles' first revision has a parent.
