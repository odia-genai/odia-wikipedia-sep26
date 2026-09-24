#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["markdown-it-py>=3"]
# ///
"""Check the built corpus: valid Markdown, and no leftovers the cleaning rules should have removed.

  uv run check.py              # all checks; exit code 1 if any fails
  uv run check.py --pandoc 0   # skip the pandoc cross-check

1. Markdown structure. Every article is parsed as CommonMark + GFM tables (markdown-it). The
   parse must give exactly the headings, list items and tables the text is meant to have, and no
   accidental emphasis, links, code, HTML, block quotes, rules or setext headings. Math is masked
   first, as a math-aware renderer (GitHub, pandoc) would treat it.
2. pandoc cross-check. A random sample, plus hard cases, is read with pandoc's GFM reader
   (`gfm+tex_math_dollars`); same expectations.
3. Consistency. Every page of the article index (raw/<wiki>-<date>-articles.jsonl) is in exactly
   one of the corpus and excluded.jsonl, at the index's revision; no page twice; every block in
   removed-blocks.jsonl belongs to a kept article, or to one excluded as mostly English; titles
   use ASCII digits.
4. Residue scan. Patterns the cleaning rules remove must be gone: wikitext outside math, file
   and image options, URLs, category links, Content Translation markup, HTML entities, Odia
   digits (the text uses ASCII digits), the unassigned danda U+0B64/65, ଯ + nukta (written ୟ),
   control characters.
"""

import argparse
import collections
import json
import random
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MATH = re.compile(r"\$\$[^$]+\$\$|(?<!\\)\$[^$\n]+?(?<!\\)\$")
UNEXPECTED = {"html_block", "html_inline", "code_block", "fence", "hr", "blockquote_open", "link_open",
              "image", "em_open", "strong_open", "s_open", "code_inline"}
PANDOC_BAD = {"RawInline", "RawBlock", "CodeBlock", "Code", "Emph", "Strong", "Link", "Image",
              "BlockQuote", "HorizontalRule", "Strikeout"}
HARD_CASES = ("ଆର୍ଯ୍ୟଭଟ୍ଟ", "ଡାଇନୋସର ଶ୍ରେଣୀବିଭାଗ", "ମ୍ୟାଟ୍‌ଲାବ୍‌", "କମ୍ପ୍ୟୁଟର", "ଓଡ଼ିଶାର ଜିଲ୍ଲାମାନଙ୍କର ତାଲିକା")
RESIDUE = {  # name -> pattern that must not occur (math masked for the wikitext one)
    "wikitext outside math": r"\{\{|\}\}|\[\[|\]\]|(?<![=<>!])={2,6}[^=\n|]{1,80}={2,6}(?!=)",
    "file/image options": r"\|\s*thumb|\.jpe?g\s*\||(?<![\w.])\d{1,4}px\b",
    "URL": r"https?://|www\.",
    "category link": r"(?m)Category:|ଶ୍ରେଣୀ:(?:\S|$)",
    "Content Translation markup": r"cx-link|data-linkid|data-cx=|mw-redirect",
    "HTML entity": r"&(?:amp|lt|gt|quot|nbsp|#1[03]);",
    "template error message": r"Error: (?:\{\{|Lang|This is not a valid|Transliteration)",
    "adjacent inline math": r"(?<![$\\])\$[^$\n]+\$\$[^$\n]+\$(?!\$)",  # $a$$b$ reads as display math
    "Odia digit": "[୦-୯]",
    "unassigned danda U+0B64/65": "[୤୥]",
    "ଯ + nukta": "ଯ଼",
    "control character": r"[\x00-\x08\x0b-\x1f\x7f-\x9f]",
}


def expected(text):
    lines = text.split("\n")
    return {
        "headings": sum(bool(re.match(r"#{1,6} ", line)) for line in lines),
        "tables": sum(bool(re.fullmatch(r"\|(?:---\|)+", line)) for line in lines),
        "items": sum(bool(re.match(r"\s*(?:- |[0-9]+\. )", line)) for line in lines if not line.startswith("|")),
    }


def check_markdown(recs):
    from markdown_it import MarkdownIt

    md = MarkdownIt("commonmark", {"maxNesting": 100}).enable(["table", "strikethrough"])
    bad = collections.defaultdict(list)
    for r in recs:
        text = MATH.sub("MATH", r["text"])
        toks = md.parse(text)
        kinds = collections.Counter(t.type for t in toks)
        inline = collections.Counter(c.type for t in toks if t.type == "inline" for c in (t.children or []))
        want = expected(text)
        if kinds["heading_open"] != want["headings"]:
            bad["heading count"].append(r["title"])
        if kinds["table_open"] != want["tables"]:
            bad["table count"].append(r["title"])
        if kinds["list_item_open"] != want["items"]:
            bad["list item count"].append(r["title"])
        if any(t.type == "heading_open" and t.markup in ("=", "-") for t in toks):
            bad["setext heading"].append(r["title"])
        for k in UNEXPECTED:
            if kinds[k] or inline[k]:
                bad[k].append(r["title"])
    return bad


