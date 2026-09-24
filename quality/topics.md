# Topic tags for Odia Wikipedia

Built by `annotate.py topics` on 2026-09-24 for the 18,695 articles of the corpus (`orwiki-20260901-trainingready.jsonl`, 4,512,644 Odia words). Output: `annotations/topics.jsonl`, one JSON object per article in corpus order; the columns are described in `annotations/topics.json`. Titles and category names are written with ASCII digits, here and in the annotation.

**Coverage: 97.4% of articles (96.4% of words) have a topic.** Precision of `primary_topic`, checked by hand on a held-out random sample: see [Precision](#precision).

## Topics

| Topic | Covers | Articles (primary) | Share | Odia words | Share | Articles listing it | Biographies | Odisha |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| `calendar` | year, date, month, decade and century pages; observances | 272 | 1.5% | 33,738 | 0.7% | 292 | 1 | 8 |
| `film` | film, television and entertainment, including actors, directors, models | 2,717 | 14.5% | 523,266 | 11.6% | 2,805 | 1,438 | 1,534 |
| `sports` | sports, sportspeople, clubs, venues, tournaments | 539 | 2.9% | 144,597 | 3.2% | 546 | 464 | 87 |
| `health` | health and medicine: diseases, drugs, anatomy, nutrition, hospitals | 3,269 | 17.5% | 678,819 | 15.0% | 3,297 | 49 | 43 |
| `biology` | biology and nature: plants, animals, taxa, ecology, forests, sanctuaries | 587 | 3.1% | 198,700 | 4.4% | 622 | 36 | 106 |
| `mathematics` | mathematics and numbers | 57 | 0.3% | 17,043 | 0.4% | 73 | 13 | 4 |
| `science` | physical and earth sciences: physics, chemistry, astronomy, geology, weather | 371 | 2.0% | 101,904 | 2.3% | 462 | 115 | 32 |
| `technology` | technology and engineering: computing, internet, vehicles, transport, space tech | 411 | 2.2% | 130,242 | 2.9% | 453 | 47 | 30 |
| `economy` | economy and business: companies, industry, agriculture, banking, trade | 190 | 1.0% | 57,829 | 1.3% | 216 | 98 | 35 |
| `education` | education: schools, colleges, universities, teachers | 157 | 0.8% | 26,388 | 0.6% | 192 | 20 | 119 |
| `religion` | religion, mythology and philosophy: deities, temples, scriptures, saints | 1,235 | 6.6% | 485,958 | 10.8% | 1,354 | 87 | 398 |
| `literature` | literature and language: writers, poets, books, periodicals, languages, scripts | 1,726 | 9.2% | 414,180 | 9.2% | 1,888 | 1,226 | 1,163 |
| `arts` | arts and culture: music, dance, theatre, painting, festivals, food, customs, crafts | 1,120 | 6.0% | 356,004 | 7.9% | 1,304 | 530 | 463 |
| `history` | history and military: empires, dynasties, rulers, wars, freedom struggle, monuments | 530 | 2.8% | 234,126 | 5.2% | 627 | 284 | 114 |
| `politics` | politics, government and law: politicians, elections, legislatures, courts, schemes | 2,557 | 13.7% | 516,263 | 11.4% | 2,667 | 2,063 | 1,858 |
| `society` | society: communities, tribes, castes, organisations, social movements, activists | 193 | 1.0% | 84,672 | 1.9% | 253 | 81 | 52 |
| `geography` | geography and places: countries, states, districts, towns, villages, rivers | 2,282 | 12.2% | 347,447 | 7.7% | 2,396 | 4 | 622 |
| (none) | no evidence | 482 | 2.6% | 161,468 | 3.6% | | 112 | 76 |

- **`school_relevant`**: 10,706 articles (57.3%), 2,614,190 words (57.9%).
- **`is_person`** (biographies): 6,668 articles (35.7%), 1,557,253 words. Film and sports biographies: 1,902 articles, 432,407 words (9.6%).
- **`odisha`**: 6,744 articles (36.1%), 1,385,318 words (30.7%).
- 1,141 articles list more than one topic.

`health` is large because of WikiProject Medicine's translation drive: 1,936 of its 3,269 articles are machine-assisted translations (`translated` in `annotations/translation.jsonl`), 1,912 of them from MDWiki sources, mostly drug and disease pages. `calendar` is date pages with events, weekdays and observances (year pages are excluded). `politics` includes 1,326 biographies of Odisha assembly members.

## Method

Every article gets evidence from three sources, strongest first:

1. **Its categories.** They come from the rendered HTML of the dump revision (`raw/html`), where Parsoid lists every category as `<link rel="mw:PageProp/Category">`, including those added by templates. (Category links nested in references' `data-mw` repeat top-level ones or are maintenance, and are ignored.) Hidden categories (`hiddencat` in `page_props`, kept in `raw/orwiki-20260901-category-graph.jsonl.gz`) are dropped, and so are visible maintenance ones: English tracking categories that templates copied from English Wikipedia (`Pages using …`, `CS1 …`, `Articles with …`) and Odia cleanup ones (ଆଧାରହୀନ "unreferenced", ସଜଡ଼ା ହେବାକୁ "to be cleaned up", bot and edit-a-thon bookkeeping). What is left is the `categories` column.
2. **Its title**: year and date pages (`1946`, `19 ମାର୍ଚ୍ଚ`) are `calendar`, and a qualifier (`(ଓଡ଼ିଆ କଥାଚିତ୍ର)`, `(ରାଜନେତା)`) is read like a category.
3. **Its Wikidata item**, only when no rule matches a category's own name or the title (2,767 dump articles): P31 (instance of), or P279 (subclass of) for a concept item that has no P31 (rice, hardware), P106 (occupation, for people) and P2175 (medical condition treated: a drug), mapped to topics through curated tables of the classes these items use (401 classes, 209 occupations), and through up to two P279 (subclass of) levels for the rest.

**Category rules.** A category name is matched against ordered keyword rules. Names are folded first, for matching only: nukta, vowel length, ଵ/ୱ, ୟ/ଯ, ଙ୍/ଂ, ZWJ/ZWNJ and Odia digits (never NFC). The first rule that matches decides, and specific rules come before general ones (ଚିକିତ୍ସା ବିଜ୍ଞାନ "medical science" is `health`, not `science`; ହିନ୍ଦୁ ପର୍ବ "Hindu festivals" is `religion`, not `arts`; କଥାଚିତ୍ର ଗୀତିକାର "film lyricists" is `film`). Words match at the start of an Odia word, so ପର୍ବ "festival" does not match ପର୍ବତ "mountain" and ଗ୍ରହ "planet" does not match ସଂଗ୍ରହ "collection".

- *People categories carry no topic*: births, deaths, ଜୀବିତ ବ୍ୟକ୍ତି "living people", "people of <place>" (କଟକ ଜିଲ୍ଲାର ଲୋକ), and awards that name no field (ପଦ୍ମଶ୍ରୀ ସମ୍ମାନିତ). A biography therefore gets the topic of its occupation categories (ଓଡ଼ିଆ କବି "Odia poets" → `literature`, ହିନ୍ଦୀ କଥାଚିତ୍ର ଅଭିନେତ୍ରୀ → `film`) or field awards (ସାହିତ୍ୟ ଏକାଡେମୀ → `literature`).
- *A bare district* (`କଟକ ଜିଲ୍ଲା`) holds articles of every kind located there, so it is weak `geography` evidence (0.4, against 1.0 for a rule match). A bare country, state or continent (`ଭାରତ`, `ଜାପାନ`, `ଏସିଆ`) matches no rule at all (see the walk).
- *No rule matches*: the category's parents are searched, up to 3 levels up. The graph comes from the `page`, `linktarget` and `categorylinks` dumps, reduced to `raw/orwiki-20260901-category-graph.jsonl.gz`, and it has cycles. The nearest level with a match decides, at weight 0.5. The walk does not continue through people categories or through general hubs. If the first level only says `geography`, the category is itself a place (ଜାପାନ in ଏସିଆର ଦେଶ "Asian countries"): its articles are things of that place (a dish, a myth, a census), so the walk gives nothing. A bare year category (`2019`) gives nothing either.

**Scores.** Each category adds its weight (split across its topics), a title adds 3 (year or date) or 1.5 (qualifier), and Wikidata adds 1.0 (P31, split) and 1.5 (P106, split). `primary_topic` is the highest score. Ties go to the topic of the earlier category, since editors list the main one first, or the earlier Wikidata statement. `topics` lists every topic scoring at least 0.5 and 35% of the top score. `topic_scores` keeps the sums. `topic_source` names the evidence behind the primary topic: `category` 15,066, `wikidata` 2,073, `category+title` 797, `none` 482, `category+wikidata` 68, `title` 60, `category+parent_category` 59, `parent_category` 44, `parent_category+wikidata` 41, `category+parent_category+title` 3, `parent_category+title` 2.

**Flags.**

- `is_person`: Wikidata P31 = human, when the item was fetched; else any people or occupation category.
- `odisha`: a category or the title names Odisha, Odia, Ollywood, Utkal, Kalinga, Jagannath, or one of the 30 districts or the main towns. District names are matched at the start of a word, and ବୌଦ୍ଧ only as "Boudh district", because it also means Buddhist.
- `school_relevant`: `primary_topic` is taught directly in Odisha's classes 1–10 (science, biology, health, mathematics, technology, geography, history, politics or civics, economy, literature and language, society, education), or is `arts`/`religion` and `odisha` (Odisha's culture is in the social science syllabus). For a biography, only science, biology, health, mathematics, technology, history and literature count: a scientist, a freedom fighter or a poet, but not a sitting MLA or an actor. It is a subject flag. Combine it with `bot_created`, `words` and `odia_ratio` from the corpus for quality.

## Coverage

18,213 of 18,695 articles (97.4%) have a topic; 482 (2.6%, 3.6% of words) have none. Why the untagged have none:

- Wikidata class not mapped to a topic: 132 (e.g. ଭାରତ ରତ୍ନ, ମମତା, ଦାଶ, କୌରବ, ଗ୍ରାଣ୍ଡ ହୋଟେଲ କାଠମାଣ୍ଡୁ)
- no Wikidata item, and no category or title rule: 122 (e.g. କାର୍ତ୍ତିକ ବ୍ରତ, ପରମାନନ୍ଦ ଆଚାର୍ଯ୍ୟ, ଓଡ଼ିଶାରେ ସାଧବ ସଂସ୍କୃତି, ହାଟଡିହୀ, କେଦାରନାଥ ଜେନା)
- Wikidata item without P31 or P279: 115 (e.g. ଓଡ଼ିଶାର ତହସିଲ ତାଲିକା, ସୁକାନ୍ତ କୁମାର ତ୍ରିପାଠୀ, ରକ୍ଷା ଉତ୍ପାଦନ ବିଭାଗ, ରକ୍ଷା ଅନୁସନ୍ଧାନ ଓ ବିକାଶ ବିଭାଗ, ରକ୍ଷା ବିଭାଗ)
- a person with no field in categories or Wikidata P106: 78 (e.g. ଗୌରୀଶଙ୍କର ରାୟ, ଦେବକୀ, ଦାଶରଥି ପଟ୍ଟନାୟକ, ପ୍ରଥମ ନରସିଂହ ଦେବ, ପ୍ରତାପ ଚନ୍ଦ୍ର ଭଞ୍ଜଦେଓ)
- a list or disambiguation page: 35 (e.g. ସାର୍ବଭୌମ ସ୍ୱାଧୀନ ଦେଶମାନଙ୍କର ତାଲିକା, ଓଡ଼ିଆ ଖବରକାଗଜ ତାଲିକା, ଓଡ଼ିଶାର ସାଂସଦ ମାନଙ୍କର ତାଲିକା, ଆସନ ତାଲିକା, ଓଡ଼ିଆ ପତ୍ରିକା ଗୁଡ଼ିକର ତାଲିକା)

## Precision

Hand check by reading each article's title and lead (not its categories), labels in `quality/topics-check.tsv`. A prediction is correct when `primary_topic` is one of the acceptable topics given for the article, usually one, two or three where the subject straddles (a temple's history, a dancer who acts).

