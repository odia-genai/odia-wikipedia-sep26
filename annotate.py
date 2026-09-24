#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Annotate the Odia Wikipedia corpus: Content Translation flags.

Steps, each re-runnable from cached inputs, all output under this directory:

  download     dump files -> raw/ (from the ACC mirror, else dumps.wikimedia.org), SHA-1 checked
               against dumps.wikimedia.org's dumpstatus.json.
  translation  change tags -> Content Translation flags -> annotations/translation.jsonl
               (+ .json), quality/translation.md

Outputs are JSON lines, one object per article of the corpus (orwiki-<date>.jsonl, read at run
time), in corpus order. Never NFC.

Usage (uv reads the dependencies from the header above; nothing is installed in the repo):
    ODIA_WIKI_CONTACT=you@example.org uv run annotate.py download
    uv run annotate.py translation

`download` sends ODIA_WIKI_CONTACT (an email or URL, never stored in a file) in the
user-agent: Wikimedia throttles bulk clients without contact details.
"""

import argparse
import collections
import datetime
import functools
import gzip
import hashlib
import http.client
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent  # the repository root: everything is written here
RAW = ROOT / "raw"
HTML_DIR = RAW / "html"
ANN = ROOT / "annotations"
QUALITY = ROOT / "quality"

# The Odia text rules (odia_text.py, a copy of odia-llm-trainer's odia_llm.text), never NFC.
# Importing must not leave __pycache__ here.
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT))
from odia_text import odia_words  # noqa: E402

WIKI = "orwiki"
DATE = "20260901"
DUMPS = f"https://dumps.wikimedia.org/{WIKI}"
CONTACT = os.environ.get("ODIA_WIKI_CONTACT", "").strip()
UA = ("odia-wikipedia-sep26/0.1 (research: annotating an Odia LLM training corpus from Wikipedia dumps"
      + (f"; {CONTACT}" if CONTACT else "") + ") python-urllib")

CORPUS = ROOT / f"{WIKI}-{DATE}.jsonl"  # the built corpus (prepare.py build): which articles get rows
INDEX = RAW / f"{WIKI}-{DATE}-articles.jsonl"  # every dump article (prepare.py download): id, title, revid

# Dump files this script reads (job name in dumpstatus.json, file suffix).
DUMP_FILES = [
    ("changetagdeftable", "change_tag_def.sql.gz"),  # tag names and how often each was applied
    ("usergroupstable", "user_groups.sql.gz"),  # bot accounts, now ...
    ("userformergroupstable", "user_former_groups.sql.gz"),  # ... and in the past
    ("xmlstubsdump", "stub-meta-history.xml.gz"),  # every revision's page, time, user, summary, size
    ("changetagstable", "change_tag.sql.gz"),  # tags on revisions
]
MAX_DOWNLOAD = 1_000_000_000  # stop rather than pull anything over ~1 GB onto the laptop
# Where dump files come from, in order. dumps.wikimedia.org served 2-3 kB/s on 2026-09-24, so the
# official mirror at ACC Umea goes first (~400 kB/s). Checksums always come from
# dumps.wikimedia.org's own dumpstatus.json, so a mirror cannot change what is read.
SOURCES = ["https://mirror.accum.se/mirror/wikimedia.org/dumps/", "https://dumps.wikimedia.org/"]
MIN_RATE = 20_000  # bytes/s; slower than this for a minute counts as a stall


# ---------------------------------------------------------------------------------- io

def replace_atomic(path, write):
    """Call write(tmp) on a temp file next to path, then move it over path."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    write(tmp)
    os.replace(tmp, path)


def write_atomic(path, data):
    """Write bytes or text to path through a temp file in the same directory."""
    if isinstance(data, str):
        replace_atomic(path, lambda tmp: tmp.write_text(data, encoding="utf-8"))
    else:
        replace_atomic(path, lambda tmp: tmp.write_bytes(data))


