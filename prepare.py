#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["lxml>=5"]
# ///
"""Download Odia Wikipedia and turn every article into clean text for LLM training.

Three steps, each resumable, all output under this directory:

  download  the latest complete orwiki pages-articles dump (XML) -> raw/, SHA-1 checked and
            indexed into raw/<wiki>-<date>-articles.jsonl (id, title, revid, timestamp, bot and
            stub flags of every article), with its provenance in raw/<wiki>-<date>-dump.json
  render    Wikipedia's own rendering (Parsoid HTML) of each article's dump revision
            -> raw/html/chunk-*.jsonl.gz, 500 articles per chunk
  build     HTML -> Markdown-style text -> orwiki-<date>.jsonl (the corpus, one article per line),
            the build statistics and README.md

Why render instead of stripping the wikitext: Odia articles build whole sentences out of
templates ('''{{PAGENAME}}''' ଏକ ଭାରତୀୟ {{TownType|M}}, {{Birth date|...}}, {{convert|...}},
{{flag|...}}), many of them Lua modules. Stripped wikitext leaves holes in those sentences;
the rendered HTML has exactly what a reader sees. Pinning the dump's revision ids keeps the
corpus an exact snapshot of the dump.

Usage (uv reads the dependencies from the header above; nothing is installed in the repo):
    uv run prepare.py download            # or --dump 20260901
    ODIA_WIKI_CONTACT=you@example.org uv run prepare.py render
    uv run prepare.py build
    uv run prepare.py all                 # the three in turn

`render` takes about 2 h for 21k articles with 6 workers. It needs ODIA_WIKI_CONTACT (an email
or URL for the user-agent): Wikimedia throttles bulk clients without contact details to about a
request a minute.
"""

import argparse
import bz2
import collections
import datetime
import gzip
import hashlib
import http.client
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent  # the repository root: everything is written here
RAW = ROOT / "raw"
HTML_DIR = RAW / "html"

# The Odia text rules (odia_text.py, a copy of odia-llm-trainer's odia_llm.text): ୟ spelling fix,
# never NFC. Importing must not leave __pycache__ here.
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT))
from odia_text import normalize_odia, odia_ratio, odia_words  # noqa: E402

WIKI = "orwiki"
DUMPS = f"https://dumps.wikimedia.org/{WIKI}"
REST = "https://or.wikipedia.org/w/rest.php/v1"
SITE = "https://or.wikipedia.org/wiki/"
CONTACT = os.environ.get("ODIA_WIKI_CONTACT", "").strip()
UA = ("odia-wikipedia-sep26/0.1 (research: Odia LLM training corpus from Wikipedia dumps"
      + (f"; {CONTACT}" if CONTACT else "") + ") python-urllib")
CHUNK = 500  # articles per raw/html chunk file

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


def corpus_stem(date):
    """orwiki-<date>: the corpus is <stem>.jsonl, its build statistics <stem>-build.json."""
    return f"{WIKI}-{date}"


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


# -------------------------------------------------------------------------------- render

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


def chunk_path(date, i):
    return HTML_DIR / date / f"chunk-{i:05d}.jsonl.gz"


FINAL = {200, 403, 404, 410}  # statuses not worth retrying: rendered, hidden or deleted revision


def fetch_html(art):
    try:
        status, headers, body = http_get(f"{REST}/revision/{art['revid']}/html")
    except NETWORK_ERRORS as e:
        print(f"  gave up on revision {art['revid']}: {e}", file=sys.stderr)
        status, body = 0, b""  # network failure; retried on the next run
    row = {"id": art["id"], "revid": art["revid"], "status": status}
    if status == 200:
        row["html"] = body.decode("utf-8")
    return row


def render(args):
    date = find_date(args.dump)
    arts = load_index(date)
    chunks = [arts[i:i + CHUNK] for i in range(0, len(arts), CHUNK)]
    todo, kept = [], {}
    for i in range(len(chunks)):
        if not chunk_path(date, i).exists():
            todo.append(i)
            continue
        with gzip.open(chunk_path(date, i), "rt", encoding="utf-8") as f:
            rows = [json.loads(line) for line in f]
        if any(r["status"] not in FINAL for r in rows):  # refetch only the failed pages
            todo.append(i)
            kept[i] = {r["id"]: r for r in rows if r["status"] in FINAL}
    if args.limit_chunks:
        todo = todo[:args.limit_chunks]
    print(f"{len(arts):,} articles in {len(chunks)} chunks; {len(todo)} to fetch "
          f"with {args.workers} workers", file=sys.stderr)
    chunk_path(date, 0).parent.mkdir(parents=True, exist_ok=True)
    t0, done, lock = time.time(), [0], threading.Lock()

    def one(art):
        row = fetch_html(art)
        with lock:
            done[0] += 1
        return row

    with ThreadPoolExecutor(args.workers) as ex:
        for n, i in enumerate(todo, 1):
            have = kept.get(i, {})
            fetched = iter(ex.map(one, [a for a in chunks[i] if a["id"] not in have]))
            rows = [have[a["id"]] if a["id"] in have else next(fetched) for a in chunks[i]]
            bad = [r for r in rows if r["status"] != 200]
            out = chunk_path(date, i)
            tmp = out.with_suffix(".tmp")
            with gzip.open(tmp, "wt", encoding="utf-8") as f:
                for r in rows:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
            tmp.replace(out)  # a chunk file exists only once complete
            rate = done[0] / (time.time() - t0)
            left = (len(todo) - n) * CHUNK / rate / 60 if rate else 0
            print(f"chunk {i:3d}: {len(rows)} pages, {len(bad)} not rendered "
                  f"{collections.Counter(r['status'] for r in bad) or ''}; "
                  f"{rate:.1f} pages/s, ~{left:.0f} min left", file=sys.stderr, flush=True)


