#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Annotate the Odia Wikipedia corpus: topic tags and Content Translation flags.

Steps, each re-runnable from cached inputs, all output under this directory:

  download     dump files -> raw/ (from the ACC mirror, else dumps.wikimedia.org), SHA-1 checked
               against dumps.wikimedia.org's dumpstatus.json.
  wikidata     P31/P106/P2175/P279 of the Wikidata items of articles that categories and titles
               leave without direct evidence -> raw/<wiki>-<date>-wikidata*.jsonl.gz (incremental)
  translation  change tags -> Content Translation flags -> annotations/translation.jsonl
               (+ .json), quality/translation.md
  topics       categories -> topics -> annotations/topics.jsonl (+ .json), quality/topics.md

Outputs are JSON lines, one object per article of the corpus (orwiki-<date>.jsonl, read at run
time), in corpus order. Never NFC.

Usage (uv reads the dependencies from the header above; nothing is installed in the repo):
    ODIA_WIKI_CONTACT=you@example.org uv run annotate.py download
    ODIA_WIKI_CONTACT=you@example.org uv run annotate.py wikidata
    uv run annotate.py translation
    uv run annotate.py topics
    uv run annotate.py all

`download` and `wikidata` send ODIA_WIKI_CONTACT (an email or URL, never stored in a file) in the
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
import urllib.parse
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
    ("pagepropstable", "page_props.sql.gz"),  # Wikidata items; hidden categories
    ("pagetable", "page.sql.gz"),  # category pages
    ("linktargettable", "linktarget.sql.gz"),  # category link targets
    ("categorylinkstable", "categorylinks.sql.gz"),  # category -> parent category
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


def wikibase_items():
    """{page id: Wikidata QID} from page_props."""
    return {r["pp_page"]: r["pp_value"] for r in sql_rows(dump_file("page_props.sql.gz"))
            if r["pp_propname"] == "wikibase_item"}


# ------------------------------------------------------------------------------ wikidata

WD_API = "https://www.wikidata.org/w/api.php"
WD_ITEMS = RAW / f"{WIKI}-{DATE}-wikidata.jsonl.gz"  # {qid, P31, P106} per fetched article item
WD_CLASSES = RAW / f"{WIKI}-{DATE}-wikidata-classes.jsonl.gz"  # {qid, en, P279} per class
WD_BATCH = 50  # ids per wbgetentities call (the API maximum)
# Item properties fetched: instance of, occupation, and "medical condition treated" (a drug: its
# P31 is only "type of chemical entity", like any chemical).
WD_ITEM_PROPS = ["P31", "P106", "P2175", "P279"]  # P279: a concept item (rice, hardware) has no P31


def truthy(claims, prop):
    """Item values of a property's best-ranked statements (preferred, else normal)."""
    stmts = [c for c in claims.get(prop, []) if c.get("rank") != "deprecated"
             and c.get("mainsnak", {}).get("snaktype") == "value"]
    if any(c["rank"] == "preferred" for c in stmts):
        stmts = [c for c in stmts if c["rank"] == "preferred"]
    out = []
    for c in stmts:
        v = c["mainsnak"].get("datavalue", {}).get("value")
        if isinstance(v, dict) and v.get("id", "").startswith("Q") and v["id"] not in out:
            out.append(v["id"])
    return out


def wbgetentities(qids, props, label=False):
    """{qid: {prop: [qid, ...], "en": label}} via the Wikidata API, WD_BATCH ids per call, one call
    at a time. Not the query service: on 2026-09-24 WDQS was 5 hours behind and overloaded (24-60 s
    for a 500-item VALUES query, then 429 with Retry-After 120). No maxlag either: Wikidata folds
    that WDQS lag into maxlag to slow down bots that edit, which these reads are not."""
    out = {}
    what = "claims|labels" if label else "claims"
    for i in range(0, len(qids), WD_BATCH):
        batch = qids[i:i + WD_BATCH]
        params = {"action": "wbgetentities", "format": "json", "ids": "|".join(batch), "props": what,
                  "languages": "en"}
        for attempt in range(8):
            status, headers, body = http_get(WD_API + "?" + urllib.parse.urlencode(params))
            data = json.loads(body) if status == 200 else {}
            if "entities" in data:
                break
            wait = int((headers or {}).get("Retry-After") or 0) or 5 * 2 ** min(attempt, 5)
            print(f"  wbgetentities {status} {data.get('error', {}).get('code', '')}; retry in {wait}s",
                  file=sys.stderr)
            time.sleep(wait)
        else:
            raise SystemExit(f"wbgetentities failed for {batch[0]}..")
        for key, e in data["entities"].items():
            qid = e.get("redirects", {}).get("from") or key
            row = {p: truthy(e.get("claims", {}), p) for p in props}
            if label:
                row["en"] = e.get("labels", {}).get("en", {}).get("value", "")
            out[qid] = row
        print(f"  {min(i + WD_BATCH, len(qids)):,}/{len(qids):,}", file=sys.stderr, flush=True)
        time.sleep(0.5)
    return out


def write_jsonl_gz(path, rows):
    """Gzipped JSON lines through a temp file."""
    def write(tmp):
        with gzip.open(tmp, "wb") as gz:
            for r in rows:
                gz.write((json.dumps(r, ensure_ascii=False) + "\n").encode("utf-8"))
    replace_atomic(path, write)


def read_jsonl_gz(path):
    if not path.exists():
        return []
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def qid_key(q):
    return int(q[1:])


def wikidata(args):
    """P31 (instance of), P106 (occupation), P2175 (medical condition treated) and P279 (subclass of)
    of the Wikidata items of the articles where no rule matches a category's own name or the title, and the
    English label and P279 (subclass of) of every class they name, two superclass levels up.
    Cached in raw/ and incremental: only items not in the cache are fetched (--refresh: all
    again). Wikidata is live, not a dump."""
    items = {} if args.refresh else {r["qid"]: r for r in read_jsonl_gz(WD_ITEMS)}
    classes = {} if args.refresh else {r["qid"]: r for r in read_jsonl_gz(WD_CLASSES)}
    base = category_title_pass()
    qids = wikibase_items()
    want = sorted({qids[i] for i, r in base.items() if not r["strong"] and i in qids}, key=qid_key)
    todo = [q for q in want if q not in items or any(p not in items[q] for p in WD_ITEM_PROPS)]
    print(f"{len(want):,} articles without a direct category or title match have a Wikidata item; "
          f"{len(todo):,} not cached", file=sys.stderr)
    if todo or not classes:
        if not CONTACT:
            raise SystemExit("set ODIA_WIKI_CONTACT (email or URL): Wikimedia throttles anonymous bulk clients")
        got = wbgetentities(todo, WD_ITEM_PROPS)
        items |= {q: {"qid": q} | got.get(q, {p: [] for p in WD_ITEM_PROPS}) for q in todo}
        write_jsonl_gz(WD_ITEMS, (items[q] for q in sorted(items, key=qid_key)))
        level = sorted({c for r in items.values() for c in r["P31"] + r["P106"] + r["P279"]}, key=qid_key)
        for _ in range(3):  # the classes themselves, then two levels of superclasses
            need = [c for c in level if c not in classes]
            if need:
                print(f"fetching labels and P279 of {len(need):,} classes", file=sys.stderr)
                got = wbgetentities(need, ["P279"], label=True)
                classes |= {c: {"qid": c} | got.get(c, {"P279": [], "en": ""}) for c in need}
            level = sorted({s for c in level for s in classes[c]["P279"]}, key=qid_key)
        write_jsonl_gz(WD_CLASSES, (classes[c] for c in sorted(classes, key=qid_key)))
    print(f"{WD_ITEMS.name}: {len(items):,} items; {WD_CLASSES.name}: {len(classes):,} classes", file=sys.stderr)


# -------------------------------------------------------------------------------- topics

# The topic vocabulary, in tie-break order (the first wins a tie for primary_topic).
TOPICS = {
    "calendar": "year, date, month, decade and century pages; observances",
    "film": "film, television and entertainment, including actors, directors, models",
    "sports": "sports, sportspeople, clubs, venues, tournaments",
    "health": "health and medicine: diseases, drugs, anatomy, nutrition, hospitals",
    "biology": "biology and nature: plants, animals, taxa, ecology, forests, sanctuaries",
    "mathematics": "mathematics and numbers",
    "science": "physical and earth sciences: physics, chemistry, astronomy, geology, weather",
    "technology": "technology and engineering: computing, internet, vehicles, transport, space tech",
    "economy": "economy and business: companies, industry, agriculture, banking, trade",
    "education": "education: schools, colleges, universities, teachers",
    "religion": "religion, mythology and philosophy: deities, temples, scriptures, saints",
    "literature": "literature and language: writers, poets, books, periodicals, languages, scripts",
    "arts": "arts and culture: music, dance, theatre, painting, festivals, food, customs, crafts",
    "history": "history and military: empires, dynasties, rulers, wars, freedom struggle, monuments",
    "politics": "politics, government and law: politicians, elections, legislatures, courts, schemes",
    "society": "society: communities, tribes, castes, organisations, social movements, activists",
    "geography": "geography and places: countries, states, districts, towns, villages, rivers",
}
# school_relevant: primary topic is a subject the Odisha school syllabus (classes 1-10) teaches
# directly. Biographies count only for the subjects where the person is taught content
# (scientists, historical figures, writers), not for politicians or entertainers.
SCHOOL_TOPICS = {"science", "biology", "health", "mathematics", "technology", "geography",
                 "history", "politics", "economy", "literature", "society", "education"}
SCHOOL_PERSON_TOPICS = {"science", "biology", "health", "mathematics", "technology", "history",
                        "literature"}

ODIA_LETTER = "ଁ-୿"
FOLD = str.maketrans({"ଡ଼": "ଡ", "ଢ଼": "ଢ", "଼": None,  # ଡ଼ ଢ଼ nukta
                      "ୟ": "ଯ", "ଵ": "ୱ",  # ୟ -> ଯ, ଵ -> ୱ
                      "ୀ": "ି", "ୂ": "ୁ", "ଈ": "ଇ", "ଊ": "ଉ",
                      "‌": None, "‍": None, "_": " "}
                     | {0x0b66 + i: str(i) for i in range(10)})


def fold(s, lower=True):
    """A matching key for Odia names: spelling variants (nukta, vowel length, ଵ/ୱ, ୟ/ଯ, ଙ୍/ଂ,
    ZWJ/ZWNJ) and Odia digits folded, lowercased. Used only to match; never stored. Regex
    patterns are folded with lower=False: lowercasing would turn \\S into \\s."""
    s = s.translate(FOLD).replace("ଙ୍", "ଂ")
    return s.lower() if lower else s


def words(*ws):
    """A regex matching any of the words at the start of an Odia word (not inside one).
    A leading ~ allows a match anywhere; a trailing $ requires the word to end there, and $$
    requires the name to end there."""
    parts = []
    for w in ws:
        anywhere, end = w.startswith("~"), "$$" if w.endswith("$$") else "$" if w.endswith("$") else ""
        w = fold(w.strip("~$"))
        tail = {"$$": "$", "$": f"(?![{ODIA_LETTER}])", "": ""}[end]
        parts.append(("" if anywhere else f"(?<![{ODIA_LETTER}])") + w + tail)
    return re.compile("|".join(parts))


