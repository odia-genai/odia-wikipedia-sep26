# Review first

The 200 articles most likely to hold data problems, ranked from Sarvam-1 bits per byte (`quality/bpb.md`). The failure types are interleaved (garbled, English, templated, table, markup, other script, whole article, then round again), so the top of the list shows every kind of problem. Within a type, the most extreme come first: how far the paragraph's bpb is from the median of its kind and length band (robust z of log bpb), plus a little weight for size.

The full list is in `annotations/bpb.jsonl` (`review_rank`, `review_type`, `review_para`, `review_reasons`; null rank = not flagged). The web app (`edaapp`) shows it as a review queue. Every paragraph's score is in `annotations/bpb.paragraphs.jsonl`.

Not in this queue: 345 paragraphs (345 distinct texts, 439,253 B, in 258 articles) whose text has no score yet (`bpb` null; see `quality/bpb.md`, Not scored yet).

## Legend

| type | meaning | in queue | candidates |
| --- | --- | ---: | ---: |
| `garbled` | Odia text the model finds very unlikely for its kind and length: garbled OCR or typing, broken sentences, odd mixtures. Verse, songs and Sanskrit also score high; they are ranked after prose within this type | 34 | 845 |
| `english` | a paragraph that is mostly English: untranslated leftovers, quotes, citations, OCR'd English | 34 | 276 |
| `templated` | very predictable text for its kind and length (bottom 1%): formulaic sentences, lists and near-copies across articles; the reasons say when a copy was found | 34 | 898 |
| `table` | a table in the top or bottom 1% of tables: English-only tables, IPA or name lists, repeated career tables | 30 | 53 |
| `markup` | leftovers of the HTML/wikitext conversion ({{ }}, [[ ]], {\| \|}, tags, class=, namespace prefixes, px sizes, URLs), any bpb | 0 | 0 |
| `script` | a paragraph mostly in another script (Shahmukhi, Brahmi, Telugu, ...) or in Latin letters that are not English (transliteration, IPA, other languages, romanised titles) | 34 | 290 |
| `article` | the article as a whole: its bpb is in the top or bottom 1% of articles >= 500 B, or most of its bytes are in extreme paragraphs (catches pages made of many tiny paragraphs) | 34 | 448 |

Reason wording: *para 7 (text, 412 B): bpb 1.84, top 0.1% of text 300-999 B (median 0.45)* means paragraph 7 (`text.split("\n\n")[7]`) is a 412-byte prose paragraph whose bpb is in the top 0.1% of prose paragraphs of 300-999 bytes, whose median is 0.45. Extra notes: the share of Latin or other-script letters and of common English words among the Latin ones, conversion leftovers found (`markup`), verse, how many other articles hold a near-copy or the same sentence frame, repetition inside the paragraph, and a table header shared with other articles. *context:* lines come from the topic and Content Translation annotations when present.

Usually fine on inspection: verse, songs and Sanskrit (`garbled`, marked *verse or song lines*), runs of names, formulaic but correct prose in `templated`, very predictable whole articles about well-known subjects (`article` with a *bottom* percentile), and quotations kept on purpose in their own script. Usually worth fixing: `markup` (all of them), `english` prose, OCR'd text (*high even for English*), and pages that are mostly English or Latin-script lists (`article` with a low odia_ratio).

## Top 50

1. **ବିଷ୍ଣୁ ମାଝୀ** (id 80916, para 6, `garbled`)
   - para 6 (text, 1,035 B): bpb 1.17, top 0.01% of text 1-3 kB (median 0.49)
   - para 1 (text, 1,371 B): bpb 0.76, top 1% of text 1-3 kB (median 0.49)
   - context: topic arts
   > ହିସାବରୁ ଜଣାଯାଏ, ମାଝୀ 5000ରୁ ଅଧିକ ଗୀତ ଗାଇଥିଲେ, ଓ ତାଙ୍କ ମଧ୍ୟରୁ ଲୋକପ୍ରିୟ ଗୀତ ଗୁଡ଼ିକର ନାମ ଯଥାକ୍ରମେ "କସଲାଅଇ ସୋଧନେ ହୋଲା", "ସିତଲ ଦିନେ ପିପଲ ସମି ଚା", "ଡ୍ରାଇଭର ଦାଇ ମନ ପର୍ୟୋ ମଲାଇ", "ଲାଲୁପତେ ନୁଘ୍ୟୋ ଭୁଇନ୍ତର", "ମାଇ ଚୋରୀ ସଲାଲା", "ନା ଜ…