def read_chunks(date):
    for p in sorted((HTML_DIR / date).glob("chunk-*.jsonl.gz")):
        with gzip.open(p, "rt", encoding="utf-8") as f:
            yield from (json.loads(line) for line in f)


# --------------------------------------------------------------------------- html -> text

# Reference-type and link sections, dropped with their subsections (matched by heading_key).
DROP_SECTIONS = {
    # Odia
    "ବାହାର ଆଧାର", "ବାହାର ତଥ୍ୟ", "ବାହାର ସ୍ରୋତ", "ଅଧିକ ତଥ୍ୟ", "ଦ୍ରଷ୍ଟବ୍ୟ", "ଆଗକୁ ପଢ଼ିବେ",
    "ଗ୍ୟାଲେରି", "ଛବି", "ଆଧାର", "ଆଧାରସୂତ୍ର", "ଆଧାର ସୂତ୍ର", "ଆଧାର ଗ୍ରନ୍ଥ", "ଆଧାରଗ୍ରନ୍ଥ", "ଆଧାର ଓ ଟୀକା", "ଟୀକା",
    "ଟୀକା ଓ ଆଧାର", "ପାଦଟୀକା", "ଟିପ୍ପଣୀ", "ଉତ୍ସ", "ତଥ୍ୟସୂତ୍ର", "ତଥ୍ୟ ସୂତ୍ର", "ସୂତ୍ର",
    "ଆହୁରି ଦେଖନ୍ତୁ", "ଆହୁରି ଦେଖିବେ", "ଅଧିକ ଦେଖନ୍ତୁ", "ଏହା ବି ଦେଖନ୍ତୁ", "ଆହୁରି ପଢ଼ନ୍ତୁ",
    "ଅଧିକ ପଢ଼ନ୍ତୁ", "ଅଧିକ ପଠନ", "ବାହାର ଲିଙ୍କ", "ବାହାରର ଲିଙ୍କ", "ବାହାର ଲିଂକ", "ବାହ୍ୟ ଲିଙ୍କ",
    "ବାହ୍ୟ ସଂଯୋଗ", "ବାହାର ସଂଯୋଗ", "ବାହାର ଯୋଗସୂତ୍ର", "ବାହ୍ୟ ଯୋଗସୂତ୍ର", "ଗ୍ୟାଲେରୀ",
    "ଚିତ୍ର ଗ୍ୟାଲେରୀ", "ଚିତ୍ରଶାଳା", "ଚିତ୍ରାବଳୀ", "ଗ୍ରନ୍ଥସୂଚୀ",
    # English headings left untranslated
    "see also", "notes", "note", "references", "reference", "further reading",
    "external links", "external link", "bibliography", "sources", "citations",
    "footnotes", "works cited", "notes and references", "references and notes", "gallery",
}
# Elements dropped wherever they appear: citations, infoboxes, navboxes, tables, images,
# maintenance banners, hatnotes, coordinates.
DROP_TAGS = {"style", "script", "link", "meta", "figure", "figcaption", "img", "table",
             "audio", "video", "map", "noscript", "caption"}
DROP_CLASSES = {
    "reference", "mw-ref", "mw-references-wrap", "references", "reflist", "refbegin",
    "navbox", "vertical-navbox", "navbox-styles", "infobox", "metadata", "ambox", "mbox-small",
    "hatnote", "dablink", "rellink", "noprint", "mw-empty-elt", "gallery", "thumb",
    "sistersitebox", "sister-project", "mw-editsection", "shortdescription", "toc", "portal",
    "portalbox", "catlinks", "geo-default", "geo", "coordinates", "asbox", "stub",
    "authority-control", "mw-kartographer-maplink", "cs1-visible-error", "error",
    "mw-cite-backlink", "sidebar", "succession-box", "mw-halign-right", "mw-halign-left",
    "tright", "tleft", "floatright", "floatleft", "haudio", "mediaContainer", "plainlist-refs",
    "side-box", "NavFrame", "NavHead", "NavContent", "mbox", "tmbox", "ombox", "cmbox", "fmbox",
    "citation", "Z3988", "IPA", "rt-commentedText",
}
# Templates whose output is dropped (lowercased names; a trailing * matches a prefix): archive
# and dead-link notes, maintenance banners, pronunciation, coordinates, sister projects.
DROP_TEMPLATES = {
    "webarchive", "wayback", "dead link", "deadlink", "citation needed", "cn", "fact",
    "wikify", "cleanup*", "unreferenced*", "refimprove*", "more citations needed*", "pov*",
    "ipa*", "ipac-*", "respell", "audio*", "pronunciation*", "coord", "coord missing",
    "commons*", "wiktionary*", "wikiquote*", "wikisource*", "wikivoyage*", "sister project*",
    "authority control", "portal*", "ମୁଣ୍ଡିଆ*", "ଅଧାଗଢ଼ା", "ଅଧାଗଢା", "ବଟ୍ ତିଆରି", "ଆଧାର",
    "reflist", "notelist", "use dmy dates", "use mdy dates", "stub*", "*-stub", "about",
    "other uses*", "redirect*", "distinguish", "main", "see also", "further", "for",
}
DROP_TYPEOF = ("mw:Extension/ref", "mw:Extension/references", "mw:Extension/gallery",
               "mw:Extension/templatestyles", "mw:Extension/graph", "mw:Extension/timeline",
               "mw:Extension/mapframe", "mw:Extension/maplink", "mw:Extension/imagemap",
               "mw:File", "mw:Error", "mw:Extension/score", "mw:Extension/inputbox")