# Categories that are not about the article's subject: cleanup, tracking, bot and project
# bookkeeping. Hidden categories (hiddencat) are dropped first; these are visible ones.
MAINTENANCE = re.compile(
    "|".join([r"^(pages?|articles?|all |cs1|wikipedia|webarchive|use |check |moveable|languages? |"
              r"ill-formatted|commons|infobox|templates?|coordinates|official website|short description|"
              r"biography with|no local image|disambiguation|stub categories|taxonbars|climate data|convert|"
              r"portal|harv|good articles|featured|accuracy|vague|engvar|redirects|unprintworthy|"
              r"language articles|iso language|webcite|dead|incomplete|lists of|user |wikidata|date of|"
              r"cite|citation|unsourced|orphan|uncategori|noindex|hidden|tracking|maintenance|"
              r"interlanguage|rtt)"]
             + [fold(w) for w in ["ଆଧାରହୀନ", "ସଜଡ଼ା ହେବାକୁ", "ବଟ ଦ୍ୱାରା ଗୁରୁତ୍ୱପୂର୍ଣ୍ଣ", "ଗୁରୁତ୍ତ୍ୱପୂର୍ଣ୍ଣ ବଦଳ",
                                  "ଅସମ୍ପୂର୍ଣ୍ଣ ଫାଇଲ", "ଲୋଡ଼ାଥିବା", "ଗଢ଼ା ବା ଉନ୍ନତ", "ଗଣସମ୍ପାଦନା",
                                  "ଅବର୍ଗୀକୃତ", "ଅନାଥ ପୃଷ୍ଠା", "ବହୁବିକଳ୍ପ", "ଅପସାରଣ", "ବିଲୋପ", "ସୁଧାର ଲୋଡ଼ା",
                                  "ଉପକ୍ରମ ସୁଧାର"]]))

# English category names on this wiki are almost all tracking categories copied with templates.
# These few carry a topic.
ENGLISH_TOPICAL = [(re.compile(r"^iucn|species|dinosaur|^check dino"), ["biology"]),
                   (re.compile(r"ollywood|bollywood|film|actor|actress|television"), ["film"]),
                   (re.compile(r"cricketer|footballer|athlete|sportspeople|olympi"), ["sports"]),
                   (re.compile(r"politician|legislative assembly|lok sabha|rajya sabha"), ["politics"]),
                   (re.compile(r"singer|musician|dancer|painter"), ["arts"]),
                   (re.compile(r"writer|poet|novelist|journalist"), ["literature"]),
                   (re.compile(r"^living people$|births$|deaths$"), [])]
ENGLISH_PERSON = re.compile(r"people|births$|deaths$|actor|actress|cricketer|footballer|athlete|sportspeople|"
                            r"politician|singer|musician|dancer|painter|writer|poet|novelist|journalist|players?$")

PERSON_ONLY = words("ଲୋକ$$", "ଜନ୍ମ$$", "ମୃତ୍ୟୁ$$", "ଜୀବିତ ବ୍ୟକ୍ତି", "ଜନ୍ମିତ ବ୍ୟକ୍ତି", "ଜୀବନୀ$$",
                    "~ଭାଷୀ ଲୋକ")
