#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Download Odia Wikipedia and turn every article into clean text for LLM training.

Steps, each resumable, all output under this directory:

  download  the latest complete orwiki pages-articles dump (XML) -> raw/, SHA-1 checked and
            indexed into raw/<wiki>-<date>-articles.jsonl (id, title, revid, timestamp, bot and
            stub flags of every article), with its provenance in raw/<wiki>-<date>-dump.json

Usage (uv reads the dependencies from the header above; nothing is installed in the repo):
    uv run prepare.py download            # or --dump 20260901
"""

import argparse
import bz2
import datetime
import gzip
import hashlib
import http.client
import json
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent  # the repository root: everything is written here
RAW = ROOT / "raw"

WIKI = "orwiki"
DUMPS = f"https://dumps.wikimedia.org/{WIKI}"
UA = "odia-wikipedia-sep26/0.1 (research: Odia LLM training corpus from Wikipedia dumps) python-urllib"

# Templates that mark a page in the wikitext (checked on the dump, before rendering).
BOT_TEMPLATES = ("ବଟ୍ ତିଆରି",)  # "created by a bot": year pages, town stubs
STUB_TEMPLATES = ("ମୁଣ୍ଡିଆ", "ଅଧାଗଢ଼ା", "ଅଧାଗଢା")


# ---------------------------------------------------------------------------------- http

NETWORK_ERRORS = (urllib.error.URLError, http.client.HTTPException, OSError)


def http_get(url, headers=None, tries=8):
    """GET with retries; honours Retry-After on 429/5xx. Returns (status, headers, body)."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip"}
                                 | (headers or {}))
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                body = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    body = gzip.decompress(body)
                return r.status, r.headers, body
        except urllib.error.HTTPError as e:
            if e.code in (403, 404, 410) or e.code < 429 or attempt == tries - 1:
                return e.code, e.headers, b""
            wait = int(e.headers.get("Retry-After") or 0) or 5 * 2 ** min(attempt, 5)
        except NETWORK_ERRORS as e:  # includes a response cut off mid-body (IncompleteRead)
            if attempt == tries - 1:
                raise
            wait = 5 * 2 ** min(attempt, 5)
            print(f"  {type(e).__name__} on {url[:90]}", file=sys.stderr)
        print(f"  retry {attempt + 1} in {wait}s: {url[:90]}", file=sys.stderr)
        time.sleep(wait)


# ------------------------------------------------------------------------------ download

def latest_dump():
    """Newest dump date whose pages-articles job has finished."""
    _, _, body = http_get(DUMPS + "/")
    dates = sorted(set(re.findall(r'href="(\d{8})/"', body.decode())), reverse=True)
    for d in dates:
        status, _, body = http_get(f"{DUMPS}/{d}/dumpstatus.json")
        if status == 200 and json.loads(body)["jobs"].get("articlesdump", {}).get("status") == "done":
            return d
    raise SystemExit("no finished dump found")


def dump_path(date):
    return RAW / f"{WIKI}-{date}-pages-articles.xml.bz2"


def provenance_path(date):
    """raw/<wiki>-<date>-dump.json: which dump the article index came from (name, URL, size,
    SHA-1)."""
    return RAW / f"{WIKI}-{date}-dump.json"


def find_date(date=None):
    """The dump date to work on: the given one, else the newest indexed one."""
    if date:
        return date
    found = sorted(RAW.glob(f"{WIKI}-*-articles.jsonl"))
    if not found:
        raise SystemExit("no article index in raw/; run `download` first")
    return found[-1].name.split("-")[1]


def download(args):
    date = args.dump or latest_dump()
    status, _, body = http_get(f"{DUMPS}/{date}/dumpstatus.json")
    if status != 200:
        raise SystemExit(f"no dump {date} (HTTP {status})")
    info = json.loads(body)["jobs"]["articlesdump"]["files"][f"{WIKI}-{date}-pages-articles.xml.bz2"]
    path = dump_path(date)
    RAW.mkdir(parents=True, exist_ok=True)
    part = path.with_suffix(".bz2.part")
    if not (path.exists() and path.stat().st_size == info["size"]):
        url = "https://dumps.wikimedia.org" + info["url"]
        print(f"downloading {url} ({info['size'] / 1e6:.1f} MB)", file=sys.stderr)
        # Resume with Range requests: the dump servers drop long transfers now and then.
        for _ in range(20):
            have = part.stat().st_size if part.exists() else 0
            if have >= info["size"]:
                break
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Range": f"bytes={have}-"})
            try:
                with urllib.request.urlopen(req, timeout=120) as r, open(part, "ab") as f:
                    while block := r.read(1 << 20):
                        f.write(block)
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
                print(f"  interrupted at {have / 1e6:.1f} MB ({e}); resuming", file=sys.stderr)
                time.sleep(5)
        part.replace(path)
    sha1 = hashlib.sha1(path.read_bytes()).hexdigest()
    if sha1 != info["sha1"]:
        path.rename(path.with_suffix(".bz2.bad"))
        raise SystemExit(f"SHA-1 mismatch for {path.name}: {sha1} != {info['sha1']}")
    print(f"{path.name}: {info['size']:,} bytes, SHA-1 ok", file=sys.stderr)
    write_index(date, path)
    prov = {"dump": path.name, "url": "https://dumps.wikimedia.org" + info["url"], "size": info["size"],
            "sha1": sha1, "date": date, "downloaded": datetime.date.today().isoformat(),
            "index": index_path(date).name}
    provenance_path(date).write_text(json.dumps(prov, indent=1) + "\n", encoding="utf-8")
    return date


def dump_articles(path):
    """[(page_id, title, revid, timestamp, flags)] for every main-namespace non-redirect page."""
    out = []
    for _, el in ET.iterparse(bz2.open(path), events=("end",)):
        if not el.tag.endswith("}page"):
            continue
        if el.findtext("{*}ns") == "0" and el.find("{*}redirect") is None:
            rev = el.find("{*}revision")
            text = rev.findtext("{*}text") or ""
            names = {m.strip().replace("_", " ") for m in re.findall(r"\{\{\s*([^|}\n]+)", text)}
            out.append({
                "id": int(el.findtext("{*}id")),
                "title": el.findtext("{*}title"),
                "revid": int(rev.findtext("{*}id")),
                "timestamp": rev.findtext("{*}timestamp"),
                "bot_created": any(t in names for t in BOT_TEMPLATES),
                "stub": any(t in names for t in STUB_TEMPLATES),
            })
        el.clear()
    return sorted(out, key=lambda a: a["id"])


def index_path(date):
    return RAW / f"{WIKI}-{date}-articles.jsonl"


def write_index(date, path):
    """raw/<wiki>-<date>-articles.jsonl: id, title, revid, timestamp and the bot/stub flags of every
    article (main namespace, not a redirect) in the dump. This is all the build needs from it."""
    ipath = index_path(date)
    if ipath.exists():
        return
    print(f"indexing {path.name}", file=sys.stderr)
    arts = dump_articles(path)
    tmp = ipath.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(a, ensure_ascii=False) + "\n" for a in arts), encoding="utf-8")
    tmp.replace(ipath)


def load_index(date):
    ipath = index_path(date)
    if not ipath.exists():
        raise SystemExit(f"no {ipath.name}; run `download` first")
    return [json.loads(line) for line in ipath.read_text(encoding="utf-8").splitlines()]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("step", choices=["download"])
    ap.add_argument("--dump", help="dump date YYYYMMDD (default: latest)")
    args = ap.parse_args()
    if args.step == "download":
        args.dump = download(args)


if __name__ == "__main__":
    main()