DROP_ROLES = {"note", "navigation", "presentation", "figure"}
BLOCK = {"p", "div", "section", "ul", "ol", "dl", "li", "dd", "dt", "blockquote", "pre",
         "h1", "h2", "h3", "h4", "h5", "h6", "center", "body", "poem", "hr"}
CONTROL = re.compile("[\x00-\x08\x0b-\x1f\x7f-\x9f]")
# U+0B64/U+0B65 are unassigned in the Odia block; some keyboards type them for the danda.
RESERVED_DANDA = {0x0B64: "\u0964", 0x0B65: "\u0965"}
INVISIBLE = re.compile("[\u00ad\u200b\u2060\ufeff]")  # soft hyphen, ZWSP, word joiner, BOM
SPACES = re.compile("[ \t\r\n\u00a0\u2002\u2003\u2009\u202f]+")  # not ZWJ/ZWNJ: Odia uses them


def template_names(el):
    try:
        parts = json.loads(el.get("data-mw") or "{}").get("parts", [])
    except ValueError:
        return []
    return [p["template"]["target"].get("wt", "").strip().lower().replace("_", " ")
            for p in parts if isinstance(p, dict) and "template" in p]


def unwanted_template(name):
    return any(name == t or (t.endswith("*") and name.startswith(t[:-1]))
               or (t.startswith("*") and name.endswith(t[1:])) for t in DROP_TEMPLATES)


def drop_templates(body):
    """Remove everything a dropped template produced (Parsoid marks it with one about id)."""
    abouts = set()
    for el in body.xpath('//*[contains(@typeof, "mw:Transclusion")]'):
        names = template_names(el)
        # Also drop a template that renders only an error message ("ତୃଟି: ...") or, when it
        # does not exist, a red link to itself ("ଛାଞ୍ଚ:ବାଲେଶ୍ୱର").
        shown = el.text_content().lstrip()
        if any(unwanted_template(n) for n in names) or shown.startswith(("ତୃଟି", "ଛାଞ୍ଚ:", "Template:")):
            abouts.add(el.get("about"))
    for el in body.xpath("//*[@about]"):
        if el.get("about") in abouts and el.getparent() is not None:
            el.drop_tree()
    # Red links to missing templates inside other templates: {{flag|ହଂକଂ}} without its
    # country data shows "ଛାଞ୍ଚ:Country data ହଂକଂ". Keep the country name, drop the rest.
    for a in body.xpath("//a"):
        shown = a.text_content().strip()
        if shown.startswith(("ଛାଞ୍ଚ:", "Template:")) and a.getparent() is not None:
            name = re.sub(r"^(?:ଛାଞ୍ଚ|Template):\s*Country data\s+", "", shown)
            if name == shown:
                a.drop_tree()
            else:
                tail = a.tail
                a.clear()
                a.text, a.tail = name, tail


def link_only_item(li):
    """A list item that is an external link or a citation with little Odia: an external-links
    or bibliography list under a heading we did not recognise."""
    if not li.xpath('.//a[contains(@rel, "mw:ExtLink")]|.//cite') and "ISBN" not in li.text_content():
        return False
    return odia_ratio(li.text_content()) < 0.5


def droppable(el):
    if not isinstance(el.tag, str) or el.tag in DROP_TAGS:
        return True
    cls = set((el.get("class") or "").split())
    if cls & DROP_CLASSES:
        return True
    typeof = el.get("typeof") or ""
    if any(t in typeof for t in DROP_TYPEOF):
        return True
    if el.get("role") in DROP_ROLES:
        return True
    style = (el.get("style") or "").replace(" ", "").lower()
    return "display:none" in style


def tex_of(el):
    """The math element as $TeX$, or $$TeX$$ for display math (Parsoid keeps its TeX in data-mw)."""
    try:
        src = json.loads(el.get("data-mw") or "{}")["body"]["extsrc"].strip()
    except (KeyError, ValueError, TypeError):
        m = el.find(".//{*}math")
        src = (m.get("alttext") or "") if m is not None else ""
        src = re.sub(r"^\{\\(?:displaystyle|textstyle)\s*(.*)\}$", r"\1", src.strip(), flags=re.S)
    data = el.get("data-mw") or ""
    tex = SPACES.sub(" ", src)
    return f"$${tex}$$" if '"display":"block"' in data.replace(" ", "") else f"${tex}$"