# Ordered: the first rule whose pattern matches a category decides its topics. Specific rules
# come before general ones ("ଚିକିତ୍ସା ବିଜ୍ଞାନ" is health, not science; "ହିନ୍ଦୁ ପର୍ବ" is
# religion, not arts; "କଥାଚିତ୍ର ଗୀତିକାର" is film, not literature).
TOPIC_RULES = [
    (["calendar"], words("ବଟ ଦ୍ୱାରା ତିଆରି ତାରିଖ", "ବଟଦ୍ୱାରା ତିଆରି ତାରିଖ", "ପାଳିତ ଦିବସ", "ଦିବସ$")),
    (["calendar"], re.compile(fold(r"^(ବର୍ଷ|ମାସ|ଦଶକ|ଶତାବ୍ଦୀ|ସହସ୍ରାବ୍ଦୀ|ସମୟ|ସପ୍ତାହ|ଦିନ|ତିଥି|ପାଞ୍ଜି|"
                                   r"ଜାନୁଆରୀ|ଫେବୃଆରୀ|ମାର୍ଚ୍ଚ|ଅପ୍ରେଲ|ମଇ|ଜୁନ|ଜୁଲାଇ|ଅଗଷ୍ଟ|ସେପ୍ଟେମ୍ବର|"
                                   r"ଅକ୍ଟୋବର|ନଭେମ୍ବର|ଡିସେମ୍ବର)$|^(ଖ୍ରୀଷ୍ଟପୂର୍ବ )?\d+\S* (ଦଶକ|ଶତାବ୍ଦୀ|"
                                   r"ସହସ୍ରାବ୍ଦୀ)$", lower=False))),
    (["biology", "health"], words("ଔଷଧୀୟ ଗଛ", "ଔଷଧୀୟ ଉଦ୍ଭିଦ")),
    (["arts", "economy"], words("ଭୌଗଳିକ ସଙ୍କେତ")),
    (["history", "geography"], words("ବିଶ୍ୱ ଐତିହ୍ୟ", "ଐତିହ୍ୟସ୍ଥଳ", "ପ୍ରତ୍ନତାତ୍ତ୍ୱିକ ସ୍ଥଳ")),
    (["biology", "geography"], words("ଜାତୀୟ ଉଦ୍ୟାନ", "ଅଭୟାରଣ୍ୟ", "ଜୀବମଣ୍ଡଳ")),
    (["technology"], words("ପ୍ରୋଗ୍ରାମିଂ ଭାଷା", "କମ୍ପ୍ୟୁଟର ଭାଷା")),
    (["literature"], words("ଭାଷା ବିଜ୍ଞାନ", "ଭାଷାବିଜ୍ଞାନ", "ଭାଷାତତ୍ତ୍ୱ", "ଜ୍ଞାନପୀଠ", "ସାହିତ୍ୟ ଏକାଡେମୀ",
                           "ଝଙ୍କାର ପୁରସ୍କାର", "ଶାରଳା ପୁରସ୍କାର", "ସାରଳା ପୁରସ୍କାର", "ବିଷୁବ ପୁରସ୍କାର",
                           "କବିତା ପୁରସ୍କାର", "ପୁସ୍ତକମେଳା")),
    (["politics"], words("ରାଜନୀତି ବିଜ୍ଞାନ", "ଜାତୀୟ ପ୍ରତୀକ", "ରାଜ୍ୟ ସଙ୍କେତ", "ବିଧାନ ସଭାର ସଭ୍ୟ", "ବିଧାନ ସଭା ସଭ୍ୟ",
                         "ଲୋକ ସଭା ସଭ୍ୟ")),  # legislators, also "ସ୍ୱାଧୀନତା ପୂର୍ବର" (pre-independence) ones
    (["society"], words("ସମାଜ ବିଜ୍ଞାନ", "ସମାଜବିଜ୍ଞାନ", "ନୃତତ୍ତ୍ୱ")),
    (["religion"], words("ଜ୍ୟୋତିଷ", "ରାଶି$", "ରାଶିଚକ୍ର", "ରଥଯାତ୍ରା", "ଶକ୍ତିପୀଠ", "ଶକ୍ତି ପୀଠ", "ଶକ୍ତିପୂଜା")),
    (["arts"], words("ସଙ୍ଗୀତ ନାଟକ ଏକାଡେମୀ", "ଲଳିତ କଳା", "ଯାତ୍ରା ଅଭିନେତା", "ଯାତ୍ରା ଅଭିନେତ୍ରୀ", "ଯାତ୍ରା ଦଳ",
                     "ଓଡ଼ିଆ ଯାତ୍ରା", "ମଞ୍ଚ ଅଭିନେତା", "ନାଟ୍ୟ ଅଭିନେତା", "ରଙ୍ଗମଞ୍ଚ", "~ଶିଳ୍ପୀ", "ହସ୍ତଶିଳ୍ପ",
                     "ହସ୍ତତନ୍ତ", "କାରୁକାର୍ଯ୍ୟ")),
    (["film"], words("କଥାଚିତ୍ର", "~ଚଳଚ୍ଚିତ୍ର", "ସିନେମା", "ସିନେ$", "ଓଲିଉଡ଼", "ବଲିଉଡ଼", "ଟଲିଉଡ଼", "କଲିଉଡ଼",
                     "ଦୂରଦର୍ଶନ", "ଟେଲିଭିଜନ", "ଟିଭି", "~ଅଭିନେତା", "~ଅଭିନେତ୍ରୀ", "ଅଭିନୟ", "ୱେବ ସିରିଜ",
                     "ୱେବ୍ ସିରିଜ", "ଟେଲି ଫିଲ୍ମ", "ଟେଲିଫିଲ୍ମ", "ଫିଲ୍ମ", "ଓଟିଟି", "ଧାରାବାହିକ", "ମଡେଲ",
                     "ମଡ଼େଲ୍", "ଫେମିନା", "ମିସ୍", "ମିସ ", "ସୁନ୍ଦରୀ", "ଆନିମେସନ", "କାର୍ଟୁନ")),
    (["sports"], words("~ଖେଳ", "କ୍ରିକେଟ", "ଫୁଟବଲ", "ହକି", "ଟେନିସ", "ବ୍ୟାଡମିଣ୍ଟନ", "ଚେସ", "ଦାବା", "କବାଡି",
                       "କବାଡ଼ି", "ଅଲିମ୍ପିକ", "ଆଥଲେଟ", "ଏଥଲେଟ", "ମୁଷ୍ଟିଯୁଦ୍ଧ", "ମୁଷ୍ଟିଯୋଦ୍ଧା", "ବକ୍ସର",
                       "ବକ୍ସିଂ", "କୁସ୍ତି", "ଭାରୋତ୍ତୋଳନ", "ଭାରୋତ୍ତୋଳକ", "ସନ୍ତରଣ", "ବନ୍ଧୁକଚାଳନା", "ସୁଟର",
                       "ତୀରନ୍ଦାଜ", "କ୍ରୀଡ଼ା", "ଷ୍ଟାଡିୟମ", "ଷ୍ଟେଡିୟମ", "ଷ୍ଟାଡିଅମ", "ଷ୍ଟେଡିଅମ", "ଟୁର୍ଣ୍ଣାମେଣ୍ଟ",
                       "ବିଶ୍ୱକପ", "ବିଶ୍ୱ କପ", "ଭଲିବଲ", "ବାସ୍କେଟବଲ", "ଗଲ୍ଫ", "ଖୋ ଖୋ", "ଧାବକ",
                       "ପର୍ବତାରୋହୀ", "ପର୍ବତାରୋହଣ", "କ୍ଲବ$", "ଲିଗ$", "କରାଟେ", "କ୍ରିକେଟର", "ରେସଲିଂ")),
    (["health"], words("ଚିକିତ୍ସା", "ଚିକିତ୍ସକ", "ଔଷଧ", "~ରୋଗ", "ସ୍ୱାସ୍ଥ୍ୟ", "ଡାକ୍ତର", "ଟିକା$", "ଟିକାକରଣ",
                       "ଭୁତାଣୁ", "ଜୀବାଣୁ", "ସଂକ୍ରମଣ", "ମହାମାରୀ", "କୋଭିଡ", "କର୍କଟ", "ଅର୍ବୁଦ", "ଅଙ୍ଗପ୍ରତ୍ୟଙ୍ଗ",
                       "ଶରୀର", "ପୁଷ୍ଟି", "ଭିଟାମିନ", "ମାନସିକ", "ହସ୍ପିଟାଲ", "ଚିକିତ୍ସାଳୟ", "ଆୟୁର୍ବେଦ",
                       "ହୋମିଓପାଥି", "ଶଲ୍ୟ", "ନର୍ସ", "ଦନ୍ତ", "hiv", "aids", "ଏଡ୍ସ", "ଗର୍ଭ", "ପ୍ରସୂତି",
                       "ରକ୍ତ", "ହୃଦ", "ହୃତ୍", "ମସ୍ତିଷ୍କ", "ସ୍ନାୟୁ", "ଯୋଗାସନ", "ବିଷଜ୍ଞାନ", "ବିଷ$")),
    (["biology"], words("ଜୀବ ବିଜ୍ଞାନ", "ଜୀବବିଜ୍ଞାନ", "ଜୀବଜଗତ", "ଜୀବଜନ୍ତୁ", "ପ୍ରାଣୀ", "ଉଦ୍ଭିଦ", "ଗଛ", "ବୃକ୍ଷ",
                        "ଫୁଲ", "ଫଳ$", "ପକ୍ଷୀ", "ଚଢ଼େଇ", "~ଡାଇନୋସର", "ସରୀସୃପ", "ସର୍ପ", "ସାପ", "ମାଛ", "ମତ୍ସ୍ୟ",
                        "ସ୍ତନ୍ୟପାୟୀ", "କୀଟ", "ପୋକ", "ପତଙ୍ଗ", "ପ୍ରଜାପତି", "ଜନ୍ତୁ", "ପଶୁ", "ବାନର", "ମାଙ୍କଡ଼",
                        "ବିଡ଼ାଳ", "କୁକୁର", "ମୃଗ", "ହରିଣ", "ଗୋରୁ", "ଉଭୟଚର", "ପରଜୀବୀ", "ବ୍ୟାକ୍ଟେରିଆ", "କବକ",
                        "ଛତୁ", "ଶୈବାଳ", "~ପ୍ରଜାତି", "ଜୀବାଶ୍ମ", "ପରିବେଶ", "ଜଙ୍ଗଲ", "ଅରଣ୍ୟ", "ଜୈବ",
                        "ତୃଣଭୋଜୀ", "ମାଂସାଶୀ", "କ୍ରେଟାସିଅସ", "ଜୁରାସିକ", "ଟ୍ରାଇଆସିକ", "ଶସ୍ୟ", "ପନିପରିବା",
                        "ଘାସ", "ଲତା", "ବନ୍ୟପ୍ରାଣୀ", "ଜୀବ$")),
    (["mathematics"], words("~ଗଣିତ", "ଜ୍ୟାମିତି", "ସଂଖ୍ୟା$", "ତ୍ରିକୋଣମିତି", "ପରିସଂଖ୍ୟାନ", "କଳନ",
                            "ଗାଣିତିକ", "ସମୀକରଣ")),
    (["technology"], words("ପ୍ରଯୁକ୍ତି", "ବୈଷୟିକ", "କମ୍ପ୍ୟୁଟର", "ସଫ୍ଟୱେର", "ସଫ୍ଟୱେୟାର", "ଇଣ୍ଟରନେଟ", "ୱେବ",
                           "ପ୍ରୋଗ୍ରାମିଂ", "ଅପରେଟିଂ", "~ନେଟୱାର୍କ", "ଆର୍ଟିଫିସିଆଲ", "ଇଲେକ୍ଟ୍ରୋନିକ",
                           "ଇଲେକ୍ଟ୍ରନିକ", "ଯନ୍ତ୍ରପାତି", "ଯନ୍ତ୍ର$", "ଉଦ୍ଭାବନ", "ଉଦ୍ଭାବକ", "ଇଞ୍ଜିନିୟର",
                           "ଯାନ$", "ବିମାନ", "ରେଳ", "ପରିବହନ", "ରକେଟ", "କ୍ଷେପଣାସ୍ତ୍ର", "ଇସ୍ରୋ", "ମହାକାଶ ଗବେଷଣା",
                           "ଚନ୍ଦ୍ର ଅଭିଯାନ", "ମହାକାଶ ଅଭିଯାନ", "ମୋବାଇଲ", "ଟେଲିଫୋନ", "ଗୁଗଲ", "ଗୁଗୁଲ",
                           "ଉଇଣ୍ଡୋଜ", "ଡାଟାବେସ", "ଇଞ୍ଜିନ", "ମୋଟର", "ଗାଡ଼ି", "ଜାହାଜ", "ରୋବଟ", "ଅସ୍ତ୍ର",
                           "ଆପ୍ଲିକେସନ", "ଉପଗ୍ରହ", "ଡିଜିଟାଲ", "ସୋସିଆଲ", "ଉଇକିପିଡ଼ିଆ ସଂସ୍କରଣ", "ୱେବସାଇଟ")),
    (["science"], words("~ବିଜ୍ଞାନ", "ପଦାର୍ଥ", "ରସାୟନ", "ମୌଳିକ$", "ଖଗୋଳ", "ମହାକାଶ", "ନକ୍ଷତ୍ର", "ଗ୍ରହ",
                        "ଗ୍ରହାଣୁ", "ସୌରମଣ୍ଡଳ", "ସୌରଜଗତ", "ତାରା$", "ଛାୟାପଥ", "ଗ୍ୟାଲାକ୍ସି", "ଭୂତତ୍ତ୍ୱ",
                        "ଖଣିଜ", "ଶିଳା", "ପାଣିପାଗ", "ଜଳବାୟୁ", "ବିଦ୍ୟୁତ", "ଚୁମ୍ବକ", "ଆଲୋକ$", "ପରମାଣୁ",
                        "ଅଣୁ$", "ଯୌଗିକ", "ଏସିଡ", "ଅମ୍ଳ", "କ୍ଷାର", "ଧାତୁ$", "ମାପ", "ଏକକ", "~ବୈଜ୍ଞାନିକ",
                        "ବିଜ୍ଞାନୀ", "ଭୂକମ୍ପ", "ଆଗ୍ନେୟଗିରି", "ଗ୍ୟାସ", "ଆବିଷ୍କାର", "ଗବେଷଣା", "ଗବେଷକ")),
    (["economy"], words("ଅର୍ଥନୀତି", "ଅର୍ଥନୈତିକ", "ଅର୍ଥବ୍ୟବସ୍ଥା", "ଅର୍ଥ ବ୍ୟବସ୍ଥା", "ଅର୍ଥଶାସ୍ତ୍ର",
                        "~ବ୍ୟବସାୟ", "ବାଣିଜ୍ୟ", "ବ୍ୟାଙ୍କ", "କମ୍ପାନୀ", "ଶିଳ୍ପ$", "ଶିଳ୍ପପତି", "ଉଦ୍ୟୋଗ",
                        "ବଜାର", "ଶେୟାର", "ଟଙ୍କା", "ମୁଦ୍ରା$", "କୃଷି", "ଚାଷ", "ଖଣି", "ଟିକସ", "ବୀମା",
                        "ସମବାୟ", "ନିଗମ", "ଲିମିଟେଡ", "ବ୍ରାଣ୍ଡ", "ବଣିକ", "ରପ୍ତାନି", "ଆମଦାନି", "ଦାରିଦ୍ର୍ୟ",
                        "ଶ୍ରମିକ", "କାରଖାନା")),
    (["education"], words("ଶିକ୍ଷା", "~ବିଦ୍ୟାଳୟ", "ଶିକ୍ଷାନୁଷ୍ଠାନ", "ଶିକ୍ଷକ", "ଶିକ୍ଷୟିତ୍ରୀ", "ଶିକ୍ଷାବିତ",
                          "ପାଠଶାଳା", "କଲେଜ", "ସ୍କୁଲ", "ୟୁନିଭର୍ସିଟି", "ଛାତ୍ର", "ପରୀକ୍ଷା", "ପାଠାଗାର",
                          "ଗ୍ରନ୍ଥାଗାର", "ପାଠ୍ୟ", "ଅଧ୍ୟାପକ", "ପ୍ରଫେସର", "ଅନୁଷ୍ଠାନ$")),
    (["religion"], words("~ଧର୍ମ", "ହିନ୍ଦୁ", "ବୌଦ୍ଧ", "ଜୈନ", "ଇସଲାମ", "ମୁସଲିମ", "ମୁସଲମାନ", "ଖ୍ରୀଷ୍ଟ ଧର୍ମ",
                         "ଖ୍ରୀଷ୍ଟଧର୍ମ", "ଖ୍ରୀଷ୍ଟିୟ", "ଖ୍ରୀଷ୍ଟାନ", "ଯୀଶୁ", "ଶିଖ$", "~ମନ୍ଦିର", "ମଠ$", "ମସଜିଦ",
                         "ଗୀର୍ଜା", "ଦେବୀ", "ଦେବତା", "ଦେବାଦେବୀ", "ଭଗବାନ", "ଈଶ୍ୱର", "ଜଗନ୍ନାଥ", "ମହାଭାରତ",
                         "ରାମାୟଣ", "~ପୁରାଣ", "ପୌରାଣିକ", "ବେଦ$", "ବେଦାନ୍ତ", "ବୈଦିକ", "ଉପନିଷଦ", "ଗୀତା",
                         "ଭାଗବତ", "~ଭଗବତ୍ଗୀତା", "~ପୀଠ", "ତୀର୍ଥ", "ଆଶ୍ରମ", "ସନ୍ଥ", "ସାଧୁ", "ଗୁରୁ$", "ଭକ୍ତ",
                         "ପୂଜା", "ବ୍ରତ", "ଓଷା", "ଏକାଦଶୀ", "ଜ୍ୟୋତିର୍ଲିଙ୍ଗ", "ଅବତାର", "ଦର୍ଶନ$", "ଦାର୍ଶନିକ",
                         "ଆଧ୍ୟାତ୍ମିକ", "ଯୋଗୀ", "ପୁରୋହିତ", "ବତ୍ରିଶ ସିଂହାସନ", "ଦେବଦାସୀ", "ଭୋଗ$", "ବେଶ$",
                         "ଶିବ$", "ବିଷ୍ଣୁ", "ଗଣେଶ", "ଦୁର୍ଗା", "କାଳୀ", "ମିଶନାରୀ", "ପ୍ରଚାରକ")),
    (["literature"], words("~ସାହିତ୍ୟ", "ଲେଖକ", "ଲେଖିକା", "କବି", "~କବିତା", "କାବ୍ୟ", "~କାବ୍ୟ", "ଉପନ୍ୟାସ",
                           "ଔପନ୍ୟାସିକ", "ଗଳ୍ପ", "ଗାଳ୍ପିକ", "କାହାଣୀ", "~ନାଟ୍ୟକାର", "ପ୍ରାବନ୍ଧିକ", "ପ୍ରବନ୍ଧ",
                           "ସମାଲୋଚକ", "ଅନୁବାଦ", "ବହି", "~ପୁସ୍ତକ", "~ଗ୍ରନ୍ଥ", "ପତ୍ରିକା", "ଖବରକାଗଜ",
                           "ସମ୍ବାଦପତ୍ର", "ଖବର କାଗଜ", "ସାମ୍ବାଦିକ", "ସମ୍ପାଦକ", "~ଭାଷା", "~ଲିପି", "ବର୍ଣ୍ଣମାଳା", "ବ୍ୟାକରଣ",
                           "ଶବ୍ଦ", "ଅଭିଧାନ", "ଛାପାଖାନା", "ପ୍ରକାଶନ", "ରଚନା", "ଲିଖନ", "ପ୍ରବାଦ", "ଲୋକକଥା",
                           "ଆତ୍ମଜୀବନୀ")),
    (["arts"], words("କଳା$", "କଳାକାର", "~ଲୋକକଳା", "ଚିତ୍ରକଳା", "କଳାକୃତି", "ଚିତ୍ରକର", "ଚିତ୍ରକାର", "ଭାସ୍କର",
                     "ସ୍ଥାପତ୍ୟ", "ସ୍ଥପତି", "~ସଙ୍ଗୀତ", "~ଗୀତ", "ଗାୟକ", "ଗାୟିକା", "ବାଦ୍ୟ", "ବାଦକ", "~ନୃତ୍ୟ",
                     "ନର୍ତ୍ତକ", "ନର୍ତ୍ତକୀ", "ଓଡ଼ିଶୀ", "ନାଟକ", "~ନାଟ୍ୟ", "ଯାତ୍ରା$", "ମଞ୍ଚ", "ଥିଏଟର", "ପର୍ବ$",
                     "ପର୍ବପର୍ବାଣି", "ପର୍ବାଣି", "ଉତ୍ସବ", "ମେଳା", "ମହୋତ୍ସବ", "~ଖାଦ୍ୟ", "ରାନ୍ଧଣା", "ପିଠା",
                     "ମିଠା", "ଭୋଜନ", "ବ୍ୟଞ୍ଜନ", "ପାନୀୟ", "ମସଲା", "ଆମିଷ", "ନିରାମିଷ", "ଘରକରଣା", "ସଂସ୍କୃତି",
                     "ଚଳଣି", "ପରମ୍ପରା", "ପାରମ୍ପରିକ", "ପାରମ୍ପାରିକ", "ବୟନ", "ଶାଢ଼ୀ", "ପୋଷାକ", "ଅଳଙ୍କାର", "ଗହଣା",
                     "ଫଟୋଗ୍ରାଫି", "ଚିତ୍ରତ୍ତୋଳକ", "ଆଲୋକଚିତ୍ର", "ସଂଗ୍ରହାଳୟ", "ଐତିହ୍ୟ", "ଖେଳନା", "ଚିତ୍ର$",
                     "ଶାସ୍ତ୍ରୀୟ")),
    (["history"], words("ଇତିହାସ", "ଐତିହାସିକ", "~ପୁରାତନ", "~ପ୍ରାଚୀନ", "ମଧ୍ୟଯୁଗ", "ସାମ୍ରାଜ୍ୟ", "~ରାଜବଂଶ",
                        "ବଂଶ$", "ରାଜା$", "ରାଣୀ", "ସମ୍ରାଟ", "ଶାସକ", "ରାଜକୁମାର", "ରାଜକୁମାରୀ", "ରାଜପରିବାର",
                        "~ଯୁଦ୍ଧ", "ସଂଗ୍ରାମ", "ସ୍ୱାଧୀନତା", "ବିଦ୍ରୋହ", "ବିପ୍ଳବ", "ଆନ୍ଦୋଳନ", "ଉପନିବେଶ",
                        "ବ୍ରିଟିଶ ଶାସନ", "ବ୍ରିଟିଶ ଭାରତ", "ମୋଗଲ", "ଗଜପତି", "ଭୌମକର", "ସୋମବଂଶୀ", "କଳିଙ୍ଗ",
                        "ପ୍ରତ୍ନ", "ସ୍ମାରକ", "ଦୁର୍ଗ$", "ଶିଳାଲେଖ", "ଅଭିଲେଖ", "ତାମ୍ରଲେଖ", "ଗୁମ୍ଫା", "ସଭ୍ୟତା",
                        "ଗାନ୍ଧୀବାଦୀ", "ଗାନ୍ଧୀ", "ଶହୀଦ", "ଗଡ଼ଜାତ", "ଜମିଦାର", "ସେନା", "ସୈନ୍ୟ", "ସାମରିକ",
                        "ସଶସ୍ତ୍ର", "ବାୟୁସେନା", "ନୌସେନା", "ବୀର ଚକ୍ର", "ଅଶୋକ ଚକ୍ର")),
    (["politics"], words("~ରାଜନୀତି", "ରାଜନୈତିକ", "ରାଜନେତା", "ନେତା", "ଦଳ$", "ଦଳର", "ପାର୍ଟି", "କଂଗ୍ରେସ",
                         "ଜନତା", "ବିଜେପି", "କମ୍ୟୁନିଷ୍ଟ", "ସମାଜବାଦୀ", "ନିର୍ବାଚନ", "ବିଧାନ ସଭା", "ବିଧାନସଭା",
                         "ଲୋକ ସଭା", "ଲୋକସଭା", "ରାଜ୍ୟ ସଭା", "ରାଜ୍ୟସଭା", "ସାଂସଦ", "ସଂସଦ", "~ମନ୍ତ୍ରୀ",
                         "ରାଷ୍ଟ୍ରପତି", "ଉପରାଷ୍ଟ୍ରପତି", "ରାଜ୍ୟପାଳ", "ସରକାର", "ସରକାରୀ", "ଯୋଜନା", "ଆଇନ",
                         "~ସମ୍ବିଧାନ", "ସାମ୍ବିଧାନିକ", "~ନ୍ୟାୟାଳୟ", "ବିଚାରପତି", "ଓକିଲ", "ପୋଲିସ", "ପ୍ରଶାସନ",
                         "ପ୍ରଶାସକ", "ଅଧିକାରୀ", "ରାଷ୍ଟ୍ରଦୂତ", "କୂଟନୀତି", "ବୈଦେଶିକ", "ଜାତିସଂଘ", "ମଣ୍ଡଳୀ",
                         "ମେୟର", "ପଞ୍ଚାୟତ", "ପୌରପାଳିକା", "ନୀତି$", "ଅପରାଧ", "ଦୁର୍ନୀତି", "ସଭ୍ୟ$", "ସଦସ୍ୟ$",
                         "ପ୍ରତୀକ")),
    (["society"], words("~ସମାଜ", "ସାମାଜିକ", "ଜନଜାତି", "ଆଦିବାସୀ", "ଜାତି$", "ଦଳିତ", "ନାରୀବାଦ", "ନାରୀ$",
                        "ଅଧିକାର", "ସଂସ୍ଥା", "ସଙ୍ଗଠନ", "ସଂଗଠନ", "ସ୍ୱେଚ୍ଛାସେବୀ", "ସଂସ୍କାରକ", "କାର୍ଯ୍ୟକର୍ତ୍ତା",
                        "କର୍ମୀ$", "ଜନସଂଖ୍ୟା", "ପରିବାର$", "ବିବାହ", "ଗୋଷ୍ଠୀ", "ସମ୍ପ୍ରଦାୟ", "ସାନ୍ତାଳ$",
                        "କନ୍ଧ$", "ସଂଘ$", "ଅନୁସୂଚିତ", "ପରୋପକାରୀ", "ଜନହିତୈଷୀ")),
    (["geography"], words("ଭୂଗୋଳ", "ଭୌଗଳିକ", "ଭୌଗୋଳିକ", "~ସହର", "ନଗର$", "ନଗରୀ", "ଗାଆଁ", "ଗାଁ", "ଗ୍ରାମ$",
                          "ଜିଲ୍ଲା", "ବ୍ଲକ", "ତହସିଲ", "ଉପଖଣ୍ଡ", "ପ୍ରଦେଶ", "ରାଜ୍ୟ", "କେନ୍ଦ୍ରଶାସିତ", "ଦେଶ",
                          "ମହାଦେଶ", "ରାଜଧାନୀ", "ନଦୀ", "ହ୍ରଦ", "ସମୁଦ୍ର", "ସାଗର$", "ମହାସାଗର", "ପର୍ବତ", "ପାହାଡ଼",
                          "~ଦ୍ୱୀପ", "ଜଳପ୍ରପାତ", "ବେଳାଭୂମି", "ବନ୍ଦର", "ଅଞ୍ଚଳ", "ଉପତ୍ୟକା", "ମରୁଭୂମି",
                          "ଜଳଭଣ୍ଡାର", "ବନ୍ଧ$", "ପର୍ଯ୍ୟଟନ", "ଦର୍ଶନୀୟ", "ସ୍ଥାନ$", "ସ୍ଥଳୀ", "~ଷ୍ଟେସନ",
                          "ବିମାନବନ୍ଦର", "ପାର୍କ")),  # not bare continents or countries: places, see WEAK_GEOGRAPHY
]
# After the topic rules: award and other people categories that name no field.
PERSON_GENERIC = words("~ସମ୍ମାନିତ", "ବିଜେତା", "ବିଜୟୀ", "ବିଜୟିନୀ", "ପ୍ରାପ୍ତ$", "ମହିଳା$", "ପୁରୁଷ$",
                       "ବ୍ୟକ୍ତି$", "ବ୍ୟକ୍ତିତ୍ୱ")  # not ବ୍ୟକ୍ତିଗତ "personal"