| Sample | Articles | Tagged | `primary_topic` correct | 95% interval | `is_person` agrees | `odisha` agrees |
|---|---:|---:|---:|---:|---:|---:|
| **test: random, held out** | 90 | 87 | 86 (98.9%) | 94–100% | 90/90 | 89/90 |
| dev: random, used to tune the rules | 111 | 109 | 109 (100.0%) | 97–100% | 111/111 | 109/111 |
| stratified: 8 per small topic | 72 | 72 | 65 (90.3%) | 81–95% | 70/72 | 70/72 |

The test set was labelled after the rules were frozen. Its one systematic error, members of the pre-independence assemblies (ସ୍ୱାଧୀନତା ପୂର୍ବର … ବିଧାନ ସଭାର ସଭ୍ୟ) tagged `history` through the word ସ୍ୱାଧୀନତା "independence", was then fixed (a legislator rule before `history`, 131 articles). At first scoring the test set gave 95 of 97. The stratified set was drawn and labelled after that fix, and gave 64 of 72 at first scoring. Changed after the stratified check (the tables above are recomputed with the final rules): Wikidata P279 for items without P31, and more Wikidata class anchors (these only reach articles with no direct category or title match); a bare district category is weak `geography` before any other rule (ଗଜପତି ଜିଲ୍ଲା is the district, not the Gajapati dynasty; ବୌଦ୍ଧ ଜିଲ୍ଲା is not Buddhism); bare continents (ଏସିଆ, ଆମେରିକା) no longer count as `geography`; and person words (ବ୍ୟକ୍ତି "person" no longer matches ବ୍ୟକ୍ତିଗତ "personal"; -ଶାସ୍ତ୍ରୀ "scholar of" and English occupation categories count). They mostly give a topic to articles that had none; in the checks, only the kimono changed (`economy` to `arts`, through the new clothing anchor).