class Writer:
    """Walks the DOM and collects blocks (kind, level, prefix, text): kind is "h" (heading of
    that level), "p" (paragraph), "li" (list item; prefix is its indent and marker) or "t"
    (a finished Markdown table)."""

    def __init__(self):
        self.blocks = []
        self.inline = []

    def flush(self, kind="p", level=0, prefix=""):
        lines = (SPACES.sub(" ", line).strip() for line in "".join(self.inline).split("\x00"))
        text = "\n".join(line for line in lines if line)
        self.inline = []
        if text:
            self.blocks.append((kind, level, prefix, text))

    def walk(self, el, in_item=False):
        """in_item: inside a list item or table cell, where blocks (<p>, <div>) run on inline."""
        if isinstance(el.tag, str) and el.tag == "table" and is_data_table(el) and not in_item:
            self.flush()
            table = table_markdown(el)
            if table:
                self.blocks.append(("t", 0, "", table))
            if el.tail:
                self.inline.append(el.tail)
            return
        if droppable(el):
            if el.tail:
                self.inline.append(el.tail)
            return
        tag, cls = el.tag, el.get("class") or ""
        if "mwe-math-element" in cls or (el.get("typeof") or "").startswith("mw:Extension/math"):
            self.inline.append(tex_of(el))
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self.flush()
            self.children(el, True)
            self.flush("h", int(tag[1]))
        elif tag in ("ul", "ol"):
            self.flush()
            self.walk_list(el, "")
        elif tag == "br":
            self.inline.append("\x00")
        elif tag in BLOCK:
            if in_item:
                self.inline.append(" ")
                self.children(el, True)
                self.inline.append(" ")
            else:
                self.flush()
                self.children(el, False)
                self.flush()
        else:  # inline: a, b, i, span, sub, sup, abbr, small, code, ...
            self.children(el, in_item)
        if el.tail:
            self.inline.append(el.tail)

    def children(self, el, in_item):
        if el.text:
            self.inline.append(el.text)
        for c in el:
            self.walk(c, in_item)

    def walk_list(self, lst, indent):
        """Items get "- " or "1. "; a sublist is indented two spaces deeper than its item."""
        items = [c for c in lst if isinstance(c.tag, str) and c.tag == "li"
                 and not droppable(c) and not link_only_item(c)]
        for i, li in enumerate(items):
            marker = f"{i + 1}. " if lst.tag == "ol" else "- "
            prefix, inner = indent + marker, indent + "  "
            if li.text:
                self.inline.append(li.text)
            for c in li:
                if isinstance(c.tag, str) and c.tag in ("ul", "ol") and not droppable(c):
                    self.flush("li", 0, prefix)
                    prefix = inner  # text after a sublist continues the item
                    self.walk_list(c, inner)
                    if c.tail:
                        self.inline.append(c.tail)
                else:
                    self.walk(c, True)
            self.flush("li", 0, prefix)


def is_data_table(el):
    """A content table (class wikitable), not an infobox, navbox or layout table."""
    cls = set((el.get("class") or "").split())
    return "wikitable" in cls and not cls & DROP_CLASSES and not el.xpath("ancestor::table")


def cell_text(cell):
    w = Writer()
    w.children(cell, True)
    w.flush()
    text = " ".join(b[3] for b in w.blocks).replace("\n", ", ")
    text = clean_block(("p", 0, "", text), cell=True)[3] if re.search(r"\w", text) else ""
    return text.replace("|", "\\|")


def table_markdown(tbl, max_span=50):
    """The table as a GitHub Markdown table. A rowspan cell repeats down its rows; a colspan
    cell fills its first column only. Columns empty in every data row (images) are left out."""
    grid, pending = [], {}  # pending: column -> [text, rows left] for rowspans
    for tr in tbl.xpath("./tr|./thead/tr|./tbody/tr|./tfoot/tr"):
        cells = iter(c for c in tr if isinstance(c.tag, str) and c.tag in ("td", "th"))
        row, col = [], 0
        while True:
            if col in pending:
                row.append(pending[col][0])
                pending[col][1] -= 1
                if not pending[col][1]:
                    del pending[col]
                col += 1
                continue
            c = next(cells, None)
            if c is None:
                break
            text = "" if droppable(c) else cell_text(c)
            span = min(int(re.sub(r"\D", "", c.get("colspan") or "") or 1), max_span)
            rows = min(int(re.sub(r"\D", "", c.get("rowspan") or "") or 1), max_span)
            for k in range(span):
                row.append("" if k else text)
                if rows > 1:
                    pending[col] = ["" if k else text, rows - 1]
                col += 1
        if any(row):
            grid.append(row)
    if len(grid) < 2:
        return ""
    width = max(map(len, grid))
    grid = [r + [""] * (width - len(r)) for r in grid]
    used = [j for j in range(width) if any(r[j] for r in grid[1:])]
    grid = [[r[j] for j in used] for r in grid]
    width = len(used)
    if not width:
        return ""
    lines = ["| " + " | ".join(r) + " |" for r in grid]
    lines.insert(1, "|" + "---|" * width)
    return "\n".join(lines)