# A bare district ("କଟକ ଜିଲ୍ଲା") holds articles of every kind located there: weak evidence. (Bare
# countries and states, "ଭାରତ", "ଜାପାନ", match no rule; the parent walk leaves them without topic.)
WEAK_GEOGRAPHY = re.compile(fold(r"^\S+ ଜିଲ୍ଲା$", lower=False))
# People: a category of this kind makes the article a biography (unless Wikidata says otherwise).
PERSON_WORDS = words(
    "ଲୋକ$$", "ଜନ୍ମ$$", "ମୃତ୍ୟୁ$$", "ବ୍ୟକ୍ତି$", "ବ୍ୟକ୍ତିତ୍ୱ", "~ଶାସ୍ତ୍ରୀ$", "~ଅଭିନେତା", "~ଅଭିନେତ୍ରୀ", "ଲେଖକ", "ଲେଖିକା", "କବି$", "~ସାହିତ୍ୟିକ",
    "ଗାୟକ", "ଗାୟିକା", "~ଶିଳ୍ପୀ", "ଖେଳାଳି", "ରାଜନେତା", "ରାଜନୀତିଜ୍ଞ", "~ବୈଜ୍ଞାନିକ", "ବିଜ୍ଞାନୀ", "ସଭ୍ୟ$",
    "ସଦସ୍ୟ$", "ସାଂସଦ", "ବିଚାରପତି", "ନିର୍ଦ୍ଦେଶକ", "ପ୍ରଯୋଜକ", "~ସଙ୍ଗୀତକାର", "ଗୀତିକାର", "~ନାଟ୍ୟକାର",
    "ଚିତ୍ରକାର", "ଚିତ୍ରକର", "ଔପନ୍ୟାସିକ", "ଗାଳ୍ପିକ", "ପ୍ରାବନ୍ଧିକ", "ସମାଲୋଚକ", "ଅନୁବାଦକ", "ସାମ୍ବାଦିକ",
    "ସମ୍ପାଦକ", "ସଂଗ୍ରାମୀ", "ନେତା$", "~ମନ୍ତ୍ରୀ", "ରାଷ୍ଟ୍ରପତି", "ରାଜ୍ୟପାଳ", "ଡାକ୍ତର", "ଚିକିତ୍ସକ", "ଶିକ୍ଷାବିତ",
    "ଶିକ୍ଷକ", "ଅଧ୍ୟାପକ", "ଦାର୍ଶନିକ", "ଗବେଷକ", "ଉଦ୍ୟୋଗପତି", "ବ୍ୟବସାୟୀ", "ସମାଜସେବୀ", "କର୍ମୀ$",
    "କାର୍ଯ୍ୟକର୍ତ୍ତା", "ପ୍ରଚାରକ", "ନର୍ତ୍ତକ", "ନର୍ତ୍ତକୀ", "ସମ୍ରାଟ", "ରାଜା$", "ରାଣୀ$", "ରାଜକୁମାର", "ଶାସକ",
    "ସନ୍ଥ", "ସାଧୁ", "ଗୁରୁ$", "ଭକ୍ତ$", "~ସମ୍ମାନିତ", "ବିଜେତା", "ପ୍ରାପ୍ତ$", "~ଗଣିତଜ୍ଞ", "ଆଇନଜ୍ଞ",
    "କୂଟନୀତିଜ୍ଞ", "ଅର୍ଥଶାସ୍ତ୍ରୀ", "ଉଦ୍ଭାବକ", "ଇଞ୍ଜିନିୟର", "ସ୍ଥପତି", "ଧାବକ", "କ୍ରିକେଟର", "ଆଥଲେଟ",
    "ମଡେଲ", "ଜୀବନୀ$", "ପରିବେଶବିତ", "ନାରୀବାଦୀ", "ଗାନ୍ଧୀବାଦୀ", "ବିପ୍ଳବୀ", "ଶହୀଦ", "ସଂସ୍କାରକ", "ପ୍ରଶାସକ",
    "ଅଧିକାରୀ", "ମହିଳା$", "ପୁରୁଷ$", "ଦେବଦାସୀ", "ଯୋଗୀ", "ପୁରୋହିତ")
