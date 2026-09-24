#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""One command from the cached inputs to the training-ready Odia Wikipedia dataset.

  uv run pipeline.py                  # build, annotate, score (carried over), check
  uv run pipeline.py --min-chars 200  # options of `prepare.py build` pass through
  ODIA_WIKI_CONTACT=you@example.org uv run pipeline.py --fetch   # from scratch

Steps (each is its own script, runnable alone):
  0. with --fetch: prepare.py download + render (the dump's article index, then the HTML of every
     article at its dump revision), annotate.py download + wikidata (category, tag and history facts)
  1. prepare.py build      HTML -> orwiki-<date>.jsonl (the corpus: cleaned Markdown with the
                           English translations folded in and reviewer decisions applied),
                           excluded.jsonl, removed-blocks.jsonl, the build stats, README.md
  2. annotate.py topics, translation   -> annotations/topics.jsonl, translation.jsonl
  3. score_bpb.py build    Sarvam-1 bits per byte per paragraph, carried over by text hash from
                           raw/bpb/; stops if a paragraph text has never been scored (that needs a
                           GPU pod: see score_bpb.py) -> annotations/bpb*.jsonl, quality/review-first.md
  4. check.py              Markdown structure, consistency with the dump index, residue scan

Every output is JSON lines, JSON or Markdown. The script exits non-zero at the first failing step.
"""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def run(name, *args):
    cmd = ["uv", "run", "--quiet", "--script", str(ROOT / name), *args]
    print(f"\n== {name} {' '.join(args)}", file=sys.stderr, flush=True)
    t = time.time()
    code = subprocess.run(cmd, stdin=subprocess.DEVNULL).returncode
    print(f"   {name} {args[0] if args else ''}: {'ok' if code == 0 else f'FAILED (exit {code})'} "
          f"in {time.time() - t:.0f} s", file=sys.stderr, flush=True)
    return code


def summary():
    build = max(ROOT.glob("orwiki-*-build.json"), key=lambda p: p.stat().st_mtime)
    stats = json.loads(build.read_text(encoding="utf-8"))
    corpus = build.with_name(build.name.replace("-build.json", ".jsonl"))
    print(f"\n{corpus.name}: {stats['articles']:,} articles, {stats['words']:,} Odia words, "
          f"{stats['utf8_bytes'] / 1e6:.0f} MB; {stats['excluded']:,} pages in excluded.jsonl")
    for path in [corpus, ROOT / "excluded.jsonl", ROOT / "removed-blocks.jsonl", build,
                 *sorted((ROOT / "annotations").glob("*.jsonl"))]:
        if path.exists():
            print(f"  {path.relative_to(ROOT)}  {path.stat().st_size / 1e6:.1f} MB")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 epilog="Other options are passed to `prepare.py build`.")
    ap.add_argument("--fetch", action="store_true", help="download and render first (network, hours)")
    ap.add_argument("--skip-bpb", action="store_true", help="skip the Sarvam-1 scores (step 3)")
    args, build_args = ap.parse_known_args()
    steps = []
    if args.fetch:
        if not os.environ.get("ODIA_WIKI_CONTACT"):
            sys.exit("--fetch needs ODIA_WIKI_CONTACT (an email or URL for the user-agent)")
        steps += [("prepare.py", "download"), ("prepare.py", "render"),
                  ("annotate.py", "download"), ("annotate.py", "wikidata")]
    steps += [("prepare.py", "build", *build_args), ("annotate.py", "topics"), ("annotate.py", "translation")]
    if not args.skip_bpb:
        steps.append(("score_bpb.py", "build"))
    steps.append(("check.py",))
    for step in steps:
        if code := run(*step):
            if step[0] == "score_bpb.py":
                print("   new paragraph texts need scores from a GPU pod (score_bpb.py `score "
                      "--only-missing`), or re-run with --skip-bpb", file=sys.stderr)
            sys.exit(code)
    summary()


if __name__ == "__main__":
    main()
