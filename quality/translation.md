# Machine-assisted translations in Odia Wikipedia

Built by `annotate.py translation` on 2026-09-24 from the `orwiki-20260901` dumps, for the 18,695 articles of the corpus (`orwiki-20260901-trainingready.jsonl`). Output: `annotations/translation.jsonl` (one JSON object per article, in corpus order; columns in `annotations/translation.json`). Titles are written with ASCII digits.

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
| `ct_created` | 3,528 | 18.9% | 665,909 | 14.8% |
| `ct_any` | 3,553 | 19.0% | 683,401 | 15.1% |
| `mdwiki_created` | 298 | 1.6% | 52,907 | 1.2% |
| `mdwiki_any` | 309 | 1.7% | 54,237 | 1.2% |
| **`translated`** | 3,845 | 20.6% | 731,135 | 16.2% |

All 18,695 articles hold 4,512,644 Odia words.

By year of the translation (`translated_at`), for `translated` articles:

| Year | Articles | Odia words | CX | MDWiki | Median words |
|---|---:|---:|---:|---:|---:|
| 2015 | 23 | 3,399 | 23 | 0 | 119 |
| 2016 | 859 | 136,990 | 859 | 0 | 133 |
| 2017 | 263 | 51,094 | 263 | 0 | 143 |
| 2018 | 61 | 23,590 | 61 | 0 | 259 |
| 2019 | 95 | 28,618 | 95 | 0 | 181 |
| 2020 | 108 | 44,478 | 108 | 0 | 312 |
| 2021 | 304 | 71,855 | 304 | 0 | 182 |
| 2022 | 514 | 91,121 | 514 | 0 | 144 |
| 2023 | 599 | 112,758 | 599 | 2 | 131 |
| 2024 | 851 | 131,960 | 670 | 188 | 120 |
| 2025 | 149 | 30,521 | 30 | 119 | 181 |
| 2026 | 19 | 4,751 | 19 | 0 | 214 |

Source wikis of `translated` articles: `en` 3,505, `mdwiki` 300, `simple` 21, `hi` 8, `as` 3, `ro` 3, `bn` 2, `km` 1, `de` 1, `te` 1. (`simple` is Simple English; `mdwiki` is mdwiki.org, English. 1,618 `en` sources are medical drafts in `User:Mr. Ibrahem/`, the English Wikipedia user space of the MDWiki translation organiser.)

## Translated articles vs. the rest

`odia_ratio` is the corpus field (share of non-space characters in the Odia block). A paragraph (`text.split("\n\n")`) is English-dominant when it has over 30 Latin letters and more than twice as many Latin as Odia letters.

| Group | Articles | Mean `odia_ratio` | Median `odia_ratio` | Articles < 0.6 | English-dominant paragraphs | of prose paragraphs | Articles with any | Median words |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| CX-created (`ct_created`) | 3,528 | 0.909 | 0.946 | 3.4% | 499 of 25,674 (1.9%) | 0.1% | 6.4% | 137 |
| MDWiki-created (`mdwiki_created`) | 298 | 0.953 | 0.956 | 0.0% | 0 of 1,363 (0.0%) | 0.0% | 0.0% | 180 |
| `translated` (all machine-assisted) | 3,845 | 0.913 | 0.947 | 3.1% | 501 of 27,375 (1.8%) | 0.1% | 5.9% | 140 |
| not `translated` | 14,850 | 0.906 | 0.938 | 1.4% | 582 of 134,677 (0.4%) | 0.1% | 2.2% | 156 |
| not `translated`, created 2015 or later | 10,970 | 0.904 | 0.936 | 1.4% | 451 of 98,327 (0.5%) | 0.1% | 2.4% | 162 |
| not `translated`, human-created, 2015 or later | 10,081 | 0.911 | 0.940 | 1.0% | 446 of 91,526 (0.5%) | 0.1% | 2.5% | 174 |

`translated` articles hold 501 of the corpus's 1,083 English-dominant paragraphs (46.3%), against 16.9% of all paragraphs. Blocks are the contract's paragraphs, so tables and lists count: 990 of the 1,083 are table, list or heading blocks (untranslated names in lists of rivers, lakes, records). All English-dominant paragraphs together contain 3,097 Odia words.

Post-editing of `translated` articles (non-bot revisions after the first):

- median 3 edits; 5.3% never edited again by a person; 25.4% never touched by anyone but the translator.
- For comparison, human-created articles that are not `translated`: median 7 edits (created 2015 or later).