# "[2]", "[୪]" typed into the text as reference markers (not a[1] in code)
MANUAL_CITE = re.compile(r"(?<![A-Za-z0-9_])\s*\[\s*[0-9୦-୯]{1,3}\s*\]")
WIKI_RESIDUE = re.compile(r"\[\[|\]\]|\{\{|\}\}|'{2,}")  # broken wikitext shown as text
FILE_RESIDUE = re.compile(
    r"\.(?:jpe?g|png|svg|gif|tiff?|webp)\s*\||(?:^|\|)\s*(?:thumb|thumbnail|frameless|\d+px)\s*[|\]]", re.I | re.M)
TABLE_RESIDUE = re.compile(r"\|\|[^|\n]*\|\||^\s*\{?\|[-+}]?", re.M)  # wikitable rows shown as text
# Raw Parsoid HTML pasted into the wikitext (a Content Translation bug) shows up as text.
HTML_RESIDUE = re.compile(r'data-(?:mw|cx)=|about="#mwt|typeof="mw:')
# Wiki and HTML tags that leaked into the text as literal markup ("<poem>", "</right>", "<meta />").
LITERAL_TAG = re.compile(
    r"</?(?:span|div|br|small|big|center|font|p|ref|references|sup|sub|b|i|u|s|poem|right|left|meta|"
    r"nowiki|gallery|onlyinclude|includeonly|noinclude|math|chem|ce|templatestyles|section|abbr|"
    r"del|ins|mark|tt|strike|em|strong|blockquote)\b[^<>]*/?>", re.I)
MAGIC_WORD = re.compile(r"__(?:LEAD_SECTION|NOTOC|TOC|FORCETOC|NOEDITSECTION|NEWSECTIONLINK|"
                        r"NONEWSECTIONLINK|NOGALLERY|HIDDENCAT|INDEX|NOINDEX|DISAMBIG|STATICREDIRECT)__")
THUMB_TOKEN = re.compile(r"\S*\|\s*(?:thumb|thumbnail|frameless|upright)\b\S*")  # "ଡାହାଣ|thumb"
URL = re.compile(r"\s*\(\s*(?:https?://|www\.)[^\s)]+\s*\)|(?:https?://|www\.)\S+")  # bare URLs typed in prose
PX_RESIDUE = re.compile(r"(?<![\w.])\d{1,4}px\b")  # image sizes left as text: "70px"
# "|" typed for the danda "।" (and "||" for "॥") after Odia text (or a closing quote/bracket
# after it), before a space or line end.
PIPE_DANDA = re.compile(r"(?<=[\u0B00-\u0B7F)\]\"'”’])(\s?)(\|\|?)(?=\s|$)", re.M)


def clean_block(block, cell=False):
    """Clean one block's text (not its prefix); "" drops it."""
    kind, level, prefix, text = block
    if kind == "t":
        return block
    # Broken [[File:...|thumb|...]] or table markup that rendered as text; punctuation alone.
    if (FILE_RESIDUE.search(text) or TABLE_RESIDUE.search(text) or HTML_RESIDUE.search(text)
            or not re.search(r"\w", text) or re.fullmatch(r"(?:ଛାଞ୍ଚ|Template):[^\n]*", text)):
        return kind, level, prefix, ""
    text = URL.sub("", THUMB_TOKEN.sub("", LITERAL_TAG.sub("", MAGIC_WORD.sub("", text))))
    text = WIKI_RESIDUE.sub("", MANUAL_CITE.sub("", PX_RESIDUE.sub("", text)))
    text = "\n".join(line for line in text.split("\n") if re.search(r"\w", line))  # no "।" lines
    text = PIPE_DANDA.sub(lambda m: m.group(1) + ("॥" if len(m.group(2)) == 2 else "।"), text)
    # Parentheses emptied by dropped pronunciation templates: "ବଙ୍ଗଳା ଭାଷା (), ..." "(; বাংলা)"
    text = re.sub(r" ?\([\s,;:]*\)", "", text)
    text = re.sub(r"\((?:\s*[,;:])+\s*", "(", text)
    text = re.sub(r"\s*(?:[,;:]\s*)+\)", ")", text)
    text = re.sub(r"[ \t]{2,}", " ", text).strip()
    if kind == "p" and not cell and re.fullmatch(r"\$[^$]+\$[.,]?", text):  # a formula on its own: display math
        text = "$" + text.rstrip(".,") + "$"
    return kind, level, prefix, text


def heading_key(text):
    """Heading for matching: lowercased, nukta letters folded (ଡ଼/ଢ଼ come precomposed or not)."""
    text = text.replace("\u0b5c", "\u0b21").replace("\u0b5d", "\u0b22").replace("\u0b3c", "")
    return SPACES.sub(" ", text).strip().rstrip(":").strip().lower()


DROP_KEYS = {heading_key(h) for h in DROP_SECTIONS}


def section_heading(sec):
    h = next((c for c in sec if isinstance(c.tag, str) and re.fullmatch(r"h[1-6]", c.tag)), None)
    return SPACES.sub(" ", h.text_content()).strip() if h is not None else None


