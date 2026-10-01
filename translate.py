#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Translation rounds for the English prose left in Odia Wikipedia articles.

`prepare.py build` replaces an English-dominant paragraph or heading with its Odia translation from
translations/english-to-odia.jsonl (keyed by the sha1 of the English block). A block without one is
kept aside in removed-blocks.jsonl with reason "awaiting translation". A round:

  uv run translate.py batches   # awaiting items -> translations/work/in-*.jsonl
  (translators write translations/work/out-*.jsonl: {"sha1", "odia", "keep_as_is", "notes"})
  uv run translate.py merge --by "<who, when>"   # check, then add to the table
  uv run translate.py mark --drop <sha1-prefix>… --reason "…"   # junk, not content
  uv run prepare.py build        # the translations go into the articles

Paragraphs a reviewer marked *fix* (English lists, tables or passages to translate) go the same way,
into curation/paragraph-fixes.jsonl, keyed by the sha1 of the paragraph as the corpus has it:

  uv run translate.py fixes     # paragraphs named by *fix* reviews -> translations/work/fix-in-*.jsonl
  (translators write translations/work/fix-out-*.jsonl: {"sha1", "text", "notes"})
  uv run translate.py merge-fixes --by "<who, when>"   # check, then add to the fixes

`merge-fixes` also checks the shape: the same lines, list markers and indentation, table cells and
table rules as the source, and no blank line inside.

`merge` refuses a batch with a missing or extra sha1. It records per-item checks and prints every
item that fails one:
  - digits: every digit sequence of the source appears in the translation (ASCII digits)
  - script: no Bengali or Devanagari letters (LLM translators drift into neighbouring scripts)
  - odia share: Odia letters are at least half of the letters (scientific names stay Latin)
  - length: Odia characters per English character between 0.5 and 2.5
  - spelling: no ଯ + nukta (the corpus writes ୟ)