ODISHA_WORDS = words(
    "ଓଡ଼ିଶା", "ଓଡ଼ିଆ", "ଉତ୍କଳ", "ଓଲିଉଡ଼", "ଓଡ଼ିଶୀ", "କଳିଙ୍ଗ", "ଜଗନ୍ନାଥ", "ଗଡ଼ଜାତ", "ସମ୍ବଲପୁରୀ", "କୋଶଳୀ",
    "ଗଜପତି", "ଚିଲିକା", "କୋଣାର୍କ", "ଭୁବନେଶ୍ୱର", "କଟକ", "ପୁରୀ", "ରାଉରକେଲା", "ବ୍ରହ୍ମପୁର", "ବରହମପୁର",
    "କୋରାପୁଟ", "ମାଲକାନଗିରି", "ନବରଙ୍ଗପୁର", "ରାୟଗଡ଼ା", "କନ୍ଧମାଳ", "ଗଞ୍ଜାମ", "ନୟାଗଡ଼", "ଖୋର୍ଦ୍ଧା",
    "ଖୋର୍ଦ୍ଦା", "ଜଗତସିଂହପୁର", "କେନ୍ଦ୍ରାପଡ଼ା", "ଯାଜପୁର", "ଜାଜପୁର", "ଭଦ୍ରକ", "ବାଲେଶ୍ୱର", "ମୟୂରଭଞ୍ଜ",
    "କେନ୍ଦୁଝର", "କେଉଁଝର", "ସୁନ୍ଦରଗଡ଼", "ଝାରସୁଗୁଡ଼ା", "ସମ୍ବଲପୁର", "ଦେବଗଡ଼", "ଅନୁଗୋଳ", "ଅନୁଗୁଳ",
    "ଢେଙ୍କାନାଳ", "ବରଗଡ଼", "ସୁବର୍ଣ୍ଣପୁର", "ସୋନପୁର", "ବଲାଙ୍ଗୀର", "ନୂଆପଡ଼ା", "କଳାହାଣ୍ଡି", "ଭବାନୀପାଟଣା",
    "ପାରାଦ୍ୱୀପ", "ଗୋପାଳପୁର", "ହୀରାକୁଦ", "ମହାନଦୀ", "ସିମିଳିପାଳ", "ଭିତରକନିକା", "ବୌଦ୍ଧ ଜିଲ୍ଲା",
    "ollywood", "odia", "odisha", "orissa", "oriya")

YEAR_TITLE = re.compile(fold(r"^(ଖ୍ରୀଷ୍ଟପୂର୍ବ\s*)?\d+(\s*ଖ୍ରୀଷ୍ଟପୂର୍ବ)?$", lower=False))
MONTHS = fold("ଜାନୁଆରୀ|ଫେବୃଆରୀ|ମାର୍ଚ୍ଚ|ଅପ୍ରେଲ|ଏପ୍ରିଲ|ମଇ|ଜୁନ|ଜୁଲାଇ|ଅଗଷ୍ଟ|ସେପ୍ଟେମ୍ବର|ଅକ୍ଟୋବର|ନଭେମ୍ବର|ଡିସେମ୍ବର")
DATE_TITLE = re.compile(rf"^({MONTHS})\s*\d+$|^\d+\s*({MONTHS})$")
QUALIFIER = re.compile(r"\(([^()]+)\)\s*$")


def category_rule(name):
    """(topics, person, kind) of one category from its name alone. kind: 'person' (people
    category, no field), 'strong', 'weak', or None (nothing matched: walk to the parents)."""
    key = fold(name)
    person = bool(PERSON_WORDS.search(key))
    if re.search(r"[a-z]", key) and not re.search(f"[{ODIA_LETTER}]", key):
        person = bool(ENGLISH_PERSON.search(key))
        for pat, topics in ENGLISH_TOPICAL:
            if pat.search(key):
                return topics, person, "strong" if topics else "person"
        return [], person, None
    if PERSON_ONLY.search(key):
        return [], True, "person"
    if WEAK_GEOGRAPHY.search(key):  # before the rules: "ଗଜପତି ଜିଲ୍ଲା" is a district, not the dynasty
        return ["geography"], person, "weak"
    for topics, pat in TOPIC_RULES:
        if pat.search(key):
            return topics, person, "strong"
    if PERSON_GENERIC.search(key):
        return [], True, "person"
    return [], person, None


def load_category_graph():
    """({category: parent categories}, hidden categories) from the SQL dumps."""
    catpage = {r["page_id"]: r["page_title"].replace("_", " ")
               for r in sql_rows(dump_file("page.sql.gz")) if r["page_namespace"] == 14}
    hidden = {catpage[r["pp_page"]] for r in sql_rows(dump_file("page_props.sql.gz"))
              if r["pp_propname"] == "hiddencat" and r["pp_page"] in catpage}
    target = {r["lt_id"]: r["lt_title"].replace("_", " ")
              for r in sql_rows(dump_file("linktarget.sql.gz")) if r["lt_namespace"] == 14}
    parents = collections.defaultdict(set)
    for r in sql_rows(dump_file("categorylinks.sql.gz")):
        if r["cl_type"] == "subcat" and r["cl_from"] in catpage and r["cl_target_id"] in target:
            parents[catpage[r["cl_from"]]].add(target[r["cl_target_id"]])
    return parents, hidden


WALK_DEPTH = 3  # parent levels searched for a category no rule matches (the graph has cycles)
WALK_BLOCK = {fold(c) for c in ["ମୂଳ ପ୍ରସଙ୍ଗ ଶ୍ରେଣୀବିଭାଗ", "ଜ୍ଞାନ", "ବିଷୟବସ୍ତୁ", "ଉଇକିପିଡ଼ିଆ", "ବିଷୟ",
                                "ଶ୍ରେଣୀ", "ପ୍ରସଙ୍ଗ"]}  # hubs too general to say anything


class CategoryTopics:
    """Topics of categories: by rule on the name, else from the nearest parents a rule matches."""

    def __init__(self, parents, hidden):
        self.parents, self.hidden, self.cache = parents, hidden, {}

    def visible(self, name):
        key = fold(name)
        if name in self.hidden or MAINTENANCE.search(key):
            return False
        english_only = re.search(r"[a-z]", key) and not re.search(f"[{ODIA_LETTER}]", key)
        return not english_only or any(p.search(key) for p, _ in ENGLISH_TOPICAL)

    def __call__(self, name):
        """(topics {topic: weight}, person, source, strong) for one visible category. strong: a
        rule matched the category's own name (not a bare place, not a parent)."""
        if name in self.cache:
            return self.cache[name]
        topics, person, kind = category_rule(name)
        if kind in ("strong", "weak"):
            w = 1.0 if kind == "strong" else 0.4
            out = ({t: w / len(topics) for t in topics}, person, "category", kind == "strong")
        elif kind == "person":
            out = ({}, True, "category", False)
        elif re.fullmatch(r"\d+", fold(name)):  # "୨୦୧୯": events of a year, no topic of their own
            out = ({}, person, None, False)
        else:
            out = ({}, person, None, False)
            frontier, seen = [name], {name}
            for depth in range(WALK_DEPTH):
                nxt, hits = [], []
                for c in frontier:
                    for p in sorted(self.parents.get(c, ())):
                        if p in seen or not self.visible(p) or fold(p) in WALK_BLOCK:
                            continue
                        seen.add(p)
                        ptopics, _, pkind = category_rule(p)
                        if pkind in ("strong", "weak") and ptopics:
                            hits.append(ptopics)
                        elif pkind != "person":  # a people category ends the walk: its parents are places
                            nxt.append(p)
                if hits and depth == 0 and all(h == ["geography"] for h in hits):
                    break  # the category is a place ("ଜାପାନ" in "ଏସିଆର ଦେଶ"): its articles are
                    # things of that place (a dish, a myth, a census), not geography
                if hits:  # the nearest level with any match decides; 0.5 in all, split
                    scores = collections.Counter()
                    for ptopics in hits:
                        for t in ptopics:
                            scores[t] += 0.5 / len(hits) / len(ptopics)
                    out = (dict(scores), person, "parent_category", False)
                    break
                frontier = nxt
        self.cache[name] = out
        return out


CATEGORY_LINK = re.compile(r'<link rel="mw:PageProp/Category" href="\./([^"]+)"')


def html_categories():
    """{page id: [category, ...]} in page order, from the rendered HTML of the dump revision.
    Parsoid lists every category there, including those added by templates. (Category links
    nested inside references' data-mw repeat top-level ones, or are maintenance: ignored.)"""
    out = {}
    for p in sorted((HTML_DIR / DATE).glob("chunk-*.jsonl.gz")):
        with gzip.open(p, "rt", encoding="utf-8") as f:
            for line in f:
                r = json.loads(line)
                cats = []
                for href in CATEGORY_LINK.findall(r.get("html") or ""):
                    title = urllib.parse.unquote(href.split("#", 1)[0]).replace("_", " ")
                    name = title.split(":", 1)[1] if ":" in title else title
                    if name not in cats:
                        cats.append(name)
                out[r["id"]] = cats
    return out

HUMAN = "Q5"


def _table(spec):
    """{qid: topic} from {topic: "Q1 Q2 ..."} (topic "" = known, carries no topic)."""
    return {q: topic for topic, qids in spec.items() for q in qids.split()}


