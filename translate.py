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
    args = ap.parse_args()
    {"batches": batches, "merge": merge, "mark": mark}[args.cmd](args)


if __name__ == "__main__":
    os.chdir(ROOT)
    main()