## Examples

- **ଗସ୍ - ସିଡ଼ାଲ୍ ପଦ୍ଧତି** (id 46156), from `en` "Gauss–Seidel method", 2015-08-28; 5 of 17 prose paragraphs English-dominant, e.g. "$A= \begin{bmatrix} 16 & 3 \\ 7 & -11 \\ \end{bmatrix}$ ଏବଂ $b= \begin{bmatrix} 11 \\ 13 \end{bmatrix}.$…"
- **କ୍ୱାଣ୍ଟମ କମ୍ପ୍ୟୁଟିଙ୍ଗ** (id 72578), from `en` "Quantum computing", 2020-02-04; 5 of 56 prose paragraphs English-dominant, e.g. "$$\binom{\frac{1}{\sqrt{2}}}{\frac{1}{\sqrt{2}}}, \binom{\frac{1}{\sqrt{3}}}{\frac{\sqrt{2}}{\sqrt{3}}}, \binom{0}{-1}, \binom{-\frac{1}{\sqrt{5}}}{\frac{{2}}{\sqrt{5}}}$$…"
- **ବୁଧି କୁନ୍ଦେରନ** (id 56226), from `en` "Budhi Kunderan", 2016-10-20; 1 of 15 prose paragraphs English-dominant, e.g. "Anshuman Pandey, 209\*, Madhya Pradesh v Uttar Pradesh, 1995-96…"
- **ଅଜୟଗଡ ରାଜ୍ୟ** (id 56693), from `en` "Ajaigarh State", 2016-11-16; 1 of 5 prose paragraphs English-dominant, e.g. "ମହାରାଜାଧିରାଜ ଛତ୍ରସାଲ : 1649-1731 (founder Ruler of many Kingdoms) \_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\|\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_ Hirdeshah ଜଗତରାଜ Bhartichandra (Panna)…"
- ଭେଲାଗ୍ଲୁସେରେଜ ଆଲଫା (id 93834): from `en` "User:Mr. Ibrahem/Velaglucerase alfa", 2024-07-31, 124 Odia words, odia_ratio 0.91, 3 later human edits
- ସ୍ୱେଦାଧିକ୍ୟ (id 79428): from `en` "User:Mr. Ibrahem/Hyperhidrosis", 2021-09-27, 194 Odia words, odia_ratio 0.95, 2 later human edits
- ରାଭୁଲିଜୁମାବ (id 91867): from `en` "User:Mr. Ibrahem/Ravulizumab", 2024-03-31, 97 Odia words, odia_ratio 0.90, 2 later human edits
- ଗର୍ଭାବସ୍ଥାରେ ଉଚ୍ଚ ରକ୍ତଚାପ (id 95184): from `mdwiki` "High blood pressure in pregnancy", 2024-12-15, 239 Odia words, odia_ratio 0.93, 1 later human edits
- ଲାରିଙ୍ଗୋମାଲାସିଆ (id 80217): from `en` "User:Mr. Ibrahem/Laryngomalacia", 2021-12-27, 170 Odia words, odia_ratio 0.98, 2 later human edits
- ଜମ୍ମୁ ତୱି ରେଳ ଷ୍ଟେସନ (id 56229): from `en` "Jammu Tawi railway station", 2016-10-20, 200 Odia words, odia_ratio 0.97, 9 later human edits

## What the dumps cannot tell

- **Translations made outside the tools.** Text machine-translated elsewhere (Google Translate in a browser) and pasted in carries no tag. In the corpus, 4 articles use a translation template (`{{Translation}}`, `{{translation}}`, `{{ଅନୁବାଦ ଚାଲିଛି}}`), too few to flag anything. User-defined tags: `ଉଇକିପିଡ଼ିଆ:ଗୁଗୁଲ ଟ୍ରାନ୍ସଲେଟ ସାହାଯ୍ୟରେ ଅନୁବାଦ` (applied 0 times).
- **Before CX.** The first CX-tagged revision here is from 2015-06-09; earlier translations cannot carry a tag.
- **How much machine output survived.** The tag says the tool was used, not how much of its machine translation the translator kept. CX keeps that measure in its own tables, which the public dumps do not include. The `contenttranslation-high-unmodified-mt-text` tag (2 revisions) is the only direct signal here.
- **Deleted and re-created pages** keep only the new history. Merged histories can put an older revision first: 2 articles' first revision has a parent.