# Wikidata classes (P31 values, or their superclasses up to two P279 levels) -> topic. Curated
# from the classes the fallback items actually use (most frequent first), plus superclass anchors.
WD_CLASS_TOPICS = _table({
    "": "Q5 Q4167410 Q13406463 Q11862829 Q1047113 Q4671286 Q618779 Q5124642 Q107655869 Q27686 Q166118 "
        "Q2319498 Q41176 Q811979 Q1190554 Q1656682 Q212805 Q12356615 Q4330518 Q2245405 Q15275719 Q7191 "
        "Q107494520 Q11303 Q66715753 Q19692233 Q17379835 Q15474042 Q22808320 Q4663903",
    "geography": "Q486972 Q56436498 Q515 Q3957 Q58339518 Q1549591 Q57178953 Q82794 Q3624078 Q6256 Q18918041 "
                 "Q2393184 Q123705 Q1093829 Q257391 Q112099 Q2264924 Q174844 Q51929311 Q23442 Q46831 Q4022 "
                 "Q8502 Q35509 Q56061 Q902814 Q2322432 Q16830604 Q6988184 Q15642541 Q618123 Q2221906 Q532 "
                 "Q1637706 Q7930989 Q10864048 Q13220204 Q1149652 Q23397 Q34038 Q40080 Q165 Q9430 Q5107 "
                 "Q34442 Q269949 Q1248784 Q62447 Q94993988 Q55488 Q22808404 Q114782406 Q12323 Q350495 "
                 "Q1010155 Q22698 Q122987723 Q28872924 Q15840617 Q114496982 Q1323642 Q51576574 Q1381899 "
                 "Q5119 Q108178728 Q129319315 Q27096213 Q200250 Q15284",
    "health": "Q112193867 Q12136 Q18123741 Q929833 Q12140 Q8386 Q28885102 Q35456 Q112965645 Q169872 "
              "Q112826905 Q4936952 Q712378 Q796194 Q930752 Q2057971 Q219067 Q4941552 Q1814351 Q544006 Q12135 "
              "Q193078",
    "biology": "Q16521 Q55983715 Q713623 Q136772238 Q23038290 Q310890 Q502895 Q473972 Q1377575 Q7187 Q8054 "
               "Q2996394 Q84467700 Q144 Q26401003 Q729 Q756 Q29548 Q14349455 Q104053 Q14865855",
    "science": "Q113145171 Q11173 Q11344 Q79529 Q119892838 Q59199015 Q214609 Q118733587 Q2465832 Q523 "
               "Q634 Q3863 Q2537 Q318 Q6999 Q8063 Q7946 Q12089225 Q33104303 Q1322005 Q241284 Q147027 "
               "Q115949945 Q79782 Q37756 Q28732711",
    "mathematics": "Q3884033 Q1569997 Q207961 Q11348 Q246672 Q1936384 Q19821 Q331350 Q37555",
    "technology": "Q35127 Q33120876 Q615699 Q620615 Q2462003 Q3220391 Q1668024 Q122759350 Q7397 Q9143 "
                  "Q166142 Q9135 Q11016 Q42889 Q1420 Q11446 Q11436 Q40218 Q2133344 Q26540 Q728 Q15141321 "
                  "Q91908084 Q728937 Q10876391 Q67080166 Q140424465 Q100709275 Q765633 Q5503 Q3491904 "
                  "Q39546 Q1183543",
    "film": "Q11424 Q5398426 Q15416 Q24856 Q202866 Q506240 Q21191270 Q2431196 Q201658 Q15773347 Q196600",
    "literature": "Q7725634 Q47461344 Q571 Q8261 Q5185279 Q1002697 Q41298 Q11032 Q1110794 Q2085381 "
                  "Q5633421 Q23622 Q4263830 Q34770 Q1288568 Q33742 Q33384 Q25295 Q8192 Q17376908 Q20162172 "
                  "Q3658341 Q1114461 Q36244",
    "religion": "Q842402 Q44539 Q24398318 Q16970 Q32815 Q337986 Q845945 Q135419779 Q120680118 Q175288 "
                "Q134917286 Q135160342 Q44613 Q178885 Q979507 Q205985 Q494511 Q67200784 Q9174 Q13414953 "
                "Q179461 Q188602 Q1435771 Q375011 Q4271324 Q4504549 Q15135589 Q5766476 Q60469796",
    "education": "Q3918 Q875538 Q38723 Q9826 Q189004 Q3914 Q2385804 Q1664720",
    "economy": "Q4830453 Q783794 Q6881511 Q891723 Q22687 Q64027599 Q2424752 Q9378373 Q1167393",
    "politics": "Q7278 Q192611 Q327333 Q2659904 Q7188 Q11204 Q189445 Q35749 Q820655 Q7748 Q33283984 "
                "Q1137809 Q1307214 Q4164871 Q484652 Q245065 Q107359024 Q2135465 Q7755 Q139902776 Q471855",
    "history": "Q1336152 Q3024240 Q839954 Q178561 Q198 Q180684 Q48349 Q417175 Q164950 Q1785071 Q16560 "
               "Q162875 Q4989906 Q28171280 Q625298 Q1078765 Q57821 Q23413 Q13418847 Q124734 Q10931 Q41397 "
               "Q173462 Q13634374",
    "arts": "Q132241 Q33506 Q17431399 Q188451 Q107357104 Q746549 Q2095 Q5159627 Q21198342 Q735 Q3305213 "
            "Q860861 Q34379 Q11639 Q40050 Q56055944 Q1053916 Q166902 Q327496 Q28099834 Q866153 Q124748491 "
            "Q11460 Q161928 Q14952",
    "society": "Q41710 Q484416 Q133311 Q16334295 Q43229 Q163740 Q708676 Q157031 Q49773 Q175331 Q79913 "
               "Q8461 Q1510761 Q1571836 Q11255025 Q83267 Q223642 Q853725 Q8445 Q93200 Q1124860",
    "sports": "Q31629 Q349 Q847017 Q476028 Q12973014 Q27020041 Q18608583 Q16510064 Q500834 Q1076486 Q483110",
    "calendar": "Q1445650 Q2558684 Q1197685 Q577 Q3186692 Q47150325 Q39911 Q578",
})
# Occupations (P106 values, or their superclasses) -> topic: what the person is known for.
WD_OCCUPATION_TOPICS = _table({
    "": "Q12859263 Q12356615 Q512314 Q9352089 Q45199 Q12038843 Q1937330 Q41546637",
    "literature": "Q36180 Q49757 Q6625963 Q482980 Q214917 Q333634 Q18814623 Q15949613 Q4853732 Q1930187 "
                  "Q14467526 Q11774202 Q1607826 Q8178443 Q864380 Q8246794 Q12144794 Q3579035 Q2516866 "
                  "Q1086863 Q6051619 Q15980158 Q18844224 Q18524037 Q17167049 Q4263842 Q11774156 Q18939491",
    "politics": "Q82955 Q40348 Q193391 Q16533 Q212238 Q185351 Q384593 Q808967 Q372436 Q11771944 Q6021340",
    "film": "Q33999 Q10800557 Q2526255 Q28389 Q10798782 Q3282637 Q4610556 Q3455803 Q578109 Q947873 "
            "Q18581305 Q2405480 Q7042855 Q69423232 Q1323191 Q222344 Q1198887 Q245068 Q15077007 Q1414443 "
            "Q1053574 Q1415090 Q13590141 Q2906862 Q17125263 Q4220892 Q706364",
    "arts": "Q177220 Q36834 Q1028181 Q639669 Q5716684 Q753110 Q2490358 Q2259451 Q483501 Q33231 Q822146 "
            "Q1281618 Q55960555 Q183945 Q644687 Q488205 Q1755412 Q42973 Q3501317 Q14915627 Q11569986 "
            "Q5322166 Q1114448 Q3387717 Q437512 Q4164507 Q16145150 Q2519376 Q3374326 Q2707485 Q15296811 "
            "Q3391743 Q235858 Q2992505 Q156839",
    "economy": "Q131524 Q43845 Q188094 Q806798 Q2961975 Q13235160 Q215536 Q484876 Q131512 Q1420489",
    "religion": "Q4964182 Q15995642 Q432386 Q219477 Q2901587 Q1234713 Q42603 Q250867 Q2259532 Q42857",
    "health": "Q39631 Q774306 Q1919436 Q186360 Q3264451 Q11974939",
    "education": "Q1622272 Q37226 Q3400985 Q1231865 Q974144",
    "history": "Q30242234 Q47064 Q201788 Q1097498 Q116 Q3242115 Q189290 Q4991371 Q10669499 Q3621491 "
               "Q2478141 Q535214 Q12097",
    "society": "Q7019111 Q15253558 Q12362622 Q1476215 Q16611574 Q28692502 Q2306091 Q16323111 Q34074720 "
               "Q8359428 Q16003550 Q1021386 Q11499147 Q11588306",
    "technology": "Q81096 Q82594 Q2095549 Q205375 Q5482740 Q13582652",
    "science": "Q169470 Q901 Q1650915 Q593644 Q11631 Q212980 Q520549",
    "mathematics": "Q170790",
    "biology": "Q3578589 Q2374149 Q16060693 Q864503 Q350979",
    "geography": "Q11900058 Q901402",
    "sports": "Q11513337 Q17486376 Q10843263 Q13474373 Q12369333 Q12299841 Q13381376 Q19204627 Q937857 "
              "Q13382355 Q2066131 Q10833314 Q13141064 Q10873124 Q11338576 Q10843402 Q50995749 Q41583 "
              "Q2309784 Q15117395 Q22122536",
})

TOPICS_SIDECAR = {
    "name": "topics",
    "description": "Subject topics of each article from its categories, its Wikidata item and its title, "
                   "for weighting a school-oriented training mix. One JSON object per line (topics.jsonl), "
                   "one per article of the corpus, in corpus order. See quality/topics.md.",
    "source": f"{WIKI}-{DATE}: categories from the rendered HTML of the dump revision "
              f"(raw/html); hidden categories and the category tree from the page, page_props, "
              f"linktarget and categorylinks SQL dumps; for articles without a direct category or "
              f"title match, the Wikidata item from page_props and its P31/P106/P2175/P279 fetched "
              f"from the Wikidata API, "
              f"wbgetentities (raw/{WD_ITEMS.name}); annotate.py topics",
    "created": None,  # set when written
    "columns": {
        "id": "page id (integer)",
        "title": "article title",
        "categories": "visible content categories, in page order: hidden (hiddencat) and maintenance or "
                      "tracking categories removed (JSON array)",
        "topics": "topics with enough evidence, strongest first (JSON array; vocabulary in 'topics' below)",
        "primary_topic": "the strongest topic, or null when there is no evidence",
        "is_person": "a biography: Wikidata P31 = human (Q5), else a people category "
                     "(births, deaths, occupations, people of a place)",
        "odisha": "Odisha-related: a category or the title names Odisha, Odia, Ollywood, Jagannath, "
                  "Utkal, Kalinga or an Odisha district or city",
        "school_relevant": "primary_topic is a school subject (science, biology, health, mathematics, "
                           "technology, geography, history, politics, economy, literature, society, "
                           "education), or arts/religion when odisha; for a biography only science, "
                           "biology, health, mathematics, technology, history or literature",
        "topic_source": "evidence behind primary_topic, '+'-joined: category (a rule on one of the "
                        "article's categories), parent_category (a rule on a parent category up to 3 "
                        "levels up), title (year or date page, or a '(qualifier)'), wikidata (P31/P106); "
                        "'none' when untagged",
        "wikidata": "the article's Wikidata item (QID) from page_props, or null",
        "topic_scores": "a string holding JSON {topic: score}: the summed evidence weights behind 'topics'",
    },
    "depends_on_text": False,
    "topics": TOPICS,
}


class WikidataTopics:
    """Topics from Wikidata: P31 (instance of) for every item, P106 (occupation) for people.
    Classes not in the curated tables are looked up through up to two P279 (subclass of) levels."""

    def __init__(self):
        self.items, self.classes = {}, {}
        if WD_ITEMS.exists() and WD_CLASSES.exists():
            self.items = {r["qid"]: r for r in read_jsonl_gz(WD_ITEMS)}
            self.classes = {r["qid"]: r for r in read_jsonl_gz(WD_CLASSES)}
        self.cache = {}

    def class_topic(self, qid, table):
        key = (qid, id(table))
        if key not in self.cache:
            topic, frontier = table.get(qid), [qid]
            for _ in range(2):
                if topic is not None:
                    break
                frontier = [s for c in frontier for s in self.classes.get(c, {}).get("P279", [])]
                found = [table[s] for s in frontier if table.get(s)]
                topic = found[0] if found else None
            self.cache[key] = topic or ""
        return self.cache[key]

    def __call__(self, qid):
        """({topic: weight}, {topic: first position}, is_human or None if unknown). Weights: P31
        classes share 1.0, a person's P106 occupations share 1.5. Positions follow the statement
        order (a person's first-listed occupation wins a tie)."""
        item = self.items.get(qid)
        classes = (item or {}).get("P31") or (item or {}).get("P279")  # a class item: its superclasses
        if not classes:
            return {}, {}, None
        human = HUMAN in item["P31"]
        scores, first = collections.Counter(), {}
        for prop, table, weight in (("P31", WD_CLASS_TOPICS, 1.0), ("P106", WD_OCCUPATION_TOPICS, 1.5)):
            if prop == "P106" and not human:
                continue
            found = [t for q in (classes if prop == "P31" else item[prop]) if (t := self.class_topic(q, table))]
            if prop == "P31" and item.get("P2175"):
                found.insert(0, "health")  # treats a medical condition: a medication, whatever its class
            for t in found:
                scores[t] += weight / len(found)
                first.setdefault(t, len(first))
        return dict(scores), first, human


