# Sarvam-1 bits per byte on the Odia Wikipedia corpus

Every paragraph of `orwiki-20260901-trainingready.jsonl` (sha1 `634b1411a0d1`), except the title, scored with `sarvamai/sarvam-1` (revision `e9607337286d`) by `score_bpb.py`, except 319 paragraphs (319 distinct texts, 366,751 B, in 233 articles) not scored yet (see [Not scored yet](#not-scored-yet)). Written by `score_bpb.py build`; the per-article and per-paragraph numbers are in `annotations/bpb.jsonl` and `annotations/bpb.paragraphs.jsonl` (each paragraph's bits, bytes, tokens, pieces and scoring run are in the score store `raw/bpb/scores.jsonl.gz`, joined on `para_sha1`), and the review queue is in `quality/review-first.md`.

## Summary

- **Not scored yet: 319 paragraphs (319 distinct texts, 366,751 B, in 233 articles).** Their text is new since the last scoring run, and this build was made with `--allow-missing`: they have `bpb` null, are left out of every number below and of the review queue, and need a pod run of about 57,961 tokens (see [Not scored yet](#not-scored-yet)).
- **Corpus bpb 0.5528** over 143,038 paragraphs, 85.9 MB and 13.13M Sarvam-1 tokens (152.9 tokens per kB). Prose alone (kind `text`): **0.5206**. For comparison, base Sarvam-1 scores 0.4951 on the native-Odia held-out set of odia-llm-trainer (E03/E06).
- The median article (>= 500 B, 18,095 articles) has bpb 0.550; the middle 98% run from 0.370 to 0.913.
- Matched for length it is the held-out number: prose paragraphs of 1 kB and more score **0.4942**. Shorter paragraphs cost more because each is scored without the text before it.
- Checks pass: word-shuffled paragraphs score higher in 300/300 cases; the repo harness gives the same pooled bpb to 0.04%; one paragraph's bpb is good to about 0.3% (median; bf16), 2% at p95; every paragraph has a row. Run 2's re-scores of 200 carried-over texts agree with them (median 0.0%, max 2.2%). Run 3's re-scores of 200 carried-over texts agree with them (median 0.0%, max 1.8%).
- 1,874 paragraphs are extreme (top or bottom 1% of their kind and length band, among 93,634 comparable ones). 200 articles are queued for review (`quality/review-first.md`), 34 garbled, 34 english, 34 templated, 31 table, 0 markup, 34 script, 33 article, interleaved.
- What bpb finds at the top: English inside Odia articles (295 comparable paragraphs), other scripts or non-English Latin text (290), and unusual Odia (verse, archaic text, name lists; genuinely garbled Odia is rare). At the bottom: formulaic biographies, year lists and a career table repeated in 220 articles. What it does not find: boilerplate repeated across articles (use `repeats`). Conversion leftovers: 0 paragraphs.
- Machine-assisted translations score about the same as the rest (pooled 0.549 vs 0.553). Pages created after Sarvam-1's release score lower (median 0.518 vs 0.556), so there is no sign that it memorised Odia Wikipedia.

## Not scored yet

319 paragraphs (319 distinct texts, 366,751 B, in 233 articles) of the current corpus have no score in `raw/bpb/scores.jsonl.gz`: their text is new since the last scoring run (the list is below). This build was made with `--allow-missing`, so:

- they have a row in `annotations/bpb.paragraphs.jsonl` with `bpb` null and no `bpb_group`, `bpb_pct`, `bpb_z` or `flag` (the text-only columns, such as `latin_share` and `repeats`, are there);
- their article's bpb, bytes, tokens and bits are over its scored paragraphs only, and `unscored_paragraphs` in `annotations/bpb.jsonl` counts them;
- they are not in the review queue, and every number in this report leaves them out;
- `unscored` in `annotations/bpb.json` has these counts and the commands below.

Scoring them needs a GPU pod: about 57,961 tokens (17 Latin-script texts at 2.3 B/token, the rest at 6.7), about 4 s of GPU time on an RTX A6000, plus the pod's start-up (minutes). Follow the steps in the docstring of `score_bpb.py` (state the hourly price before creating the pod, terminate it when done). The pod command, after copying `score_bpb.py`, the corpus and the store to `/root`:

```
cd /root && setsid nohup python score_bpb.py score --corpus orwiki-20260901-trainingready.jsonl --work /root/bpb --only-missing scores.jsonl.gz < /dev/null > score.log 2>&1 &
```

Then, on the laptop, with the run's files pulled into `raw/bpb/<date>/`:

```
uv run --script score_bpb.py build --add raw/bpb/<date>
```

| article (id/para) | kind | B | excerpt |
| --- | --- | ---: | --- |
| ଜଗନ୍ନାଥ (2089/22) | heading | 104 | ## ତାନ୍ତ୍ରିକ ଦୃଷ୍ଟିଭଙ୍ଗୀରେ ଶ୍ରୀଜଗନ୍ନାଥ |
| ଜଗନ୍ନାଥ (2089/23) | text | 1,389 | ଶ୍ରୀ ଜଗନ୍ନାଥ ସଂସ୍କୃତିରେ ଅନ୍ୟ ବହୁ ମତବାଦ ପରି ତନ୍ତ୍ରର ମଧ୍ୟ ବିପୁଳ ପ୍ରଭାବ ପଡିଛି । ଐତିହାସିକ ଦୃଷ… |
| ଜଗନ୍ନାଥ (2089/30) | heading | 47 | ### ଗଜପତିଙ୍କ ଭୂମିକା |
| ଜଗନ୍ନାଥ (2089/31) | text | 330 | ଗଜପତି ରାଜାଙ୍କ ମୁଖ୍ୟ ଭୂମିକା ହେଉଛି ମହାରାଜ ଛେରା ପହଁରା କରନ୍ତି ଯେତେବେଳେ ମହାପ୍ରଭୁ ତାଙ୍କ ରଥକୁ ଆସ… |
| ଆସାମ (3003/1) | text | 1,801 | ଆସାମ ପୂର୍ବ ହିମାଳୟର ଦକ୍ଷିଣରେ ବ୍ରହ୍ମପୁତ୍ର ଏବଂ ବରାକ ନଦୀ ଉପତ୍ୟକାରେ ଅବସ୍ଥିତ ଏକ ଉତ୍ତରପୂର୍ବ ଭାରତ… |
| ମୁମ୍ବାଇ (3074/14) | text | 1,891 | ଗ୍ରୀଷ୍ମମଣ୍ଡଳୀୟ ଅଞ୍ଚଳରେ ଆରବ ସାଗର ନିକଟରେ ଥିବାରୁ ମୁମ୍ବାଇର ଜଳବାୟୁରେ ମୁଖ୍ୟତଃ ଦୁଇ ପ୍ରକାର ଋତୁ ଦେ… |
| ମୁମ୍ବାଇ (3074/15) | text | 879 | ମୁମ୍ବାଇର ସର୍ବାଧିକ ତାପମାତ୍ରା $38^0$ସେ. ($100^0$ଫା.)ରୁ ସର୍ବନିମ୍ନ $11^0$ସେ. ($52^0$ଫା.) ମଧ୍ୟ… |
| ଭାରତର ରାଜ୍ୟ ଓ କେନ୍ଦ୍ରଶାସିତ ଅଞ୍ଚଳ (3192/5) | table | 3,749 | \| ରାଜ୍ୟ \| ରାଜଧାନୀ \| ସ୍ଥାପନା ବର୍ଷ \| ଆୟତନ (କି.ମି.$^2$) \| ଜନସଂଖ୍ୟା (2011) \| ସାକ୍ଷରତା ହାର \| ମ… |
| ଭାରତର ରାଜ୍ୟ ଓ କେନ୍ଦ୍ରଶାସିତ ଅଞ୍ଚଳ (3192/8) | table | 1,042 | \| କେନ୍ଦ୍ରଶାସିତ ଅଞ୍ଚଳ \| ରାଜଧାନୀ \| ଆୟତନ (କି.ମି.$^2$) \| ଜନସଂଖ୍ୟା (ଅନୁମାନିତ) \| ସାକ୍ଷରତା ହାର \|… |
| ବାଲେଶ୍ୱର ଜିଲ୍ଲା (7083/10) | text | 681 | ଓଡ଼ିଶାର ପୂର୍ବରେ ବାଲେଶ୍ୱର ଅବସ୍ଥିତ । ଭାରତର ଦକ୍ଷିଣ ପୂର୍ବ ଉପକୂଳରେ ବଙ୍ଗୋପସାଗର କଡ଼ରେ ଏହା ଅଛି ।… |
| ଆମ୍ବ (8594/12) | text | 1,561 | ଆମ୍ବରେ ପ୍ରଚୁର ପରିମାଣରେ ଖାଦ୍ୟସାର ଓ ଜୀବନିକା ରହିଛି । ଭିଟାମିନ A, C ଓ E ଛଡ଼ା ଭିଟାମିନ B$_6$ ତଥା… |
| ଆମ୍ବ (8594/14) | list | 853 | 1. କାର୍ବୋହାଇଡ୍ରେଟ - 17 ଗ୍ରାମ ⏎ 2. ଶର୍କରା - 14.8 ଗ୍ରାମ ⏎ 3. ସ୍ନେହସାର - 0.27 ଗ୍ରାମ ⏎ 4. ତନ୍… |
| ଆମ୍ବ (8594/18) | text | 4,265 | ଏହା ସାଧାରଣତଃ ଖରାଦିନେ ପାଚେ । ଆମ୍ବର ସ୍ୱତନ୍ତ୍ର ସ୍ୱାଦ, ଗନ୍ଧ ଓ ବହୁଳମାତ୍ରାରେ ଉତ୍ପାଦନ ପାଇଁ ଏହାକୁ… |
| ଓଡ଼ିଶାର ଜିଲ୍ଲାମାନଙ୍କର ତାଲିକା (10945/5) | table | 3,324 | \| ଜିଲ୍ଲା \| ସଦର ମହକୁମା \| ଆୟତନ (କି.ମି.$^2$) \| ଜନସଂଖ୍ୟା \| ସାକ୍ଷରତା ହାର \| ବ୍ଲକ ସଂଖ୍ୟା \| ଗ୍ରାମ… |
| 0 (ସଂଖ୍ୟା) (12883/1) | text | 138 | ବାସ୍ତବ ସଂଖ୍ୟା ସମୂହରେ ଶୁନ୍ୟ (0) ହେଉଛି ଏକ ଯୁଗ୍ମ ସଂଖ୍ୟା । |
| ନେପଚୁନ (15472/7) | text | 74 | $7.6183 \times 10^{9}$ କିମି, ପୃଥିବୀର 214.98ଗୁଣ |
| ନେପଚୁନ (15472/9) | text | 74 | $6.254 \times 10^{10}$କିମି, ପୃଥୀବିର 357.74 ଗୁଣ |
| ନେପଚୁନ (15472/11) | text | 64 | $1.0243 \times 10^{26}$kg 17.147 Earths$5.15 \times 10^{-5}$Suns |
| ୟୁକ୍ରେନ (16222/1) | text | 322 | ୟୁକ୍ରେନ୍ (ରୋମାନ୍ ଉଚ୍ଚାରଣ: [Ukrayina]) ପୂର୍ବ ଇଉରୋପରେ ଥିବା ଏକ ଦେଶ ଅଟେ । ଏହାର କ୍ଷେତ୍ରଫଳ 603,… |
| ଜଳ (16327/7) | text | 240 | ଗୋଟିଏ ଜଳ ଅଣୁ 2ଟି ଉଦଜାନ ପରମାଣୁ ଓ ଗୋଟିଏ ଅମ୍ଳଜାନ ପରମାଣୁରୁ ତିଆରି । ଏହାର ରାସାୟନିକ ସୂତ୍ର ହେଉଛି… |
| ଓଡ଼ିଶା ଇଞ୍ଜିନିୟରିଂ କଲେଜ (17053/3) | text | 459 | ଓଡ଼ିଶା ଇଞ୍ଜିନିୟରିଂ କଲେଜ (ଓ.ଇ.ସି) ଭୁବନେଶ୍ୱରରୁ ପ୍ରାୟ 20 କି.ମି. ଦୂରରେ ଅବସ୍ଥିତ। ପ୍ରାୟ 58 ଏକର… |
| ସୂର୍ଯ୍ୟ (17809/1) | text | 1,013 | ସୂର୍ଯ୍ୟ ହେଉଛି ଏକ ନକ୍ଷତ୍ର, ଏହା ସୌରଜଗତର ମଧ୍ୟଭାଗରେ ଅବସ୍ଥିତ । ଏହା ପାଖା ପାଖି ଗୋଲକ ଆକାରର ଓ ଏକାଧ… |
| ଶକ୍ତି (18032/14) | text | 707 | ଏବେ ଜଣା ପଡ଼ିଲାଣି ଯେ ଆଣବିକ ବିଖଣ୍ଡନ ଓ ସମ୍ମିଶ୍ରଣଦ୍ୱାରା ବସ୍ତୁକୁ ଶକ୍ତିରେ ଏବଂ ଶକ୍ତିକୁ ବସ୍ତୁରେ ପ… |
| ଶକ୍ତି (18032/22) | text | 407 | ଶକ୍ତିର SI ଏକକ ହେଉଛି ଜୁଲ୍ । ଏହା ଜେମ୍‌ସ୍ ପ୍ରେସ୍କଟ୍ ଜୁଲ୍‌ଙ୍କ ନାମରେ ନାମିତ ହୋଇଛି ।1 ଜୁଲ୍ ଏକ ନ୍… |
| ଭାରତର ପ୍ରଧାନମନ୍ତ୍ରୀମାନଙ୍କର ତାଲିକା (18050/2) | table | 4,054 | \| ସଂ \| ନାମ \| କାର୍ଯ୍ୟଗ୍ରହଣ \| କାର୍ଯ୍ୟତ୍ୟାଗ \| କାର୍ଯ୍ୟକାଳ \| ଜନ୍ମ \| ମୃତ୍ୟୁ \| ଦଳ \| ⏎ \|---\|---\|-… |
| ଭାରତର ପ୍ରଧାନମନ୍ତ୍ରୀମାନଙ୍କର ତାଲିକା (18050/6) | list | 373 | - $^1$ ନିହତ କିମ୍ବା କାର୍ଯ୍ୟରତ ଅବସ୍ଥାରେ ମୃତ ⏎ - $^2$ କାର୍ଯ୍ୟରେ ପୁନଃ ପ୍ରବେଶ ⏎ - $^3$ ତ୍ୟାଗ ପ… |
| ଆର୍କମିଡ଼ିସ (18066/9) | text | 3,395 | ସେ ଭାରଦଣ୍ଡ (Lever) ଉଦ୍ଭାବନ କରି ନଥିଲେ, କିନ୍ତୁ ଭାରଦଣ୍ଡ ବିଷୟରେ ବିସ୍ତୃତ ବିବରଣୀ ପ୍ରଦାନ କରିଥିଲେ… |
| ଆର୍କମିଡ଼ିସ (18066/22) | text | 1,213 | ଆର୍କ୍‌ମିଡ଼ିସ୍‌ ବିଶ୍ୱବ୍ରହ୍ମାଣ୍ଡର ଧୂଳିକଣାର ସଂଖ୍ୟା କେତେ ଏହାକୁ ଗଣନା କରିବାକୁ ଆଗେଇ ଆସିଲେ । ସେ କ… |
| ଆର୍କମିଡ଼ିସ (18066/26) | text | 1,976 | ଆର୍କ୍‌ମିଡ଼ିସ୍‌ଙ୍କ ଗୃହପାଳିତ ପଶୁର ସମସ୍ୟା ଉପରେ ଗୋଟହୋଲଡ ଇଫ୍ରାଇମ ଲେସିଙ୍ଗ (Gotthold Ephraim Les… |
| ପୃଥିବୀର ବାୟୁମଣ୍ଡଳ (19080/3) | text | 1,198 | ବାୟୁ ମଣ୍ଡଳର ବସ୍ତୁତ୍ୱ ପ୍ରାୟ $5.15 \times 10^{18}$ କେଜି, ($5.15 \times 10^{18}$ kg) ଯାହାର ତ… |
| ପୃଥିବୀର ବାୟୁମଣ୍ଡଳ (19080/6) | text | 2,496 | ବାୟୁ ତ‌ଥା ପୃଥିବୀର ବାୟୁ ମଣ୍ଡଳର ତିନୋଟି ମୂଖ୍ୟ ଉପାଦାନ ଥାଏ ଯଥା:- ଯବକ୍ଷାରଜାନ, ଅମ୍ଳଜାନ ଓ ଆର୍ଗନ୍… |
| ପୃଥିବୀର ବାୟୁମଣ୍ଡଳ (19080/7) | table | 1,268 | \| ଗ୍ୟାସ୍ \|  \| ଆୟତନ(A) \|  \| ⏎ \|---\|---\|---\|---\| ⏎ \| ନାମ \| ଫର୍ମୁଲା \| ପିପିଏମ‌ଭି(B) \|  \| ⏎ \|… |
| ପୃଥିବୀର ବାୟୁମଣ୍ଡଳ (19080/17) | text | 490 | ଥର୍ମୋସ୍ଫିଅରର ଉତ୍ତାପ ଉଚ୍ଚତା ଅନୁସାରେ ବଢ଼ିଚାଲେ । ଏହି ଉତ୍ତାପ ପରିମାଣ $1500^0$ସେ ($2700^0$ ଫା)… |
| ପଞ୍ଜାବ, ପାକିସ୍ତାନ (19924/5) | text | 1,293 | 1947 ମସିହାରେ ଭାରତୀୟ ପଞ୍ଜାବ ସୃଷ୍ଟି ହୋଇଥିଲା ଯେତେବେଳେ ଭାରତର ବିଭାଜନ ପଞ୍ଜାବର ପୂର୍ବ ରାଜ ପ୍ରଦେଶକ… |
| ଅସ୍ଥି ଭଗ୍ନ (19937/1) | text | 1,545 | ଅସ୍ଥି ଭଗ୍ନ ଶବ୍ଦଟିକୁ କଥିତ ଓଡ଼ିଆରେ ହାଡ ଭଙ୍ଗା କୁହାଯାଏ । ଡାକ୍ତରୀ ଭାଷାରେ ଏହାକୁ ଫ୍ରାକ୍ଚର୍ କହନ୍ତ… |
| ଯକ୍ଷ୍ମା (20372/22) | text | 450 | ମଦ୍ୟପାନ ବା ଆଲକୋହୋଲିଜ୍ମ ଓ ଡାଏବେଟିସ ମେଲିଟସ ଏହାର ସଙ୍କଟ 3 ଗୁଣ ବଢ଼ାଏ । ସ୍ଟିରଏଡ ସେବନଦ୍ୱାରା ବିକଶ… |
| ବ୍ରହ୍ମଗିରି (20478/4) | text | 104 | ଅଲାରନାଥ ପୀଠ, ମା ହରଚଣ୍ଡି ମନ୍ଦିର, କରମଳା ମଠ |
| ଡେଙ୍ଗୁ ଜର (21600/11) | text | 2,873 | ଡେଙ୍ଗୁ ଭାଇରସ ଏଡିସ ଜାତୀୟ ମଶା ମୂଖ୍ୟତଃ ଏଡିସ ଇଜିପ୍ଟିଦ୍ୱାରା (Aedes aegypti) ବିସ୍ତାର ଲାଭ କରେ ।… |
| ଡେଙ୍ଗୁ ଜର (21600/22) | text | 4,352 | ଡେଙ୍ଗୁର ବିଶିଷ୍ଟ ଲକ୍ଷଣ ମଧ୍ୟରେ ହଠାତ ଜର, ଆଖି ପଛ ପଟେ ମୁଣ୍ଡ ବଥା, ପେଶୀ ତଥା ଗଣ୍ଠି ଯନ୍ତ୍ରଣା ଅନୁଭବ… |
| ମୂତ୍ରାଙ୍ଗ ସଂକ୍ରମଣ (21740/28) | text | 1,550 | ଉପଯୁକ୍ତ ଲକ୍ଷଣ ଦେଖାଗଲେ ରୋଗ ନିର୍ଣ୍ଣୟରେ ଅସୁବିଧା ହୁଏ ନାହିଁ ଓ ଔଷଧ ଦିଆ ଯାଇପାରେ । ଜଟିଳ ଥିଲେ ପରିସ… |
| ସିରୋସିସ୍ (22233/7) | list | 2,700 | - ସ୍ପାଇଡର୍ ଆଞ୍ଜିଓମା ବା ସ୍ପାଇଡର ନେଭସ୍ । ସରୁ ରକ୍ତ ନଳୀ ସାଇକଲ ସ୍ପୋକ୍ ଭଳି ଦେଖାଯାଏ । ⏎ - ପାପୁଲି… |
| ମେନିଞ୍ଜାଇଟିସ୍ (22300/6) | text | 4,715 | ବୟସ୍କମାନଙ୍କର ମେନିଞ୍ଜାଇଟିସ୍ ହେଲେ ସାଧାରଣତଃ ଜୀବାଣୁ ଜନିତ ହୋଇଥିଲେ 90% ରୋଗୀଙ୍କର ମୁଣ୍ଡ ବଥା ହୁଏ ଓ… |
| ମେନିଞ୍ଜାଇଟିସ୍ (22300/26) | table | 987 | \| ମେନିଞ୍ଜାଇଟିସ୍‌ର ପ୍ରକାର \| ଗ୍ଲୁକୋଜ୍ \| ସମ୍ପୁର୍ଣ୍ଣ ପୁଷ୍ଟିସାର \| ରକ୍ତ କୋଷ ବୃଦ୍ଧି \| ⏎ \|---\|---… |
| ରୋମ (22566/1) | text | 2,188 | ରୋମ (ଇଟାଲୀୟ ଓ ଲାଟିନ୍: ରୋମା ), ଇଟାଲୀର ରାଜଧାନୀ ସହର ଓ ଏକ ବିଶେଷ କୋମୁନେ (କମୁନେଡି ରୋମା କାପିଟାଲେ… |
| ଭାରତୀୟ ଜନସଞ୍ଚାର ସଂସ୍ଥାନ, ଢେଙ୍କାନାଳ (23383/3) | text | 830 | 1965 ମସିହା ଅଗଷ୍ଟ 17 ତାରିଖରେ ସ୍ୱର୍ଗତ ଇନ୍ଦିରା ଗାନ୍ଧୀଙ୍କଦ୍ୱାରା ଦିଲ୍ଲୀରେ ଏହା ଉଦଘାଟିତ ହୋଇଥିଲା… |
| ଚିଲିକା (କବିତା) (23693/5) | text | 154 | hiii ଉତ୍କଳ କମଳା ବିଳାସ ଦୀର୍ଘିକା, ମରାଳ ମାଳିନୀ ନୀଳାମ୍ବୁ ଚିଲିକା। |
| ଅମ୍ଳଜାନ (24987/8) | text | 61 | ପୃଥିବୀ ପୃଷ୍ଠ : $4.74 \times 10^{5}$ ppm |
| ଅମ୍ଳଜାନ (24987/10) | text | 33 | $6.1 \times 10^{8}$ ppb by weight |
| ଅମ୍ଳଜାନ (24987/11) | text | 32 | $2.4 \times 10^{8}$ ppb by atoms |
| ଯବକ୍ଷାରଜାନ (25155/4) | text | 267 | ବହୁଳତା: ବିଶ୍ୱ: 1000 ppm (by weight) ସୂର୍ଯ୍ୟ: 1000 ppm (by weight) ପୃଥିବୀ ପୃଷ୍ଠ : 25 ppm ସ… |

... and 269 more (`bpb` null in `annotations/bpb.paragraphs.jsonl`).

## Method

Matches odia-llm-trainer's `src/odia_llm/evaluation/harness.py` (`Scorer.loglik`, `Scorer.bpb`, `split_text`), so the numbers are on the same scale as the experiments' `bpb_odia`:

- Paragraph *i* = `text.split("\n\n")[i]`; paragraph 0 (the `# title` heading) is not scored. Headings, list blocks and tables count as one paragraph each.
- A paragraph over 1,000 characters is cut at whitespace into pieces by the harness's `split_text`; each piece is scored on its own and the bits and bytes are summed.
- A piece is scored as BOS + its tokens (the harness's `self.start`; the tokenizer is called with `add_special_tokens=False`, so it adds its usual leading `▁`). Every piece token is scored, the first one given only BOS. Sequences over 4,096 tokens would be left-truncated as in the harness (0 were).
- bits = −Σ ln p / ln 2; bpb = bits / UTF-8 bytes of the (stripped) pieces. Article bpb = Σ bits / Σ bytes over its paragraphs, headings included.
- Scores are kept per paragraph *text*, for good, in `raw/bpb/scores.jsonl.gz`: when the corpus is rebuilt they are carried over by `para_sha1`, and only texts never scored before go to a GPU (see the runs below). A text found in several paragraphs of one run gets the mean of its scores.
- bf16 weights, SDPA attention, `torch.inference_mode`. Batches are sorted by length and cut at 32,768 tokens. The decoder runs once per batch, and the output head runs on 8,192 positions at a time with an fp32 log-softmax (`cross_entropy`), so the full batch × length × 68,096 logits tensor is never built. Progress is checkpointed every ~500k tokens.

## Runs

Model `sarvamai/sarvam-1` @ `e9607337286ddf496d4a2562b194e489dcf3feea`, bf16, the same code path in every run. Each paragraph's score comes from the run that first scored its text (`score_run` in the store, `raw/bpb/scores.jsonl.gz`).

| run | scored | pod | GPU | price | pod up (UTC) | pod time, cost | tokens (with BOS) | corpus sha1 |
| ---: | --- | --- | --- | ---: | --- | ---: | ---: | --- |
| 1 | all 148,745 paragraphs | `uandv1iutcwyqg` | NVIDIA RTX A6000 (48 GB), Secure, US-TX-1 | $0.53/h | 2026-09-24T13:59 to 14:29 | 30 min, $0.26 | 13,461,968 in 816 s (16,487/s) | 7a9fcced |
| 2 | 1,379 new texts + 200 rechecks | `okby731xk0lcet` | NVIDIA RTX 4000 Ada Generation (20 GB), Secure, EU-RO-1 | $0.28/h | 2026-09-24T16:57 to 17:02 | 5 min, $0.02 | 210,361 in 25 s (8,357/s) | 4f64265f |
| 3 | 2,445 new texts + 200 rechecks | `t7qxsgbz59epm5` | NVIDIA RTX A6000 (48 GB), Secure, EU-SE-1 | $0.53/h | 2026-09-24T18:27 to 18:30 | 4 min, $0.03 | 525,058 in 34 s (15,231/s) | a80c57fd |

Image `runpod/pytorch:1.0.3-cu1281-torch291-ubuntu2404`, torch 2.9.1+cu128 (CUDA 12.8), transformers 5.17.0 in every run. Total pod cost $0.32.

## Sanity checks

Checks 1-3 ran with run 1, on the whole corpus as it was then; 4 and 5 are recomputed on the current corpus at every build. Later runs are checked against carried-over scores in their own section below.

1. **Shuffle control.** 300 random prose paragraphs (>= 100 B, >= 10 words, one piece) were scored as they are and with their words shuffled (the whitespace kept in place, so the bytes are identical). The shuffled copy scored higher for **300 of 300** (100.0%); pooled bpb 0.518 → 0.883, median ratio 1.69, smallest 1.08.
2. **Determinism.** 100 random paragraphs re-scored: same batches twice, max |Δbpb| = 0.00e+00; one paragraph per batch (no padding), max |Δbpb| = 2.60e-02 (max relative Δbits 2.3e-02); against the main run (other batch neighbours), max |Δbpb| = 2.81e-02.
   **How precise is one paragraph's bpb?** The differences above are bf16 rounding, not a bug: on 150 random paragraphs, fp32 batched and fp32 one-at-a-time agree to 8e-06 (so batching and padding are exact), while bf16 differs from fp32 by a median 0.3% of the bits (p95 2.0%, max 3.4%, worst on a 18-byte paragraph) in the main run and 0.3% scored one at a time. Pooled bpb is unbiased: 0.5550 (bf16) vs 0.5551 (fp32). A 1% tail is far wider than this (see the robust spreads below), so the flags are stable; the exact rank of two paragraphs a few percent apart is not.
3. **Parity with odia-llm-trainer's harness.** `odia_llm.evaluation.harness.Scorer` (its own `loglik`, batch 8) on 208 pieces from 200 random paragraphs: max |Δ log-likelihood| per piece 3.551 nats (relative 3.8e-02); pooled bpb 0.54221 (harness) vs 0.54241 (this script); `Scorer.bpb` on the same paragraphs: 0.54221. `split_text` differs from the harness's on 0 of 143,038 paragraphs.
4. **Plausibility.** Corpus bpb 0.5528 and prose 0.5206, against 0.4951 for base Sarvam-1 on the held-out set. That set is scored as whole documents cut into ~1,000-character pieces; here every paragraph starts afresh from BOS, so short paragraphs lose context and cost more. Matched for length the numbers agree: prose paragraphs of 1 kB and more score 0.4942 (26,064 paragraphs), while those of 100-299 B score 0.710 and headings 1.129. Encyclopedic Odia is not easier for Sarvam-1 than the held-out mix (which already includes 300 Wikipedia documents from 2023).
5. **Coverage.** 143,038 of 143,357 non-title paragraphs of the current corpus (sha1 `634b1411a0d1`) have a score, 319 have none (not scored yet, see above), 0 are duplicated; each score is looked up by the sha1 of the paragraph's current text (113,472 distinct texts; rows by scoring run: run 1: 139,849, run 2: 955, run 3: 2,234, not scored: 319). 18,689 of 18,695 articles have scored paragraphs, and `text_sha1` and `para_sha1` are computed from the current corpus file. The latest run (run 3) scored an earlier corpus file (sha1 `a80c57fd72f3`), whose scores carry over. 0 paragraphs have no bytes to score.

## Re-scoring after the 2026-09-24 cleanup

On 2026-09-24 the corpus was rebuilt with text cleanups: 917 ପରୁଷ→ପୁରୁଷ typo fixes, external-link templates (IMDb, Facebook, …) dropped, conversion leftovers stripped, and 6,622 English-dominant paragraphs, list items and tables removed (kept in `removed/english-paragraphs.parquet`). It went from 20,834 to 20,827 articles and from 148,745 to 144,751 non-title paragraphs.

A paragraph's bpb depends only on its own text (it is scored from BOS, and paragraph *i* is still `text.split("\n\n")[i]`), so every score was carried over by `para_sha1` and only texts never scored before went to the GPU (`score --only-missing`), with the same model revision, chunking, bf16 and code as run 1.

- **Before**: 20,834 articles, 148,745 paragraphs, corpus bpb 0.5564 (prose 0.5234).
- **Now**: 18,689 articles, 143,038 paragraphs (113,153 distinct texts), corpus bpb 0.5528 (prose 0.5206). 142,083 rows carried over; 955 rows (1,379 new texts, 0.89 MB, 183,357 tokens) in 952 articles were scored in run 2: 939 text, 11 table, 5 list; their bpb is 0.5905.
- **Pod**: `okby731xk0lcet`, NVIDIA RTX 4000 Ada Generation (20 GB), Secure cloud, EU-RO-1, $0.28/h, up 5 min ($0.02); scoring took 25 s of GPU time (8,357 tokens/s).
- **Consistency**: 200 carried-over texts, scored again on the new pod, came out identical for 102; |Δ bits| / bits has median 0.00%, p95 1.04%, max 2.20% (paragraphs >= 100 B: median 0.00%, max 2.20%), mean signed +0.03%; pooled bpb 0.5360 new vs 0.5359 carried over. That is the size of run 1's own bf16 batch noise (see determinism above), so old and new scores are on the same scale and the flags are unaffected.
- **Review queue** (types among the 200, before → now): garbled 32 → 34, english 32 → 34, templated 31 → 34, table 30 → 31, markup 13 → 0, script 31 → 34, article 31 → 33.

## Re-scoring after the 2026-09-24 cleanup

On 2026-09-24 the corpus was rebuilt again: 980 English prose paragraphs and headings were replaced by hand-checked Odia translations (`translations/english-to-odia.jsonl`), English list items and tables were restored (5,754 English blocks are now kept as they are), and 136 citation and template-error blocks were removed. It went from 20,827 to 20,836 articles and from 144,751 to 146,769 non-title paragraphs; 2,445 paragraph texts in 1,064 articles were new. The RTX 4000 Ada was out of stock, so this run used an RTX A6000.

A paragraph's bpb depends only on its own text (it is scored from BOS, and paragraph *i* is still `text.split("\n\n")[i]`), so every score was carried over by `para_sha1` and only texts never scored before went to the GPU (`score --only-missing`), with the same model revision, chunking, bf16 and code as run 1.

- **Before**: 20,827 articles, 144,751 paragraphs, corpus bpb 0.5500 (prose 0.5219).
- **Now**: 18,689 articles, 143,038 paragraphs (113,153 distinct texts), corpus bpb 0.5528 (prose 0.5206). 140,804 rows carried over; 2,234 rows (2,445 new texts, 1.37 MB, 497,209 tokens) in 877 articles were scored in run 3: 670 list, 534 heading, 518 table, 512 text; their bpb is 0.8615.
- **Pod**: `t7qxsgbz59epm5`, NVIDIA RTX A6000 (48 GB), Secure cloud, EU-SE-1, $0.53/h, up 4 min ($0.03); scoring took 34 s of GPU time (15,231 tokens/s).
- **Consistency**: 200 carried-over texts, scored again on the new pod, came out identical for 126; |Δ bits| / bits has median 0.00%, p95 0.91%, max 1.79% (paragraphs >= 100 B: median 0.00%, max 1.79%), mean signed +0.05%; pooled bpb 0.5234 new vs 0.5233 carried over. That is the size of run 1's own bf16 batch noise (see determinism above), so old and new scores are on the same scale and the flags are unaffected.
- **Review queue** (types among the 200, before → now): garbled 35 → 34, english 35 → 34, templated 35 → 34, table 24 → 31, markup 2 → 0, script 35 → 34, article 34 → 33.

## Distributions

### By paragraph kind

| kind | paragraphs | MB | bytes | pooled bpb | median | p1 | p99 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| text | 86,688 | 73.68 | 85.8% | 0.521 | 0.537 | 0.324 | 2.009 |
| heading | 43,816 | 1.73 | 2.0% | 1.129 | 1.161 | 0.624 | 2.656 |
| list | 9,488 | 6.08 | 7.1% | 0.682 | 0.825 | 0.351 | 2.344 |
| table | 2,940 | 4.35 | 5.1% | 0.686 | 0.759 | 0.253 | 1.717 |
| math | 106 | 0.01 | 0.0% | 1.278 | 1.567 | 0.528 | 4.255 |
| all | 143,038 | 85.86 | 100% | 0.553 | 0.655 | 0.333 | 2.356 |

Pooled bpb = Σ bits / Σ bytes (what the harness reports); median and percentiles are over paragraphs.

### By length

| bytes | all: paragraphs | pooled | median | text: paragraphs | pooled | median | p1 | p99 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| <100 B | 48,624 | 1.177 | 1.192 | 4,101 | 1.325 | 1.431 | 0.549 | 5.174 |
| 100-299 B | 16,114 | 0.768 | 0.733 | 12,270 | 0.710 | 0.691 | 0.388 | 1.588 |
| 300-999 B | 49,449 | 0.556 | 0.543 | 44,253 | 0.536 | 0.532 | 0.325 | 0.928 |
| 1-3 kB | 27,070 | 0.505 | 0.495 | 24,750 | 0.492 | 0.490 | 0.311 | 0.738 |
| >=3 kB | 1,781 | 0.541 | 0.509 | 1,314 | 0.507 | 0.499 | 0.351 | 0.699 |

Short pieces cost more bits per byte: the first tokens after BOS have no context. So paragraphs are only compared with paragraphs of the same kind and length band, and only text, list and math paragraphs of >= 100 B and tables of >= 200 B are compared at all (93,634 of 143,038). Headings are never flagged. Articles are ranked only from 500 B.

### Articles

| articles | n | pooled bpb | median | p1 | p5 | p95 | p99 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| all articles | 18,695 | 0.553 | 0.551 | nan | nan | nan | nan |
| >= 500 B | 18,095 | 0.553 | 0.550 | 0.370 | 0.409 | 0.770 | 0.913 |
| stubs | 1,738 | 0.525 | 0.514 | 0.347 | 0.401 | 0.698 | 0.825 |
| neither | 16,357 | 0.556 | 0.555 | 0.374 | 0.410 | 0.777 | 0.921 |
| odia_ratio < 0.6 | 325 | 0.756 | 0.736 | 0.428 | 0.550 | 1.141 | 1.488 |

### Did Sarvam-1 memorise Odia Wikipedia?

Sarvam-1 was released in October 2024. If it had memorised Odia Wikipedia, pages that existed before then should score clearly lower than pages created after it, which it cannot have seen. On 2026-09-24 they did not: pages created since then scored *lower*, continuing a fall that starts around 2019 (more formulaic biographies, more machine-assisted translation); the tables below are recomputed at every build. That does not rule out memorisation of some famous pages, but it is not what makes the low end low. (Page creation dates are from `annotations/translation.jsonl`; without it, the year of the scored revision is used.)

| page | made by | articles >= 500 B | pooled bpb | median | median prose bpb |
| --- | --- | ---: | ---: | ---: | ---: |
| created 2024-10 or later | written in Odia | 2,132 | 0.534 | 0.525 | 0.494 |
| created 2024-10 or later | machine-assisted translation | 345 | 0.477 | 0.479 | 0.478 |
| created before 2024-10 | written in Odia | 12,147 | 0.557 | 0.563 | 0.530 |
| created before 2024-10 | machine-assisted translation | 3,471 | 0.556 | 0.525 | 0.512 |

| page created | articles >= 500 B | median bpb | median prose bpb | translated |
| --- | ---: | ---: | ---: | ---: |
| 2011 | 1,124 | 0.624 | 0.558 | 0% |
| 2012 | 864 | 0.563 | 0.541 | 0% |
| 2013 | 835 | 0.563 | 0.535 | 0% |
| 2014 | 523 | 0.574 | 0.547 | 0% |
| 2015 | 1,688 | 0.628 | 0.588 | 1% |
| 2016 | 1,667 | 0.608 | 0.583 | 50% |
| 2017 | 1,415 | 0.573 | 0.547 | 19% |
| 2018 | 1,241 | 0.560 | 0.526 | 5% |
| 2019 | 884 | 0.515 | 0.477 | 11% |
| 2020 | 913 | 0.491 | 0.465 | 12% |
| 2021 | 896 | 0.485 | 0.461 | 34% |
| 2022 | 1,196 | 0.495 | 0.473 | 43% |
| 2023 | 1,263 | 0.502 | 0.481 | 47% |
| 2024 | 1,525 | 0.504 | 0.489 | 55% |
| 2025 | 1,129 | 0.524 | 0.498 | 13% |
| 2026 | 850 | 0.510 | 0.485 | 2% |
| <=2010 | 82 | 0.546 | 0.513 | 2% |

### Content Translation (`annotations/translation.jsonl`)

`translated` is that file's recommended flag (Content Translation or MDWiki created the page, or wrote at least half of it).

| articles >= 500 B | n | pooled bpb | median | median prose bpb | in review queue |
| --- | ---: | ---: | ---: | ---: | ---: |
| `translated` = False | 14,279 | 0.553 | 0.557 | 0.522 | 157 |
| `translated` = True | 3,816 | 0.549 | 0.518 | 0.507 | 42 |
| `ct_created` = False | 14,596 | 0.552 | 0.555 | 0.521 | 157 |
| `ct_created` = True | 3,499 | 0.556 | 0.525 | 0.512 | 42 |
| `mdwiki_created` = False | 17,797 | 0.554 | 0.551 | 0.520 | 199 |
| `mdwiki_created` = True | 298 | 0.474 | 0.479 | 0.479 | 0 |

### By topic (`annotations/topics.jsonl`, `primary_topic`)

| topic | articles >= 500 B | pooled bpb | median | median prose bpb | in review queue |
| --- | ---: | ---: | ---: | ---: | ---: |
| health | 3,266 | 0.525 | 0.515 | 0.514 | 1 |
| film | 2,687 | 0.610 | 0.600 | 0.520 | 34 |
| politics | 2,535 | 0.474 | 0.452 | 0.426 | 30 |
| geography | 2,076 | 0.586 | 0.633 | 0.594 | 18 |
| literature | 1,663 | 0.588 | 0.591 | 0.509 | 23 |
| religion | 1,215 | 0.569 | 0.558 | 0.543 | 10 |
| arts | 1,098 | 0.583 | 0.576 | 0.548 | 17 |
| sports | 524 | 0.542 | 0.544 | 0.532 | 11 |
| history | 523 | 0.497 | 0.510 | 0.496 | 3 |
| biology | 506 | 0.581 | 0.616 | 0.590 | 9 |
| (none) | 467 | 0.580 | 0.556 | 0.537 | 14 |
| technology | 396 | 0.548 | 0.545 | 0.528 | 10 |
| science | 297 | 0.531 | 0.542 | 0.528 | 9 |
| calendar | 263 | 0.524 | 0.563 | 0.543 | 2 |
| society | 191 | 0.506 | 0.516 | 0.495 | 4 |
| economy | 184 | 0.516 | 0.507 | 0.486 | 2 |
| education | 150 | 0.492 | 0.476 | 0.460 | 2 |
| mathematics | 54 | 0.592 | 0.596 | 0.591 | 0 |
| `school_relevant` = False | 7,876 | 0.553 | 0.539 | 0.499 | 103 |
| `school_relevant` = True | 10,219 | 0.552 | 0.557 | 0.533 | 96 |
| `odisha` = False | 11,484 | 0.550 | 0.549 | 0.532 | 142 |
| `odisha` = True | 6,611 | 0.559 | 0.550 | 0.496 | 57 |
| `is_person` = False | 11,526 | 0.556 | 0.559 | 0.537 | 106 |
| `is_person` = True | 6,569 | 0.546 | 0.527 | 0.484 | 93 |

## What the extremes look like

Spread within groups (robust sd of log bpb, 1.4826 MAD): list 1-3 kB 0.27, list 100-299 B 0.29, list 300-999 B 0.30, table 300-999 B 0.34, text 1-3 kB 0.18, text 100-299 B 0.27, text 300-999 B 0.22, text >=3 kB 0.14. So the top 1% of prose sits roughly 2.5 robust sd, or about 1.7x, above its group's median.

**The high end.** Of the 828 prose paragraphs in the top 1%, 2% are mostly Latin script, 1% mostly another script and 97% Odia.
- English: 295 comparable text and list paragraphs are mostly Latin script with common English words, median bpb 1.02 (Sarvam-1 reads English at about 0.8 bpb, E03); the largest are in ଗାଲିଲିଓ (18991), ମହିଳାଙ୍କ ସୁରକ୍ଷା ନିମନ୍ତେ ଭାରତରେ ଥିବା ଆଇନ ତାଲିକା (44352), କଳ୍ପନା ଚାୱଲା (18747).
- Another script, or Latin letters that are not English (transliteration, IPA, romanised titles, code): 290 comparable paragraphs, most extreme in ପଞ୍ଜାବୀ ଭାଷା (13376), ମୁକ୍ରୀ (56147), ମ୍ୟାଟ୍‌ଲାବ୍‌ (34400), ମୋହନ ଚୋଟି (56180).
- Odia: 801 paragraphs, 15% of them verse by line shape; the most extreme prose ones are in ବିଶାଳାକ୍ଷୀ ମନ୍ଦିର (49282), ବିଷ୍ଣୁ ମାଝୀ (80916), ସରହପା (12785), ସାଲବେଗ (11367). Read on 2026-09-24, the Odia top 1% was mostly legitimate but unusual text: poems, folk songs and Sanskrit shlokas, archaic Odia, and runs of names (weapons, song and film titles transliterated into Odia). Genuinely garbled Odia was rare. That is why verse is ranked after prose in the `garbled` type.

**The low end is formulaic writing.** Of the 824 prose paragraphs in the bottom 1%, 266 (32%) have a near-copy in another article, a shared sentence frame or internal repetition (most extreme in ଚିନ୍ତାମଣି ଜେନା (ରାଜନୀତିଜ୍ଞ) (70599), ଲୋକନାଥ ମିଶ୍ର (ରାଜନେତା, 1967 ମୃତ୍ୟୁ) (83827), ପୂଜା ଭସ୍ତ୍ରାକର (100132)). The rest is plain, well-formed encyclopedic prose (most extreme in କିରଣ ବ୍ୟାସ (99029), ଲକ୍ଷ୍ମଣ ମଲ୍ଲିକ (72519), ଓଡ଼ିଶାର ମୁଖ୍ୟମନ୍ତ୍ରୀମାନଙ୍କର ତାଲିକା (10935)). The most repeated table header row, `| ଆରମ୍ଭ | ଶେଷ | ପଦବୀ | ନିର୍ବାଚନ ମଣ୍ଡଳୀ | ଦଳ |`, is in 220 articles.

**bpb does not find boilerplate.** Each paragraph is scored on its own, so a sentence frame repeated across many articles is no more predictable to the model than any other sentence. The 4,162 comparable prose paragraphs whose frame (names and numbers masked) recurs in 5+ articles score 0.569 pooled against 0.517 for the rest. They are shorter (median 266 B against 764 B), and within their own kind and length band their median sits at the 49th percentile; only 12 of them reach the bottom 1%. Use the `repeats` column, not bpb, to find templates; 6,059 more templated paragraphs are under 100 B and are not compared at all.

**Conversion leftovers**: 0 paragraphs in 0 articles match the markup patterns outside math.

**Highest-bpb prose paragraphs (>= 100 B)**

| bpb | B | article (id/para) | excerpt |
| ---: | ---: | --- | --- |
| 2.595 | 141 | ମ୍ୟାଟ୍‌ଲାବ୍‌ (34400/6) | \>> x = 17 x = 17 >> x = 'hat' x = hat >> y = x + 0 y = 104 97 116 >> x = [3\*4, pi/2] x = 12.0000 1.5708 >>… |
| 2.355 | 417 | ପଞ୍ଜାବୀ ଭାଷା (13376/21) | ଟ୍ରାନ୍ସ ଲିଟେରେସନ୍: lahaur pākistānī panjāb dī rājdā̀ni ài. lok giṇtī de nāḷ karācī tõ bāad lahaur dūjā sáb tõ… |
| 2.180 | 121 | ଅଡ଼ାଣା (ରାଗ) (65827/3) | ଆରୋହଣ S R M P n P M P n S' ପ୍ରାୟତଃ ଛୋଟ ⏎ S R g M P n P S' ⏎ ଅବରୋହଣ S' d n P g M R s |
| 2.152 | 112 | ହନୁମାନ ଚାଳିଶା (55252/44) | ଆପନ ତେଜ ସମହ।ରୋ ଆପୈ । ⏎ ତୀନୈ ଲୋକ ହାଁକ ତେ କାପୈ ॥ |
| 2.112 | 114 | ହନୁମାନ ଚାଳିଶା (55252/47) | ନାସୌ ରୋଗ ହରୌ ସବ ପୀର। ⏎ ଜପତ ନିରଂତର ହନୁମତ ବୀରା ॥ |
| 2.101 | 111 | ଓଡ଼ିଆ ସାହିତ୍ୟ (2658/8) | ରାମ ହେ ଲଇଖଣ\* ହେ ଗଲେ ମିରିଗ ମାରି...ହୁ...ଡ଼...ରରର.... |
| 2.039 | 105 | ମିର୍ଜା ଗାଲିବ (31541/20) | ମୋତ ସେ ପ‌ହଲେ ଆଦମୀ ଗମ ସେ ନିଜାତ ପାଏ କ୍ୟୁଁ । |
| 2.020 | 139 | ନେତରହାଟ (56357/3) | ନେତାରହାଟରସ୍ଥିତ 23°29′00″N 84°16′00″E / ,ଠାରେ ଓ ଏହାର ପତ୍ତନ 3.514 ଫୁଟ |
| 2.011 | 100 | ଅଜନ୍ତା ଗୁମ୍ଫା (72200/52) | — ୱାଲ୍‍ଟର୍ ସ୍ପିଂକ୍, Ajanta: History and Development, Cave by Cave, |
| 1.981 | 146 | ଶିବ ପଞ୍ଚାକ୍ଷର ସ୍ତୋତ୍ର (98362/42) | ଚନ୍ଦ୍ରାର୍କ ବୈଶ୍ୱାନର ଲୋଚନାୟ ତସ୍ମୈ "ବ" କାରାୟ ନମଃ ଶିବାୟ॥\*\*) |
| 1.946 | 115 | ମୋତି ପ୍ରକାଶ (82232/10) | 4\* 'ଚିନିଙ୍ଗା ଭିଚ୍ ଛୋଲେ' (ମୋ କୋଳରେ ଝୁଲ), (କବିତା-1983) |
| 1.927 | 161 | କାହ୍ନାପା (81962/6) | এক সো পদমা চউষট্‌ঠী পাখুড়ি । তহিঁ চড়ি নাচই ডোম্বি বাপুড়ি ॥ |

**Lowest-bpb prose paragraphs (>= 100 B)**

| bpb | B | article (id/para) | excerpt |
| ---: | ---: | --- | --- |
| 0.204 | 1,313 | ଚିନ୍ତାମଣି ଜେନା (ରାଜନୀତିଜ୍ଞ) (70599/11) | 1980 ଭାରତୀୟ ସାଧାରଣ ନିର୍ବାଚନରେ ଚିନ୍ତାମଣି ଭାରତୀୟ ଜାତୀୟ କଂଗ୍ରେସ (ଇ)ର ପ୍ରାର୍ଥୀ ଭାବରେ ବାଲେଶ୍ୱର ଲୋକ ସଭା ନିର୍ବାଚନ ମଣ… |
| 0.209 | 530 | କିରଣ ବ୍ୟାସ (99029/6) | 2024 ମସିହାରେ, ଯୋଗ କ୍ଷେତ୍ରରେ ତାଙ୍କର ଅବଦାନ ପାଇଁ ଭାରତ ସରକାରଙ୍କ ଦ୍ୱାରା କିରଣ ବ୍ୟାସଙ୍କୁ ପଦ୍ମଶ୍ରୀ ପୁରସ୍କାରରେ ସମ୍ମାନି… |
| 0.219 | 1,006 | ଚିନ୍ତାମଣି ଜେନା (ରାଜନୀତିଜ୍ଞ) (70599/9) | 1971 ମସିହାର ଓଡ଼ିଶା ବିଧାନ ସଭା ନିର୍ବାଚନରେ ସେ ଭାରତୀୟ କମ୍ୟୁନିଷ୍ଟ ପାର୍ଟିର ପ୍ରାର୍ଥୀ ଭାବରେ ପୁଣିଥରେ ବସ୍ତା ବିଧାନ ସଭା ନ… |
| 0.233 | 1,911 | ଲକ୍ଷ୍ମଣ ମଲ୍ଲିକ (72519/1) | ଲକ୍ଷ୍ମଣ ମଲ୍ଲିକ (12 ଅକ୍ଟୋବର 1927 - 19 ଅକ୍ଟୋବର 2014) ଜଣେ ଓଡ଼ିଆ ରାଜନୀତିଜ୍ଞ ଥିଲେ । ଲକ୍ଷ୍ମଣ ଓଡ଼ିଶା ରାଜନୀତିରେ ଭାରତୀ… |
| 0.237 | 1,825 | ଓଡ଼ିଶାର ମୁଖ୍ୟମନ୍ତ୍ରୀମାନଙ୍କର ତାଲିକା (10935/1) | ଓଡ଼ିଶାର ମୁଖ୍ୟମନ୍ତ୍ରୀ ହେଉଛନ୍ତି ଭାରତର ଏକ ରାଜ୍ୟ ଓଡ଼ିଶା ସରକାରଙ୍କ ମୁଖ୍ୟ । ଭାରତର ସମ୍ବିଧାନ ଅନୁଯାୟୀ, ରାଜ୍ୟର ରାଜ୍ୟପାଳ… |
| 0.238 | 1,241 | କମଳା ଦାସ (69843/2) | କମଳା 1990 ମସିହାରେ ଓଡ଼ିଶାରେ ହୋଇଥିବା ବିଧାନ ସଭା ନିର୍ବାଚନରେ ଭୋଗରାଇ ବିଧାନ ସଭା ନିର୍ବାଚନ ମଣ୍ଡଳୀରୁ ଜନତା ଦଳର ପ୍ରତିନିଧି… |
| 0.243 | 859 | ଖାରବେଳ ସ୍ୱାଇଁ (70384/9) | 1998 ଭାରତୀୟ ସାଧାରଣ ନିର୍ବାଚନରେ ଖାରବେଳ ଭାରତୀୟ ଜନତା ପାର୍ଟିର ପ୍ରାର୍ଥୀ ଭାବରେ ବାଲେଶ୍ୱର ଲୋକ ସଭା ନିର୍ବାଚନ ମଣ୍ଡଳୀରୁ ନି… |
| 0.245 | 482 | ଭାରତୀୟ ସ୍ୱାଧୀନତା ଆନ୍ଦୋଳନ (72927/25) | 1857ର ଭାରତୀୟ ବିଦ୍ରୋହ ଉତ୍ତର ଏବଂ ମଧ୍ୟ ଭାରତରେ ବ୍ରିଟିଶ ଇଷ୍ଟ ଇଣ୍ଡିଆ କମ୍ପାନୀର ଶାସନ ବିରୁଦ୍ଧରେ ଏକ ବଡ଼ ଧରଣର ବିଦ୍ରୋହ ଥି… |
| 0.246 | 966 | ଗଣମାଧ୍ୟମର ସ୍ୱାଧିନତା (92303/25) | ଗଣମାଧ୍ୟମ ସ୍ୱାଧୀନତା ହେଉଛି ଏକ ମୌଳିକ ଅଧିକାର ଯାହା ୟୁରୋପୀୟ ସଂଘର ସମସ୍ତ ସଦସ୍ୟ ରାଷ୍ଟ୍ର ଏବଂ ଏହାର ନାଗରିକମାନଙ୍କ ପାଇଁ ପ୍ର… |
| 0.247 | 803 | ଗଣମାଧ୍ୟମର ସ୍ୱାଧିନତା (92303/3) | ଜାତିସଂଘର 1948ର ସାର୍ବଜନୀନ ମାନବାଧିକାର ଘୋଷଣାନାମାରେ କୁହାଯାଇଛି: "ସମସ୍ତଙ୍କର ମତ ଓ ଅଭିବ୍ୟକ୍ତିର ସ୍ୱାଧୀନତା ଅଧିକାର ଅଛି;… |
| 0.248 | 649 | ପୂଜା ଭସ୍ତ୍ରାକର (100132/6) | ସେ 10 ଫେବୃଆରୀ 2018ରେ ଦକ୍ଷିଣ ଆଫ୍ରିକା ମହିଳା ବିପକ୍ଷରେ ଭାରତ ମହିଳା ପାଇଁ ତାଙ୍କର ମହିଳା ଦିନିକିଆ ଆନ୍ତର୍ଜାତୀୟ କ୍ରିକେଟ (… |
| 0.248 | 1,418 | ଶାନ୍ତି ସ୍ୱରୂପ ଭଟ୍ଟନାଗର ବିଜ୍ଞାନ ଓ ପ୍ରଯୁକ୍ତିବିଦ୍ୟା ପୁରସ୍କାର (75555/1) | ଶାନ୍ତି ସ୍ୱରୂପ ଭଟ୍ଟନାଗର ବିଜ୍ଞାନ ଓ ପ୍ରଯୁକ୍ତିବିଦ୍ୟା ପୁରସ୍କାର ଭାରତରେ ବିଜ୍ଞାନ ଗବେଷଣା କ୍ଷେତ୍ରରେ ପ୍ରଦାନ କରାଯାଉଥିବା ଏ… |

**Highest-bpb prose, 300-999 B**

| bpb | B | article (id/para) | excerpt |
| ---: | ---: | --- | --- |
| 2.355 | 417 | ପଞ୍ଜାବୀ ଭାଷା (13376/21) | ଟ୍ରାନ୍ସ ଲିଟେରେସନ୍: lahaur pākistānī panjāb dī rājdā̀ni ài. lok giṇtī de nāḷ karācī tõ bāad lahaur dūjā sáb tõ… |
| 1.572 | 478 | ପଞ୍ଜାବୀ ଭାଷା (13376/20) | لہور پاکستانی پنجاب دا دارالحکومت اے۔ لوک گنتی دے نال کراچی توں بعد لہور دوجا سبھ توں وڈا شہر اے۔ لہور پاکستا… |
| 1.470 | 405 | ଦିଗମ୍ବର ମହାପାତ୍ର (94701/4) | "କାନ୍ତୋଃସ୍ତି କେଳି ସଦନେ ଚମସଃ (ଚାମଚା) ପ୍ରିୟାୟାଃ ⏎ କାନ୍ତା କଦାପି କୁତୁକେନ କରୋତି ଚାଟମ୍ ⏎ ଏଷା ପ୍ରଥା ଯଦି ଶୃହେଃସ୍ତି ତଥ… |
| 1.452 | 308 | ସରହପା (12785/6) | ତହି ବଟ ଚିତ୍ତ ବିସାମକରୁ ସରେହେ କହିଓ ଭୱସେ । ଘୋର ଅନ୍ଧାରେ ଚନ୍ଦ୍ରମଣି ଜିମି ଉତ୍ ଜୋଳା କରେଇ ପରମ ମହାସୁଖ ଏଣୁ କଣେ ହୁରିଓ ଅଶେ… |
| 1.432 | 310 | ସାଲବେଗ (11367/9) | ଆହେ ନୀଳ ଶଇଳ , ପ୍ରବଳ ମତ୍ତ ବାରଣ, ମୋ ଆରତ ନଲିନୀ ବନକୁ କର ଦଲନ । ଗଜରାଜ ଚିନ୍ତା କଲା ଥାଇ ଘୋର ଜଳେଣ, ଚକ୍ର ପେଶୀ ନକ୍ର ନାଶୀ… |
| 1.414 | 301 | ସତ୍ୟମେବ ଜୟତେ (13878/2) | ସତ୍ୟମେବ ଜଯତେ ନାନୃତମ୍ ସତ୍ୟନ ପନ୍ଥାବ ବିତତୋବଦେବଯାନଃ । ⏎ ଯେନାକ୍ରମନ୍ୟୁତ୍ୟଷଯୋ ହ୍ୟାତ୍ମକାମୋ ଯତ୍ର ତତ୍ତ୍ୟସ୍ୟ ପରମଂ ନିଧାନଂ… |
| 1.412 | 442 | ଶାଗ (7424/25) | ଶାଗ୍ ଧୁଆ ଛଟନା କି ଗୋ ଶାଗ୍ ଧୁଆ ଛଟନା ⏎ ତୁଇମୁଇଁ ପଲାଇଯିବା ରାଏଗଡ଼ ବସନା ⏎ ରାଏଗଡ ବସନା ଗଲେ କାଣା ଖାଏମା ⏎ ହାତେ ଅଛି ହୀରାମ… |
| 1.410 | 373 | ଆତସବାଜି (21611/7) | କେତେକ ବାଣର ନାମ ଚଂପା, କୁଂପୀ, ତୁଂବ, ହାବିଳି, ଚକ୍ର ଆକାଶ ମଲ୍ଲୀ, ଆସମାନଗୋଲା, ଚେଂଗ, କାଠଚଂପା, ଚଂଦ୍ରଉଦିଆ ବା ମହତାପ, ପାଣି… |

**Highest-bpb lists (>= 100 B)**

| bpb | B | article (id/para) | excerpt |
| ---: | ---: | --- | --- |
| 3.528 | 187 | କାଳିଚରଣ ହେମ୍ବ୍ରମ (72870/7) | - Bousoyem Upol ⏎ - Dhibur Gating ⏎ - Jabor Gansng ⏎ - Damadol ⏎ - Guligodoriyo ⏎ - Saota Sechet ⏎ - Nirmoya… |
| 3.054 | 194 | ଫେରଦୌସୀ ମଜୁମଦାର (98015/12) | - Daktar Abdullahar Karkhana ⏎ - Eka ⏎ - Songsoptok ⏎ - Kokilara ⏎ - Tamoshi ⏎ - Payer Awaj Pawa Jay ⏎ - Ekho… |
| 2.928 | 227 | ଜିନାତ ସାନୁ ସ୍ୱାଗତା (98368/7) | - Linza ⏎ - Somman ⏎ - Satiputra Abdullah ⏎ - Top Mastan ⏎ - Satru Satru khela ⏎ - Koti takar fokir ⏎ - Asant… |
| 2.551 | 210 | ରେହାନା ଜଲି (98227/3) | - Ma o Chhele (1985) ⏎ - Nishpap ⏎ - Biraj Bou ⏎ - Prem Protigga ⏎ - Golmaal ⏎ - Moharani ⏎ - Chetona ⏎ - Pra… |
| 2.512 | 200 | ରୁମାନା ରଶୀଦ ଈଶିତା (98406/9) | - Ghotona Samanyo (1997) ⏎ - Nirjon Aranye ⏎ - Swapno Swapneel ⏎ - Godhuli Belaye ⏎ - Firey Ase Fire Asa ⏎ -… |
| 2.484 | 202 | ସାନଜିଦା ପ୍ରୀତି (98398/8) | - Sporsher Baire ⏎ - Kacher Manush (2006) ⏎ - Doll's House ⏎ - Poush Faguner Pala ⏎ - Baburchiana (2018) ⏎ -… |

**Lowest-bpb lists (>= 100 B)**

| bpb | B | article (id/para) | excerpt |
| ---: | ---: | --- | --- |
| 0.134 | 1,102 | ସ୍ୱାମୀ ବିବେକାନନ୍ଦ (18187/192) | 1. ସ୍ୱାମୀ ବିବେକାନନ୍ଦ ବାଣୀ ଓ ରଚନା, ଖଣ୍ଡ 1 ⏎ 2. ସ୍ୱାମୀ ବିବେକାନନ୍ଦ ବାଣୀ ଓ ରଚନା, ଖଣ୍ଡ 2 ⏎ 3. ସ୍ୱାମୀ ବିବେକାନନ୍ଦ ବା… |
| 0.189 | 1,495 | ନବରଙ୍ଗପୁର (ଲୋକ ସଭା ନିର୍ବାଚନ ମଣ୍ଡଳୀ) (11546/4) | - 2014: ବଳଭଦ୍ର ମାଝି (ବିଜୁ ଜନତା ଦଳ) ⏎ - 2019: ରମେଶ ଚନ୍ଦ୍ର ମାଝୀ (ବିଜୁ ଜନତା ଦଳ) ⏎ - 2014: ବଳଭଦ୍ର ମାଝୀ (ବିଜୁ ଜନତା… |
| 0.202 | 520 | ବିଧାନ ପରିଷଦ (66984/7) | - ଆନ୍ଧ୍ର ପ୍ରଦେଶ ବିଧାନ ପରିଷଦ ⏎ - ବିହାର ବିଧାନ ପରିଷଦ ⏎ - ଜାମ୍ମୁ ଏବଂ କାଶ୍ମୀର ବିଧାନ ପରିଷଦ ⏎ - କର୍ଣ୍ଣାଟକ ବିଧାନ ପରିଷ… |
| 0.208 | 1,361 | ଶ୍ରଦ୍ଧା ଜାଧବ (99679/11) | - 1992: ବୃହନ୍ମୁମ୍ବାଇ ମୁନିସିପାଲ କର୍ପୋରେସନରେ କର୍ପୋରେଟର (ପ୍ରଥମ ମୟାଦ) ⏎ - 1997: ବୃହନ୍ମୁମ୍ବାଇ ମୁନିସିପାଲ କର୍ପୋରେସନର… |
| 0.216 | 3,498 | ହିନ୍ଦୀ ଉଇକିପିଡ଼ିଆ (98790/7) | - 11 ଜୁଲାଇ 2003 – ହିନ୍ଦୀ ଉଇକିପିଡ଼ିଆର ଶୁଭାରମ୍ଭ। ⏎ - 25 ଜାନୁଆରୀ 2005 – ହିନ୍ଦୀ ଉଇକିପିଡ଼ିଆରେ ଲେଖାର ସଂଖ୍ୟା 1,000 ପ… |
| 0.219 | 335 | ଭାରତୀୟ ଜନଗଣନା (82806/8) | - 1951 ଭାରତର ଜନଗଣନା ⏎ - 1961 ଭାରତର ଜନଗଣନା ⏎ - 1971 ଭାରତର ଜନଗଣନା ⏎ - 1981 ଭାରତର ଜନଗଣନା ⏎ - 1991 ଭାରତର ଜନଗଣନା ⏎… |

**Highest-bpb tables (>= 200 B)**

| bpb | B | article (id/para) | excerpt |
| ---: | ---: | --- | --- |
| 2.413 | 429 | ଆବିଦ୍ଜି ଭାଷା (99543/5) | \| Village Name \| Native name (IPA) \| ⏎ \|---\|---\| ⏎ \| Soukoukro \| sukwebi \| ⏎ \| Badasso \| gbadatɛ \| ⏎ \| Elibou… |
| 1.998 | 521 | ୟେବୁ ଭାଷା (100086/11) | \| Village name \| IPA \| Hausa/Fulfulde name \| Notes \| ⏎ \|---\|---\|---\|---\| ⏎ \| Bwara \| ɓʷǎrâ \|  \|  \| ⏎ \| Feka \|… |
| 1.932 | 357 | ବିଜେପୁର (57066/22) | \| Badbausen \| Badbramahani \| Bairakhapali \| Beniachal \| Bhatigaon \| Bijepur \| Budapali \| Baramunda \| ⏎ \|---\|-… |
| 1.865 | 239 | ଅଧରା ଖାନ୍ (98243/6) | \| Year \| Film \| Role \| Notes \| ⏎ \|---\|---\|---\|---\| ⏎ \| 2018 \| Nayok \| Parisha \| Debut film \| ⏎ \| 2018 \| Matal… |
| 1.848 | 260 | ବିସ୍ସା ଭାଷା (100048/26) | \| ବାକ୍ୟଖଣ୍ଡ \| ଲେରେ \| ବାର୍କା/ଗୁରମାଇନ୍ \| ଲେବ୍ରେ \| ⏎ \|---\|---\|---\|---\| ⏎ \| Good morning \| Domireh ki \| Idomleki… |
| 1.844 | 308 | ନିଝୁମ ରୁବିନା (98123/5) | \| Year \| Films \| Role \| Notes \| ⏎ \|---\|---\|---\|---\| ⏎ \| 2013 \| Er Beshi Bhalobasha Jay Na \| Kiran \| Debut fil… |

**Lowest-bpb tables (>= 200 B)**

| bpb | B | article (id/para) | excerpt |
| ---: | ---: | --- | --- |
| 0.107 | 1,846 | ଲୋକ ସଭା (33455/6) | \| ଲୋକ ସଭା \| ନିର୍ବାଚନ ବର୍ଷ \| କାର୍ଯ୍ୟକାଳ \| ⏎ \|---\|---\|---\| ⏎ \| 1ମ ଲୋକ ସଭା \| ସାଧାରଣ ନିର୍ବାଚନ 1952 \| 1952–1957 \|… |
| 0.173 | 1,424 | ବାସୁଦେବ ମାଝି (80957/7) | \| ଆରମ୍ଭ \| ଶେଷ \| ପଦବୀ \| ନିର୍ବାଚନ ମଣ୍ଡଳୀ \| ଦଳ \| ⏎ \|---\|---\|---\|---\|---\| ⏎ \| 1974 \| 1977 \| ସଭ୍ୟ, 6ଷ୍ଠ ଓଡ଼ିଶା ବିଧ… |
| 0.180 | 1,451 | ହବିବୁଲ୍ଲା ଖାଁ (81253/7) | \| ଆରମ୍ଭ \| ଶେଷ \| ପଦବୀ \| ନିର୍ବାଚନ ମଣ୍ଡଳୀ \| ଦଳ \| ⏎ \|---\|---\|---\|---\|---\| ⏎ \| 1971 \| 1974 \| ସଭ୍ୟ, 5ମ ଓଡ଼ିଶା ବିଧାନ… |
| 0.181 | 1,790 | 2019 ଭାରତୀୟ ସାଧାରଣ ନିର୍ବାଚନ (69377/7) | \| ନିର୍ବାଚନ ପର୍ଯ୍ୟାୟ \| ନିର୍ବାଚନ ତାରିଖ \| ଲୋକ ସଭା ନିର୍ବାଚନ ମଣ୍ଡଳୀ \| ⏎ \|---\|---\|---\| ⏎ \| ପ୍ରଥମ \| 11 ଅପ୍ରେଲ 2019 \|… |
| 0.205 | 1,617 | ଭି.ସୁଜ୍ଞାନୀ କୁମାରୀ ଦେଓ (59329/9) | \| ଆରମ୍ଭ \| ଶେଷ \| ପଦବୀ \| ନିର୍ବାଚନ ମଣ୍ଡଳୀ \| ଦଳ \| ⏎ \|---\|---\|---\|---\|---\| ⏎ \| 1961 \| 1967 \| ସଭ୍ୟ, 3ୟ ଓଡ଼ିଶା ବିଧାନ… |
| 0.208 | 1,281 | ପ୍ରସନ୍ନ କୁମାର ପଣ୍ଡା (78793/7) | \| ଆରମ୍ଭ \| ଶେଷ \| ପଦବୀ \| ନିର୍ବାଚନ ମଣ୍ଡଳୀ \| ଦଳ \| ⏎ \|---\|---\|---\|---\|---\| ⏎ \| 1961 \| 1967 \| ସଭ୍ୟ, 3ୟ ଓଡ଼ିଶା ବିଧାନ… |

**Highest-bpb articles (>= 500 B)**

| bpb | B | article (id) | bot | odia_ratio |
| ---: | ---: | --- | --- | ---: |
| 2.007 | 3,211 | କେଷ୍ଟୋ ମୁଖାର୍ଜୀ (55695) |  | 0.29 |
| 1.655 | 1,588 | ସାମରୋଜ ଆଜମି ଆଲଭୀ (98338) |  | 0.22 |
| 1.597 | 1,073 | "ଖ ଚମ୍ପୂ" - ଖରାପ ତୁ ହେଲୁ ରେ (98320) |  | 0.95 |
| 1.549 | 3,735 | ୟୁରୁ ୟୁରୁ ଦେ-ଓ (22752) |  | 0.54 |
| 1.503 | 3,083 | ଭାରତୀୟ ହ୍ରଦ ସମୂହର ତାଲିକା (56546) |  | 0.11 |
| 1.439 | 1,200 | ହିମାଚଳ ପ୍ରଦେଶ ରେ ହ୍ରଦ ସମୂହ ର ସାରଣୀ (56549) |  | 0.34 |
| 1.404 | 1,876 | "ଗ ଚମ୍ପୂ" - ଗଲାଣି ତ ଗଲା କଥାରେ ସଙ୍ଗାତ (98321) |  | 0.97 |
| 1.383 | 1,366 | ଗଉଣି (13537) |  | 0.86 |

**Lowest-bpb articles (>= 500 B)**

| bpb | B | article (id) | bot | odia_ratio |
| ---: | ---: | --- | --- | ---: |
| 0.264 | 7,291 | ଲୋକ ସଭା (33455) |  | 0.77 |
| 0.275 | 1,069 | ବିଜୁ ଜନତା ଦଳ (19026) |  | 0.98 |
| 0.289 | 1,028 | ବିଶ୍ୱ ସ୍ୱାସ୍ଥ୍ୟ ଦିବସ (80742) |  | 0.96 |
| 0.294 | 2,339 | ଦିଲ୍ଲୀପ କେ. ବିଶ୍ୱାସ (95453) |  | 0.98 |
| 0.296 | 1,433 | କର୍ଣ୍ଣାଟକ (3008) |  | 0.91 |
| 0.305 | 2,135 | ଗୋବିନ୍ଦ ଚନ୍ଦ୍ର ଦାସ (ରାଜନୀତିଜ୍ଞ) (70535) |  | 0.96 |
| 0.305 | 2,418 | ବିଶ୍ଵ ଉପଭୋକ୍ତା ଅଧିକାର ଦିବସ (80646) |  | 0.98 |
| 0.307 | 1,632 | ଆପଲ ଇନକର୍ପୋରେଟେଡ (7032) |  | 0.95 |

**Templated text, all kinds and lengths.** 10,278 paragraphs (2.2% of bytes) share their sentence frame (digits, the article's title and rare words masked) with 5 or more articles; they score 0.640 pooled against 0.551 for the rest.

## Files

- `annotations/bpb.jsonl`: one row per article (scores, extremes, the review queue); `annotations/bpb.paragraphs.jsonl`: one row per non-title paragraph. Their sidecars `bpb.json` and `bpb.paragraphs.json` describe every field, and `bpb.json` holds the record of every scoring run.
- `raw/bpb/scores.jsonl.gz`: the store, one row per paragraph text ever scored (`para_sha1`, `score_run`, `bytes`, `tokens`, `pieces`, `bits`), kept when a text leaves the corpus. It is the only copy of those fields: `bpb.paragraphs.jsonl` has each paragraph's `bpb`, and its sidecar's `joins` says that the rest is in the store row with the same `para_sha1`.
- Rerun: `uv run --script score_bpb.py build` (about 30 s, no GPU) rebuilds every output from the store and the run records; rerun it when the corpus or `annotations/topics.jsonl` or `translation.jsonl` change. It stops if a paragraph text has no score: run `score --only-missing` on a pod and add it with `build --add <dir>` (see the script's docstring), or build with `--allow-missing` (as `pipeline.py` does), which leaves such paragraphs unscored and says so here.