"""

import argparse
import datetime
import hashlib
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TABLE = ROOT / "translations" / "english-to-odia.jsonl"
WORK = ROOT / "translations" / "work"
REMOVED = ROOT / "removed-blocks.jsonl"
FIXES = ROOT / "curation" / "paragraph-fixes.jsonl"
REVIEWS = ROOT / "reviews" / "reviews.jsonl"
DATASET = "odia-wikipedia"

ODIA = re.compile(r"[\u0B00-\u0B65\u0B70-\u0B7F]")
LATIN = re.compile(r"[A-Za-z]")
OTHER_SCRIPT = re.compile(r"[\u0980-\u09FF\u0900-\u0963\u0966-\u097F]")
UNESCAPE = re.compile(r"\\([!-/:-@\[-`{-~])")


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path, rows):
    tmp = path.with_name(path.name + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def checks(source, odia):
    """Automatic checks of one translation; each value True means it passes."""
    plain = UNESCAPE.sub(r"\1", source)
    letters_o, letters_l = len(ODIA.findall(odia)), len(LATIN.findall(odia))
    ratio = len(odia) / max(len(plain), 1)
    return {
        "digits": set(re.findall(r"\d+", plain)) <= set(re.findall(r"\d+", odia)),
        "script": not OTHER_SCRIPT.search(odia),
        "odia_share": letters_o >= 0.5 * (letters_o + letters_l),
        "length": 0.5 <= ratio <= 2.5,
        "spelling": "\u0b2f\u0b3c" not in odia,
    }


CELL = re.compile(r"(?<!\\)\|")
TABLE_RULE = re.compile(r"\|(?:\s*:?-+:?\s*\|)+")
LINE_PREFIX = re.compile(r"\s*(?:- |\d+\. )?")


def shape(source, text):
    """True when text has the Markdown shape of source: the same lines, each with the same
    indentation and list marker, the same number of table cells, the same table rules, and no
    blank line inside (a paragraph stays one paragraph)."""
    s, t = source.split("\n"), text.split("\n")
    if "\n\n" in text.strip() or len(s) != len(t):
        return False
    for a, b in zip(s, t):
        if a.startswith("|") or b.startswith("|"):
            if len(CELL.findall(a)) != len(CELL.findall(b)) or (TABLE_RULE.fullmatch(a.strip()) and a != b):
                return False
        elif LINE_PREFIX.match(a).group() != LINE_PREFIX.match(b).group():
            return False
    return True


def fixes(args):
    """Work items for the paragraphs that reviewers marked *fix*: the paragraph named in the review's
    note ("para N"), as the corpus has it now. Paragraphs already fixed are skipped."""
    latest = {}
    for e in read_jsonl(REVIEWS):
        if e.get("dataset") == DATASET:
            latest[int(e["id"])] = e
    todo = {i: e for i, e in latest.items() if e.get("verdict") == "fix"}
    done = {f["sha1"] for f in read_jsonl(FIXES)} if FIXES.exists() else set()
    corpus = max(ROOT.glob("*-trainingready.jsonl"), key=lambda p: p.stat().st_mtime)
    items, skipped = [], []
    for line in open(corpus, encoding="utf-8"):
        r = json.loads(line)
        if (e := todo.get(r["id"])) is None:
            continue
        m = re.search(r"para (\d+)", e.get("note") or "")
        paras = r["text"].split("\n\n")
        if not m or int(m.group(1)) >= len(paras):
            skipped.append((r["id"], r["title"], "no paragraph named in the note"))
            continue
        q = paras[int(m.group(1))]
        h = hashlib.sha1(q.encode()).hexdigest()
        if h in done:
            continue
        kind = "table" if q.startswith("|") else "list" if LINE_PREFIX.match(q).group().strip() else "paragraph"
        items.append({"sha1": h, "id": r["id"], "title": r["title"], "para": int(m.group(1)), "kind": kind,
                      "note": e["note"], "source": q})
    WORK.mkdir(parents=True, exist_ok=True)
    size = args.size
    for i in range(0, len(items), size):
        write_jsonl(WORK / f"fix-in-{i // size + 1:02d}.jsonl", items[i:i + size])
    print(f"{len(items)} paragraphs to fix -> {-(-len(items) // size)} batches in {WORK}")
    for s in skipped:
        print("   skipped (fix it by hand):", s)


def merge_fixes(args):
    table = {f["sha1"]: f for f in read_jsonl(FIXES)} if FIXES.exists() else {}
    added, failed = 0, []
    for out in sorted(WORK.glob("fix-out-*.jsonl")):
        src = WORK / out.name.replace("fix-out-", "fix-in-")
        inputs, outputs = read_jsonl(src), read_jsonl(out)
        if [r["sha1"] for r in inputs] != [r["sha1"] for r in outputs]:
            sys.exit(f"{out.name}: sha1s don't match {src.name} (missing, extra or reordered items)")
        for i, o in zip(inputs, outputs, strict=True):
            text = (o.get("text") or "").strip("\n")
            row = {"sha1": i["sha1"], "id": i["id"], "title": i["title"], "para": i["para"], "kind": "translation",
                   "source": i["source"], "text": text, "checks": {**checks(i["source"], text), "shape": shape(i["source"], text)},
                   "notes": o.get("notes") or "", "review_note": i["note"], "by": args.by}
            bad = [k for k, v in row["checks"].items() if not v]
            if bad:
                failed.append((out.name, i["sha1"][:10], i["title"], bad))
            added += i["sha1"] not in table
            table[i["sha1"]] = row
    FIXES.parent.mkdir(parents=True, exist_ok=True)
    write_jsonl(FIXES, sorted(table.values(), key=lambda r: (r["id"], r["para"])))
    print(f"{FIXES.relative_to(ROOT)}: {len(table)} fixes ({added} new); items failing a check: {len(failed)}")
    for f in failed:
        print("  ", f)


def batches(args):
    have = {t["sha1"] for t in read_jsonl(TABLE)} if TABLE.exists() else set()
    todo = {}
    for r in read_jsonl(REMOVED):
        if r.get("reason") != "awaiting translation":
            continue
        h = hashlib.sha1(r["text"].encode()).hexdigest()
        if h not in have:
            todo.setdefault(h, {"sha1": h, "kind": r["kind"], "title": r["title"], "source": r["text"]})
    WORK.mkdir(parents=True, exist_ok=True)
    items = sorted(todo.values(), key=lambda r: (r["kind"], r["title"]))
    size = args.size
    for i in range(0, len(items), size):
        write_jsonl(WORK / f"in-{i // size + 1:02d}.jsonl", items[i:i + size])
    print(f"{len(items)} items awaiting translation -> {-(-len(items) // size)} batches in {WORK}")


def merge(args):
    table = {t["sha1"]: t for t in read_jsonl(TABLE)} if TABLE.exists() else {}
    added, failed = 0, []
    for out in sorted(WORK.glob("out-*.jsonl")):
        src = WORK / out.name.replace("out-", "in-")
        inputs, outputs = read_jsonl(src), read_jsonl(out)
        if [r["sha1"] for r in inputs] != [r["sha1"] for r in outputs]:
            sys.exit(f"{out.name}: sha1s don't match {src.name} (missing, extra or reordered items)")
        for i, o in zip(inputs, outputs, strict=True):
            row = {"sha1": i["sha1"], "kind": i["kind"], "title": i["title"], "source": i["source"],
                   "odia": o.get("odia"), "keep_as_is": bool(o.get("keep_as_is")) and not o.get("odia"),
                   "notes": o.get("notes") or "", "by": args.by}
            if row["odia"]:
                row["checks"] = checks(i["source"], row["odia"])
                bad = [k for k, v in row["checks"].items() if not v]
                if bad:
                    failed.append((out.name, i["sha1"][:10], bad, i["source"][:70], row["odia"][:70]))
            added += i["sha1"] not in table
            table[i["sha1"]] = row
    TABLE.parent.mkdir(parents=True, exist_ok=True)
    write_jsonl(TABLE, sorted(table.values(), key=lambda r: (r["kind"], r["title"], r["sha1"])))
    n = len(table)
    print(f"{TABLE.relative_to(ROOT)}: {n} translations ({added} new); "
          f"{sum(1 for t in table.values() if t['keep_as_is'])} kept as is")
    print(f"items failing a check: {len(failed)}")
    for f in failed:
        print("  ", f)


def mark(args):
    """Mark translation-table entries (by sha1 prefix) as junk to drop from the articles."""
    table = read_jsonl(TABLE)
    hits = 0
    for t in table:
        if any(t["sha1"].startswith(p) for p in args.drop):
            t["drop"], t["drop_reason"] = True, args.reason
            hits += 1
    write_jsonl(TABLE, table)
    print(f"marked {hits} entries to drop ({args.reason})")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    k = sub.add_parser("mark", help="mark entries as junk to drop (vandalism, leaked template text)")
    k.add_argument("--drop", nargs="+", required=True, metavar="SHA1_PREFIX")
    k.add_argument("--reason", required=True)
    b = sub.add_parser("batches")
    b.add_argument("--size", type=int, default=70)
    m = sub.add_parser("merge")
    m.add_argument("--by", default=f"LLM translation, {datetime.date.today().isoformat()}")
    f = sub.add_parser("fixes", help="work items for the paragraphs reviewers marked fix")
    f.add_argument("--size", type=int, default=6)
    mf = sub.add_parser("merge-fixes", help="check fixed paragraphs, then add them to curation/paragraph-fixes.jsonl")
    mf.add_argument("--by", default=f"LLM translation, {datetime.date.today().isoformat()}")
    args = ap.parse_args()
    {"batches": batches, "merge": merge, "mark": mark, "fixes": fixes, "merge-fixes": merge_fixes}[args.cmd](args)


if __name__ == "__main__":
    os.chdir(ROOT)
    main()