def category_title_evidence(title, cats, cat_topics):
    """Topic evidence from an article's categories and title."""
    visible = [c for c in cats if cat_topics.visible(c)]
    scores, sources, first = collections.Counter(), collections.defaultdict(set), {}
    person_votes, strong = 0, False
    for pos, c in enumerate(visible):
        topics, person, source, is_strong = cat_topics(c)
        person_votes += person
        strong |= is_strong
        for t, w in topics.items():
            scores[t] += w
            sources[t].add(source)
            first.setdefault(t, pos)
    key = fold(title)
    if YEAR_TITLE.search(key) or DATE_TITLE.search(key):
        scores["calendar"] += 3
        sources["calendar"].add("title")
        strong = True
    elif m := QUALIFIER.search(title):
        topics, person, kind = category_rule(m.group(1))
        person_votes += person
        topics = [t for t in topics if t != "calendar"]  # "(୨୦୧୫)" dates a film, it is no year page
        if kind == "strong" and topics:
            strong = True
            for t in topics:
                scores[t] += 1.5 / len(topics)
                sources[t].add("title")
    odisha = any(ODISHA_WORDS.search(fold(c)) for c in visible) or bool(ODISHA_WORDS.search(key))
    return {"visible": visible, "scores": scores, "sources": sources, "person_votes": person_votes,
            "odisha": odisha, "first": first, "strong": strong}


def category_title_pass():
    """{page id: evidence} for every article in the index, from categories and titles only."""
    print("reading categories from the rendered HTML", file=sys.stderr)
    cats = html_categories()
    parents, hidden = load_category_graph()
    cat_topics = CategoryTopics(parents, hidden)
    return {a["id"]: category_title_evidence(a["title"], cats.get(a["id"], []), cat_topics)
            for a in load_index()}


def decide(ev, wd):
    """(topics, primary, source, is_person, scores) from the evidence. Wikidata (wd = (topic
    weights, first positions, is_human)) is added when no rule matched a category's own name or
    the title: then the evidence is at most a parent category (0.5) or a bare place (0.4), and
    Wikidata's P31 (1.0) or P106 (1.5) outweighs it."""
    scores, sources = collections.Counter(ev["scores"]), collections.defaultdict(set, ev["sources"])
    wd_scores, wd_first, human = wd
    first = dict(ev["first"])
    if not ev["strong"] and wd_scores:
        for t, w in wd_scores.items():
            scores[t] += w
            sources[t].add("wikidata")
        first = {t: wd_first.get(t, 100 + first.get(t, 0)) for t in scores}
    is_person = human if human is not None else ev["person_votes"] > 0
    if not scores:
        return [], None, "none", is_person, {}
    # Ties: the topic of the earlier category (editors put the main one first) or the earlier
    # Wikidata statement, then TOPICS order.
    order = list(TOPICS)
    ranked = sorted(scores, key=lambda t: (-round(scores[t], 6), first.get(t, 999), order.index(t)))
    top = scores[ranked[0]]
    topics = [t for t in ranked if scores[t] >= max(0.5, 0.35 * top)] or ranked[:1]
    primary = ranked[0]
    source = "+".join(s for s in ("category", "parent_category", "title", "wikidata") if s in sources[primary])
    return topics, primary, source, is_person, {t: round(scores[t], 3) for t in ranked}


def school_relevant(primary, is_person, odisha):
    if primary is None:
        return False
    if is_person:
        return primary in SCHOOL_PERSON_TOPICS
    return primary in SCHOOL_TOPICS or (odisha and primary in {"arts", "religion"})


def topics(args):
    base = category_title_pass()
    wd = WikidataTopics()
    qids = wikibase_items()
    if not wd.items:
        print("  no Wikidata cache (run `wikidata`): categories and titles only", file=sys.stderr)
    rows = []
    for a in load_index():
        ev = base[a["id"]]
        topics_, primary, source, is_person, scores = decide(ev, wd(qids.get(a["id"], "")))
        rows.append({"id": a["id"], "title": a["title"], "categories": ev["visible"], "topics": topics_,
                     "primary_topic": primary, "is_person": bool(is_person), "odisha": ev["odisha"],
                     "school_relevant": school_relevant(primary, is_person, ev["odisha"]),
                     "topic_source": source, "wikidata": qids.get(a["id"]),
                     "topic_scores": json.dumps(scores, ensure_ascii=False)})
    rows = restrict_to_corpus(rows)
    write_annotation("topics", rows, TOPICS_SIDECAR)
    n_fallback = sum(1 for a in base if not base[a]["strong"] and a in qids)
    write_report("topics.md", topics_report(rows, wd, n_fallback))
    return rows


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


CHECK = QUALITY / "topics-check.tsv"  # hand labels for the precision check (see its header)