def write_jsonl_atomic(path, rows):
    """One JSON object per line, UTF-8 (Odia stays readable), through a temp file."""
    def write(tmp):
        with open(tmp, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    replace_atomic(path, write)


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

def dump_file(suffix):
    return RAW / f"{WIKI}-{DATE}-{suffix}"


def dumpstatus():
    """The dump's dumpstatus.json (sizes and checksums of every file), cached in raw/."""
    path = RAW / f"{WIKI}-{DATE}-dumpstatus.json"
    if not path.exists():
        status, _, body = http_get(f"{DUMPS}/{DATE}/dumpstatus.json")
        if status != 200:
            raise SystemExit(f"no dumpstatus.json for {DATE} (HTTP {status})")
        write_atomic(path, body)
    return json.loads(path.read_text(encoding="utf-8"))


def sha1_of(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        while block := f.read(1 << 20):
            h.update(block)
    return h.hexdigest()


def fetch_dump_file(job, suffix, status):
    name = f"{WIKI}-{DATE}-{suffix}"
    info = status["jobs"][job]
    if info.get("status") != "done":
        raise SystemExit(f"dump job {job} is {info.get('status')}, not done")
    info = info["files"][name]
    if info["size"] > MAX_DOWNLOAD:
        raise SystemExit(f"{name} is {info['size'] / 1e9:.1f} GB; over the laptop limit, stopping")
    path = dump_file(suffix)
    ok = path.exists() and path.stat().st_size == info["size"] and sha1_of(path) == info["sha1"]
    if not ok:
        part = path.with_name(path.name + ".part")
        # Resume with Range requests: the dump servers drop long transfers now and then. A
        # source that stalls three times in a row is abandoned for the next one.
        for source in SOURCES:
            url = source + info["url"].removeprefix("/")
            print(f"downloading {url} ({info['size'] / 1e6:.1f} MB)", file=sys.stderr)
            stalls = 0
            while stalls < 3:
                have = part.stat().st_size if part.exists() else 0
                if have >= info["size"]:
                    break
                req = urllib.request.Request(url, headers={"User-Agent": UA, "Range": f"bytes={have}-"})
                t0 = time.time()
                try:
                    with urllib.request.urlopen(req, timeout=60) as r, open(part, "ab") as f:
                        while block := r.read(1 << 16):
                            f.write(block)
                            if time.time() - t0 > 60 and f.tell() - have < 60 * MIN_RATE:
                                raise TimeoutError(f"under {MIN_RATE // 1000} kB/s")
                except (*NETWORK_ERRORS, TimeoutError) as e:
                    now = part.stat().st_size if part.exists() else 0
                    stalls = stalls + 1 if now - have < 60 * MIN_RATE else 0
                    print(f"  interrupted at {now / 1e6:.1f} MB ({e}); resuming", file=sys.stderr)
                    time.sleep(5)
            if part.exists() and part.stat().st_size >= info["size"]:
                break
        else:
            raise SystemExit(f"could not download {name} from any of {SOURCES}")
        sha1 = sha1_of(part)
        if sha1 != info["sha1"]:
            part.rename(path.with_name(path.name + ".bad"))
            raise SystemExit(f"SHA-1 mismatch for {name}: {sha1} != {info['sha1']}")
        os.replace(part, path)
    print(f"{name}: {info['size']:,} bytes, SHA-1 ok", file=sys.stderr)
    return path


def download(args):
    RAW.mkdir(parents=True, exist_ok=True)
    status = dumpstatus()
    for job, suffix in DUMP_FILES:
        fetch_dump_file(job, suffix, status)


# ------------------------------------------------------------------------- article facts

# Revisions kept in full in the facts: any tag of Content Translation, or an edit summary
# about translating (CX's and MDWiki's "Created by translating ...", and hand-written ones).
TRANSLATION_SUMMARY = re.compile(r"(?i)translat")
CX_TAG_PREFIXES = ("contenttranslation", "sectiontranslation")


def revision_tags():
    """{revision id: [tag name, ...]} of every tagged revision, from change_tag and change_tag_def."""
    names = {r["ctd_id"]: r["ctd_name"] for r in sql_rows(dump_file("change_tag_def.sql.gz"))}
    out = collections.defaultdict(set)
    for r in sql_rows(dump_file("change_tag.sql.gz")):
        if r["ct_rev_id"] is not None:
            out[r["ct_rev_id"]].add(names.get(r["ct_tag_id"], f"tag {r['ct_tag_id']}"))
    return {rev: sorted(t) for rev, t in out.items()}


def page_facts(pid, revs, tags):
    """The facts of one article from its revisions (history()) and the change tags."""
    revs = sorted(revs, key=lambda r: (r["ts"], r["id"]))
    by_id = {r["id"]: r for r in revs}
    editors = {}
    for r in revs[1:]:
        editors[r["user"], r["uid"]] = editors.get((r["user"], r["uid"]), 0) + 1
    kept = []
    for i, r in enumerate(revs):
        t = tags.get(r["id"], [])
        if TRANSLATION_SUMMARY.search(r["comment"]) or any(x.startswith(CX_TAG_PREFIXES) for x in t):
            prev = by_id.get(r["parent"]) or (revs[i - 1] if i else None)
            kept.append({"rev": r["id"], "parent": r["parent"], "ts": r["ts"], "bytes": r["bytes"],
                         "prev_bytes": prev["bytes"] if prev else 0, "tags": t, "comment": r["comment"]})
    first, last = revs[0], revs[-1]
    return {"id": pid, "revisions": len(revs),
            "first": {"rev": first["id"], "parent": first["parent"], "ts": first["ts"], "user": first["user"],
                      "uid": first["uid"]},
            "last": {"rev": last["id"], "ts": last["ts"], "bytes": last["bytes"]},
            "later_editors": [[u, uid, n] for (u, uid), n in editors.items()],
            "translation_revs": kept}


@functools.cache
def load_facts():
    """{page id: facts} of every dump article, from the revision history and the change tags."""
    ids = {a["id"] for a in load_index()}
    tags = revision_tags()
    print("streaming the revision history", file=sys.stderr)
    return {pid: page_facts(pid, revs, tags) for pid, revs in history(ids)}


# ----------------------------------------------------------------------------- sql dumps

# One token of a MySQL INSERT ... VALUES list: a quoted string, NULL, a number, or ( ) ,
SQL_TOKEN = re.compile(r"'((?:[^'\\]|\\.)*)'|(NULL)|(-?[0-9][0-9.eE+-]*)|([(),])", re.S)
SQL_UNESCAPE = re.compile(r"\\(.)", re.S)
SQL_ESCAPES = {"0": "\0", "n": "\n", "r": "\r", "t": "\t", "b": "\b", "Z": "\x1a"}


def sql_columns(path):
    """Column names of the table in a mysqldump file, from its CREATE TABLE statement."""
    cols = []
    with gzip.open(path, "rt", encoding="utf-8", errors="replace") as f:
        inside = False
        for line in f:
            if line.startswith("CREATE TABLE"):
                inside = True
            elif inside:
                m = re.match(r"\s+`(\w+)`", line)
                if m:
                    cols.append(m.group(1))
                elif line.startswith(")"):
                    return cols
    raise SystemExit(f"no CREATE TABLE in {path.name}")


def sql_rows(path):
    """Stream the rows of a mysqldump file as dicts {column: value}; strings unescaped, numbers
    as int or float, NULL as None. Checks every row has as many values as the table has columns."""
    cols = sql_columns(path)
    with gzip.open(path, "rt", encoding="utf-8", errors="replace") as f:
        for line in f:
            if not line.startswith("INSERT INTO"):
                continue
            row = None
            for m in SQL_TOKEN.finditer(line, line.index(" VALUES ") + 8):
                s, null, num, punct = m.groups()
                if punct == "(":
                    row = []
                elif punct == ")":
                    if len(row) != len(cols):
                        raise SystemExit(f"{path.name}: row of {len(row)} values, {len(cols)} columns")
                    yield dict(zip(cols, row, strict=True))
                elif punct is None and row is not None:
                    if s is not None:
                        row.append(SQL_UNESCAPE.sub(lambda e: SQL_ESCAPES.get(e.group(1), e.group(1)), s))
                    elif null:
                        row.append(None)
                    else:
                        row.append(float(num) if any(c in num for c in ".eE") else int(num))


# ------------------------------------------------------------------------------ articles

def load_index():
    """The stable article index: every main-namespace non-redirect page of the dump."""
    with open(INDEX, encoding="utf-8") as f:
        return [json.loads(line) for line in f]


@functools.cache
def load_corpus():
    """{id: {title, words, odia_ratio, text}} of the built corpus (the JSONL as it is at run time),
    in corpus order. Each id must appear once."""
    out = {}
    with open(CORPUS, encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            if r["id"] in out:
                raise SystemExit(f"{CORPUS.name}: id {r['id']} appears twice")
            out[r["id"]] = {k: r[k] for k in ("id", "title", "words", "odia_ratio", "text")}
    return out


def write_annotation(name, rows, sidecar):
    """annotations/<name>.jsonl, one object per corpus article in corpus order (columns in the
    sidecar's order), and its sidecar annotations/<name>.json."""
    cols = list(sidecar["columns"])
    ids = [r["id"] for r in rows]
    if ids != list(load_corpus()) or any(list(r) != cols for r in rows):
        raise SystemExit(f"{name}: rows are not one per corpus article with the columns {cols}")
    write_jsonl_atomic(ANN / f"{name}.jsonl", rows)
    write_atomic(ANN / f"{name}.json", json.dumps(sidecar | {"created": now()}, indent=1, ensure_ascii=False)
                 + "\n")
    print(f"annotations/{name}.jsonl: {len(rows):,} articles; annotations/{name}.json", file=sys.stderr)


def write_report(name, text):
    """quality/<name>."""
    write_atomic(QUALITY / name, text)
    print(f"quality/{name}", file=sys.stderr)


def now():
    return datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def restrict_to_corpus(rows):
    """Keep one row per corpus article (ids from the corpus JSONL as it is now), in corpus order."""
    ids = list(load_corpus())
    by_id = {r["id"]: r for r in rows}
    missing = [i for i in ids if i not in by_id]
    if missing:
        raise SystemExit(f"{len(missing)} corpus ids are not in the article index, e.g. {missing[:5]}")
    return [by_id[i] for i in ids]


# ---------------------------------------------------------------------------- translation

# Change tags that Content Translation (CX) puts on the revisions it publishes.
CT_TAGS = {"contenttranslation", "contenttranslation-v2", "sectiontranslation",
           "contenttranslation-high-unmodified-mt-text"}
# MDWiki's translation dashboard (WikiProject Medicine) runs CX on mdwiki.org and publishes here
# through OAuth consumer 9394: machine-assisted like CX, but with no CX tag. Its edit summary
# says "... to:or #mdwikicx".
MDWIKI_SUMMARY = re.compile(r"Created by translating .*#mdwikicx")
# CX edit summaries (English, not localised on orwiki): 'Created by translating the page
# "[[:en:Special:Redirect/revision/663243963|Devdutt Pattanaik]]"', or '... the section "History"
# from the page "[[...]]"', or '... the opening section from the page ...'.
CX_SOURCE = re.compile(r"\[\[:([a-z][a-z0-9-]*):Special:Redirect/revision/(\d*)\|([^\]]+)\]\]")
CX_SECTION = re.compile(r'translating the section "([^"]*)"|translating the (opening section)')
# Global interwiki bots are not in this wiki's user_groups; their names end in "bot" by policy.
BOT_NAME = re.compile(r"(?i)bot(?:\b|$|[\s\d_-])|^(CommonsDelinker|MediaWiki message delivery|"
                      r"Maintenance script|MediaWiki default|Global Sysops|New user message)$")
MT_SHARE = 0.5  # translation-tool revisions added at least this share of the page's bytes


# Templates that mark a page as a translation (in progress or needing cleanup).
TRANSLATION_TEMPLATE = re.compile(r'"target":\{"wt":"\s*(?:[Tt]emplate:|ଛାଞ୍ଚ:)?\s*((?:[Gg]oogle )?[Tt]ranslation'
                                  r'(?: WIP)?|[Rr]ough translation|[Mm]achine translation|ଅନୁବାଦ ଚାଲିଛି)\s*"')


def translation_template_pages():
    """{page id: [template, ...]} of articles whose dump revision uses a translation template."""
    out = {}
    for p in sorted((HTML_DIR / DATE).glob("chunk-*.jsonl.gz")):
        with gzip.open(p, "rt", encoding="utf-8") as f:
            for line in f:
                r = json.loads(line)
                found = sorted(set(TRANSLATION_TEMPLATE.findall(r.get("html") or "")))
                if found:
                    out[r["id"]] = found
    return out


def bot_accounts():
    """User ids that are, or were, in this wiki's bot group."""
    ids = {r["ug_user"] for r in sql_rows(dump_file("user_groups.sql.gz")) if r["ug_group"] == "bot"}
    return ids | {r["ufg_user"] for r in sql_rows(dump_file("user_former_groups.sql.gz"))
                  if r["ufg_group"] == "bot"}


def history(page_ids):
    """Stream the revision-metadata dump: yield (page id, [revision dict, ...]) for the given pages.
    One page's revisions are in memory at a time."""
    import xml.etree.ElementTree as ET

    ns = "{http://www.mediawiki.org/xml/export-0.11/}"
    revs = []
    with gzip.open(dump_file("stub-meta-history.xml.gz")) as f:
        for _, el in ET.iterparse(f, events=("end",)):
            if el.tag == ns + "revision":
                c, t, cm = el.find(ns + "contributor"), el.find(ns + "text"), el.find(ns + "comment")
                uid = c.findtext(ns + "id") if c is not None else None
                revs.append({
                    "id": int(el.findtext(ns + "id")),
                    "parent": int(el.findtext(ns + "parentid") or 0),
                    "ts": el.findtext(ns + "timestamp"),
                    "user": c.findtext(ns + "username") or ("ip:" + (c.findtext(ns + "ip") or "?"))
                    if c is not None and not c.get("deleted") else None,
                    "uid": int(uid) if uid else None,
                    "comment": (cm.text or "") if cm is not None else "",
                    "bytes": int(t.get("bytes") or 0) if t is not None else 0,
                })
                el.clear()
            elif el.tag == ns + "page":
                pid = int(el.findtext(ns + "id"))
                if el.findtext(ns + "ns") == "0" and pid in page_ids:
                    yield pid, revs
                revs = []
                el.clear()


def translation_row(f, bots):
    """The translation annotation of one article from its facts (page_facts()). Only the
    revisions in translation_revs can carry a CX tag or the MDWiki summary (the facts keep every
    revision with a CX tag or a summary matching /translat/i), so the rest are not needed."""
    def is_bot(user, uid):
        return uid in bots or bool(user and BOT_NAME.search(user))

    revs = f["translation_revs"]  # in time order
    ct = [sorted(set(r["tags"]) & CT_TAGS) for r in revs]
    mdwiki = [bool(MDWIKI_SUMMARY.search(r["comment"])) for r in revs]
    tool = [i for i in range(len(revs)) if ct[i] or mdwiki[i]]
    creation = next((i for i, r in enumerate(revs) if r["rev"] == f["first"]["rev"]), None)
    ct_created = bool(ct[creation]) if creation is not None else False
    mdwiki_created = mdwiki[creation] if creation is not None else False
    added = sum(max(0, revs[i]["bytes"] - revs[i]["prev_bytes"]) for i in tool)
    size = f["last"]["bytes"]
    share = round(min(1.0, added / size), 4) if size else 0.0
    first_tool = revs[tool[0]] if tool else None
    src = CX_SOURCE.search(first_tool["comment"]) if first_tool else None
    sec = CX_SECTION.search(first_tool["comment"]) if first_tool else None
    first = f["first"]
    human = [(user, n) for user, uid, n in f["later_editors"] if not is_bot(user, uid)]
    later = sum(n for _, n in human)
    return {
        "id": f["id"],
        "ct_created": ct_created,
        "ct_any": any(ct),
        "ct_tags": sorted({t for c in ct for t in c}),
        "mdwiki_created": mdwiki_created,
        "mdwiki_any": any(mdwiki),
        "tool_bytes_share": share,
        "translated": ct_created or mdwiki_created or share >= MT_SHARE,
        "translated_at": first_tool["ts"] if first_tool else None,
        "source_lang": src.group(1) if src else None,
        "source_title": src.group(3) if src else None,
        "source_revid": int(src.group(2)) if src and src.group(2) else None,
        "source_section": (sec.group(1) or sec.group(2)) if sec else None,
        "created": first["ts"],
        "creator_is_bot": is_bot(first["user"], first["uid"]),
        "revisions": f["revisions"],
        "edits_after_creation": later,
        "bot_edits_after_creation": f["revisions"] - 1 - later,
        "editors_after_creation": len({user for user, _ in human} - {first["user"], None}),
        "bytes": size,
    }


TRANSLATION_SIDECAR = {
    "name": "translation",
    "description": "Articles created or largely written with Content Translation (CX) or MDWiki's CX-based "
                   "translation dashboard: machine-assisted translations, mostly from English. One JSON "
                   "object per line (translation.jsonl), one per article of the corpus, in corpus order. "
                   "See quality/translation.md.",
    "source": f"{WIKI}-{DATE}: stub-meta-history (every revision's id, timestamp, contributor, edit summary, "
              f"size) and the change_tag and change_tag_def SQL dumps (tags on revisions); user_groups "
              f"and user_former_groups SQL dumps (bot accounts); "
              f"annotate.py translation",
    "created": None,  # set when written
    "columns": {
        "id": "page id (integer)",
        "ct_created": "the page's first revision carries a CX tag (contenttranslation, "
                      "contenttranslation-v2, sectiontranslation, contenttranslation-high-unmodified-mt-text)",
        "ct_any": "any revision of the page carries a CX tag",
        "ct_tags": "the CX tags found on the page's revisions (JSON array)",
        "mdwiki_created": "the first revision was published by MDWiki's translation dashboard (edit summary "
                          "'Created by translating the page [[:mdwiki:...]] to:or #mdwikicx'); CX-based "
                          "machine translation of medical articles, without a CX tag",
        "mdwiki_any": "any revision was published by the MDWiki dashboard",
        "tool_bytes_share": "bytes added by CX or MDWiki revisions / bytes of the dump revision (0-1)",
        "translated": "use this flag: ct_created or mdwiki_created or tool_bytes_share >= 0.5",
        "translated_at": "timestamp of the first CX or MDWiki revision, or null",
        "source_lang": "source wiki of the first CX or MDWiki revision (en, simple, hi, mdwiki, ...), from "
                       "its edit summary",
        "source_title": "source page title from that summary (as written: may be a "
                        "user-space draft, e.g. 'User:Mr. Ibrahem/...')",
        "source_revid": "source page revision id from that summary, or null",
        "source_section": "the translated section when only a section was translated ('opening section' "
                          "for the lead), else null",
        "created": "timestamp of the page's first revision",
        "creator_is_bot": "the first revision's account is, or was, in the bot group, or is a global bot "
                          "(name ends in 'bot')",
        "revisions": "revisions up to the dump (the last is the corpus revision)",
        "edits_after_creation": "revisions after the first by accounts that are not bots (IP edits count)",
        "bot_edits_after_creation": "revisions after the first by bots",
        "editors_after_creation": "distinct non-bot editors after the first revision, other than the creator",
        "bytes": "size of the dump revision's wikitext in bytes",
    },
    "depends_on_text": False,
}
ODIA_LETTERS = re.compile(r"[଀-୥୰-୿]")
LATIN_LETTERS = re.compile(r"[A-Za-z]")


def block_kind(par):
    """heading, table, list or prose: what a text.split("\\n\\n") block of the corpus is."""
    if par.startswith("#"):
        return "heading"
    if par.startswith("|"):
        return "table"
    if re.match(r"(?:[-*+]|\d+\.) ", par):
        return "list"
    return "prose"


def english_dominant(par):
    """A paragraph where Latin letters are over 30 and over twice the Odia letters."""
    latin = len(LATIN_LETTERS.findall(par))
    return latin > 30 and latin > 2 * len(ODIA_LETTERS.findall(par))


def translation(args):
    index = load_index()
    facts = load_facts()
    if len(facts) != len(index):
        raise SystemExit(f"the history has {len(facts):,} of the {len(index):,} dump articles")
    stale = [a["id"] for a in index if facts[a["id"]]["last"]["rev"] != a["revid"]]
    if stale:
        raise SystemExit(f"{len(stale)} articles' last revision is not the dump revision, e.g. {stale[:5]}")
    bots = bot_accounts()
    rows = restrict_to_corpus([translation_row(facts[a["id"]], bots) for a in index])
    write_annotation("translation", rows, TRANSLATION_SIDECAR)
    tag_defs = list(sql_rows(dump_file("change_tag_def.sql.gz")))
    first_has_parent = {r["id"] for r in rows if facts[r["id"]]["first"]["parent"] != 0}
    write_report("translation.md", translation_report(rows, len(index), translation_template_pages(), tag_defs,
                                                      first_has_parent))


def pct(n, d):
    return f"{100 * n / d:.1f}%" if d else "–"


def median(xs):
    xs = sorted(xs)
    return (xs[len(xs) // 2] + xs[(len(xs) - 1) // 2]) / 2 if xs else 0


def translation_report(rows, n_index, template_pages, tag_defs, first_has_parent):
    corpus = load_corpus()
    total_words = sum(c["words"] for c in corpus.values())
    rows = [dict(r) for r in rows]  # the report's own fields (_words, ...) stay out of the annotation
    for r in rows:
        c = corpus[r["id"]]
        pars = c["text"].split("\n\n")
        r["_words"], r["_ratio"], r["_title"] = c["words"], c["odia_ratio"], c["title"]
        r["_pars"], r["_eng"] = len(pars), sum(english_dominant(p) for p in pars)
        eng = [p for p in pars if english_dominant(p)]
        prose = [p for p in pars if block_kind(p) == "prose"]
        r["_prose"], r["_eng_prose"] = len(prose), sum(english_dominant(p) for p in prose)
        r["_eng_example"] = next((p for p in prose if english_dominant(p)), None)
        r["_eng_odia_words"] = sum(len(odia_words(p)) for p in eng)
    groups = {
        "CX-created (`ct_created`)": [r for r in rows if r["ct_created"]],
        "MDWiki-created (`mdwiki_created`)": [r for r in rows if r["mdwiki_created"]],
        "`translated` (all machine-assisted)": [r for r in rows if r["translated"]],
        "not `translated`": [r for r in rows if not r["translated"]],
        "not `translated`, created 2015 or later": [r for r in rows if not r["translated"]
                                                    and r["created"] >= "2015"],
        "not `translated`, human-created, 2015 or later": [r for r in rows if not r["translated"]
                                                           and r["created"] >= "2015" and not r["creator_is_bot"]],
    }
    n = len(rows)
    # How often each CX tag was applied in the whole wiki: change_tag_def's own count (on 2026-09-01
    # equal to the change_tag rows of each tag, all of them on revisions).
    tag_counts = sorted(((d["ctd_name"], d["ctd_count"]) for d in tag_defs if d["ctd_name"] in CT_TAGS),
                        key=lambda tc: -tc[1])
    lines = [
        "# Machine-assisted translations in Odia Wikipedia",
        "",
        f"Built by `annotate.py translation` on {now()[:10]} from the `{WIKI}-{DATE}` dumps, for the "
        f"{n:,} articles of the corpus (`{CORPUS.name}`). Output: `annotations/translation.jsonl` "
        "(one JSON object per article, in corpus order; columns in `annotations/translation.json`).",
        "",
        "## Method",
        "",
        "- **Tags.** `change_tag` links tags to revision ids. Content Translation (CX) marks every revision it "
        "publishes with `contenttranslation`, plus `contenttranslation-v2` (CX's second version), "
        "`sectiontranslation` (section translation, mostly a section added to an existing page) and, twice, "
        "`contenttranslation-high-unmodified-mt-text`. Revisions carrying these tags in the whole wiki "
        "(`change_tag_def` counts): " + ", ".join(f"`{t}` {c:,}" for t, c in tag_counts) + ".",
        "- **Revisions to pages.** `stub-meta-history` (44 MB) gives every revision's page, "
        "timestamp, contributor, edit summary and size. The last revision of each article is exactly the "
        f"corpus revision (checked for all {n_index:,} dump articles).",
        "- **Edit summaries are English, not localised**: `Created by translating the page "
        '"[[:en:Special:Redirect/revision/663243963|Devdutt Pattanaik]]"`, `... the section "History" from '
        'the page "[[...]]"`, or `... the opening section from the page ...`. Source language, title and '
        "revision come from the link.",
        "- **MDWiki.** 2024–2025 revisions with the summary `Created by translating the page "
        "[[:mdwiki:Special:Redirect/revision/N|Title]] to:or #mdwikicx` carry no CX tag, only `OAuth CID: "
        "9394`. They come from WikiProject Medicine's translation dashboard, which runs CX on mdwiki.org "
        "and publishes here. They are machine-assisted in the same way, so they are flagged too "
        "(`mdwiki_created`, `mdwiki_any`).",
        f"- **Created vs. largely translated.** {sum(r['ct_any'] and not r['ct_created'] for r in rows)} "
        "articles got CX text after their first revision: a placeholder (`{{ତିଆରି ଚାଲିଛି}}`, \"under "
        "construction\") or a redirect left by a move came first, or a section translation added most of the "
        "page (" + ", ".join([r["_title"] for r in rows if r["translated"] and r["source_section"]
                              and not r["ct_created"]][:4]) + "). "
        "Others only added a section to an existing article. `tool_bytes_share` is the "
        "bytes the CX/MDWiki revisions added over the dump revision's size, and `translated` = created by "
        f"CX or MDWiki, or `tool_bytes_share` ≥ {MT_SHARE}. **Use `translated`.**",
        "- **Bots** (`creator_is_bot`, and excluded from `edits_after_creation`): accounts in this wiki's "
        "`bot` group now or before (`user_groups`, `user_former_groups`), plus global interwiki bots, which "
        "are not in the local tables, by name (ending in *bot*, e.g. InternetArchiveBot, EmausBot).",
        "",
        "## How many",
        "",
        "| Flag | Articles | Share | Odia words | Share of words |",
        "|---|---:|---:|---:|---:|",
    ]
    for label, key in [("`ct_created`", "ct_created"), ("`ct_any`", "ct_any"), ("`mdwiki_created`", "mdwiki_created"),
                       ("`mdwiki_any`", "mdwiki_any"), ("**`translated`**", "translated")]:
        sel = [r for r in rows if r[key]]
        w = sum(r["_words"] for r in sel)
        lines.append(f"| {label} | {len(sel):,} | {pct(len(sel), n)} | {w:,} | {pct(w, total_words)} |")
    lines += ["", f"All {n:,} articles hold {total_words:,} Odia words.", "",
              "By year of the translation (`translated_at`), for `translated` articles:", "",
              "| Year | Articles | Odia words | CX | MDWiki | Median words |", "|---|---:|---:|---:|---:|---:|"]
    by_year = collections.defaultdict(list)
    for r in rows:
        if r["translated"]:
            by_year[r["translated_at"][:4]].append(r)
    for y in sorted(by_year):
        sel = by_year[y]
        lines.append(f"| {y} | {len(sel):,} | {sum(r['_words'] for r in sel):,} | "
                     f"{sum(bool(r['ct_tags']) for r in sel):,} | {sum(r['mdwiki_any'] for r in sel):,} | "
                     f"{median([r['_words'] for r in sel]):,.0f} |")
    langs = collections.Counter(r["source_lang"] for r in rows if r["translated"])
    ibrahem = sum(1 for r in rows if r["translated"] and (r["source_title"] or "").startswith("User:Mr. Ibrahem/"))
    lines += ["", "Source wikis of `translated` articles: " + ", ".join(
        f"`{k}` {v:,}" for k, v in langs.most_common()) + ". (`simple` is Simple English; `mdwiki` is "
        f"mdwiki.org, English. {ibrahem:,} `en` sources are medical drafts in `User:Mr. Ibrahem/`, the "
        "English Wikipedia user space of the MDWiki translation organiser.)", ""]
    # Comparison with the rest
    lines += ["## Translated articles vs. the rest", "",
              "`odia_ratio` is the corpus field (share of non-space characters in the Odia block). A paragraph "
              "(`text.split(\"\\n\\n\")`) is English-dominant when it has over 30 Latin letters and more than "
              "twice as many Latin as Odia letters.", "",
              "| Group | Articles | Mean `odia_ratio` | Median `odia_ratio` | Articles < 0.6 | "
              "English-dominant paragraphs | of prose paragraphs | Articles with any | Median words |",
              "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for label, sel in groups.items():
        if not sel:
            continue
        pars, eng = sum(r["_pars"] for r in sel), sum(r["_eng"] for r in sel)
        lines.append(
            f"| {label} | {len(sel):,} | {sum(r['_ratio'] for r in sel) / len(sel):.3f} | "
            f"{median([r['_ratio'] for r in sel]):.3f} | {pct(sum(r['_ratio'] < 0.6 for r in sel), len(sel))} | "
            f"{eng:,} of {pars:,} ({pct(eng, pars)}) | "
            f"{pct(sum(r['_eng_prose'] for r in sel), sum(r['_prose'] for r in sel))} | "
            f"{pct(sum(r['_eng'] > 0 for r in sel), len(sel))} | "
            f"{median([r['_words'] for r in sel]):,.0f} |")
    tr = groups["`translated` (all machine-assisted)"]
    all_eng = sum(r["_eng"] for r in rows)
    lines += ["", f"`translated` articles hold {sum(r['_eng'] for r in tr):,} of the corpus's {all_eng:,} "
              f"English-dominant paragraphs ({pct(sum(r['_eng'] for r in tr), all_eng)}), against "
              f"{pct(sum(r['_pars'] for r in tr), sum(r['_pars'] for r in rows))} of all paragraphs. Blocks are "
              "the contract's paragraphs, so tables and lists count: "
              f"{sum(r['_eng'] - r['_eng_prose'] for r in rows):,} of the {all_eng:,} are table, list or heading "
              "blocks (untranslated names in lists of rivers, lakes, records). "
              f"All English-dominant paragraphs together contain {sum(r['_eng_odia_words'] for r in rows):,} "
              "Odia words.", ""]
    edits = [r["edits_after_creation"] for r in tr]
    human_2015 = groups["not `translated`, human-created, 2015 or later"]
    lines += ["Post-editing of `translated` articles (non-bot revisions after the first):", "",
              f"- median {median(edits):.0f} edits; {pct(sum(e == 0 for e in edits), len(edits))} never edited "
              f"again by a person; {pct(sum(r['editors_after_creation'] == 0 for r in tr), len(tr))} never "
              "touched by anyone but the translator.",
              f"- For comparison, human-created articles that are not `translated`: median "
              f"{median([r['edits_after_creation'] for r in human_2015]):.0f} edits (created 2015 or later).", ""]
    # Examples
    lines += ["## Examples", ""]
    shown = 0
    for r in sorted(tr, key=lambda r: (-r["_eng_prose"], r["id"])):
        if r["_eng_example"] and shown < 4:
            ex = r["_eng_example"].replace("\n", " ")[:220].replace("|", "\\|")
            lines.append(f"- **{r['_title']}** (id {r['id']}), from `{r['source_lang']}` \"{r['source_title']}\", "
                         f"{r['translated_at'][:10]}; {r['_eng_prose']} of {r['_prose']} prose paragraphs "
                         f"English-dominant, e.g. \"{ex}…\"")
            shown += 1
    import random

    rng = random.Random(0)
    for r in rng.sample(tr, 6):
        lines.append(f"- {r['_title']} (id {r['id']}): from `{r['source_lang']}` \"{r['source_title']}\""
                     + (f" (section \"{r['source_section']}\")" if r["source_section"] else "")
                     + f", {r['translated_at'][:10]}, {r['_words']:,} Odia words, odia_ratio {r['_ratio']:.2f}, "
                     f"{r['edits_after_creation']} later human edits")
    lines += ["", "## What the dumps cannot tell", "",
              "- **Translations made outside the tools.** Text machine-translated elsewhere (Google Translate "
              "in a browser) and pasted in carries no tag. In the corpus, "
              f"{sum(1 for r in rows if r['id'] in template_pages)} articles use a translation template ("
              + ", ".join(sorted({f"`{{{{{t}}}}}`" for r in rows for t in template_pages.get(r["id"], [])}))
              + "), too few to flag anything. User-defined tags: "
              + ", ".join(f"`{d['ctd_name']}` (applied {d['ctd_count']} times)" for d in tag_defs
                          if d["ctd_user_defined"]) + ".",
              f"- **Before CX.** The first CX-tagged revision here is from "
              f"{min(r['translated_at'] for r in tr if r['ct_tags'])[:10]}; earlier translations cannot carry "
              "a tag.",
              "- **How much machine output survived.** The tag says the tool was used, not how much of its "
              "machine translation the translator kept. CX keeps that measure in its own tables, which the "
              "public dumps do not include. The `contenttranslation-high-unmodified-mt-text` tag (2 revisions) "
              "is the only direct signal here.",
              "- **Deleted and re-created pages** keep only the new history. Merged histories can put an older "
              f"revision first: {len(first_has_parent):,} articles' first revision has a parent.",
              ""]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("step", choices=["download", "translation"])
    args = ap.parse_args()
    if args.step == "download":
        download(args)
    if args.step == "translation":
        translation(args)


if __name__ == "__main__":
    main()