Per predicted topic (random samples dev + test, and the stratified sample):

| `primary_topic` | Random: correct / predicted | Stratified: correct / predicted |
|---|---:|---:|
| `calendar` | 4 / 4 | – |
| `film` | 28 / 28 | – |
| `sports` | 5 / 5 | 8 / 8 |
| `health` | 38 / 38 | – |
| `biology` | 3 / 3 | 8 / 8 |
| `mathematics` | – | 8 / 8 |
| `science` | 2 / 2 | 5 / 8 |
| `technology` | 2 / 2 | 8 / 8 |
| `economy` | 3 / 3 | 7 / 7 |
| `education` | 2 / 2 | 7 / 8 |
| `religion` | 17 / 17 | – |
| `literature` | 17 / 17 | – |
| `arts` | 10 / 10 | 1 / 1 |
| `history` | 5 / 5 | 8 / 8 |
| `politics` | 34 / 35 | – |
| `society` | 3 / 3 | 5 / 8 |
| `geography` | 22 / 22 | – |

Every error in the checks:

- test: **ହରିପୁରା** (village in Gujarat, 1938 Congress session): `politics` from category, expected geography/history
- stratified: **କେ.ଏସ୍. ଚନ୍ଦ୍ରିକା** (Malayalam writer (categories: Dalit activist, feminist)): `society` from category, expected literature
- stratified: **ମଲ୍ଲୁ ସ୍ୱରାଜୟମ** (CPI(M) politician, freedom fighter): `society` from wikidata, expected history/politics
- stratified: **ଅଣଧର୍ମବାଦ** (irreligion): `society` from wikidata, expected religion
- stratified: **ଲେସିନୁରାଡ** (gout drug): `science` from wikidata, expected health
- stratified: **ସୁଦିପ୍ତ କବିରାଜ** (political scientist (categorised as scientist)): `science` from category, expected history/politics
- stratified: **ରୋହିଣୀ (ନକ୍ଷତ୍ର)** (Rohini, nakshatra in Hindu astrology): `science` from category+title, expected religion
- stratified: **ଅମିତା ଅଗ୍ରୱାଲ୍** (clinical immunologist): `education` from wikidata, expected health
- untagged in the checks: ତଥ୍ୟ ନିରାପତ୍ତା (information security), ମଥୁରା ପେଡ଼ା (Mathura peda, sweet), ରାଷ୍ଟ୍ରୀୟ ପ୍ରଦ୍ୟୋଗିକ ସଂସ୍ଥାନ (ଏନ.ଆଇ.ଟି) (National Institutes of Technology), ପଶ୍ଚିମ ଓଡ଼ିଶା ବିକାଶ ପରିଷଦ (Western Odisha Development Council), ବାମାପଦ ତ୍ରିପାଠୀ (Odia journalist and editor)

## Known weaknesses

- **Categories are only as good as the editors'.** A political scientist in ବୈଜ୍ଞାନିକ "scientists" becomes `science`; a writer categorised only as a Dalit activist and a feminist becomes `society`.
- **The parent walk and bare places are the weakest evidence.** On 30 walk-only articles, about a third were wrong before the place rule and the Wikidata fallback were added. 44 articles still rest on the walk alone. The `geography` ones among them are natural disasters (cyclones, tsunamis).
- **Wikidata drugs look like chemicals**: a drug with no P2175 (medical condition treated) is `science`, not `health`.
- **Wikidata is live, not a dump.** The items were fetched on the build date (cached in `raw/orwiki-20260901-wikidata.jsonl.gz`), so they can differ from Wikidata on 2026-09-01.
- **`odisha` needs a category or the title.** An Odia writer or journalist with no categories is missed; Wikidata's place of birth is not used.
- **Borderline subjects**: a nakshatra is `science` (astronomy) here, `religion` (astrology) to some readers; a Wikipedia language edition is `technology`.