def wilson(k, n, z=1.96):
    """95% Wilson score interval of a proportion k/n, as 'lo–hi' percentages."""
    if not n:
        return "–"
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * (p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5 / (1 + z * z / n)
    return f"{100 * max(0, centre - half):.0f}–{100 * min(1, centre + half):.0f}%"


def read_check():
    if not CHECK.exists():
        return []
    rows = []
    with open(CHECK, encoding="utf-8") as f:
        for line in f:
            if line.startswith("#") or line.startswith("id\t") or not line.strip():
                continue
            i, title, which, topics_, person, odisha, note = line.rstrip("\n").split("\t")
            rows.append({"id": int(i), "title": title, "set": which, "topics": set(topics_.split(",")),
                         "is_person": person == "1", "odisha": odisha == "1", "note": note})
    return rows


def md_cell(s):
    return str(s).replace("|", "\\|").replace("\n", " ")


def health_note(rows):
    """How much of `health` is machine-assisted translation (needs annotations/translation.jsonl
    covering the corpus: run `translation` first)."""
    path = ANN / "translation.jsonl"
    tr = {}
    if path.exists():
        with open(path, encoding="utf-8") as f:
            tr = {r["id"]: r for r in map(json.loads, f)}
    health = [r["id"] for r in rows if r["primary_topic"] == "health"]
    if not tr or any(i not in tr for i in health):
        print("  annotations/translation.jsonl missing or stale (run `translation` first): no health note",
              file=sys.stderr)
        return ""
    mt = [i for i in health if tr[i]["translated"]]
    med = [i for i in mt if tr[i]["source_lang"] == "mdwiki"
           or (tr[i]["source_title"] or "").startswith("User:Mr. Ibrahem/")]
    return (f"`health` is large because of WikiProject Medicine's translation drive: {len(mt):,} of its "
            f"{len(health):,} articles are machine-assisted translations (`translated` in "
            f"`annotations/translation.jsonl`), {len(med):,} of them from MDWiki sources, mostly drug and "
            "disease pages. ")


def topics_report(rows, wd, n_fallback):
    corpus = load_corpus()
    words = {i: c["words"] for i, c in corpus.items()}
    n, total_words = len(rows), sum(words.values())
    tagged = [r for r in rows if r["primary_topic"]]
    untagged = [r for r in rows if not r["primary_topic"]]
    by_primary = collections.defaultdict(list)
    for r in rows:
        by_primary[r["primary_topic"]].append(r)
    listing = collections.Counter(t for r in rows for t in r["topics"])
    sources = collections.Counter(r["topic_source"] for r in rows)
    lines = [
        "# Topic tags for Odia Wikipedia",
        "",
        f"Built by `annotate.py topics` on {now()[:10]} for the {n:,} articles of the corpus "
        f"(`{CORPUS.name}`, {total_words:,} Odia words). Output: `annotations/topics.jsonl`, one JSON object "
        "per article in corpus order; the columns are described in `annotations/topics.json`.",
        "",
        f"**Coverage: {pct(len(tagged), n)} of articles ({pct(sum(words[r['id']] for r in tagged), total_words)} of "
        f"words) have a topic.** Precision of `primary_topic`, checked by hand on a held-out random sample: see "
        "[Precision](#precision).",
        "",
        "## Topics",
        "",
        "| Topic | Covers | Articles (primary) | Share | Odia words | Share | Articles listing it | "
        "Biographies | Odisha |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for t, desc in TOPICS.items():
        sel = by_primary.get(t, [])
        w = sum(words[r["id"]] for r in sel)
        lines.append(f"| `{t}` | {desc} | {len(sel):,} | {pct(len(sel), n)} | {w:,} | {pct(w, total_words)} | "
                     f"{listing[t]:,} | {sum(r['is_person'] for r in sel):,} | {sum(r['odisha'] for r in sel):,} |")
    w = sum(words[r["id"]] for r in untagged)
    lines.append(f"| (none) | no evidence | {len(untagged):,} | {pct(len(untagged), n)} | {w:,} | "
                 f"{pct(w, total_words)} | | {sum(r['is_person'] for r in untagged):,} | "
                 f"{sum(r['odisha'] for r in untagged):,} |")
    school = [r for r in rows if r["school_relevant"]]
    people = [r for r in rows if r["is_person"]]
    odisha = [r for r in rows if r["odisha"]]
    ent = [r for r in people if r["primary_topic"] in ("film", "sports")]
    lines += [
        "",
        f"- **`school_relevant`**: {len(school):,} articles ({pct(len(school), n)}), "
        f"{sum(words[r['id']] for r in school):,} words ({pct(sum(words[r['id']] for r in school), total_words)}).",
        f"- **`is_person`** (biographies): {len(people):,} articles ({pct(len(people), n)}), "
        f"{sum(words[r['id']] for r in people):,} words. Film and sports biographies: {len(ent):,} articles, "
        f"{sum(words[r['id']] for r in ent):,} words ({pct(sum(words[r['id']] for r in ent), total_words)}).",
        f"- **`odisha`**: {len(odisha):,} articles ({pct(len(odisha), n)}), "
        f"{sum(words[r['id']] for r in odisha):,} words ({pct(sum(words[r['id']] for r in odisha), total_words)}).",
        f"- {sum(len(r['topics']) > 1 for r in rows):,} articles list more than one topic.",
        "",
        health_note(rows) + "`calendar` is the bot-made year and date pages. `politics` includes "
        f"{sum(1 for r in rows if any('ବିଧାନ ସଭା' in c for c in r['categories']) and r['is_person']):,} "
        "biographies of Odisha assembly members.",
        "",
        "## Method",
        "",
        "Every article gets evidence from three sources, strongest first:",
        "",
        "1. **Its categories.** They come from the rendered HTML of the dump revision (`raw/html`), where "
        "Parsoid lists every category as `<link rel=\"mw:PageProp/Category\">`, including those added by "
        "templates. (Category links nested in references' `data-mw` repeat top-level ones or are maintenance, "
        "and are ignored.) Hidden categories (`hiddencat` in `page_props`) are dropped, and so are visible "
        "maintenance ones: English tracking categories that templates copied from English Wikipedia "
        "(`Pages using …`, `CS1 …`, `Articles with …`) and Odia cleanup ones (ଆଧାରହୀନ \"unreferenced\", "
        "ସଜଡ଼ା ହେବାକୁ \"to be cleaned up\", bot and edit-a-thon bookkeeping). What is left is the `categories` "
        "column.",
        "2. **Its title**: year and date pages (`୧୯୪୬`, `୧୯ ମାର୍ଚ୍ଚ`) are `calendar`, and a qualifier "
        "(`(ଓଡ଼ିଆ କଥାଚିତ୍ର)`, `(ରାଜନେତା)`) is read like a category.",
        f"3. **Its Wikidata item**, only when no rule matches a category's own name or the title ({n_fallback:,} "
        "dump articles): P31 (instance of), or P279 (subclass of) for a concept item that has no P31 (rice, "
        "hardware), P106 (occupation, for people) and P2175 (medical condition treated: a drug), mapped to "
        "topics through curated tables of the classes these items use "
        f"({len(WD_CLASS_TOPICS)} classes, {len(WD_OCCUPATION_TOPICS)} occupations), and through up to two P279 "
        "(subclass of) levels for the rest.",
        "",
        "**Category rules.** A category name is matched against ordered keyword rules. Names are folded first, "
        "for matching only: nukta, vowel length, ଵ/ୱ, ୟ/ଯ, ଙ୍/ଂ, ZWJ/ZWNJ and Odia digits (never NFC). The first "
        "rule that matches decides, and specific rules come before general ones (ଚିକିତ୍ସା ବିଜ୍ଞାନ \"medical "
        "science\" is `health`, not `science`; ହିନ୍ଦୁ ପର୍ବ \"Hindu festivals\" is `religion`, not `arts`; "
        "କଥାଚିତ୍ର ଗୀତିକାର \"film lyricists\" is `film`). Words match at the start of an Odia word, so ପର୍ବ "
        "\"festival\" does not match ପର୍ବତ \"mountain\" and ଗ୍ରହ \"planet\" does not match ସଂଗ୍ରହ \"collection\".",
        "",
        "- *People categories carry no topic*: births, deaths, ଜୀବିତ ବ୍ୟକ୍ତି \"living people\", \"people of "
        "<place>\" (କଟକ ଜିଲ୍ଲାର ଲୋକ), and awards that name no field (ପଦ୍ମଶ୍ରୀ ସମ୍ମାନିତ). A biography therefore "
        "gets the topic of its occupation categories (ଓଡ଼ିଆ କବି \"Odia poets\" → `literature`, "
        "ହିନ୍ଦୀ କଥାଚିତ୍ର ଅଭିନେତ୍ରୀ → `film`) or field awards (ସାହିତ୍ୟ ଏକାଡେମୀ → `literature`).",
        "- *A bare district* (`କଟକ ଜିଲ୍ଲା`) holds articles of every kind located there, so it is weak "
        "`geography` evidence (0.4, against 1.0 for a rule match). A bare country, state or continent "
        "(`ଭାରତ`, `ଜାପାନ`, `ଏସିଆ`) matches no rule at all (see the walk).",
        "- *No rule matches*: the category's parents are searched, up to 3 levels up (the graph from the "
        "`categorylinks`/`linktarget`/`page` dumps has cycles). The nearest level with a match decides, at weight "
        "0.5. The walk does not continue through people categories or through general hubs. If the first level "
        "only says `geography`, the category is itself a place (ଜାପାନ in ଏସିଆର ଦେଶ \"Asian countries\"): its "
        "articles are things of that place (a dish, a myth, a census), so the walk gives nothing. A bare year "
        "category (`୨୦୧୯`) gives nothing either.",
        "",
        "**Scores.** Each category adds its weight (split across its topics), a title adds 3 (year or date) or "
        "1.5 (qualifier), and Wikidata adds 1.0 (P31, split) and 1.5 (P106, split). `primary_topic` is the "
        "highest score. Ties go to the topic of the earlier category, since editors list the main one first, or "
        "the earlier Wikidata statement. `topics` lists every topic scoring at least 0.5 and 35% of the top "
        "score. `topic_scores` keeps the sums. `topic_source` names the evidence behind the primary topic: "
        + ", ".join(f"`{k}` {v:,}" for k, v in sources.most_common()) + ".",
        "",
        "**Flags.**",
        "",
        "- `is_person`: Wikidata P31 = human, when the item was fetched; else any people or occupation "
        "category.",
        "- `odisha`: a category or the title names Odisha, Odia, Ollywood, Utkal, Kalinga, Jagannath, or one of "
        "the 30 districts or the main towns. District names are matched at the start of a word, and ବୌଦ୍ଧ only "
        "as \"Boudh district\", because it also means Buddhist.",
        "- `school_relevant`: `primary_topic` is taught directly in Odisha's classes 1–10 (science, biology, "
        "health, mathematics, technology, geography, history, politics or civics, economy, literature and "
        "language, society, education), or is `arts`/`religion` and `odisha` (Odisha's culture is in the social "
        "science syllabus). For a biography, only science, biology, health, mathematics, technology, history "
        "and literature count: a scientist, a freedom fighter or a poet, but not a sitting MLA or an actor. It "
        "is a subject flag. Combine it with `bot_created`, `words` and `odia_ratio` from the corpus for quality.",
        "",
        "## Coverage",
        "",
        f"{len(tagged):,} of {n:,} articles ({pct(len(tagged), n)}) have a topic; {len(untagged):,} "
        f"({pct(len(untagged), n)}, {pct(sum(words[r['id']] for r in untagged), total_words)} of words) have none. "
        "Why the untagged have none:",
        "",
    ]
    reasons = collections.Counter()
    examples = collections.defaultdict(list)
    for r in untagged:
        item = wd.items.get(r["wikidata"] or "")
        if not r["wikidata"]:
            why = "no Wikidata item, and no category or title rule"
        elif item is None:
            why = "Wikidata item not fetched (a weak category matched)"
        elif not item["P31"] and not item.get("P279"):
            why = "Wikidata item without P31 or P279"
        elif HUMAN in item["P31"]:
            why = "a person with no field in categories or Wikidata P106"
        elif set(item["P31"]) & {"Q13406463", "Q4167410"}:
            why = "a list or disambiguation page"
        else:
            why = "Wikidata class not mapped to a topic"
        reasons[why] += 1
        examples[why].append(r["title"])
    for why, k in reasons.most_common():
        lines.append(f"- {why}: {k:,} (e.g. {', '.join(examples[why][:5])})")
    # Precision
    check = read_check()
    by_id = {r["id"]: r for r in rows}
    check = [c for c in check if c["id"] in by_id]
    lines += ["", "## Precision", "",
              "Hand check by reading each article's title and lead (not its categories), labels in "
              "`quality/topics-check.tsv`. A prediction is correct when `primary_topic` is one of the "
              "acceptable topics given for the article, usually one, two or three where the subject straddles "
              "(a temple's history, a dancer who acts).",
              "",
              "| Sample | Articles | Tagged | `primary_topic` correct | 95% interval | `is_person` agrees | "
              "`odisha` agrees |",
              "|---|---:|---:|---:|---:|---:|---:|"]
    labels = {"dev": "dev: random, used to tune the rules", "test": "**test: random, held out**",
              "stratified": "stratified: 8 per small topic"}
    pre_independence = sum(1 for r in rows if any("ସ୍ୱାଧୀନତା ପୂର୍ବର" in c and "ସଭ୍ୟ" in c for c in r["categories"]))
    errors = []
    for which in ("test", "dev", "stratified"):
        sel = [c for c in check if c["set"] == which]
        if not sel:
            continue
        tg = [c for c in sel if by_id[c["id"]]["primary_topic"]]
        ok = [c for c in tg if by_id[c["id"]]["primary_topic"] in c["topics"]]
        errors += [(which, c) for c in tg if c not in ok]
        lines.append(f"| {labels[which]} | {len(sel)} | {len(tg)} | {len(ok)} ({pct(len(ok), len(tg))}) | "
                     f"{wilson(len(ok), len(tg))} | "
                     f"{sum(by_id[c['id']]['is_person'] == c['is_person'] for c in sel)}/{len(sel)} | "
                     f"{sum(by_id[c['id']]['odisha'] == c['odisha'] for c in sel)}/{len(sel)} |")
    lines += ["",
              "The test set was labelled after the rules were frozen. Its one systematic error, members of the "
              "pre-independence assemblies (ସ୍ୱାଧୀନତା ପୂର୍ବର … ବିଧାନ ସଭାର ସଭ୍ୟ) tagged `history` through the word "
              f"ସ୍ୱାଧୀନତା \"independence\", was then fixed (a legislator rule before `history`, {pre_independence:,} "
              "articles). At first "
              "scoring the test set gave 95 of 97. The stratified set was drawn and labelled after that fix, and "
              "gave 64 of 72 at first scoring. "
              "Changed after the stratified check (the tables above are recomputed with the final rules): "
              "Wikidata P279 for items without P31, and more Wikidata class anchors (these only reach articles "
              "with no direct category or title match); a bare district category is weak `geography` before any "
              "other rule (ଗଜପତି ଜିଲ୍ଲା is the district, not the Gajapati dynasty; ବୌଦ୍ଧ ଜିଲ୍ଲା is not Buddhism); "
              "bare continents (ଏସିଆ, ଆମେରିକା) no longer count as `geography`; and person words (ବ୍ୟକ୍ତି "
              "\"person\" no longer matches ବ୍ୟକ୍ତିଗତ \"personal\"; -ଶାସ୍ତ୍ରୀ \"scholar of\" and English "
              "occupation categories count). They mostly give a topic to articles that had none; in the checks, "
              "only the kimono changed (`economy` to `arts`, through the new clothing anchor).",
              "",
              "Per predicted topic (random samples dev + test, and the stratified sample):",
              "",
              "| `primary_topic` | Random: correct / predicted | Stratified: correct / predicted |",
              "|---|---:|---:|"]
    for t in TOPICS:
        cells = []
        for sets in (("dev", "test"), ("stratified",)):
            sel = [c for c in check if c["set"] in sets and by_id[c["id"]]["primary_topic"] == t]
            ok = sum(by_id[c["id"]]["primary_topic"] in c["topics"] for c in sel)
            cells.append(f"{ok} / {len(sel)}" if sel else "–")
        lines.append(f"| `{t}` | {cells[0]} | {cells[1]} |")
    lines += ["", "Every error in the checks:", ""]
    for which, c in errors:
        r = by_id[c["id"]]
        lines.append(f"- {which}: **{c['title']}** ({c['note']}): `{r['primary_topic']}` from "
                     f"{r['topic_source']}, expected {'/'.join(sorted(c['topics']))}")
    untagged_checked = [c for c in check if not by_id[c["id"]]["primary_topic"]]
    if untagged_checked:
        lines.append("- untagged in the checks: " + ", ".join(f"{c['title']} ({c['note']})"
                                                             for c in untagged_checked))
    lines += ["",
              "## Known weaknesses",
              "",
              "- **Categories are only as good as the editors'.** A political scientist in ବୈଜ୍ଞାନିକ "
              "\"scientists\" becomes `science`; a writer categorised only as a Dalit activist and a feminist "
              "becomes `society`.",
              "- **The parent walk and bare places are the weakest evidence.** On 30 walk-only articles, about "
              "a third were wrong before the place rule and the Wikidata fallback were added. "
              f"{sources.get('parent_category', 0)} articles still rest on the walk alone. The `geography` ones "
              "among them are natural disasters (cyclones, tsunamis).",
              "- **Wikidata drugs look like chemicals**: a drug with no P2175 (medical condition treated) is "
              "`science`, not `health`.",
              "- **Wikidata is live, not a dump.** The items were fetched on the build date (cached in "
              f"`raw/{WD_ITEMS.name}`), so they can differ from Wikidata on 2026-09-01.",
              "- **`odisha` needs a category or the title.** An Odia writer or journalist with no categories is "
              "missed; Wikidata's place of birth is not used.",
              "- **Borderline subjects**: a nakshatra is `science` (astronomy) here, `religion` (astrology) to "
              "some readers; a Wikipedia language edition is `technology`.",
              ""]
    return "\n".join(lines)


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
    ap.add_argument("step", choices=["download", "wikidata", "translation", "topics", "all"])
    ap.add_argument("--refresh", action="store_true", help="fetch Wikidata again (wikidata)")
    args = ap.parse_args()
    if args.step in ("download", "all"):
        download(args)
    if args.step in ("wikidata", "all"):
        wikidata(args)
    # translation before topics: the topics report counts the translations among `health`
    if args.step in ("translation", "all"):
        translation(args)
    if args.step in ("topics", "all"):
        topics(args)


if __name__ == "__main__":
    main()