2. **ଗାଲିଲିଓ** (id 18991, para 19, `english`)
   - para 19 (list, 7,621 B): bpb 0.72, 89th percentile of list >=100 B (median 0.51); 85% Latin letters, 31% common English words
   - context: topic science
   > - 1543 – ନିକୋଲସ କପରନିକସ (Nicolaus Copernicus), ଟଲେମୀଙ୍କ ଭୂ-କୈନ୍ଦ୍ରିକ ମଡେଲର ବିକଳ୍ପ ରୂପେ De revolutionibus orbium coelestium ପ୍ରକାଶ କଲେ, ଯାହାକି କପରନିକସଙ୍କ ମୃତ୍ୟୁ ପରବର୍ତ୍ତୀ କାଳରେ ଆରିଷ୍ଟୋଟଲୀୟ ଭୌତିକ-ଶାସ୍ତ୍ର (Aristotelian phy…

3. **ସ୍ୱାମୀ ବିବେକାନନ୍ଦ** (id 18187, para 192, `templated`)
   - para 192 (list, 1,102 B): bpb 0.13, bottom 0.1% of list 1-3 kB (median 0.56); 79% of its word triples repeat within it
   - para 188 (text, 985 B): bpb 0.30, bottom 0.5% of text 300-999 B (median 0.53)
   - para 125 (text, 436 B): bpb 0.96, top 1% of text 300-999 B (median 0.53)
   - context: topic religion
   > 1. ସ୍ୱାମୀ ବିବେକାନନ୍ଦ ବାଣୀ ଓ ରଚନା, ଖଣ୍ଡ 1 ⏎ 2. ସ୍ୱାମୀ ବିବେକାନନ୍ଦ ବାଣୀ ଓ ରଚନା, ଖଣ୍ଡ 2 ⏎ 3. ସ୍ୱାମୀ ବିବେକାନନ୍ଦ ବାଣୀ ଓ ରଚନା, ଖଣ୍ଡ 3 ⏎ 4. ସ୍ୱାମୀ ବିବେକାନନ୍ଦ ବାଣୀ ଓ ରଚନା, ଖଣ୍ଡ 4 ⏎ 5. ସ୍ୱାମୀ ବିବେକାନନ୍ଦ ବାଣୀ ଓ ରଚନା, ଖଣ୍ଡ 5 ⏎ 6. ସ…

4. **ଲୋକ ସଭା** (id 33455, para 6, `table`)
   - para 6 (table, 1,846 B): bpb 0.11, bottom 0.5% of table 1-3 kB (median 0.70); 83% of its word triples repeat within it
   - article: bpb 0.26, bottom 0.01% of articles >= 500 B (median 0.55)
   - para 1 (text, 1,420 B): bpb 0.30, bottom 1% of text 1-3 kB (median 0.49)
   - context: topic politics
   > | ଲୋକ ସଭା | ନିର୍ବାଚନ ବର୍ଷ | କାର୍ଯ୍ୟକାଳ | ⏎ |---|---|---| ⏎ | 1ମ ଲୋକ ସଭା | ସାଧାରଣ ନିର୍ବାଚନ 1952 | 1952–1957 | ⏎ | 2ୟ ଲୋକ ସଭା | ସାଧାରଣ ନିର୍ବାଚନ 1957 | 1957–1962 | ⏎ | 3ୟ ଲୋକ ସଭା | ସାଧାରଣ ନିର୍ବାଚନ 1962 | 1962–1967 | ⏎ | 4ର…

5. **ମୁକ୍ରୀ** (id 56147, para 11, `script`)
   - para 11 (list, 6,417 B): bpb 1.46, top 0.01% of list >=100 B (median 0.51); 100% Latin letters, 4% common English words
   - article: bpb 1.21, top 0.1% of articles >= 500 B (median 0.55); 66% of its bytes are in extreme paragraphs; odia\_ratio 0.15
   - para 13 (list, 152 B): bpb 1.55, top 5% of list 100-299 B (median 0.97); 100% Latin letters, 7% common English words
   - context: machine-assisted translation (Content Translation, from en)
   - context: topic film
   > - Sharaabi (1984).... Natthulal ⏎ - Betaaj Badshah (1994) .... College Principal ⏎ - Baali Umar Ko Salaam (1994) (uncredited) .... Rahul ⏎ - Farishtay (1991) ⏎ - Trinetra (1991) .... Organizer Show Fight Wrestler in Wat…

6. **"ଖ ଚମ୍ପୂ" - ଖରାପ ତୁ ହେଲୁ ରେ** (id 98320, whole article, `article`)
   - article: bpb 1.60, top 0.01% of articles >= 500 B (median 0.55)
   - para 2 (text, 123 B): bpb 1.68, top 0.5% of text 100-299 B (median 0.69)
   - para 7 (text, 171 B): bpb 1.64, top 1% of text 100-299 B (median 0.69)
   > (para 2, bpb 1.68) ଖରାପ ତୁ ହେଲୁରେ ॥ ଖେଳଲୋଳଖଞ୍ଜନାଖି କି ସାହସ କଲୁ ରେ,

7. **ପାଇକ** (id 17158, para 9, `garbled`)
   - para 9 (text, 1,111 B): bpb 1.10, top 0.01% of text 1-3 kB (median 0.49)
   - para 22 (text, 1,024 B): bpb 0.75, top 1% of text 1-3 kB (median 0.49)
   - para 29 (text, 1,774 B): bpb 1.05, top 0.1% of text 1-3 kB (median 0.49); verse or song lines
   - context: topic society, Odisha
   > ପାଇକମାନେ ବ୍ୟବହାର କରୁ ଥିବା ଅସ୍ତ୍ର ଶସ୍ତ୍ରଗୁଡ଼ିକ ହେଉଛି କଣ୍ଟିଆ, ଅର୍ଦ୍ଧଚନ୍ଦ୍ର, ହବୁଡ଼ା, ଭୂଷଣ୍ଡୀ, କଟାରି, ଯୋଡ଼ାଗୁଆଳି, ଭିନ୍ଦିପାଳ, ଭାଲିମୁଖ, ପଞ୍ଚସୁର, ଜଜାଳ, କଉତୁଣ୍ଡି, ସପ୍ତସୁର, ଢାଲ, ଚକ୍ର, ଫରକଟ, ଶୂଳ, ବାଘନଖିଆ, ଗୋପୁଚ୍ଛ, ଭାଲ୍ଳା, କାଣ୍ଡଶର…

8. **ସାଦିୟା ଆୟମାନ** (id 97980, para 5, `english`)
   - para 5 (text, 1,285 B): bpb 1.03, top 0.1% of text 1-3 kB (median 0.49); 55% Latin letters, 44% common English words
   - article: bpb 1.14, top 0.5% of articles >= 500 B (median 0.55); 60% of its bytes are in extreme paragraphs; odia\_ratio 0.25
   - para 14 (table, 1,609 B): bpb 1.57, top 0.01% of table 1-3 kB (median 0.70); 100% Latin letters, 2% common English words
   - context: topic film
   > ଆୟମାନ 2019 ମସିହାରେ ଇମ୍ରାଉଲ ରାଫତଙ୍କ ନିର୍ଦ୍ଦେଶିତ ଟେଲିଭିଜନ ଡ୍ରାମା ଟୁ ବି ୱାଇଫ ଜରିଆରେ ଅଭିନୟ ଜଗତରେ କାମ କରିବା ଆରମ୍ଭ କରିଥିଲେ । ପରେ, ସେ ଆଜ ଆକାଶେ ଚାନ୍ଦ ନେଇ, ବ୍ରିଷ୍ଟିର ଓପେଖାଇ, ହାସି, ମେଡିସିନ୍ ମ୍ୟାନ୍, ନିରେର ପଖି, ତୁଇ ଚାରା ମୋନ ଭାଲୋ ନେ…

9. **ଚିନ୍ତାମଣି ଜେନା (ରାଜନୀତିଜ୍ଞ)** (id 70599, para 11, `templated`)
   - para 11 (text, 1,313 B): bpb 0.20, bottom 0.01% of text 1-3 kB (median 0.49); near-copy (word-pair overlap 64%) in 1 other article
   - para 9 (text, 1,006 B): bpb 0.22, bottom 0.01% of text 1-3 kB (median 0.49); near-copy (word-pair overlap 58%) in 4 other articles
   - para 8 (text, 564 B): bpb 0.28, bottom 0.5% of text 300-999 B (median 0.53); near-copy (word-pair overlap 71%) in 4 other articles
   - context: topic politics, Odisha
   > 1980 ଭାରତୀୟ ସାଧାରଣ ନିର୍ବାଚନରେ ଚିନ୍ତାମଣି ଭାରତୀୟ ଜାତୀୟ କଂଗ୍ରେସ (ଇ)ର ପ୍ରାର୍ଥୀ ଭାବରେ ବାଲେଶ୍ୱର ଲୋକ ସଭା ନିର୍ବାଚନ ମଣ୍ଡଳୀରୁ ନିର୍ବାଚନ ଲଢ଼ିଥିଲେ । ଏହି ନିର୍ବାଚନରେ ସେ ବିଜୟୀ ହୋଇ 7ମ ଲୋକ ସଭାକୁ ନିର୍ବାଚିତ ହୋଇଥିଲେ । ଏହି ଲୋକ ସଭାରେ ସେ 1980…

10. **ବାସୁଦେବ ମାଝି** (id 80957, para 7, `table`)
   - para 7 (table, 1,424 B): bpb 0.17, bottom 0.5% of table 1-3 kB (median 0.70); near-copy (word-pair overlap 89%) in 193 other articles; 73% of its word triples repeat within it; same header row in 220 articles
   - context: topic politics, Odisha
   > | ଆରମ୍ଭ | ଶେଷ | ପଦବୀ | ନିର୍ବାଚନ ମଣ୍ଡଳୀ | ଦଳ | ⏎ |---|---|---|---|---| ⏎ | 1974 | 1977 | ସଭ୍ୟ, 6ଷ୍ଠ ଓଡ଼ିଶା ବିଧାନ ସଭା | କୋଟପାଡ଼ | ଭାରତୀୟ ଜାତୀୟ କଂଗ୍ରେସ | ⏎ | 1977 | 1980 | ସଭ୍ୟ, 7ମ ଓଡ଼ିଶା ବିଧାନ ସଭା | କୋଟପାଡ଼ | ଭାରତୀୟ ଜାତୀୟ…

11. **ମୋହନ ଚୋଟି** (id 56180, para 6, `script`)
   - para 6 (list, 5,588 B): bpb 1.41, top 1% of list >=100 B (median 0.51); 100% Latin letters, 1% common English words
   - article: bpb 1.16, top 0.1% of articles >= 500 B (median 0.55); 70% of its bytes are in extreme paragraphs; odia\_ratio 0.14
   - context: machine-assisted translation (Content Translation, from en)
   - context: topic film
   > - 1994 Do Fantoosh ⏎ - 1994 Mere Data Garib Nawaz ⏎ - 1993 Badi Bahen ⏎ - 1993 Kala Coat ⏎ - 1993 Shuruaat ⏎ - 1992 Sarphira ⏎ - 1992 Naseebwaala ⏎ - 1990 Shandaar ⏎ - 1990 Doodh Ka Karz ⏎ - 1990 Muqaddar Ka Badshaah ⏎…

12. **"କ ଚମ୍ପୂ" - କି ହେଲାରେ କହିତ ନୁହଇ ଭାରତୀରେ** (id 98319, whole article, `article`)
   - article: bpb 1.37, top 0.1% of articles >= 500 B (median 0.55); 53% of its bytes are in extreme paragraphs
   - para 6 (text, 315 B): bpb 1.38, top 0.1% of text 300-999 B (median 0.53)
   - para 5 (text, 323 B): bpb 1.29, top 0.1% of text 300-999 B (median 0.53)
   > (para 6, bpb 1.38) କି ନୀତି କି ଜାତିଶୀଳ, କି କୁଳବରତ ଫଳ, ଠଉର ପାରିଲା ମୋ ମତିରେ, କୋମଳତର ମୋହନ, କୁଞ୍ଜକୁକ୍ଷିରୁ ନିଃସ୍ୱନ, ଆସି ଚୁମ୍ବିଦେଲା ମୋ ଶ୍ରୁତିରେ ॥ 4 ॥

13. **ଶ୍ରୀଦେବୀ** (id 63100, para 17, `garbled`)
   - para 17 (text, 1,197 B): bpb 1.07, top 0.01% of text 1-3 kB (median 0.49)
   - para 18 (text, 1,177 B): bpb 0.86, top 0.5% of text 1-3 kB (median 0.49)
   - para 14 (text, 1,515 B): bpb 0.77, top 1% of text 1-3 kB (median 0.49)
   - context: topic film
   > ସେ କମଲ୍ ହାସନଙ୍କ ସହ 27ଟି ଚଳଚ୍ଚିତ୍ରରେ ଅଭିନୟ କରିଥିଲେ, ଯଥା: ଓକା ରାଧା ଇଧ୍ଧାରୁ କ୍ରିଷ୍ଣୁଲୁ (1986), ଆଖରୀ ସଙ୍ଗମ୍ (1984)- ହିନ୍ଦୀ, ସଦମା (1983)- ହିନ୍ଦୀ, ଅନ୍ଧାଗାଡ୍ଡୁ (1982), ମୁନରମ୍ ପିରାଇ (1982), ବାଜଭେ ମାୟମ (1982), ଅକାଳି ରାଜ୍ୟମ୍ (198…

14. **ରୀତିକା ଖେରା** (id 74270, para 9, `english`)
   - para 9 (list, 3,016 B): bpb 0.85, 94th percentile of list >=100 B (median 0.51); 100% Latin letters, 20% common English words
   - para 7 (list, 575 B): bpb 1.35, top 5% of list 300-999 B (median 0.75); 100% Latin letters, 19% common English words
   - context: topic education
   > - Impact of Aadhaar on Welfare Programmes, Economic and Political Weekly, 16 December, Vol 52, No. 50. Special Article. ⏎ - Aadhaar and Food Security in Jharkhand, Pain Without Gain?, With Jean Drèze, Nazar Khalid and A…

15. **ହିନ୍ଦୀ ଉଇକିପିଡ଼ିଆ** (id 98790, para 7, `templated`)
   - para 7 (list, 3,498 B): bpb 0.22, bottom 1% of list >=100 B (median 0.51); 65% of its word triples repeat within it
   - article: bpb 0.33, bottom 0.5% of articles >= 500 B (median 0.55)
   - context: topic technology
   > - 11 ଜୁଲାଇ 2003 – ହିନ୍ଦୀ ଉଇକିପିଡ଼ିଆର ଶୁଭାରମ୍ଭ। ⏎ - 25 ଜାନୁଆରୀ 2005 – ହିନ୍ଦୀ ଉଇକିପିଡ଼ିଆରେ ଲେଖାର ସଂଖ୍ୟା 1,000 ପହଞ୍ଚିଲା। ⏎ - 16 ଜାନୁଆରୀ 2007 – ହିନ୍ଦୀ ଉଇକିପିଡ଼ିଆରେ ଲେଖାର ସଂଖ୍ୟା 5,000 ପହଞ୍ଚିଲା। ⏎ - 14 ମାର୍ଚ୍ଚ 2007 – ହିନ୍ଦୀ ଉ…

16. **2019 ଭାରତୀୟ ସାଧାରଣ ନିର୍ବାଚନ** (id 69377, para 7, `table`)
   - para 7 (table, 1,790 B): bpb 0.18, bottom 0.5% of table 1-3 kB (median 0.70); 55% of its word triples repeat within it
   - para 11 (text, 404 B): bpb 0.32, bottom 1% of text 300-999 B (median 0.53)
   - context: machine-assisted translation (Content Translation, from en)
   - context: topic politics
   > | ନିର୍ବାଚନ ପର୍ଯ୍ୟାୟ | ନିର୍ବାଚନ ତାରିଖ | ଲୋକ ସଭା ନିର୍ବାଚନ ମଣ୍ଡଳୀ | ⏎ |---|---|---| ⏎ | ପ୍ରଥମ | 11 ଅପ୍ରେଲ 2019 | କଳାହାଣ୍ଡି | ⏎ | ପ୍ରଥମ | 11 ଅପ୍ରେଲ 2019 | ନବରଙ୍ଗପୁର | ⏎ | ପ୍ରଥମ | 11 ଅପ୍ରେଲ 2019 | ବ୍ରହ୍ମପୁର | ⏎ | ପ୍ରଥମ | 11…

17. **ପେଣ୍ଟାଲ, ହାସ୍ୟ ଅଭିନେତା** (id 55987, para 4, `script`)
   - para 4 (list, 3,271 B): bpb 1.32, top 5% of list >=100 B (median 0.51); 100% Latin letters, 2% common English words
   - article: bpb 1.27, top 0.1% of articles >= 500 B (median 0.55); odia\_ratio 0.07
   - para 6 (list, 415 B): bpb 1.77, top 0.1% of list 300-999 B (median 0.75); 100% Latin letters, 13% common English words; high even for English (English paragraphs: median 1.02)
   - context: machine-assisted translation (Content Translation, from en)
   - context: topic film
   > - Kaalo (2010) ⏎ - Shri Chaitanya Mahaprabhu (2009) (Special Appearance) ⏎ - Paying Guest (2009) ⏎ - Fun 2shh: Dudes in the 10th Century (2003) ⏎ - Kasam (2001) ⏎ - Zulm-O-Sitam (1998) ⏎ - Hatyara (1998) ⏎ - Mere Sapno…

18. **ବିଜୁ ଜନତା ଦଳ** (id 19026, whole article, `article`)
   - article: bpb 0.28, bottom 0.1% of articles >= 500 B (median 0.55)
   - para 1 (text, 1,069 B): bpb 0.28, bottom 0.5% of text 1-3 kB (median 0.49)
   - context: topic politics
   > (para 1, bpb 0.28) ବିଜୁ ଜନତା ଦଳ (ବିଜେଡି) ହେଉଛି ଭାରତର ଏକ ଆଞ୍ଚଳିକ ରାଜନୈତିକ ଦଳ, ଯାହାର ଓଡ଼ିଶା ରାଜ୍ୟରେ ଯଥେଷ୍ଟ ପ୍ରଭାବ ରହିଛି। ପୂର୍ବତନ ମୁଖ୍ୟମନ୍ତ୍ରୀ ବିଜୁ ପଟ୍ଟନାୟକଙ୍କ ଆଦର୍ଶକୁ ବଜାୟ ରଖିବା, ଓଡ଼ିଆ ଜାତୀୟତାବାଦକୁ ପ୍ରୋତ୍ସାହନ ଦେବା ଏବଂ ରା…

19. **ସରହପା** (id 12785, para 6, `garbled`)
   - para 6 (text, 308 B): bpb 1.45, top 0.01% of text 300-999 B (median 0.53)
   - context: topic religion, Odisha
   > ତହି ବଟ ଚିତ୍ତ ବିସାମକରୁ ସରେହେ କହିଓ ଭୱସେ । ଘୋର ଅନ୍ଧାରେ ଚନ୍ଦ୍ରମଣି ଜିମି ଉତ୍ ଜୋଳା କରେଇ ପରମ ମହାସୁଖ ଏଣୁ କଣେ ହୁରିଓ ଅଶେଷ ହରେଇ ।"

20. **ହରମାନ କୁଲ୍କେ** (id 99867, para 9, `english`)
   - para 9 (list, 2,817 B): bpb 1.08, top 5% of list 1-3 kB (median 0.56); 100% Latin letters, 22% common English words
   - context: topic history
   > - Cidambaramahatmya (PhD, Wiesbaden 1970) ⏎ - The Cult of Jagannath and the Regional Tradition of Orissa (Delhi 1978, with A. Eschmann and G.C. Tripathi) ⏎ - The Devaraja Cult (Cornell University 1978) ⏎ - Jagannath Cul…

21. **ନବରଙ୍ଗପୁର (ଲୋକ ସଭା ନିର୍ବାଚନ ମଣ୍ଡଳୀ)** (id 11546, para 4, `templated`)
   - para 4 (list, 1,495 B): bpb 0.19, bottom 0.5% of list 1-3 kB (median 0.56); 69% of its word triples repeat within it
   - article: bpb 0.31, bottom 0.1% of articles >= 500 B (median 0.55); 67% of its bytes are in extreme paragraphs
   - context: topic politics, Odisha
   > - 2014: ବଳଭଦ୍ର ମାଝି (ବିଜୁ ଜନତା ଦଳ) ⏎ - 2019: ରମେଶ ଚନ୍ଦ୍ର ମାଝୀ (ବିଜୁ ଜନତା ଦଳ) ⏎ - 2014: ବଳଭଦ୍ର ମାଝୀ (ବିଜୁ ଜନତା ଦଳ) ⏎ - 2009: ପ୍ରଦୀପ କୁମାର ମାଝି (ଭାରତୀୟ ଜାତୀୟ କଂଗ୍ରେସ) ⏎ - 2004: ପର୍ଶୁରାମ ମାଝୀ (ଭାରତୀୟ ଜନତା ପାର୍ଟି) ⏎ - 1999:…

22. **ଭାରତୀୟ ମୁଦ୍ରା** (id 8638, para 8, `table`)
   - para 8 (table, 570 B): bpb 0.24, bottom 0.5% of table 300-999 B (median 0.76); 53% Latin letters, 15% common English words; 39% letters in other scripts
   - para 3 (list, 435 B): bpb 0.26, bottom 0.5% of list 300-999 B (median 0.75); 39% of its word triples repeat within it
   - context: topic economy
   > | ଭାଷା | 1 | 2 | 5 | 10 | 20 | 50 | 100 | 500 | 1000 | ⏎ |---|---|---|---|---|---|---|---|---|---| ⏎ | ଇଂରାଜୀ | One rupee | Two rupees | Five rupees | Ten rupees | Twenty rupees | Fifty rupees | Hundred rupees | Five hu…

23. **ପଞ୍ଜାବୀ ଭାଷା** (id 13376, para 21, `script`)
   - para 21 (text, 417 B): bpb 2.35, top 0.01% of text 300-999 B (median 0.53); 88% Latin letters, 2% common English words
   - para 20 (text, 478 B): bpb 1.57, top 0.01% of text 300-999 B (median 0.53); 100% letters in other scripts
   - para 18 (text, 715 B): bpb 0.45, 21th percentile of text 300-999 B (median 0.53); 100% letters in other scripts
   - context: topic literature
   > ଟ୍ରାନ୍ସ ଲିଟେରେସନ୍: lahaur pākistānī panjāb dī rājdā̀ni ài. lok giṇtī de nāḷ karācī tõ bāad lahaur dūjā sáb tõ vaḍḍā šáir ài. lahor pākistān dā siāsī, rátalī te paṛā̀ī dā gáṛ ài te is laī ínū̃ pākistān dā dil vī kihā jān…

24. **ବିଶ୍ୱ ସ୍ୱାସ୍ଥ୍ୟ ଦିବସ** (id 80742, whole article, `article`)
   - article: bpb 0.29, bottom 0.1% of articles >= 500 B (median 0.55)
   - para 1 (text, 1,028 B): bpb 0.29, bottom 0.5% of text 1-3 kB (median 0.49)
   - context: topic calendar
   > (para 1, bpb 0.29) ବିଶ୍ୱ ସ୍ୱାସ୍ଥ୍ୟ ସଂଗଠନ (WHO) ହେଉଛି ବିଶ୍ୱର ବିଭିନ୍ନ ସଦସ୍ୟ ଦେସ ମାନଙ୍କ ମଧ୍ୟରେ ସ୍ୱାସ୍ଥ୍ୟ ସମ୍ବନ୍ଧୀୟ ସମସ୍ୟା ଉପରେ ପାରସ୍ପରିକ ସହଯୋଗ ଏବଂ ମାନକ ବିକାଶ ପାଇଁ ଏକ ସଂଗଠନ । ବିଶ୍ୱ ସ୍ୱାସ୍ଥ୍ୟ ସଂଗଠନରେ 193ଟି ସଦସ୍ୟ ରାଷ୍ଟ୍ର ଏବଂ…

25. **ସାଲବେଗ** (id 11367, para 9, `garbled`)
   - para 9 (text, 310 B): bpb 1.43, top 0.01% of text 300-999 B (median 0.53)
   - para 10 (text, 341 B): bpb 1.21, top 0.5% of text 300-999 B (median 0.53)
   - para 11 (text, 359 B): bpb 1.14, top 0.5% of text 300-999 B (median 0.53)
   - context: topic literature, Odisha
   > ଆହେ ନୀଳ ଶଇଳ , ପ୍ରବଳ ମତ୍ତ ବାରଣ, ମୋ ଆରତ ନଲିନୀ ବନକୁ କର ଦଲନ । ଗଜରାଜ ଚିନ୍ତା କଲା ଥାଇ ଘୋର ଜଳେଣ, ଚକ୍ର ପେଶୀ ନକ୍ର ନାଶୀ , ଉଧାରିଲେ ଆପଣ ।

26. **କୁଳଭୂଷଣ ଖରବନ୍ଦା** (id 56128, para 11, `english`)
   - para 11 (list, 1,964 B): bpb 1.14, top 1% of list 1-3 kB (median 0.56); 99% Latin letters, 10% common English words
   - context: machine-assisted translation (Content Translation, from en)
   - context: topic film
   > - Azhar (film) (Hindi) ⏎ - Dictator (2016) (Telugu) ⏎ - Brothers (Hindi) (2015) ⏎ - Haider (Hindi) (2014) ⏎ - Kirpaan - The Sword of Honour (Punjabi) (2014) ⏎ - Saadi Love Story(Punjabi) (2013) ⏎ - Delhi in a Day (2012)…

27. **ଭାରତୀୟ ଜନଗଣନା** (id 82806, para 8, `templated`)
   - para 8 (list, 335 B): bpb 0.22, bottom 0.1% of list 300-999 B (median 0.75); 87% of its word triples repeat within it
   - para 5 (list, 335 B): bpb 0.22, bottom 0.1% of list 300-999 B (median 0.75); 87% of its word triples repeat within it
   - article: bpb 0.35, bottom 1% of articles >= 500 B (median 0.55)
   - context: machine-assisted translation (Content Translation, from en)
   > - 1951 ଭାରତର ଜନଗଣନା ⏎ - 1961 ଭାରତର ଜନଗଣନା ⏎ - 1971 ଭାରତର ଜନଗଣନା ⏎ - 1981 ଭାରତର ଜନଗଣନା ⏎ - 1991 ଭାରତର ଜନଗଣନା ⏎ - 2001 ଭାରତର ଜନଗଣନା ⏎ - 2011 ଭାରତର ଜନଗଣନା ⏎ - 2021 ଭାରତର ଜନଗଣନା

28. **ଆବିଦ୍ଜି ଭାଷା** (id 99543, para 5, `table`)
   - para 5 (table, 429 B): bpb 2.41, top 0.01% of table 300-999 B (median 0.76); 98% Latin letters, 2% common English words
   - article: bpb 1.07, top 0.5% of articles >= 500 B (median 0.55); odia\_ratio 0.36
   - para 28 (table, 3,976 B): bpb 1.75, top 1% of table >=200 B (median 0.80); 90% Latin letters, 3% common English words
   - context: topic literature
   > | Village Name | Native name (IPA) | ⏎ |---|---| ⏎ | Soukoukro | sukwebi | ⏎ | Badasso | gbadatɛ | ⏎ | Elibou | elibu | ⏎ | Sahuyé | sayjɛ | ⏎ | gomon | goma | ⏎ | Yaobou | jawebi; joabu; djabõ; nadja côtôcô; Amougbrous…

29. **ରାଇଜୋବିୟମ** (id 75761, para 8, `script`)
   - para 8 (list, 3,985 B): bpb 0.95, top 5% of list >=100 B (median 0.51); 91% Latin letters, 2% common English words; 41% of its word triples repeat within it
   - context: topic biology
   > - ରାଇଜୋବିୟମ ଏଜିପ୍ସିଆକମ ଶେମ୍ସେଲ୍ଡିନ ଏବଂ ଅନ୍ଯମାନେ. 2016 ⏎ - ରାଇଜୋବିୟମ ଆଗ୍ରେଗାଟମ (ହିର୍ସ୍ଚ ଓ ମୁଲର 1986) କୌର ଏବଂ ଅନ୍ଯମାନେ 2011 ⏎ - ରାଇଜୋବିୟମ ଆଲାମୀ ବର୍ଜ ଏବଂ ଅନ୍ଯମାନେ 2009 ⏎ - ରାଇଜୋବିୟମ ଆଲ୍ଟିପ୍ଲାନୀ ବରୌନା ଏବଂ ଅନ୍ଯମାନେ 2016 ⏎ -…

30. **ଦିଲ୍ଲୀପ କେ. ବିଶ୍ୱାସ** (id 95453, whole article, `article`)
   - article: bpb 0.29, bottom 0.1% of articles >= 500 B (median 0.55)
   - para 1 (text, 2,339 B): bpb 0.29, bottom 0.5% of text 1-3 kB (median 0.49)
   - context: topic biology
   > (para 1, bpb 0.29) ଦିଲ୍ଲୀପ କେ. ବିଶ୍ୱାସ ଜଣେ ଭାରତୀୟ ପରିବେଶବିତ୍ ଏବଂ କେନ୍ଦ୍ରୀୟ ପ୍ରଦୂଷଣ ନିୟନ୍ତ୍ରଣ ବୋର୍ଡ଼ ଓ ଦିଲ୍ଲୀ ପ୍ରଦୂଷଣ ନିୟନ୍ତ୍ରଣ କମିଟିର ପୂର୍ବତନ ଅଧ୍ୟକ୍ଷ । ସେ ସାଇଲେଣ୍ଟ ଭ୍ୟାଲିରେ ପରିବେଶ ଅଧ୍ୟୟନ କରିଥିବା ପ୍ୟାନେଲର ସଦସ୍ୟ ଥିଲେ ଏବଂ…

31. **ଆତସବାଜି** (id 21611, para 7, `garbled`)
   - para 7 (text, 373 B): bpb 1.41, top 0.1% of text 300-999 B (median 0.53)
   - context: topic technology
   > କେତେକ ବାଣର ନାମ ଚଂପା, କୁଂପୀ, ତୁଂବ, ହାବିଳି, ଚକ୍ର ଆକାଶ ମଲ୍ଲୀ, ଆସମାନଗୋଲା, ଚେଂଗ, କାଠଚଂପା, ଚଂଦ୍ରଉଦିଆ ବା ମହତାପ, ପାଣିକୁଆ, ବଂବାଜୀ, ତୋପବାଜୀ, କଦଂବଗଛ, ରସକଦଂବ ।

32. **କଦଳୀଗଛ** (id 75809, para 12, `english`)
   - para 12 (list, 1,103 B): bpb 1.36, top 0.01% of list 1-3 kB (median 0.56); 92% Latin letters, 12% common English words
   - context: topic biology
   > - ମୁ. × ଅଲିନ୍ସନାୟା ର.ଭ.ଭାଲ୍ମେୟର [ଅ] ⏎ - ମୁ. ଅଜିଜୀ ହାକିନେନ ⏎ - ମୁ. ବାରିଓନେନ୍ସିସ Häkkinen ⏎ - M. bauensis Häkkinen & Meekiong [C] ⏎ - M. beccarii N.W.Simmonds [A] ⏎ - M. boman Argent [A] ⏎ - M. borneensis Becc. [C] ⏎ - M.…

33. **ଶ୍ରଦ୍ଧା ଜାଧବ** (id 99679, para 11, `templated`)
   - para 11 (list, 1,361 B): bpb 0.21, bottom 0.5% of list 1-3 kB (median 0.56); 44% of its word triples repeat within it
   - context: topic politics
   > - 1992: ବୃହନ୍ମୁମ୍ବାଇ ମୁନିସିପାଲ କର୍ପୋରେସନରେ କର୍ପୋରେଟର (ପ୍ରଥମ ମୟାଦ) ⏎ - 1997: ବୃହନ୍ମୁମ୍ବାଇ ମୁନିସିପାଲ କର୍ପୋରେସନରେ ପୁନର୍ନିର୍ବାଚିତ କର୍ପୋରେଟର (ଦ୍ୱିତୀୟ ମିୟାଦ) ⏎ - 2002: ବୃହନ୍ମୁମ୍ବାଇ ମୁନିସିପାଲ କର୍ପୋରେସନରେ ପୁନର୍ନିର୍ବାଚିତ କର୍ପୋରେ…

34. **ଭାରତୀୟ ଜାତୀୟ କ୍ରିକେଟ ଦଳ** (id 88702, para 50, `table`)
   - para 50 (table, 3,059 B): bpb 0.30, bottom 0.5% of table >=200 B (median 0.80); 74% of its word triples repeat within it
   - para 78 (text, 1,947 B): bpb 0.26, bottom 0.1% of text 1-3 kB (median 0.49)
   - para 76 (text, 3,164 B): bpb 0.31, bottom 0.1% of text >=3 kB (median 0.50)
   - context: topic sports
   > | ଟୁର୍ଣ୍ଣାମେଣ୍ଟ | କିଟ୍ ନିର୍ମାତା | ସ୍ଲିଭ୍ ପ୍ରାୟୋଜକ | ⏎ |---|---|---| ⏎ | 1975 କ୍ରିକେଟ ବିଶ୍ୱକପ |  |  | ⏎ | 1979 କ୍ରିକେଟ ବିଶ୍ୱକପ |  |  | ⏎ | 1983 କ୍ରିକେଟ ବିଶ୍ୱକପ |  |  | ⏎ | 1987 କ୍ରିକେଟ ବିଶ୍ୱକପ୍ |  |  | ⏎ | 1992 କ୍ରିକେଟ ବ…

35. **ଓଡ଼ିଆ ମସଲାର ଇଂରାଜୀ ପ୍ରତିଶବ୍ଦ** (id 43428, para 2, `script`)
   - para 2 (text, 1,194 B): bpb 0.97, top 0.1% of text 1-3 kB (median 0.49); 61% Latin letters, 3% common English words; verse or song lines
   - article: bpb 0.93, top 1% of articles >= 500 B (median 0.55); odia\_ratio 0.44
   - context: topic arts, Odisha
   > ଅଦା - Ginger ⏎ ଚାରୁ ମଞ୍ଜି -Chironji ⏎ ଆମ୍ବ ଅଦା - Mango ginger ⏎ ସୋରିଷ - Mustard ⏎ ପୋସ୍ତୋ - Poppy seeds ⏎ ପାନମଧୂରୀ - Fennel ⏎ ଜିରା - Cumin ⏎ ଲଙ୍କା - Chilli ⏎ ମେଥୀ - fenugreek ⏎ କଳାଜିରା - Nigella ⏎ ଖଜୁରି -Dates ⏎ ବାଦାମ -…

36. **କର୍ଣ୍ଣାଟକ** (id 3008, whole article, `article`)
   - article: bpb 0.30, bottom 0.1% of articles >= 500 B (median 0.55); 92% of its bytes are in extreme paragraphs
   - para 1 (text, 1,316 B): bpb 0.25, bottom 0.1% of text 1-3 kB (median 0.49)
   - context: topic geography
   > (para 1, bpb 0.25) କର୍ଣ୍ଣାଟକ (କନ୍ନଡ: ಕರ್ನಾಟಕ) ଦକ୍ଷିଣ-ପଶ୍ଚିମ ଭାରତର ଏକ ରାଜ୍ୟ । 1956 ମସିହା ନଭେମ୍ବର 1 ତାରିଖରେ ରାଜ୍ୟ ପୁନର୍ଗଠନ ଆଇନ ବଳରେ ଏହି ରାଜ୍ୟ ସ୍ଥାପିତ ହୋଇଥିଲା । ଏହାର ପୂର୍ବ ନାମ ମହୀଶୂର ଥିଲା । 1973 ମସିହାରେ ଏହାର ନୂତନ ନାମକରଣ ହ…

37. **ତାନିଆ ଅହମଦ** (id 62459, para 3, `garbled`)
   - para 3 (text, 2,893 B): bpb 1.01, top 0.1% of text 1-3 kB (median 0.49)
   - article: bpb 0.93, top 1% of articles >= 500 B (median 0.55); 59% of its bytes are in extreme paragraphs
   - para 13 (list, 2,071 B): bpb 1.21, top 0.5% of list 1-3 kB (median 0.56)
   - context: topic film
   > ତାନିଆ 1991ରେ ଜଣେ ମଡେଲ ଭାବରେ ତାଙ୍କର ପେଷା ଆରମ୍ଭ କରିଥିଲେ । ସେ ସମ୍ପର୍କ ନାମକ ଏକ ଦୂରଦର୍ଶନ ଧାରାବାହିକ ଜରିଆରେ ଅଭିନୟ ଦୁନିଆରେ ପ୍ରବେଶ କରିଥିଲେ । ଫାରିଆ ହୋସାଇନ ନାମକ ଏକ ନାଟକର ନିର୍ଦ୍ଦେଶନାରୁ ସେ ତାଙ୍କର ନିର୍ଦ୍ଦେଶନା ପେଷା ମଧ୍ୟ ଆରମ୍ଭ କରିଥିଲେ…

38. **କଳ୍ପନା ଚାୱଲା** (id 18747, para 28, `english`)
   - para 28 (list, 4,596 B): bpb 0.66, 85th percentile of list >=100 B (median 0.51); 75% Latin letters, 34% common English words
   - context: topic science
   > - Asteroid 51826 Kalpanachawla, କଲମ୍ବିଆରେ ଯାତ୍ରା କରିଥିବା ମହାକାଶଚାରୀ ଦଳରେ କଳ୍ପନାଙ୍କ ନାମ । ⏎ - 2003 ମସିହା ଫେବୃୟାରୀ ମାସ 5 ତାରିଖରେ ଭାରତର ପ୍ରଧାନ ମନ୍ତ୍ରୀ ଘୋଷଣା କରିଥିଲେ ଯେ ପାଣିପାଗ ସୂଚନା ଦେଉଥିବା କୃତ୍ରିମ ଉପଗ୍ରହ ଶୃଙ୍ଖଳା MetSatର ପ…

39. **ବଲାଙ୍ଗୀର (ଲୋକ ସଭା ନିର୍ବାଚନ ମଣ୍ଡଳୀ)** (id 11542, para 4, `templated`)
   - para 4 (list, 823 B): bpb 0.26, bottom 0.5% of list 300-999 B (median 0.75); 58% of its word triples repeat within it
   - 61% of its bytes are in extreme paragraphs
   - context: topic politics, Odisha
   > - 2024: ସଙ୍ଗୀତା କୁମାରୀ ସିଂହ ଦେଓ (ଭାରତୀୟ ଜନତା ପାର୍ଟି) ⏎ - 2019: ସଙ୍ଗୀତା କୁମାରୀ ସିଂହଦେଓ (ଭାରତୀୟ ଜନତା ପାର୍ଟି) ⏎ - 2014: କଳିକେଶ ନାରାୟଣ ସିଂହଦେଓ (ବିଜୁ ଜନତା ଦଳ) ⏎ - 2009: କଳିକେଶ ନାରାୟଣ ସିଂଦେଓ (ବିଜୁ ଜନତା ଦଳ) ⏎ - 2004: ସଙ୍ଗୀତା କ…

40. **ଓଡ଼ିଶା ବିଧାନ ସଭାର ବାଚସ୍ପତିଙ୍କ ତାଲିକା** (id 87748, para 3, `table`)
   - para 3 (table, 4,028 B): bpb 0.31, bottom 1% of table >=200 B (median 0.80); 66% of its word triples repeat within it
   - article: bpb 0.32, bottom 0.5% of articles >= 500 B (median 0.55); 95% of its bytes are in extreme paragraphs
   - para 1 (text, 600 B): bpb 0.32, bottom 1% of text 300-999 B (median 0.53)
   - context: topic politics, Odisha
   > | ନାମ | ବିଧାନ ସଭା | ଆରମ୍ଭ ଦିନ | ଶେଷ ଦିନ | ⏎ |---|---|---|---| ⏎ | ମୁକୁନ୍ଦ ପ୍ରସାଦ ଦାସ | 1ମ ଓଡ଼ିଶା ବିଧାନ ସଭା (ସ୍ୱାଧୀନତା ପୂର୍ବରୁ) | 28 ଜୁଲାଇ 1937 | 29 ମଇ 1946 | ⏎ | ଲାଲ ମୋହନ ପଟ୍ଟନାୟକ | 2ୟ ଓଡ଼ିଶା ବିଧାନ ସଭା (ସ୍ୱାଧୀନତା ପୂର୍ବର…

41. **ସାତକର୍ଣ୍ଣୀ-2ୟ** (id 101018, para 5, `script`)
   - para 5 (text, 507 B): bpb 1.36, top 0.1% of text 300-999 B (median 0.53); 28% letters in other scripts; verse or song lines
   - context: machine-assisted translation (Content Translation, from en)
   > 𑀭𑀸𑀜𑁄 𑀲𑀺𑀭𑀺 𑀲𑀸𑀢𑀓𑀡𑀺𑀲 (ରାଞୋ ସିରି ସାତକଣିସ) ⏎ 𑀆𑀯𑁂𑀲𑀡𑀺𑀲 𑀯𑀸𑀲𑀺𑀣𑀻𑀧𑀼𑀢𑀲 (ଆବେସଣିସ ବାସିଥୀପୁତସ) ⏎ 𑀆𑀦𑀁𑀤𑀲 𑀤𑀸𑀦𑀁 (ଆନଂଦସ ଦାନଂ) ⏎ "ରାଜନ ସିରି ସାତକର୍ଣ୍ଣୀଙ୍କ କାରିଗରମାନଙ୍କ ମୁଖିଆ, ବାସିଥୀଙ୍କ ପୁତ୍ର ଆନନ୍ଦଙ୍କ ଦାନ"

42. **ମନିରା ମିଠୁ** (id 98098, whole article, `article`)
   - article: bpb 1.17, top 0.1% of articles >= 500 B (median 0.55); 55% of its bytes are in extreme paragraphs
   - para 4 (list, 1,214 B): bpb 1.21, top 0.5% of list 1-3 kB (median 0.56)
   - context: topic film
   > (para 4, bpb 1.21) - ଅପେଣ୍ଟି ବାଇସ୍କୋପ୍ (2001) ⏎ - ନିଲ୍ ତୋୱାଲେ ⏎ - ଏମୋନ ଦେଶଟି କୋଥାଓ କୁଜେୟ ପାବେ ନାକୋ ତୁମି ⏎ - ସ୍ପାର୍ଟାକସ୍ ଏକୋତ୍ତର ⏎ - ହାଉସ୍ ଫୁଲ୍ ⏎ - ବିକୋଲ୍ ପାଖିର୍ ଗାଁ ⏎ - ବୁଆ ବିଲାଶ ⏎ - କମିଂ ସୁନ୍ ⏎ - ପୁତୁଲ୍ ଖେଲା ⏎ - ଚାନ୍ଦ…

43. **ସତ୍ୟମେବ ଜୟତେ** (id 13878, para 2, `garbled`)
   - para 2 (text, 301 B): bpb 1.41, top 0.1% of text 300-999 B (median 0.53)
   > ସତ୍ୟମେବ ଜଯତେ ନାନୃତମ୍ ସତ୍ୟନ ପନ୍ଥାବ ବିତତୋବଦେବଯାନଃ । ⏎ ଯେନାକ୍ରମନ୍ୟୁତ୍ୟଷଯୋ ହ୍ୟାତ୍ମକାମୋ ଯତ୍ର ତତ୍ତ୍ୟସ୍ୟ ପରମଂ ନିଧାନଂ ॥

44. **ଖୁସୱନ୍ତ ସିଂହ** (id 72168, para 27, `english`)
   - para 27 (list, 1,680 B): bpb 1.02, top 5% of list 1-3 kB (median 0.56); 100% Latin letters, 39% common English words
   - para 29 (list, 172 B): bpb 1.62, top 5% of list 100-299 B (median 0.97); 74% Latin letters, 44% common English words; 26% letters in other scripts
   - context: machine-assisted translation (Content Translation, from en)
   - context: topic literature
   > - The Mark of Vishnu and Other Stories, (Short Story) 1950 ⏎ - The History of Sikhs, 1953 ⏎ - Train to Pakistan, (Novel) 1956 ⏎ - The Voice of God and Other Stories, (Short Story) 1957 ⏎ - I Shall Not Hear the Nightinga…

45. **ଲୋକନାଥ ମିଶ୍ର (ରାଜନେତା, 1967 ମୃତ୍ୟୁ)** (id 83827, para 1, `templated`)
   - para 1 (text, 1,314 B): bpb 0.26, bottom 0.1% of text 1-3 kB (median 0.49); near-copy (word-pair overlap 50%) in 1 other article
   - para 5 (text, 686 B): bpb 0.32, bottom 1% of text 300-999 B (median 0.53); near-copy (word-pair overlap 62%) in 12 other articles
   - 74% of its bytes are in extreme paragraphs
   - context: topic politics, Odisha
   > ଲୋକନାଥ ମିଶ୍ର (4 ଅପ୍ରେଲ 1909 - 17 ଜୁଲାଇ 1967) ଜଣେ ଓଡ଼ିଆ ରାଜନୀତିଜ୍ଞ ଥିଲେ । ସେ ଓଡ଼ିଶା ବିଧାନ ସଭାରେ ଜଣେ ବିଧାୟକ ଭାବରେ ସ୍ୱାଧୀନତା ପୂର୍ବରୁ ଓ ପରେ ଚାରି ଥର କାର୍ଯ୍ୟ କରିଥିଲେ । ସ୍ୱାଧୀନତା ପୂର୍ବରୁ 1936 ମସିହାରେ ହୋଇଥିବା ପ୍ରଥମ ଓଡ଼ିଶା ବିଧାନ…

46. **କାସ ଅଧିତ୍ୟକା** (id 28613, para 9, `table`)
   - para 9 (table, 1,628 B): bpb 1.49, top 0.5% of table 1-3 kB (median 0.70); 96% Latin letters, 0% common English words
   - article: bpb 0.96, top 1% of articles >= 500 B (median 0.55); odia\_ratio 0.38
   - context: topic geography
   > | ବୈଜ୍ଞାନିକ ନାମ | ମରାଠୀ ନାମ | ⏎ |---|---| ⏎ | Ceropegia Vincaefolia | କାଣ୍ଟିଲପୁଷ୍ପ | ⏎ | Ceropegia Jainii | ସୋମଡ଼ା | ⏎ | Drosera Indica | Gavati Davbindu | ⏎ | Smithia hirsute / hirsuta | Kavala | ⏎ | Senecio grahami /…

47. **ଡାଇନୋସର ଶ୍ରେଣୀବିଭାଗ** (id 32047, para 10, `script`)
   - para 10 (list, 5,422 B): bpb 0.60, 77th percentile of list >=100 B (median 0.51); 100% Latin letters, 3% common English words
   - para 1 (text, 1,101 B): bpb 0.76, top 1% of text 1-3 kB (median 0.49)
   - para 13 (list, 2,293 B): bpb 0.53, 44th percentile of list 1-3 kB (median 0.56); 100% Latin letters, 0% common English words
   - context: topic biology
   > - Herrerasauria (Herrerasaurus > Liliensternus, Plateosaurus) ⏎   - Herrerasauridae (Herrerasaurus + Staurikosaurus) ⏎ - ? Eoraptor lunensis ⏎ - Sauropodomorpha (Saltasaurus > Theropoda) ⏎   - ? Saturnalia tupiniquim ⏎…

48. **ଆପଲ ଇନକର୍ପୋରେଟେଡ** (id 7032, whole article, `article`)
   - article: bpb 0.31, bottom 0.1% of articles >= 500 B (median 0.55)
   - para 1 (text, 1,632 B): bpb 0.31, bottom 1% of text 1-3 kB (median 0.49)
   - context: topic technology
   > (para 1, bpb 0.31) ଆପଲ ଇନକର୍ପୋରେଟେଡ ଏକ ଆମେରିକୀୟ ବହୁରାଷ୍ଟ୍ରୀୟ ଟେକ୍ନୋଲୋଜି କମ୍ପାନୀ ଓ ଏହାର ମୁଖ୍ୟାଳୟ କାଲିଫର୍ଣ୍ଣିଆର କୁପରଟିନୋରେ ରହିଛି । ମାର୍ଚ୍ଚ 2023 ସୁଦ୍ଧା, ଆପଲ ବଜାର ପୁଞ୍ଜି ଦୃଷ୍ଟିରୁ ବିଶ୍ୱର ସର୍ବବୃହତ କମ୍ପାନୀ, ଏବଂ 2022 ରାଜସ୍ୱ ସୁ…

49. **ଚନ୍ଦନ ତିୱାରୀ** (id 72805, para 8, `garbled`)
   - para 8 (text, 884 B): bpb 1.33, top 0.1% of text 300-999 B (median 0.53)
   - para 5 (text, 1,236 B): bpb 0.76, top 1% of text 1-3 kB (median 0.49)
   - context: topic arts
   > ପୁର୍ବଇୟା ଉସ୍ତାଦ, ବେଟି ଚରୟିଆ ସମାନ, ରାଧା ରସିୟା, ସବକେ (ରାମ ରସୁଲ, ବସନ୍ତି ବାୟେର, ଶିବ ଜୋଗିଆ, ସଝି ରାଗ, ସ‌ୱାନି ବାହାର, ନିର୍ଗୁନିଆ କବୀର, ମହାତ୍ମା ଗାନ୍ଧୀ, ନ‌ଦୀଆ ଧୀରେ ବ‌ହୋ, ଭଏସ ଅଫ ଗ୍ୟାଞ୍ଜେସ, ମାଇ, ଛଟ୍ଟି ମଇୟା, ରଙ୍କ କଳସ, ଚରଖ‌ୱା ଚାଲୁ ରହେ…

50. **ଜେ. ବି. ଏସ. ହାଲଡେନ** (id 24214, para 8, `english`)
   - para 8 (list, 1,327 B): bpb 1.03, top 5% of list 1-3 kB (median 0.56); 100% Latin letters, 28% common English words
   - para 6 (list, 1,677 B): bpb 0.71, 78th percentile of list 1-3 kB (median 0.56); 100% Latin letters, 46% common English words
   - context: topic science
   > - A Mathematical Theory of Natural and Artificial Selection, a series of papers beginning in 1924 ⏎ - Callinicus: A Defence of Chemical Warfare (1925), E. P. Dutton ⏎ - Animal Biology (1929) Oxford: Clarendon ⏎ - The In…