def html_to_text(doc):
    """(Markdown text without the title, info) for one Parsoid HTML page."""
    from lxml import html as lhtml

    root = lhtml.document_fromstring(doc)
    info = {"disambiguation": bool(root.xpath('//meta[@property="mw:PageProp/disambiguation"]'))}
    body = root.find("body")
    # Reference/navigation sections go whole, with their subsections.
    for sec in list(body.iter("section")):
        head = section_heading(sec)
        if head and heading_key(head) in DROP_KEYS and sec.getparent() is not None:
            sec.drop_tree()
    drop_templates(body)
    w = Writer()
    w.walk(body)
    w.flush()
    blocks = [clean_block(b) for b in w.blocks]
    # Drop headings with no content before the next heading of the same or higher level.
    keep = []
    for k in reversed(range(len(blocks))):
        kind, level, prefix, text = blocks[k]
        if not text:
            continue
        if kind == "h" and (not keep or (keep[-1][0] == "h" and keep[-1][1] <= level)):
            continue
        keep.append((kind, level, prefix, text))
    keep.reverse()
    out = []
    for i, (kind, level, prefix, text) in enumerate(keep):
        if kind == "h":
            text = "#" * level + " " + text
        elif kind == "li":
            text = prefix + text
        # Consecutive list items stay together; everything else is a paragraph of its own.
        sep = "\n" if kind == "li" and i and keep[i - 1][0] == "li" else "\n\n"
        out.append((sep if out else "") + text)
    text = CONTROL.sub("", INVISIBLE.sub("", "".join(out))).translate(RESERVED_DANDA)
    return normalize_odia(text).strip(), info


# --------------------------------------------------------------------------------- build

MAIN_PAGE = "ପ୍ରଧାନ ପୃଷ୍ଠା"  # orwiki's main page lives in the article namespace


def md_heading(title):
    return re.sub(r"(\s)(#+)$", r"\1\\\2", title)  # a trailing "#" would close the heading


def convert(row):
    body, info = html_to_text(row["html"])
    return row["id"], body, info