def check_pandoc(recs, n):
    if not n or not shutil.which("pandoc"):
        return None
    random.seed(11)
    sample = random.sample(recs, min(n, len(recs))) + [r for r in recs if r["title"] in HARD_CASES]
    bad = collections.defaultdict(list)

    def walk(x, c):
        if isinstance(x, dict):
            if "t" in x:
                c[x["t"]] += 1
            for v in x.values():
                walk(v, c)
        elif isinstance(x, list):
            for v in x:
                walk(v, c)

    for r in sample:
        out = subprocess.run(["pandoc", "-f", "gfm+tex_math_dollars", "-t", "json"], input=r["text"],
                             capture_output=True, text=True, check=True).stdout
        c = collections.Counter()
        walk(json.loads(out)["blocks"], c)
        for k in PANDOC_BAD & set(c):
            bad[k].append(r["title"])
        if c["Header"] != expected(r["text"])["headings"]:
            bad["header count"].append(r["title"])
        if c["Table"] != r["tables"]:
            bad["table count"].append(r["title"])
    return len(sample), bad


def check_consistency(recs, corpus):
    """{problem: [example ids]} for the corpus, excluded.jsonl and removed-blocks.jsonl against the index."""
    wiki, date = re.match(r"(\w+)-(\d{8})", corpus.name).groups()
    index_file = ROOT / "raw" / f"{wiki}-{date}-articles.jsonl"
    if not index_file.exists():
        return {f"no article index {index_file.name} for {date}": [None]}
    index = {a["id"]: a for a in map(json.loads, open(index_file, encoding="utf-8"))}
    excluded = [json.loads(line) for line in open(ROOT / "excluded.jsonl", encoding="utf-8")]
    blocks = [json.loads(line) for line in open(ROOT / "removed-blocks.jsonl", encoding="utf-8")]
    bad = collections.defaultdict(list)
    kept_ids = collections.Counter(r["id"] for r in recs)
    out_ids = collections.Counter(e["id"] for e in excluded)
    bad["page twice in the corpus"] = [i for i, n in kept_ids.items() if n > 1]
    bad["page twice in excluded.jsonl"] = [i for i, n in out_ids.items() if n > 1]
    bad["page in both corpus and excluded.jsonl"] = sorted(set(kept_ids) & set(out_ids))
    bad["index page in neither"] = sorted(set(index) - set(kept_ids) - set(out_ids))
    bad["page not in the index"] = sorted((set(kept_ids) | set(out_ids)) - set(index))
    bad["revision differs from the index"] = [r["id"] for r in recs + excluded
                                              if r["id"] in index and r["revid"] != index[r["id"]]["revid"]]
    mostly_english = {e["id"] for e in excluded if e["reason"] == "mostly English"}
    bad["removed block of an excluded page"] = sorted({b["id"] for b in blocks if b["id"] not in kept_ids
                                                      and b["id"] not in mostly_english})
    bad["Odia digit in a title"] = [r["id"] for r in recs + excluded + blocks if re.search("[୦-୯]", r["title"])]
    return {k: v for k, v in bad.items() if v}


def check_residue(recs):
    hits = collections.defaultdict(list)
    for r in recs:
        masked = MATH.sub("MATH", r["text"])
        for name, pattern in RESIDUE.items():
            text = masked if name == "wikitext outside math" else r["text"]
            for m in re.finditer(pattern, text):
                hits[name].append((r["title"], text[max(0, m.start() - 50):m.end() + 40].replace("\n", "⏎")))
    return hits


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--corpus", type=Path, help="corpus JSONL (default: the newest *-trainingready.jsonl here)")
    ap.add_argument("--pandoc", type=int, default=400, help="articles for the pandoc cross-check (0: skip)")
    args = ap.parse_args()
    corpus = args.corpus or max(ROOT.glob("*-trainingready.jsonl"), key=lambda p: p.stat().st_mtime)
    recs = [json.loads(line) for line in open(corpus, encoding="utf-8")]
    failed = False

    bad = check_markdown(recs)
    print(f"markdown: {len(recs):,} articles parsed; " + ("all as intended" if not bad else "PROBLEMS"))
    for k, v in bad.items():
        failed = True
        print(f"  {k}: {len(v)} e.g. {v[:4]}")

    res = check_pandoc(recs, args.pandoc)
    if res is None:
        print("pandoc: skipped")
    else:
        n, pbad = res
        print(f"pandoc: {n} articles; " + ("no problems" if not pbad else "PROBLEMS"))
        for k, v in pbad.items():
            failed = True
            print(f"  {k}: {len(v)} e.g. {v[:4]}")

    cbad = check_consistency(recs, corpus)
    print("consistency: " + ("every indexed page is in the corpus or excluded.jsonl, once" if not cbad
                             else "PROBLEMS"))
    for k, v in cbad.items():
        failed = True
        print(f"  {k}: {len(v)} e.g. ids {v[:6]}")

    hits = check_residue(recs)
    print("residue: " + ("none" if not hits else "FOUND"))
    for name in RESIDUE:
        if hits.get(name):
            failed = True
            print(f"  {name}: {len(hits[name])} in {len({t for t, _ in hits[name]})} articles, "
                  f"e.g. {hits[name][0][0]}: …{hits[name][0][1]}…")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