def build(args):
    """HTML -> the corpus (JSON lines), the build statistics and README.md."""
    date = find_date(args.dump)
    arts = {a["id"]: a for a in load_index(date)}
    prov = json.loads(provenance_path(date).read_text(encoding="utf-8")) if provenance_path(date).exists() else {}
    n_chunks = -(-len(arts) // CHUNK)
    have = len(list((HTML_DIR / date).glob("chunk-*.jsonl.gz")))
    if have < n_chunks and not args.partial:
        raise SystemExit(f"{have}/{n_chunks} chunks rendered; run `render` first (or --partial)")
    records, excluded, seen = [], [], {}

    def exclude(a, reason, detail=""):
        excluded.append({"title": a["title"], "reason": reason, "detail": detail})

    print(f"converting {len(arts):,} pages", file=sys.stderr)
    for r in read_chunks(date):  # streamed: the HTML of all pages is ~1.3 GB
        a = arts[r["id"]]
        if r["status"] != 200:
            exclude(a, "not rendered", f"HTTP {r['status']}")
            continue
        if a["title"] == MAIN_PAGE:
            exclude(a, "main page")
            continue
        _, body, info = convert(r)
        if info["disambiguation"]:
            exclude(a, "disambiguation")
            continue
        if len(odia_words(body)) < args.min_words:
            exclude(a, f"under {args.min_words} Odia words")
            continue
        key = hashlib.sha1(body.encode()).hexdigest()
        if key in seen:
            first = seen[key]
            exclude(a, "duplicate text", f"same text as id {first['id']} ({first['title']})")
            continue
        seen[key] = a
        text = f"# {md_heading(a['title'])}\n\n{body}"
        records.append({
            "id": a["id"],
            "title": a["title"],
            "url": SITE + urllib.parse.quote(a["title"].replace(" ", "_")),
            "revid": a["revid"],
            "timestamp": a["timestamp"],
            "text": text,
            "words": len(odia_words(text)),
            "chars": len(text),
            "odia_ratio": round(odia_ratio(text), 4),
            "tables": len(re.findall(r"(?m)^\|(?:---\|)+$", text)),
            "bot_created": a["bot_created"],
            "stub": a["stub"],
        })

    stem = corpus_stem(date)
    jl = ROOT / f"{stem}.jsonl"
    tmp = jl.with_suffix(".tmp")  # readers never see a half-written file
    write_jsonl(tmp, records)
    tmp.replace(jl)

    dropped = collections.Counter(e["reason"] for e in excluded)
    stats = {
        "dump": prov.get("dump"), "dump_sha1": prov.get("sha1"), "built": datetime.date.today().isoformat(),
        "pages_in_dump": len(arts), "articles": len(records), "excluded": len(excluded),
        "words": sum(r["words"] for r in records), "chars": sum(r["chars"] for r in records),
        "utf8_bytes": sum(len(r["text"].encode()) for r in records),
        "bot_created": sum(r["bot_created"] for r in records),
        "stub": sum(r["stub"] for r in records),
        "table_words": sum(len(odia_words("\n".join(line for line in r["text"].split("\n")
                                                     if line.startswith("|")))) for r in records),
        "articles_with_tables": sum(r["tables"] > 0 for r in records),
        "odia_ratio_below_0.6": sum(r["odia_ratio"] < 0.6 for r in records),
        "odia_ratio_below_0.6_words": sum(r["words"] for r in records if r["odia_ratio"] < 0.6),
        "min_words": args.min_words,
        "dropped": dict(sorted(dropped.items(), key=lambda kv: -kv[1])),
        "dropped_titles": excluded,
    }
    (ROOT / f"{stem}-build.json").write_text(json.dumps(stats, indent=1, ensure_ascii=False) + "\n",
                                             encoding="utf-8")
    write_readme(stats, records, stem)
    print(f"{len(records):,} articles, {stats['words']:,} Odia words -> {jl.name}; "
          f"{len(excluded):,} dropped ({dict(dropped)})", file=sys.stderr)


def write_jsonl(path, rows):
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def write_readme(stats, records, stem):
    date = stats["dump"].split("-")[1]
    pretty = f"{date[:4]}-{date[4:6]}-{date[6:]}"
    words = sorted(r["words"] for r in records)
    q = lambda f: words[min(len(words) - 1, int(f * len(words)))]  # noqa: E731
    bot = [r for r in records if r["bot_created"]]
    size_rows = []
    for lo, hi in [(0, 50), (50, 200), (200, 1000), (1000, 5000), (5000, None)]:
        rs = [r for r in records if r["words"] >= lo and (hi is None or r["words"] < hi)]
        label = f"{lo:,}+" if hi is None else f"{lo:,}–{hi - 1:,}"
        size_rows.append(f"| {label} | {len(rs):,} | {sum(r['words'] for r in rs):,} |")
    sample = min((r for r in records if 60 <= r["words"] <= 90 and not r["bot_created"]
                  and r["odia_ratio"] > 0.9), key=lambda r: r["id"], default=records[0])
    sample_json = json.dumps(sample, ensure_ascii=False, indent=2)
    dropped = "\n".join(f"| {k} | {v:,} |" for k, v in stats["dropped"].items())
    readme = f"""# Odia Wikipedia, cleaned for LLM training

Every article on [Odia Wikipedia](https://or.wikipedia.org) in the **{pretty} dump**
(`{stats['dump']}`, the latest complete dump when built on {stats['built']}), as clean
Markdown-style text, one article per record: **{stats['articles']:,} articles, {stats['words']:,}
Odia words, {stats['utf8_bytes'] / 1e6:,.0f} MB of UTF-8 text**.

| File | What it is |
|---|---|
| `{stem}.jsonl` | the corpus, one JSON object per line (fields below) |
| `{stem}-build.json` | build statistics and the title of every page left out, with the reason |
| `prepare.py` | the script that made all of it (download, render, build) |
| `LEARNINGS.md` | what building this corpus taught us, and ideas for next steps |
| `odia_text.py` | the Odia text rules the steps share: normalisation, Odia words, digits |
| `raw/` | inputs kept for rebuilds: the dump, the article index, Wikipedia's rendered HTML of every article |

## Record format

| Field | Meaning |
|---|---|
| `id` | page id on or.wikipedia.org |
| `title` | article title |
| `url` | article URL |
| `revid` | revision in the dump; `https://or.wikipedia.org/w/index.php?oldid=<revid>` is exactly this text |
| `timestamp` | when that revision was saved |
| `text` | the article: `# title`, then the lead, `##`/`###` section headings, paragraphs separated by a blank line, `- ` / `1. ` lists, data tables as Markdown tables, math as `$...$` |
| `words` | Odia words in `text` (runs of Odia-script characters) |
| `chars` | characters in `text` |
| `odia_ratio` | share of non-space characters in the Odia block (`odia_text.odia_ratio`) |
| `tables` | data tables in `text`, as Markdown tables |
| `bot_created` | the page carries `{{{{ବଟ୍ ତିଆରି}}}}`: made by a bot (year pages, town stubs), formulaic text |
| `stub` | the page carries a stub template (`{{{{ମୁଣ୍ଡିଆ}}}}`, `{{{{ଅଧାଗଢ଼ା}}}}`) |

A short example record:

```json
{sample_json}
```

## How it was made

1. **Download.** `{stats['dump']}` from dumps.wikimedia.org, with its SHA-1
   (`{stats['dump_sha1']}`) checked against the dump's `dumpstatus.json`.
2. **Render.** Every main-namespace page that is not a redirect ({stats['pages_in_dump']:,} pages)
   was fetched as Wikipedia's own rendering (Parsoid HTML) **of the exact revision in the
   dump**, from `/w/rest.php/v1/revision/<revid>/html`. Odia articles build whole sentences out
   of templates: `'''{{{{PAGENAME}}}}''' ଏକ ଭାରତୀୟ {{{{TownType|M}}}}` in over 1,600 town stubs, and
   `{{{{Birth date}}}}`, `{{{{convert}}}}`, `{{{{flag}}}}` and other Lua-module templates across
   the wiki. Stripping the wikitext would leave holes in those sentences. The rendered HTML has
   what a reader sees.
3. **Clean** (HTML to text). Prose, headings, lists and math are kept. Dropped:
   - citations, reference lists, and reference-type sections (ଆଧାର, ଟୀକା, ଆହୁରି ଦେଖନ୍ତୁ,
     ବାହାର ଲିଙ୍କ, ଅଧିକ ପଢ଼ନ୍ତୁ, ଗ୍ୟାଲେରୀ, … and their English equivalents)
   - infoboxes, navboxes, layout tables, images, galleries, captions, maps. Data tables
     (`wikitable`) are kept as Markdown tables. A cell spanning rows repeats in each row, a cell
     spanning columns fills the first one, and image-only columns are removed. Tables hold
     {stats['table_words']:,} Odia words ({stats['table_words'] / stats['words']:.1%}) in
     {stats['articles_with_tables']:,} articles: lists of districts, constituencies, award winners
     and office holders. For prose only, drop the lines starting with `|`.
   - hatnotes, maintenance and stub banners, coordinates, pronunciation (IPA), sister-project
     boxes, archive notes ("Archived … at the Wayback Machine"), template error messages
   - list items that are only an external link or a book citation, under any heading
   - hand-typed reference markers (`[2]`, `[୪]`) and broken wikitext that renders as text
     (`[[`, `{{{{`, `''`, stray `File:…|thumb|` lines)
   - bare URLs typed into the prose, and raw HTML pasted into the wikitext (a Content
     Translation bug) that renders as text
   - empty sections, and parentheses emptied by the removed pronunciations
4. **Normalise.** `normalize_odia` from `odia_text.py` (ୟ written as ଯ + nukta becomes
   U+0B5F). A `|` typed for the danda after Odia text becomes `।` (and `||` becomes `॥`). Soft
   hyphens, zero-width spaces, word joiners and BOMs are removed, and runs of spaces are
   collapsed. ZWJ and ZWNJ stay, because Odia spelling uses them. There is **no** NFC or other
   Unicode normalisation (by design).
5. **Filter.** {sum(stats['dropped'].values()):,} pages were left out. Their titles are in
   `{stem}-build.json`. Stubs are kept and flagged, not dropped.

| Reason | Pages |
|---|---:|
{dropped}

## Size

The median article has {q(0.5):,} Odia words (10th percentile {q(0.1):,}, 90th {q(0.9):,}).
{len(bot):,} articles ({sum(r['words'] for r in bot):,} words) are bot-created and formulaic;
{stats['stub']:,} are marked as stubs.
{stats['odia_ratio_below_0.6']:,} articles
({stats['odia_ratio_below_0.6_words']:,} Odia words) have `odia_ratio` under 0.6, mostly from English
bibliographies and numeric tables; a threshold of 0.6 (the default of odia-llm-trainer's
`odia-build-cpt --min-odia-ratio`) skips them.

| Odia words per article | Articles | Words |
|---|---:|---:|
{chr(10).join(size_rows)}

## Using it

```python
import json
docs = [r["text"] for r in map(json.loads, open("{stem}.jsonl", encoding="utf-8"))
        if r["words"] >= 50 and not r["bot_created"]]  # e.g. without tiny and bot-made pages
```

- In odia-llm-trainer, `odia-build-cpt --local {stem}.jsonl --local-upsample 1` adds all of it
  to a continued-pretraining build. `--local` upsamples 3× by default, which is meant for
  textbooks. The builder's own `wikipedia` source still reads the older Hugging Face snapshot
  (`wikimedia/wikipedia`, `20231101.or`).
- The text is already normalised with `normalize_odia`, so a pipeline that applies it again
  (and line dedup) barely touches it.

## License

The text is by Odia Wikipedia contributors, licensed
[CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/). Anything derived from it must
keep that license and credit Wikipedia. Each record's `revid` names its exact source revision,
whose history lists the authors.

## Rebuild

```bash
uv run prepare.py download   # newest complete dump, or --dump YYYYMMDD
ODIA_WIKI_CONTACT=you@example.org uv run prepare.py render  # ~2 h, resumable
uv run prepare.py build      # about a minute
```

`render` needs contact details in the user-agent (`ODIA_WIKI_CONTACT`). Wikimedia throttles
anonymous bulk clients to about one request a minute per connection. With contact details and 6
parallel requests it ran at about 3 pages/s (21,095 pages in about 2 hours), backing off on the
occasional 429 as `Retry-After` asks. Rendered chunks are cached in `raw/html/<date>/`, so a rerun fetches only what is
missing. The script writes only inside this directory. uv keeps its environment in its own cache.
"""
    (ROOT / "README.md").write_text(readme, encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("step", choices=["download", "render", "build", "all"])
    ap.add_argument("--dump", help="dump date YYYYMMDD (default: latest)")
    ap.add_argument("--workers", type=int, default=6, help="parallel HTTP requests (render)")
    ap.add_argument("--limit-chunks", type=int, help="render at most this many chunks")
    ap.add_argument("--min-words", type=int, default=5,
                    help="drop articles with fewer Odia words than this after cleaning")
    ap.add_argument("--partial", action="store_true", help="build from the chunks rendered so far")
    args = ap.parse_args()
    if args.step in ("download", "all"):
        args.dump = download(args)
    if args.step in ("render", "all"):
        render(args)
    if args.step in ("build", "all"):
        build(args)


if __name__ == "__main__":
    main()
