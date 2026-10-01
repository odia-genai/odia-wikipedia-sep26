#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["lxml>=5"]
# ///
"""Download Odia Wikipedia and turn every article into clean text for LLM training.

Three steps, each resumable, all output under this directory:

  download  the latest complete orwiki pages-articles dump (XML), SHA-1 checked, reduced to
            raw/<wiki>-<date>-articles.jsonl (id, title, revid, timestamp, bot and stub flags of
            every article) and raw/<wiki>-<date>-dump.json (provenance), then deleted
  render    Wikipedia's own rendering (Parsoid HTML) of each article's dump revision
            -> raw/html/chunk-*.jsonl.gz, 500 articles per chunk
  build     HTML -> GitHub-flavoured Markdown -> orwiki-<date>-trainingready.jsonl (the corpus,
            one article per line; counts first, `text` last), excluded.jsonl (every page left out, with the reason),
            removed-blocks.jsonl, README.md; markdown/<title>.md only with --markdown

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
import unicodedata
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
from odia_text import ODIA_DIGITS, normalize_odia, odia_ratio, odia_words  # noqa: E402

WIKI = "orwiki"
DUMPS = f"https://dumps.wikimedia.org/{WIKI}"
REST = "https://or.wikipedia.org/w/rest.php/v1"
SITE = "https://or.wikipedia.org/wiki/"
GITHUB = "https://github.com/odia-genai/odia-wikipedia-sep26"  # where this repository lives
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
    """orwiki-<date>-trainingready: the corpus is <stem>.jsonl, its build statistics <stem>-build.json."""
    return f"{WIKI}-{date}-trainingready"


def provenance_path(date):
    """raw/<wiki>-<date>-dump.json: which dump the article index came from (name, URL, size,
    SHA-1). The dump itself is deleted once indexed: everything the build needs is in the index."""
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
    if index_path(date).exists() and provenance_path(date).exists():
        prov = json.loads(provenance_path(date).read_text(encoding="utf-8"))
        if prov.get("sha1") == info["sha1"]:  # already indexed; the dump itself is not needed
            print(f"{index_path(date).name} is up to date ({prov['dump']})", file=sys.stderr)
            return date
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
    if not args.keep_dump:  # the index holds all the build needs; the dump would only duplicate it
        path.unlink()
        print(f"indexed and deleted {path.name} (provenance in {provenance_path(date).name})", file=sys.stderr)
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
    # external-link lines written in Odia ("ଇଣ୍ଟରନେଟ ମୁଭି ଡାଟାବେସରେ <title>", "ଫେସବୁକରେ <name>"): the
    # link-only filter misses them because their text is Odia (2,116 IMDb lines alone)
    "imdb*", "facebook*", "instagram*", "twitter*", "youtube*", "bollywood hungama*",
    "official website", "official", "dmoz", "curlie", "cia world factbook link", "allmusic*",
    "discogs*", "rotten tomatoes*", "spotify*", "linkedin*",
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
    # English template error messages ("Error: {{Lang}}: text has italic markup (help)", "Error: This
    # is not a valid number …"): a red span or a strong.error that links to an error category.
    for el in body.xpath('//*[self::span or self::strong][starts-with(normalize-space(.), "Error:")]'):
        style = (el.get("style") or "").replace(" ", "").lower()
        if (("#d33" in style or "red" in style or "error" in (el.get("class") or "")
             or el.xpath('.//a[contains(@href, "errors")]')) and el.getparent() is not None):
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


# Math is set aside while the text is cleaned and escaped: the text carries a placeholder
# (\ue000<n>\ue001, private-use characters) and the TeX goes back in at the very end.
_MATH = []  # (display, tex) of the page being converted
MATH_SLOT = re.compile("\ue000(\\d+)\ue001")


def tex_of(el):
    """A placeholder for the math element; its TeX source (Parsoid keeps it in data-mw) is saved."""
    try:
        src = json.loads(el.get("data-mw") or "{}")["body"]["extsrc"].strip()
    except (KeyError, ValueError, TypeError):
        m = el.find(".//{*}math")
        src = (m.get("alttext") or "") if m is not None else ""
        src = re.sub(r"^\{\\(?:displaystyle|textstyle)\s*(.*)\}$", r"\1", src.strip(), flags=re.S)
    data = el.get("data-mw") or ""
    if (el.get("typeof") or "") == "mw:Extension/chem":  # <chem> is mhchem: its source goes in \ce{…}
        src = "\\ce{" + src + "}"
    _MATH.append(['"display":"block"' in data.replace(" ", ""), SPACES.sub(" ", src)])
    return f"\ue000{len(_MATH) - 1}\ue001"


def put_math_back(text):
    return MATH_SLOT.sub(lambda m: ("$${}$$" if _MATH[int(m.group(1))][0] else "${}$")
                         .format(_MATH[int(m.group(1))][1].translate(ODIA_DIGITS)), text)


SUP_TEXT = re.compile(r"[+\-\u2212\u2013]?[0-9\u0B66-\u0B6F]{0,4}[+\-\u2212n]?")  # 26, -7, 2+, +, n
SUB_TEXT = re.compile(r"[0-9\u0B66-\u0B6F]{1,3}[A-Za-z]?|[A-Za-z]{1,6}|[0-9]?[+\-\u2212]")  # 2, 12, 1a, n, sol, +
SUP_BASE = re.compile(r"(?<![\w.,])(?:([0-9\u0B66-\u0B6F]+(?:\.[0-9\u0B66-\u0B6F]+)?)\s?[x×*]\s?(?:10|୧୦)|"
                      r"([0-9\u0B66-\u0B6F]+(?:\.[0-9\u0B66-\u0B6F]+)?))$")  # "6×10", "10", "30"


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
            slot = tex_of(el)
            n = int(MATH_SLOT.fullmatch(slot).group(1))
            if self.inline and (m := MATH_SLOT.fullmatch(self.inline[-1])) and not _MATH[n][0] \
                    and not _MATH[int(m.group(1))][0]:  # two formulas side by side: one $…$, not $…$$…$
                _MATH[int(m.group(1))][1] += " " + _MATH[n][1]
            else:
                self.inline.append(slot)
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self.flush()
            self.children(el, True)
            self.flush("h", int(tag[1]))
        elif tag in ("ul", "ol"):
            self.flush()
            self.walk_list(el, "")
        elif tag == "br":
            self.inline.append("\x00")
        elif tag == "sup" and (sup := el.text_content().strip()) and SUP_TEXT.fullmatch(sup):
            self.superscript(sup)
        elif tag == "sub" and (sub := el.text_content().strip()) and SUB_TEXT.fullmatch(sub):
            self.subscript(sub)
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

    def superscript(self, sup):
        """A numeric superscript as LaTeX math, like the rest of the corpus's math. Flattened,
        10<sup>26</sup>, 30<sup>0</sup> (degrees) and km<sup>2</sup> read 1026, 300 and km2 and change
        the number. A number just before it becomes the base, $10^{26}$; otherwise the superscript
        attaches to the text before it, km$^{2}$, β$^{+}$."""
        exp = sup.replace("\u2212", "-").replace("\u2013", "-")
        exp = exp if len(exp) == 1 else "{" + exp + "}"
        base = ""
        # the base is a whole number just before it, even across spans ("5.15", "×", "10"); in
        # "NO<sub>3</sub><sup>-</sup>" the 3 belongs to NO, so the charge attaches to the text
        if m := SUP_BASE.search("".join(self.inline[-6:])):
            base = f"{m.group(1)} \\times 10" if m.group(1) else m.group(2)  # 6×10<sup>21</sup>
            n = len(m.group(0))
            while n:  # take the base's characters off the end of the text runs
                last = self.inline.pop()
                if len(last) > n:
                    self.inline.append(last[:-n])
                n = max(0, n - len(last))
        self.inline_math(f"{base}^{exp}")
        FIX_COUNTS["superscripts as LaTeX"] += 1

    def subscript(self, sub):
        """A short subscript as LaTeX math attached to the text before it: H<sub>2</sub>O → H$_2$O,
        x<sub>n</sub> → x$_n$, L<sub>sol</sub> → L$_{\\mathrm{sol}}$ (a word index is upright)."""
        sub = sub.replace("\u2212", "-")
        if re.fullmatch(r"[A-Za-z]{2,}", sub):
            sub = "{\\mathrm{" + sub + "}}"
        elif len(sub) > 1:
            sub = "{" + sub + "}"
        self.inline_math(f"_{sub}")
        FIX_COUNTS["subscripts as LaTeX"] += 1

    def inline_math(self, tex):
        """Add inline math; right after another inline formula it joins that one, so
        NO<sub>3</sub><sup>-</sup> is NO$_3^-$, not NO$_3$$^-$ (a $$ would open display math)."""
        if self.inline and (m := MATH_SLOT.fullmatch(self.inline[-1])) and not _MATH[int(m.group(1))][0]:
            _MATH[int(m.group(1))][1] += tex
            return
        _MATH.append([False, tex])
        self.inline.append(f"\ue000{len(_MATH) - 1}\ue001")

    def children(self, el, in_item):
        if el.text:
            self.inline.append(el.text)
        for c in el:
            self.walk(c, in_item)

    def walk_list(self, lst, indent):
        """Items get "- " or "1. "; a sublist is indented to its parent's content column
        (2 spaces under "- ", 3 under "1. "), as CommonMark requires."""
        items = [c for c in lst if isinstance(c.tag, str) and c.tag == "li"
                 and not droppable(c) and not link_only_item(c)]
        for i, li in enumerate(items):
            marker = f"{i + 1}. " if lst.tag == "ol" else "- "
            prefix, inner = indent + marker, indent + " " * len(marker)
            if li.text:
                self.inline.append(li.text)
            for c in li:
                if isinstance(c.tag, str) and c.tag in ("ul", "ol") and not droppable(c):
                    n = len(self.blocks)
                    self.flush("li", 0, prefix)
                    if len(self.blocks) > n:  # the item has text: nest under it
                        prefix = inner  # text after a sublist continues the item
                        self.walk_list(c, inner)
                    else:  # an item holding only a sublist: skip the empty level, or the
                        self.walk_list(c, indent)  # indent jumps and reads as a code block
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
    return put_math_back(text).replace("|", "\\|")  # GFM reads \| as a pipe even inside math


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

# Spelling mistakes copied by bots into many articles: (name, pattern, replacement). Each fix is
# tied to the context the mistake appears in, so a genuine use of the same letters survives.
TYPO_FIXES = [
    # ପରୁଷ (harsh) for ପୁରୁଷ (man): the census template "…% ଜଣ ପରୁଷ ହୋଇଥିବା ବେଳେ…" (904×) and
    # its forms ପରୁଷଙ୍କ, ପରୁଷମାନଙ୍କ, ପରୁଷୋତ୍ତମ; the corpus has no genuine ପରୁଷ
    ("ପରୁଷ→ପୁରୁଷ", re.compile(r"ପରୁଷ(?=ୋତ୍ତମ|ଙ୍କ|ମାନ|\s+(?:ହୋଇଥିବା|ଓ\s))"), "ପୁରୁଷ"),
]
# Conversion leftovers found by the bpb review queue (2026-09-24)
ANCHOR_TAG = re.compile(r"<a\s[^<>]*>|</a>")  # Content Translation's <a href=… class="cx-link">…</a>
WIKITABLE_MARK = re.compile(r"\{\||\|\}|(?:^|(?<=\s))\|-(?=\s|$)")  # raw {| … |- … |} table syntax
TEMPLATE_PARAMS = re.compile(r"(?:\b[A-Z][A-Za-z ]{0,30})?(?:\|\s*[A-Za-z_]+\s*=\s*[^|\n]*)+")
TEMPLATE_PARAMS_HINT = re.compile(r"\|\s*(?:width|bgcolor|align|style|class|quote|border)\s*=")
HTML_ATTR = re.compile(r'\b(?:style|class|align|width|bgcolor|colspan|rowspan|valign|cellpadding|border)'
                       r'\s*=\s*"[^"]*"')
CATEGORY_TEXT = re.compile(r"Category:Articles containing potentially dated statements(?: from)?\s*"
                           r"|Category:[^\]\n]*\]"  # "…Category:ଜୀବିତ ବ୍ୟକ୍ତି]": a category link as text
                           r"|\bCategory:")  # a bare namespace prefix; the category's name stays
FIX_COUNTS = collections.Counter()  # how often each rule above fired in this build


def fix_residue(text):
    """Strip conversion leftovers; returns "" when the block is raw wikitable text."""
    n = len(text)
    for name, pattern, repl in TYPO_FIXES:
        text, k = pattern.subn(repl, text)
        FIX_COUNTS[f"typo {name}"] += k
    text, k = ANCHOR_TAG.subn("", text)
    FIX_COUNTS["anchor tags"] += k
    k = text.count("&amp;") + text.count("&#13;") + text.count("&#10;")
    text = text.replace("&amp;", "&").replace("&#13;", "").replace("&#10;", " ")
    FIX_COUNTS["html entities"] += k
    if WIKITABLE_MARK.search(text):
        if text.count(" | ") >= 3:  # a whole table as text: nothing to salvage
            FIX_COUNTS["raw wikitable blocks dropped"] += 1
            return ""
        text, k = WIKITABLE_MARK.subn("", text)
        FIX_COUNTS["stray wikitable marks"] += k
    if TEMPLATE_PARAMS_HINT.search(text):
        text, k = TEMPLATE_PARAMS.subn("", text)
        FIX_COUNTS["template parameter text"] += k
    text, k = HTML_ATTR.subn("", text)
    FIX_COUNTS["html attributes"] += k
    text, k = CATEGORY_TEXT.subn("", text)
    FIX_COUNTS["category text"] += k
    FIX_COUNTS["_chars removed"] += n - len(text)
    return text

# English-dominant blocks: more than twice as many Latin as Odia letters (digits are ASCII by
# then, so only letters count): untranslated leftovers, English quotes, bibliographies, Latin-script
# lists. Translated from translations/english-to-odia.jsonl, else kept aside in removed-blocks.jsonl.
# Judged per paragraph (>= 30 Latin letters),
# per list item (>= 10; a whole list judged at once took mixed Odia lists with it), and per table
# (>= 30, and under 200 Odia letters, so bilingual tables with an Odia column stay).
LATIN = re.compile(r"[A-Za-z]")
ODIA_LETTER = re.compile(r"[\u0B00-\u0B65\u0B70-\u0B7F]")
ENGLISH_MIN_LATIN = {"p": 30, "li": 10, "t": 30, "h": 5}  # "h": English headings ("Early life")
TABLE_KEEP_ODIA = 200


# Citations (bibliography entries) among the English blocks are removed, not translated.
# Structural signals for any block; for list items also "(1997). Title" and publisher words with a
# year. Prose that mentions a film "(1962). " or a "journal" is not a citation.
_CITATION_CORE = (r"\b(?:ISBN|ISSN|doi|OCLC)\b|\bpp?\.\s*\d|[Vv]ol\.\s*\d|\bRetrieved\b"
                  r"|^\s*[A-Z][A-Za-z'-]+,\s+(?:[A-Z]\.\s*){1,3}[,(&]"
                  r"|^\s*[A-Z][A-Za-z'-]+,\s+[A-Z][a-z]+(?:\s+[A-Z]\.?)*\s*\((?:1[5-9]|20)\d\d\)")
CITATION = re.compile(_CITATION_CORE)
CITATION_ITEM = re.compile(_CITATION_CORE + r"|\((?:1[5-9]|20)\d\d\)\.\s")
PUBLISHER = re.compile(r"\b(?:[Pp]ress|[Pp]ublishers?|[Pp]ublications|[Jj]ournal|[Pp]roceedings|[Ee]dited by)\b"
                       r"|\(eds?\.\)")
YEAR = re.compile(r"\b(?:1[5-9]|20)\d\d\b")


def is_citation(text, kind):
    if kind == "li":
        return bool(CITATION_ITEM.search(text) or (PUBLISHER.search(text) and YEAR.search(text)))
    return bool(CITATION.search(text))


# Odia translations of English paragraphs and headings: sha1 of the English block (as the build
# produces it) -> {"odia", "keep_as_is", "by", …}. Made by hand-checked LLM translation (see
# METHODOLOGY.md); a missing entry means "awaiting translation".
TRANSLATIONS_FILE = ROOT / "translations" / "english-to-odia.jsonl"
TRANSLATIONS = {}
if TRANSLATIONS_FILE.exists():
    for _line in TRANSLATIONS_FILE.read_text(encoding="utf-8").splitlines():
        if _line.strip():
            _t = json.loads(_line)
            TRANSLATIONS[_t["sha1"]] = _t

# The translation table as plain English-Odia pairs for training (written by build).
PAIRS_FILE = ROOT / "translations" / "english-odia-pairs.jsonl"
MD_UNESCAPE = re.compile(r"\\([!-/:-@\[-`{-~])")
MATH_SPAN = re.compile(r"((?<!\\)\$\$[^$]+\$\$|(?<!\\)\$[^$\n]+?(?<!\\)\$)")


def training_pairs():
    """The translation table as English-Odia pairs fit for training: (pairs, Counter of what was left
    out and why). A pair is kept when it is a translation (not junk, not English kept as it is),
    passed every automatic check, has no Odia letter on the English side (a block that mixed the two
    languages), no template residue, and differs from its English. The English loses its Markdown
    escapes (outside math); the Odia gets ASCII digits, as in the corpus. Each pair once, in the
    table's order."""
    pairs, seen, left = [], set(), collections.Counter()
    for t in TRANSLATIONS.values():
        en = "".join(q if i % 2 else MD_UNESCAPE.sub(r"\1", q) for i, q in enumerate(MATH_SPAN.split(t["source"])))
        en, od = en.strip(), (t.get("odia") or "").translate(ODIA_DIGITS).strip()
        if t.get("drop") or t.get("keep_as_is"):
            left["junk, or names and titles kept in English"] += 1
        elif not t.get("checks") or not all(t["checks"].values()):
            left["failed an automatic check"] += 1
        elif ODIA_LETTER.search(en):
            left["the English side has Odia in it"] += 1
        elif not od or en == od:
            left["the same on both sides"] += 1
        elif "|" in en or "{{" in en:
            left["template residue in the English"] += 1
        elif (en, od) in seen:
            left["duplicate"] += 1
        else:
            seen.add((en, od))
            pairs.append({"english": en, "odia": od})
    return pairs, left


def escape_translation(text, kind):
    """A translation is plain text: normalise it like the rest and escape it for Markdown,
    leaving $…$ math untouched."""
    text = text.translate(ODIA_DIGITS).strip()
    parts = re.split(r"(\$\$[^$]+\$\$|\$[^$\n]+\$)", text)
    out = "".join(q if i % 2 else md_escape(q, line_starts=kind != "h") for i, q in enumerate(parts))
    return re.sub(r"(\s)(#+)$", r"\1\\\2", out) if kind == "h" else out


def english_dominant(text, kind="p"):
    latin, odia = len(LATIN.findall(text)), len(ODIA_LETTER.findall(text))
    if kind == "t" and odia >= TABLE_KEEP_ODIA:
        return False
    return latin >= ENGLISH_MIN_LATIN[kind] and latin > 2 * odia

# Markdown escaping, so that text from the page never turns into markup: emphasis, code,
# HTML, links, math ($), and, at the start of a line, headings, quotes, lists, rules.
WORDCHAR = "0-9A-Za-z\u0B00-\u0B7F"
MD_INLINE = [
    (re.compile(r"\\(?=[!-/:-@\[-`{-~])"), r"\\\\"),  # a backslash before ASCII punctuation
    (re.compile(r"([*`$])"), r"\\\1"),
    (re.compile(rf"(?<![{WORDCHAR}])_|_(?![{WORDCHAR}])"), r"\\_"),  # not intraword snake_case
    (re.compile(r"<(?=[A-Za-z/!?])"), r"\\<"),  # "<alt>+<F4>", "<a> element"
    (re.compile(r"\](?=[(\[])"), r"\\]"),  # "[text](...)"
    (re.compile(r"~(?=~)"), r"\\~"),  # GFM strikethrough and ~~~ fences
]
MD_LINE_START = [
    (re.compile(r"^(#{1,6})(?=\s|$)"), r"\\\1"),  # heading
    (re.compile(r"^([>|])"), r"\\\1"),  # block quote (">> x = 17" in MATLAB), table row
    (re.compile(r"^([-+])(?=\s|$)"), r"\\\1"),  # bullet
    (re.compile(r"^(\d{1,9})([.)])(?=\s|$)"), r"\1\\\2"),  # "1. " numbered item
    (re.compile(r"^([=-])(?=[=-]*\s*$)"), r"\\\1"),  # setext underline or thematic break
]


def md_escape(text, line_starts=True):
    for pattern, repl in MD_INLINE:
        text = pattern.sub(repl, text)
    if line_starts:
        lines = text.split("\n")
        for pattern, repl in MD_LINE_START:
            lines = [pattern.sub(repl, line) for line in lines]
        text = "\n".join(lines)
    return text


ODIA_DIGIT = re.compile("[\u0b66-\u0b6f]")
DIGITS_CONVERTED = [0]  # Odia digits converted to ASCII in this build, for the statistics


# Powers of ten typed without the superscript upstream: "6.1 x 108 ppb", "6×1021 ଟନ", "5.15×10-5".
# Only a decimal mantissa, or one digit with a two-digit exponent, reads as a power of ten: the
# power-station table's "2 x 105" (two 105 MW units) is a real product and stays.
TYPED_POWER = re.compile(r"(?<![\d.,])(\d+(?:\.\d+)?)\s?[x×*]\s?10([-\u2212]?[1-9]\d?)(?!\d)(?![.,]\d)")


def typed_power(m):
    mantissa, exp = m.group(1), m.group(2).replace("\u2212", "-")
    if "." not in mantissa and (len(mantissa) > 1 or len(exp.lstrip("-")) < 2):
        return m.group(0)
    _MATH.append([False, f"{mantissa} \\times 10^{{{exp}}}"])
    FIX_COUNTS["typed powers of ten as LaTeX"] += 1
    return f"\ue000{len(_MATH) - 1}\ue001"


def clean_block(block, cell=False):
    """Clean one block's text (not its prefix) and escape it for Markdown; "" drops it."""
    kind, level, prefix, text = block
    if kind == "t":
        return block
    # Odia digits become ASCII here, before escaping: "୧. ବିଧାୟିକା" must end up "1\. ବିଧାୟିକା",
    # not a numbered list.
    DIGITS_CONVERTED[0] += len(ODIA_DIGIT.findall(text))
    text = fix_residue(text.translate(ODIA_DIGITS))
    text = TYPED_POWER.sub(typed_power, text)
    if not text:
        return kind, level, prefix, ""
    # Broken [[File:...|thumb|...]] or table markup that rendered as text; punctuation alone.
    if (FILE_RESIDUE.search(text) or TABLE_RESIDUE.search(text) or HTML_RESIDUE.search(text)
            or not re.search(r"\w", text) or re.fullmatch(r"(?:ଛାଞ୍ଚ|Template):[^\n]*", text)
            # a block that is only a category link: "Category:…", "ଶ୍ରେଣୀ:" or "ଶ୍ରେଣୀ:<name>"
            # (not "ଶ୍ରେଣୀ: ସ୍ତନ୍ୟପାୟୀ", Odia for "class: mammals", which has a space)
            or re.fullmatch(r"\[*\s*(?:Category\s*:[^\n]*|ଶ୍ରେଣୀ\s*:(?:\S[^\n]*?)?)\s*\]*", text)):
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
    if not re.search(r"\w", MATH_SLOT.sub("x", text)):
        return kind, level, prefix, ""
    if kind == "p" and not cell and (m := re.fullmatch("(\ue000(\\d+)\ue001)[.,]?", text)):
        _MATH[int(m.group(2))][0] = True  # a formula on its own line: display math
        return kind, level, prefix, m.group(1)
    text = md_escape(text, line_starts=kind != "h" and not cell)
    if kind == "h":
        text = re.sub(r"(\s)(#+)$", r"\1\\\2", text)  # a trailing "#" would close the heading
    return kind, level, prefix, text


def heading_key(text):
    """Heading for matching: lowercased, nukta letters folded (ଡ଼/ଢ଼ come precomposed or not),
    wikitext heading marks typed into the heading ("== ଆଧାର ==") ignored."""
    text = text.strip().strip("='").strip()
    text = text.replace("\u0b5c", "\u0b21").replace("\u0b5d", "\u0b22").replace("\u0b3c", "")
    return SPACES.sub(" ", text).strip().rstrip(":").strip().lower()


DROP_KEYS = {heading_key(h) for h in DROP_SECTIONS}
# Spelling variants of the same reference headings (a virama more or less, "ଏହା ମଧ୍ୟ" / "ଆଉରି" /
# "ଏହାକୁ ମଧ୍ୟ ଦେଖନ୍ତୁ", "ବାହ୍ୟ ଆଧାର", "ବାହାର ଲିଙ୍କ୍" …), matched on heading_key(). Content headings
# that share a word (ଉତ୍ସବ "festival", ଖାଦ୍ୟ ଉତ୍ସ, … ସୂତ୍ର "formula") don't match.
DROP_SECTION_PATTERNS = [re.compile(p) for p in (
    r"ଦେଖ(?:ନ୍ତୁ|ିବେ)$",  # "see also" in any wording ("… ଦେଖନ୍ତୁ", "… ଦେଖିବେ")
    # "see also" as ପୁନଶ୍ଚ ଦେଖଣା (227 articles, 2026-09-25) and its misspellings, "ଏହାକୁ ବି ଦେଖ", …
    r"ପୁନଶ\S* ଦେଖ", r"^(?:ଏହାକୁ ବି|ଏହା ମଧ୍ୟ|ଆହୁରି) ଦେଖ\S*$", r"^ଦେଖନ୍ତୁ ମଧ୍ୟ$", r"^ଅଧିକ ଜାଣନ୍ତୁ$",
    r"ପଢନ୍ତୁ$", r"^(?:ଅଧିକ|ଆହୁରି|ଆଗକୁ) ପଢ\S*$",  # "further reading" (nukta folded)
    r"^(?:ବାହ୍ୟ|ବାହାର|ବାହର|ବାହାରର|ଅନ୍ୟାନ୍ୟ|ଅନ୍ୟନ୍ୟ|ବହର|ବାହାଡ)\s*(?:ଲିଙ୍କ|ଲିଂକ|ଆଧାର|ସଂଯୋଗ|ଯୋଗସୂତ୍ର|ସ୍ରୋତ|ତଥ୍ୟ|ଉତ୍ସ|ସନ୍ଦର୍ଭ)",
    r"^(?:ଲିଙ୍କ|ଲିଂକ)୍?$",
    r"^ପଠନ ତାଲିକା$",
    r"^(?:ସହାୟକ ଗ୍ରନ୍ଥ|ସନ୍ଦର୍ଭ|ପୁସ୍ତକ ଆଧାର|ବହି ଆଧାର|ଅନ୍ୟ ଆଧାର|ଆଧାର ନୋଟ)$",  # references, bibliography
)]


def reference_heading(head):
    key = heading_key(head)
    return key in DROP_KEYS or any(p.search(key) for p in DROP_SECTION_PATTERNS)


def section_heading(sec):
    h = next((c for c in sec if isinstance(c.tag, str) and re.fullmatch(r"h[1-6]", c.tag)), None)
    return SPACES.sub(" ", h.text_content()).strip() if h is not None else None


# Wikitext headings typed where MediaWiki doesn't read them as headings (mid-line, or after a
# stray quote), so they render as text: "… ଯୁଗ୍ମ ସଂଖ୍ୟା । '== ଗାଣିତିକ ଧର୍ମ ==", "==ଭୂଗୋଳ==1947 …".
WIKI_HEADING = re.compile(r"'?(={2,6})\s*([^=\n|]{1,80}?)\s*\1(?!=)")
SENTENCE_BREAK = re.compile(r"(?:^|[।.!?॥:]|\n)\s*$")


def split_wiki_headings(blocks):
    """A wikitext heading at the start of a paragraph or after a sentence becomes a heading block
    (level = number of "="; a reference heading is dropped), splitting the paragraph around it.
    Mid-sentence ("କୋଟାୟମ, ==ଜମ୍ମୁ କାଶ୍ମୀର==, …") only the marks go. A heading block whose text is
    wrapped in marks loses them."""
    def plain(t):
        t, k = WIKI_HEADING.subn(lambda m: m.group(2), t)
        FIX_COUNTS["wikitext heading marks"] += k
        return t

    out = []
    for kind, level, prefix, text in blocks:
        if kind == "h":
            out.append((kind, level, prefix, plain(text)))
            continue
        if kind != "p" or "==" not in text:
            out.append((kind, level, prefix, text))
            continue
        pos = 0
        for m in WIKI_HEADING.finditer(text):
            before = text[pos:m.start()]
            if not SENTENCE_BREAK.search(before):
                continue  # mid-sentence: plain() below keeps the words
            if before.strip():
                out.append(("p", 0, "", plain(before)))
            if not reference_heading(m.group(2)):
                out.append(("h", len(m.group(1)), "", m.group(2)))
            FIX_COUNTS["wikitext heading"] += 1
            pos = m.end()
        if text[pos:].strip():
            out.append(("p", 0, "", plain(text[pos:])))
    return out


LI_PREFIX = re.compile(r"^( *)((?:- |\d+\. )?)$")  # a list block's prefix: indent, then marker


def html_to_text(doc):
    """(Markdown text without the title, info) for one Parsoid HTML page."""
    from lxml import html as lhtml

    _MATH.clear()
    root = lhtml.document_fromstring(doc)
    info = {"disambiguation": bool(root.xpath('//meta[@property="mw:PageProp/disambiguation"]'))}
    body = root.find("body")
    # Reference/navigation sections go whole, with their subsections.
    for sec in list(body.iter("section")):
        head = section_heading(sec)
        if head and reference_heading(head) and sec.getparent() is not None:
            sec.drop_tree()
    drop_templates(body)
    w = Writer()
    w.walk(body)
    w.flush()
    blocks = [clean_block(b) for b in split_wiki_headings(w.blocks)]
    # English-dominant blocks (see english_dominant): citations go; paragraphs and headings are
    # replaced by their Odia translation (TRANSLATIONS, keyed by the sha1 of the English block);
    # list items and tables (names, titles, data) stay as they are. A paragraph or heading with no
    # translation yet is taken out and returned, so the build can keep it aside for translation.
    removed, translated, kept = [], set(), 0  # translated: indices of blocks replaced by a translation
    for k, (kind, level, prefix, text) in enumerate(blocks):
        if kind not in ENGLISH_MIN_LATIN or not text or not english_dominant(MATH_SLOT.sub("", text), kind):
            continue
        full = put_math_back(text)
        name = {"p": "paragraph", "li": "list item", "t": "table", "h": "heading"}[kind]
        if is_citation(full, kind):
            removed.append(("citation", name, full))
            blocks[k] = (kind, level, prefix, "")
        elif kind in ("p", "h"):
            tr = TRANSLATIONS.get(hashlib.sha1(full.encode()).hexdigest())
            if tr is None:
                removed.append(("awaiting translation", name, full))
                blocks[k] = (kind, level, prefix, "")
            elif tr.get("drop"):  # not content: vandalism, leaked template instructions, errors
                removed.append(("junk", name, full))
                blocks[k] = (kind, level, prefix, "")
            elif tr.get("odia"):
                blocks[k] = (kind, level, prefix, escape_translation(tr["odia"], kind))
                translated.add(k)
            else:  # the translator judged it not prose (names, code): it stays as it is
                kept += 1
        else:
            kept += 1
    info["english_removed"], info["english_kept"] = removed, kept
    # Drop headings with no content before the next heading of the same or higher level.
    keep, info["translated"] = [], 0  # translated blocks that survive into the text
    for k in reversed(range(len(blocks))):
        kind, level, prefix, text = blocks[k]
        if not text:
            continue
        if kind == "h" and (not keep or (keep[-1][0] == "h" and keep[-1][1] <= level)):
            continue
        keep.append((kind, level, prefix, text))
        info["translated"] += k in translated
    keep.reverse()
    out, prev_col = [], 0
    for i, (kind, level, prefix, text) in enumerate(keep):
        if kind == "h":
            text = "#" * level + " " + text
        elif kind == "li":
            # An item may be indented at most to the content column of the list item before it.
            # Removing an item (English filter, empty item) can strand its sublist deeper than
            # that, which CommonMark reads as an indented code block; such items move up.
            indent, marker = LI_PREFIX.match(prefix).groups()
            limit = prev_col if i and keep[i - 1][0] == "li" else 0
            prefix = indent[:limit] + marker
            if marker:
                prev_col = len(prefix)
            # continuation lines line up with the item's content
            text = prefix + text.replace("\n", "\n" + " " * len(prefix))
        # Consecutive list items stay together; everything else is a paragraph of its own.
        sep = "\n" if kind == "li" and i and keep[i - 1][0] == "li" else "\n\n"
        out.append((sep if out else "") + text)
    text = CONTROL.sub("", INVISIBLE.sub("", "".join(out))).translate(RESERVED_DANDA)
    return normalize_odia(put_math_back(text)).strip(), info


# --------------------------------------------------------------------------------- build

MAIN_PAGE = "ପ୍ରଧାନ ପୃଷ୍ଠା"  # orwiki's main page lives in the article namespace
MD_DIR = ROOT / "markdown"  # one .md file per article; the build owns this directory
MD_ESCAPE = re.compile(r"\\[!-/:-@\[-`{-~]")  # a Markdown backslash escape, for the statistics


def md_heading(title):
    title = title.translate(ODIA_DIGITS)
    return re.sub(r"(\s)(#+)$", r"\1\\\2", md_escape(title, line_starts=False))


def md_filename(title, page_id, used):
    """A file name for the article: its title, made safe on macOS/Windows/Linux, unique."""
    name = re.sub(r'[/\\:*?"<>|\x00-\x1f]', "_", title).strip(" .") or "_"
    while len(name.encode()) > 180:
        name = name[:-1]
    # File systems compare names case- and normalisation-insensitively (APFS, NTFS). NFC is used
    # only for this comparison; the text itself is never normalised.
    key = unicodedata.normalize("NFC", name).casefold()
    if key in used:
        name, key = f"{name} ({page_id})", f"{key} ({page_id})"
    used.add(key)
    return name + ".md"


def write_markdown(records, dump):
    """Optional (--markdown): markdown/<title>.md, YAML front matter (JSON-quoted values), then the
    article. The same text as the corpus, for reading in an editor."""
    MD_DIR.mkdir(exist_ok=True)
    written, used = set(), set()
    for r in records:
        meta = {
            "title": r["title"], "page_id": r["id"], "source": r["url"],
            "revision": f"https://or.wikipedia.org/w/index.php?oldid={r['revid']}",
            "revision_timestamp": r["timestamp"], "dump": dump, "words": r["words"],
            "tables": r["tables"], "bot_created": r["bot_created"], "stub": r["stub"],
            "license": "CC BY-SA 4.0 (text by Odia Wikipedia contributors)",
        }
        front = "\n".join(f"{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in meta.items())
        name = md_filename(r["title"], r["id"], used)
        (MD_DIR / name).write_text(f"---\n{front}\n---\n\n{r['text']}\n", encoding="utf-8")
        written.add(name)
    for old in MD_DIR.glob("*.md"):  # articles gone since the last build
        if old.name not in written:
            old.unlink()


def convert(row):
    body, info = html_to_text(row["html"])
    return row["id"], body, info


REVIEWS = ROOT / "reviews" / "reviews.jsonl"  # review decisions, one JSON event per line
DATASET = "odia-wikipedia"  # the `dataset` field of this corpus's review events


def shown_path(path):
    """A path as the build statistics record it: relative to this folder when it is inside it."""
    p = Path(path).resolve()
    return str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(path)


def load_reviews(path, dataset):
    """Latest review event per page id for this dataset (the app appends; the last event wins)."""
    latest = {}
    if path and Path(path).exists():
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            try:
                e = json.loads(line)
            except ValueError:  # a line the app is still writing
                continue
            if e.get("dataset") == dataset and "id" in e:
                latest[int(e["id"])] = e
    return latest


def apply_review(text, review, counts):
    """Remove the paragraphs a reviewer dropped, matched by content (sha1), not by position,
    so a decision survives rebuilds that shift paragraphs. Returns the new text."""
    shas = {d["sha1"] for d in review.get("drop_paragraphs") or [] if d.get("sha1")}
    if not shas:
        return text
    keep, gone = drop_paragraphs(text.split("\n\n"), shas, keep_first=True)
    counts["paragraphs_dropped"] += len(gone)
    counts["paragraph_refs_not_found"] += len(shas - {para_sha1(q) for q in gone})
    return "\n\n".join(keep)


HEADING_LINE = re.compile(r"(#{1,6}) ")


def para_sha1(q):
    return hashlib.sha1(q.encode()).hexdigest()


def drop_paragraphs(paras, shas, keep_first):
    """(kept, dropped): paras without those whose sha1 is in shas, and without the headings that
    leaves with no content before the next heading of the same or a higher level. keep_first: the
    first paragraph (the # title) always stays."""
    kept, gone = [], []
    for i, q in enumerate(paras):
        (gone if (i or not keep_first) and para_sha1(q) in shas else kept).append(q)
    if gone:
        head, rest, out = kept[:1] if keep_first else [], kept[1:] if keep_first else kept, []
        for q in reversed(rest):
            m = HEADING_LINE.match(q)
            if m and (not out or ((n := HEADING_LINE.match(out[-1])) and len(n.group(1)) <= len(m.group(1)))):
                continue
            out.append(q)
        kept = head + out[::-1]
    return kept, gone


# Curated junk (curation/junk-paragraphs.jsonl): paragraphs judged by hand not to be content, such
# as test edits, colour legends of tables whose colours are gone, a leaked timeline template, pasted
# search-result snippets. One line per paragraph: page id, sha1 of its text in the corpus, the reason
# and the text itself. Matched by content, so a decision survives rebuilds that shift paragraphs; an
# entry whose text is no longer in its article is reported, not applied.
CURATED = ROOT / "curation" / "junk-paragraphs.jsonl"


def load_curated(path=CURATED):
    out = collections.defaultdict(dict)
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                e = json.loads(line)
                out[e["id"]][e["sha1"]] = e["reason"]
    return out


# Paragraph fixes (curation/paragraph-fixes.jsonl): paragraphs a reviewer marked *fix*, replaced by
# their fixed text, mostly English lists, tables and passages translated into Odia with the names
# transliterated. One line per paragraph: page id, the sha1 of the paragraph as the build produces
# it, the new text and the kind (translation or correction). Matched by content, like the curated
# junk; an entry whose paragraph is no longer in its article is reported, not applied.
# `translate.py fixes` and `merge-fixes` make them.
FIXES = ROOT / "curation" / "paragraph-fixes.jsonl"


def load_fixes(path=FIXES):
    out = collections.defaultdict(dict)
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                e = json.loads(line)
                out[e["id"]][e["sha1"]] = e
    return out


def apply_fixes(text, fixes, counts):
    """(text with each paragraph that has a fix replaced by its fixed text, translations applied)."""
    if not fixes:
        return text, 0
    paras, done, translated = text.split("\n\n"), set(), 0
    for i, q in enumerate(paras):
        if (f := fixes.get(para_sha1(q))) is not None:
            paras[i] = f["text"]
            done.add(f["sha1"])
            translated += f["kind"] == "translation"
    counts["paragraphs_fixed"] += len(done)
    counts["entries_not_found"] += len(set(fixes) - done)
    return "\n\n".join(paras), translated


def block_kind(q):
    if HEADING_LINE.match(q):
        return "heading"
    if q.startswith("|"):
        return "table"
    return "list item" if re.match(r"\s*(?:- |\d+\. )", q) else "paragraph"


# Boilerplate pages: articles that say nothing beyond their title. A "frame" is a paragraph with
# the article's own title and all numbers masked; a frame found in FRAME_ARTICLES or more articles
# is a template sentence. An article's free words are its Odia words outside template sentences
# and headings. (Measured 2026-09-25: 1,863 year pages, 260 of 364 date pages and 19 of 75 year
# film lists had nothing else. Fact-bearing stubs, e.g. a village's block and district or a town's
# census figures, stay: their templated_share lets training down-weight them.)
FRAME_ARTICLES, FREE_WORDS = 5, 25
MONTHS = "(?:ଜାନୁଆରୀ|ଫେବୃଆରୀ|ମାର୍ଚ୍ଚ|ଅପ୍ରେଲ|ମଇ|ଜୁନ|ଜୁଲାଇ|ଅଗଷ୍ଟ|ସେପ୍ଟେମ୍ବର|ଅକ୍ଟୋବର|ନଭେମ୍ବର|ଡିସେମ୍ବର)"
YEAR_TITLE = re.compile(r"[0-9]+(?:\s*(?:ଖ୍ରୀଷ୍ଟପୂର୍ବ|ଖ୍ରୀ\.?\s*ପୂ\.?))?(?:\s*\(ମସିହା\))?")  # "0 (ମସିହା)": year 0
DATE_TITLE = re.compile(rf"[0-9]{{1,2}}\s*{MONTHS}|{MONTHS}\s*[0-9]{{1,2}}")
FILM_YEAR_TITLE = re.compile(r"[0-9]{4}ର ଓଡ଼ିଆ (?:କଥାଚିତ୍ର|ଚଳଚ୍ଚିତ୍ର|ସିନେମା)")


def frame_key(paragraph, title):
    for t in sorted({title, re.sub(r"\s*\([^)]*\)\s*$", "", title), title.split(",")[0].strip()},
                    key=len, reverse=True):
        if len(t) >= 2:
            paragraph = paragraph.replace(t, "TITLE")
    return re.sub(r"\d[\d.,]*", "N", paragraph)


def body_paragraphs(text):
    return [q for q in text.split("\n\n")[1:] if not q.startswith("#")]


def templated(records):
    """{id: (templated words, free words)} over the whole corpus."""
    frames = collections.defaultdict(set)
    keys = {}
    for r in records:
        keys[r["id"]] = [frame_key(q, r["title"]) for q in body_paragraphs(r["text"])]
        for k in keys[r["id"]]:
            frames[k].add(r["id"])
    out = {}
    for r in records:
        tw = fw = 0
        for q, k in zip(body_paragraphs(r["text"]), keys[r["id"]], strict=True):
            n = len(odia_words(q))
            if len(frames[k]) >= FRAME_ARTICLES:
                tw += n
            else:
                fw += n
        out[r["id"]] = (tw, fw)
    return out


def boilerplate_reason(title, free_words):
    """Why a page is boilerplate (title shape + nothing beyond templates), or None."""
    if YEAR_TITLE.fullmatch(title):
        return "year page"
    if DATE_TITLE.fullmatch(title) and free_words < FREE_WORDS:
        return "date page without events"
    if FILM_YEAR_TITLE.fullmatch(title) and free_words < FREE_WORDS:
        return "empty list page"
    return None


GUTTED_WORDS, GUTTED_RATIO = 25, 20


def gutted(body, english):
    """What was taken out (citations, prose still awaiting translation) left under GUTTED_WORDS Odia
    words outside headings, and it had at least GUTTED_RATIO times as many Latin letters as Odia
    words remain: an English page with an Odia title, not an Odia article. Since English lists and
    tables stay and prose is translated, this now fires only while translations are missing."""
    left = len(odia_words("\n\n".join(q for q in body.split("\n\n") if not q.startswith("#"))))
    latin = sum(len(LATIN.findall(t)) for *_, t in english)
    return left < GUTTED_WORDS and latin >= GUTTED_RATIO * max(left, 1)


def atomic_write(path, write):
    """write(tmp) into a temp file next to path, then rename it over path; on failure the temp
    file is removed and the old path is left untouched."""
    tmp = path.with_name(path.name + ".tmp")
    try:
        write(tmp)
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def build(args):
    """HTML -> the training-ready corpus (JSON lines), excluded.jsonl, removed-blocks.jsonl, the
    build statistics and README.md. Everything decided about a page is applied here: cleaning,
    translations, review decisions, and every exclusion rule."""
    date = find_date(args.dump)
    arts = {a["id"]: a for a in load_index(date)}
    prov = json.loads(provenance_path(date).read_text(encoding="utf-8")) if provenance_path(date).exists() else {}
    n_chunks = -(-len(arts) // CHUNK)
    have = len(list((HTML_DIR / date).glob("chunk-*.jsonl.gz")))
    if have < n_chunks and not args.partial:
        raise SystemExit(f"{have}/{n_chunks} chunks rendered; run `render` first (or --partial)")
    records, excluded, seen = [], [], {}
    removed_blocks = []  # blocks taken out of articles: citations, junk, prose awaiting translation
    english_totals = collections.Counter()
    reviews = {} if args.no_reviews else load_reviews(args.reviews, DATASET)
    curated, curated_counts, curated_stale = load_curated(), collections.Counter(), []
    fixes, fix_counts = load_fixes(), collections.Counter(paragraphs_fixed=0, entries_not_found=0)
    review_counts = collections.Counter(articles_dropped=0, paragraphs_dropped=0, fix_pending=0,
                                        paragraph_refs_not_found=0)

    def exclude(a, reason, detail=""):
        excluded.append({"id": a["id"], "revid": a["revid"], "title": a["title"].translate(ODIA_DIGITS),
                         "reason": reason, "detail": detail})

    def blocks_of(a, info, kept):
        return [{"id": a["id"], "title": a["title"].translate(ODIA_DIGITS), "kind": kind, "reason": reason,
                 "text": para, "article_kept": kept} for reason, kind, para in info["english_removed"]]

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
        if shas := curated.get(a["id"]):
            paras, gone = drop_paragraphs(body.split("\n\n"), shas, keep_first=False)
            body = "\n\n".join(paras)
            curated_counts["paragraphs_dropped"] += len(gone)
            curated_stale += [(a["id"], h[:10]) for h in set(shas) - {para_sha1(q) for q in gone}]
            removed_blocks += [{"id": a["id"], "title": a["title"].translate(ODIA_DIGITS), "kind": block_kind(q),
                                "reason": f"curated: {shas[para_sha1(q)]}", "text": q, "article_kept": True}
                               for q in gone]
        english = info["english_removed"]
        if english and gutted(body, english):
            # Mostly English once citations and untranslated prose are out: an English page with an
            # Odia title. Its blocks are kept in removed-blocks.jsonl for a later translation round.
            exclude(a, "mostly English", "what is left after removing English is under 25 Odia words")
            removed_blocks += blocks_of(a, info, False)
            continue
        if len(odia_words(body)) < args.min_words:
            exclude(a, f"under {args.min_words} Odia words")
            continue
        key = hashlib.sha1(body.encode()).hexdigest()
        if key in seen:
            first = seen[key]
            exclude(a, "duplicate text", f"same text as id {first['id']} ({first['title'].translate(ODIA_DIGITS)})")
            continue
        seen[key] = a
        title = a["title"].translate(ODIA_DIGITS)
        text = f"# {md_heading(a['title'])}\n\n{body}"
        if review := reviews.get(a["id"]):
            if review.get("verdict") == "drop":
                exclude(a, "reviewer: drop", review.get("note") or "")
                review_counts["articles_dropped"] += 1
                continue
            review_counts["fix_pending"] += review.get("verdict") == "fix"
            text = apply_review(text, review, review_counts)
        text, fixed_translations = apply_fixes(text, fixes.get(a["id"]), fix_counts)
        if args.min_chars and len(text) < args.min_chars:
            exclude(a, f"under {args.min_chars} characters")
            continue
        removed_blocks += blocks_of(a, info, True)
        english_totals["translated"] += info["translated"]
        english_totals["kept as is"] += info["english_kept"]
        records.append({
            "id": a["id"],
            "title": title,
            "url": SITE + urllib.parse.quote(a["title"].replace(" ", "_")),  # the page's real name
            "revid": a["revid"],
            "timestamp": a["timestamp"],
            "text": text,
            "words": len(odia_words(text)),
            "chars": len(text),
            "odia_ratio": round(odia_ratio(text), 4),
            "tables": len(re.findall(r"(?m)^\|(?:---\|)+$", text)),
            "translated_paragraphs": info["translated"] + fixed_translations,  # paragraphs/headings machine-translated from English
            "bot_created": a["bot_created"],
            "stub": a["stub"],
        })

    # Boilerplate pages, judged over the whole corpus (a template sentence is one found in many
    # articles), and the templated share of every article that stays.
    tmpl = templated(records)
    kept = []
    for r in records:
        tw, fw = tmpl[r["id"]]
        if reason := boilerplate_reason(r["title"], fw):
            exclude(arts[r["id"]], reason, f"{fw} Odia words outside template sentences")
            continue
        r["templated_share"] = round(tw / (tw + fw), 3) if tw + fw else 0.0
        kept.append(r)
    records = sorted(kept, key=lambda r: r["id"])
    kept_ids = {r["id"] for r in records}
    removed_blocks = [b for b in removed_blocks if b["id"] in kept_ids or not b["article_kept"]]
    excluded.sort(key=lambda e: (e["reason"], e["id"]))
    removed_blocks.sort(key=lambda b: (b["id"], b["reason"]))

    stem = corpus_stem(date)
    jl = ROOT / f"{stem}.jsonl"
    # `text` goes last, so the counts and metadata lead each line (`head` shows them).
    records = [{k: v for k, v in r.items() if k != "text"} | {"text": r["text"]} for r in records]
    # Readers (edaapp, agents) never see a half-written file, and a failed write (a full disk)
    # leaves no partial file behind.
    atomic_write(jl, lambda tmp: write_jsonl(tmp, records))
    # The gzipped copy is what git tracks and what to download (the corpus itself is over 50 MB).
    atomic_write(gz_path(jl), lambda tmp: gzip_copy(jl, tmp))
    pairs, pairs_left = training_pairs()
    if TRANSLATIONS:
        atomic_write(PAIRS_FILE, lambda tmp: write_jsonl(tmp, pairs))
    atomic_write(ROOT / "excluded.jsonl", lambda tmp: write_jsonl(tmp, excluded))
    atomic_write(ROOT / "removed-blocks.jsonl", lambda tmp: write_jsonl(tmp, removed_blocks))
    if args.markdown:
        write_markdown(records, prov.get("dump", stem))

    dropped = collections.Counter(e["reason"] for e in excluded)
    stats = {
        "dump": prov.get("dump"), "dump_sha1": prov.get("sha1"), "built": datetime.date.today().isoformat(),
        "pages_in_dump": len(arts), "articles": len(records), "excluded": len(excluded),
        "words": sum(r["words"] for r in records), "chars": sum(r["chars"] for r in records),
        "utf8_bytes": sum(len(r["text"].encode()) for r in records),
        "bot_created": sum(r["bot_created"] for r in records),
        "stub": sum(r["stub"] for r in records),
        "templated_share_over_half": sum(r["templated_share"] > 0.5 for r in records),
        "odia_digits_converted": DIGITS_CONVERTED[0],
        "english": {"translated": english_totals["translated"], "kept as is": english_totals["kept as is"],
                    "removed_blocks": dict(collections.Counter(b["reason"] for b in removed_blocks))},
        "cleanup_fixes": dict(FIX_COUNTS),
        "table_words": sum(len(odia_words("\n".join(line for line in r["text"].split("\n")
                                                     if line.startswith("|")))) for r in records),
        "articles_with_tables": sum(r["tables"] > 0 for r in records),
        "odia_ratio_below_0.6": sum(r["odia_ratio"] < 0.6 for r in records),
        "odia_ratio_below_0.6_words": sum(r["words"] for r in records if r["odia_ratio"] < 0.6),
        "markdown_escapes": sum(len(MD_ESCAPE.findall(r["text"])) for r in records),
        "articles_with_escapes": sum(bool(MD_ESCAPE.search(r["text"])) for r in records),
        "reviews": {"file": shown_path(args.reviews) if reviews else None, "articles_reviewed": len(reviews),
                    **review_counts},
        "translation_pairs": {"file": str(PAIRS_FILE.relative_to(ROOT)), "pairs": len(pairs),
                              "left_out": dict(pairs_left.most_common())},
        "paragraph_fixes": {"file": str(FIXES.relative_to(ROOT)), "entries": sum(map(len, fixes.values())),
                            **fix_counts},
        "curated": {"file": str(CURATED.relative_to(ROOT)), "entries": sum(map(len, curated.values())),
                    "paragraphs_dropped": curated_counts["paragraphs_dropped"],
                    "entries_not_found": len(curated_stale)},
        "min_words": args.min_words, "min_chars": args.min_chars,
        "dropped": dict(sorted(dropped.items(), key=lambda kv: -kv[1])),  # titles: excluded.jsonl
    }
    (ROOT / f"{stem}-build.json").write_text(json.dumps(stats, indent=1, ensure_ascii=False) + "\n",
                                             encoding="utf-8")
    write_readme(stats, records, stem)
    if curated_stale:
        print(f"{len(curated_stale)} curated entries match no paragraph any more (text changed?): "
              f"{curated_stale[:8]}", file=sys.stderr)
    if fix_counts["entries_not_found"]:
        print(f"{fix_counts['entries_not_found']} paragraph fixes match no paragraph any more (text changed?)",
              file=sys.stderr)
    print(f"{len(records):,} articles, {stats['words']:,} Odia words -> {jl.name}; "
          f"{len(excluded):,} excluded ({dict(dropped)}) -> excluded.jsonl", file=sys.stderr)


def write_jsonl(path, rows):
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def gz_path(path):
    return path.with_name(path.name + ".gz")


def gzip_copy(src, dst):
    """src gzipped into dst with no name or time in the header, so the same corpus always gives the
    same bytes (and git sees no change when nothing changed)."""
    with open(src, "rb") as f, open(dst, "wb") as raw, \
            gzip.GzipFile(filename="", mode="wb", fileobj=raw, compresslevel=9, mtime=0) as gz:
        while chunk := f.read(1 << 20):
            gz.write(chunk)


def annotations_section(stats):
    """README section built from whatever annotations/*.json sidecars and quality/*.md exist."""
    lines = []
    for side in sorted((ROOT / "annotations").glob("*.json")):
        try:
            meta = json.loads(side.read_text(encoding="utf-8"))
        except ValueError:
            continue
        table = next((t for t in (side.with_suffix(".jsonl"), side.with_suffix(".parquet")) if t.exists()),
                     side.with_suffix(".jsonl"))
        key = "para_sha1" if ".paragraphs." in table.name else "text_sha1"
        stale = f" Depends on the text: rows carry `{key}`." if meta.get("depends_on_text") else ""
        joins = "".join(f" More columns, `{', '.join(j.get('columns', {}))}`, are read from `{j['path']}`"
                        f" on `{j['on']}` (kept there once per key)." for j in meta.get("joins") or [])
        lines.append(f"- **`annotations/{table.name}`**: {meta.get('description', '').strip()}"
                     f" Columns: {', '.join(f'`{c}`' for c in meta.get('columns', {}))}.{joins}{stale}")
    reports = sorted((ROOT / "quality").glob("*.md"))
    body = "\n".join(lines) or "- none yet"
    reps = ", ".join(f"[`quality/{r.name}`](quality/{r.name})" for r in reports) or "none yet"
    rv = stats["reviews"]
    return f"""## Annotations

Extra fields per article live next to the corpus, one JSON-lines file each (with a `.json`
description), joined on `id`. A file named `*.paragraphs.jsonl` has one row per paragraph (`id`,
`para`). Paragraph `i` of an article
is `text.split("\\n\\n")[i]`, and paragraph 0 is the `# title` heading. Annotations that depend on
the text carry `text_sha1` (sha1 of the scored `text`), so a rebuild that changes an article makes
them visibly stale.

{body}

Reports: {reps}.

## Review decisions

Review decisions are kept in `reviews/reviews.jsonl`, one append-only JSON event per line: `ts`,
`dataset` (`odia-wikipedia`), `id`, `title`, `verdict` (`keep`, `drop`, `fix` or null), `note`,
`drop_paragraphs` (a list of `{{"para": i, "sha1": …}}`) and `text_sha1` (the text reviewed). They were
recorded with edaapp, the review web app in `edaapp/` (`cd edaapp && uv run edaapp`), which writes
them to this file; appending events by hand works as well.
`build` applies the **latest event per article**, so every event carries the article's complete
decision: articles marked *drop* are left out, and dropped paragraphs are removed, matched by the
sha1 of their text (not by position), so they survive rebuilds. Paragraph 0 (the title) is never
dropped. This
build applied {rv['articles_reviewed']:,} reviews: {rv['articles_dropped']:,} articles dropped,
{rv['paragraphs_dropped']:,} paragraphs dropped, {rv['fix_pending']:,} still marked *fix*, and
{rv['paragraph_refs_not_found']:,} paragraph decisions whose text is no longer in the article.
{stats['paragraph_fixes']['paragraphs_fixed']:,} paragraphs of articles marked *fix* have been fixed
(`curation/paragraph-fixes.jsonl`, made by `translate.py fixes` and `merge-fixes`).
Use `--no-reviews` to build without the decisions.
"""


def dataset_card(stats, stem, pretty):
    """The YAML header Hugging Face reads from README.md: licence, language, size and one viewer
    config per table (the corpus first)."""
    n = stats["articles"]
    size = next((f"{a}<n<{b}" for a, b, lo, hi in [("1K", "10K", 1e3, 1e4), ("10K", "100K", 1e4, 1e5),
                                                   ("100K", "1M", 1e5, 1e6)] if lo <= n < hi),
                "n<1K" if n < 1e3 else "1M<n<10M")
    tables = [("corpus", f"{stem}.jsonl.gz"), ("translation_pairs", "translations/english-odia-pairs.jsonl"),
              ("translation_table", "translations/english-to-odia.jsonl"),
              ("excluded", "excluded.jsonl"), ("removed_blocks", "removed-blocks.jsonl"),
              ("topics", "annotations/topics.jsonl"), ("translation_flags", "annotations/translation.jsonl"),
              ("bpb", "annotations/bpb.jsonl"), ("bpb_paragraphs", "annotations/bpb.paragraphs.jsonl")]
    configs = "".join(f"- config_name: {name}\n" + ("  default: true\n" if i == 0 else "")
                      + f"  data_files:\n  - split: train\n    path: {path}\n" for i, (name, path) in enumerate(tables))
    return (f"---\npretty_name: Odia Wikipedia, cleaned for LLM training ({pretty} dump)\nlanguage:\n- or\n"
            f"license: cc-by-sa-4.0\ntask_categories:\n- text-generation\n- translation\ntags:\n- wikipedia\n- odia\n"
            f"size_categories:\n- {size}\nconfigs:\n{configs}---\n\n")


def download_section(stats, stem):
    """The two files to take and use, at the top of README.md."""
    gz = gz_path(ROOT / f"{stem}.jsonl")
    gz_mb = gz.stat().st_size / 1e6 if gz.exists() else 0
    tp = stats["translation_pairs"]
    pf, tf = PAIRS_FILE.relative_to(ROOT), TRANSLATIONS_FILE.relative_to(ROOT)
    left = "; ".join(f"{reason} ({n:,})" for reason, n in tp["left_out"].items())
    return f"""## Download

Two files are ready to take and use as they are:

- **[`{gz.name}`]({gz.name})**: **the corpus**. {stats['articles']:,} Odia Wikipedia articles as clean
  Markdown, one JSON object per line, {stats['words']:,} Odia words ({gz_mb:,.0f} MB gzipped,
  {stats['utf8_bytes'] / 1e6:,.0f} MB unpacked). This is the file to train on.
- **[`{pf}`]({pf})**: **English-to-Odia translation pairs**, if you want them separately, ready
  for training: {tp['pairs']:,} pairs, one per line, `{{"english": …, "odia": …}}`. They are English
  paragraphs and headings found in these articles, with their Odia translations, which the corpus has
  in place of the English.

```python
import gzip, json
articles = [json.loads(line) for line in gzip.open("{gz.name}", "rt", encoding="utf-8")]
pairs = [json.loads(line) for line in open("{pf}", encoding="utf-8")]
```

The pairs come from the translation table, [`{tf}`]({tf}), which also keeps each translation's
article, kind, checks and notes. Left out of the pairs: {left}.

Everything else in this repository is how they were made, and what it takes to make them again.

"""


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
GitHub-flavoured Markdown, one article per record: **{stats['articles']:,} articles, {stats['words']:,}
Odia words, {stats['utf8_bytes'] / 1e6:,.0f} MB of UTF-8 text**.

{download_section(stats, stem)}## What is here

| File | What it is |
|---|---|
| **`{stem}.jsonl.gz`** | **the training-ready corpus**, one JSON object per line (fields below), gzipped; `build` also writes it unpacked, as `{stem}.jsonl` |
| **`translations/english-odia-pairs.jsonl`** | **English-Odia translation pairs for training**, `english` and `odia` only |
| `translations/english-to-odia.jsonl` | the translation table the pairs come from: each translation with its article, kind, checks and notes |
| `excluded.jsonl` | every page of the dump that is not in the corpus: `id`, `revid`, `title`, `reason`, `detail` |
| `removed-blocks.jsonl` | blocks taken out of articles: citations, junk, prose awaiting translation |
| `{stem}-build.json` | build statistics (counts per exclusion reason; the pages are in `excluded.jsonl`) |
| `annotations/*.jsonl` | topics, translation flags, Sarvam-1 scores and the review queue, joined on `id` |
| `pipeline.py` | **one command that rebuilds everything** from the cached inputs, in order, and checks it |
| `prepare.py`, `check.py`, `translate.py`, `annotate.py`, `score_bpb.py` | the steps |
| `METHODOLOGY.md` | every step and rule applied to the data, with the evidence and counts |
| `LEARNINGS.md` | what building this corpus taught us, and ideas for next steps |
| `curation/junk-paragraphs.jsonl` | paragraphs judged by hand not to be content, with the reason; `build` drops them |
| `reviews/reviews.jsonl` | review decisions (keep, drop, fix, paragraphs to drop); `build` applies them |
| `odia_text.py` | the Odia text rules the steps share: normalisation, Odia words, digits |
| `edaapp/` | the review web app: browse the dataset, read the methodology, record review decisions |
| `publish_hub.py` | uploads the dataset to Hugging Face: the files git tracks, without `edaapp/` |
| `LICENSE` | CC BY-SA 4.0, for the data and the code |
| `raw/` | rebuild inputs: article index, dump provenance, rendered HTML, annotation inputs, model scores |

All outputs are JSON, JSON lines or Markdown, to read with any editor or `jq`. Every file here is
tracked in git, all under 50 MB and none through Git LFS: the corpus as `{stem}.jsonl.gz`, while the
unpacked `{stem}.jsonl` is left out and rebuilt by `pipeline.py` from the inputs in `raw/`,
`translations/`, `curation/` and `reviews/`.

## Record format

| Field | Meaning |
|---|---|
| `id` | page id on or.wikipedia.org |
| `title` | article title |
| `url` | article URL |
| `revid` | revision in the dump; `https://or.wikipedia.org/w/index.php?oldid=<revid>` is exactly this text |
| `timestamp` | when that revision was saved |
| `text` | the article (format below) |
| `words` | Odia words in `text`: runs of Odia letters, title included; numbers and Latin words don't count |
| `chars` | characters in `text` (Unicode code points), title line and Markdown included |
| `odia_ratio` | share of non-space characters in the Odia block (`odia_text.odia_ratio`) |
| `tables` | data tables in `text`, as Markdown tables |
| `translated_paragraphs` | paragraphs and headings machine-translated from English (0 = all native Odia) |
| `bot_created` | the page carries `{{{{ବଟ୍ ତିଆରି}}}}`, made by a bot (in 2026-09 only year pages, all excluded) |
| `stub` | the page carries a stub template (`{{{{ମୁଣ୍ଡିଆ}}}}`, `{{{{ଅଧାଗଢ଼ା}}}}`) |
| `templated_share` | share of the article's Odia words in template sentences (found in 5+ articles); high = formulaic |

`text` is GitHub-flavoured Markdown: `# title`, the lead, `##`/`###` section headings,
paragraphs separated by a blank line, `- ` / `1. ` lists (sublists indented to their parent's
content column), data tables as GFM tables (`| a | b |`), and math as `$...$` (`$$...$$` on its
own line). Links and emphasis are reduced to their text.

It is checked as Markdown, not just shaped like it. Every article parses (markdown-it,
CommonMark + GFM tables) into exactly the headings, list items and tables it is meant to have,
with no accidental emphasis, links, code, HTML, block quotes or rules. pandoc's GFM reader agrees
on a random sample of 405 articles. Text from the page that would read as markup is
backslash-escaped: `\\*`, `\\_`, `\\$`, `\\<alt>`, `1\\.` or `\\-` at the start of a line.
There are {stats['markdown_escapes']:,} escapes in {stats['articles_with_escapes']:,} articles, about
{stats['markdown_escapes'] / stats['chars'] * 1e6:.0f} per million characters.

A short example record:

```json
{sample_json}
```

## How it was made

1. **Download.** `{stats['dump']}` from dumps.wikimedia.org, with its SHA-1
   (`{stats['dump_sha1']}`) checked against the dump's `dumpstatus.json`. The build needs only
   each article's id, title, revision id, timestamp and bot/stub flags, so the dump is reduced to
   that index (`{index_path(date).relative_to(ROOT)}`, with its provenance in `{provenance_path(date).relative_to(ROOT)}`) and
   deleted; `download` fetches it again.
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
   - external-link lines written in Odia (IMDb, Facebook, Instagram, Twitter, official website, …),
     dropped by template name
   - conversion leftovers found by the review queue: Content Translation `<a href=… cx-link>` tags,
     raw `{{| … |}}` wikitable text, template parameters shown as text (`Quote box|width=…`), HTML
     attributes, `Category:` text, and the entities `&amp;` / `&#13;`
     ({sum(v for k, v in stats['cleanup_fixes'].items() if not k.startswith(('typo', '_', 'wikitext'))
          and 'LaTeX' not in k):,} fixes)
   - wikitext headings typed mid-line (`… । == ଇତିହାସ ==`) become real headings
     ({stats['cleanup_fixes'].get('wikitext heading', 0):,})
   - numeric superscripts are written as LaTeX math, like the rest of the math: `10<sup>26</sup>`
     becomes `$10^{{26}}$` and `km<sup>2</sup>` becomes `km$^{{2}}$`
     ({stats['cleanup_fixes'].get('superscripts as LaTeX', 0):,}). Flattened, they would read 1026 and
     km2. Powers of ten typed without the superscript upstream, `6.1 x 108`, become
     `$6.1 \\times 10^{{8}}$` ({stats['cleanup_fixes'].get('typed powers of ten as LaTeX', 0):,}). Short
     subscripts too: `H<sub>2</sub>O` becomes `H$_2$O`
     ({stats['cleanup_fixes'].get('subscripts as LaTeX', 0):,}); chemistry markup is `$\\ce{{…}}$`
   - paragraphs judged by hand not to be content: test edits, colour legends of tables whose colours
     are gone, a leaked timeline template, pasted search-result snippets
     ({stats['curated']['paragraphs_dropped']:,}; each with its reason in `curation/junk-paragraphs.jsonl`)
   - paragraphs a reviewer marked *fix*, replaced by their fixed text: English lists, tables and
     passages translated into Odia with the names transliterated, and a wrong name corrected
     ({stats['paragraph_fixes']['paragraphs_fixed']:,}; each with its source in `curation/paragraph-fixes.jsonl`)
   - **English inside articles** (blocks with more than twice as many Latin as Odia letters):
     citations are removed ({stats['english']['removed_blocks'].get('citation', 0):,}), paragraphs and
     headings are replaced by their Odia translation ({stats['english'].get('translated', 0):,},
     see `translations/english-to-odia.jsonl` and `METHODOLOGY.md`), and lists, tables and names stay
     as they are ({stats['english'].get('kept as is', 0):,})
4. **Fix known typos.** Bots copied some misspellings into hundreds of articles; each fix is tied to
   its context: {', '.join(f"{k[5:]} ({v:,})" for k, v in stats['cleanup_fixes'].items() if k.startswith('typo'))}.
5. **Normalise.** `normalize_odia` from `odia_text.py` (ୟ written as ଯ + nukta becomes
   U+0B5F). **Odia digits become ASCII** (`୧୯୪୭` → `1947`; {stats['odia_digits_converted']:,} digits), in
   text, headings, tables and math, so numbers look the same everywhere, titles included (`url` keeps the page's
   real name). A `|` typed for the danda after Odia text becomes `।` (and `||` becomes `॥`). Soft
   hyphens, zero-width spaces, word joiners and BOMs are removed, and runs of spaces are
   collapsed. ZWJ and ZWNJ stay, because Odia spelling uses them. There is **no** NFC or other
   Unicode normalisation (by design).
6. **Filter.** {sum(stats['dropped'].values()):,} pages were left out, each listed in `excluded.jsonl`
   with its id, revision and reason. Boilerplate pages (year pages, date pages without events,
   empty film-year lists) say nothing beyond their title. Fact-bearing stubs stay, with
   `templated_share` for down-weighting.

| Reason | Pages |
|---|---:|
{dropped}

## Size

The median article has {q(0.5):,} Odia words (10th percentile {q(0.1):,}, 90th {q(0.9):,}).
{stats['templated_share_over_half']:,} articles have more than half of their words in template
sentences (`templated_share` > 0.5: bot-made villages, towns and film pages),
{f"{len(bot):,} carry the bot-created template, " if bot else ""}and {stats['stub']:,} are marked as stubs.
{stats['odia_ratio_below_0.6']:,} articles
({stats['odia_ratio_below_0.6_words']:,} Odia words) have `odia_ratio` under 0.6, mostly from English
bibliographies and numeric tables; a threshold of 0.6 (the default of odia-llm-trainer's
`odia-build-cpt --min-odia-ratio`) skips them.

| Odia words per article | Articles | Words |
|---|---:|---:|
{chr(10).join(size_rows)}

{annotations_section(stats)}
## Using it

```python
import gzip, json
docs = [r["text"] for r in map(json.loads, gzip.open("{stem}.jsonl.gz", "rt", encoding="utf-8"))
        if r["templated_share"] < 0.8]  # e.g. down-weight or skip formulaic stubs
```

```bash
jq -r 'select(.reason == "year page") | .title' excluded.jsonl | head   # why a page is missing
gzip -dc {stem}.jsonl.gz | jq -c 'select(.chars >= 500 and .chars < 600) | {{id, title, words, chars}}' | head
```

- In odia-llm-trainer, `odia-build-cpt --local {stem}.jsonl --local-upsample 1` adds all of it
  to a continued-pretraining build. `--local` upsamples 3× by default, which is meant for
  textbooks. The builder's own `wikipedia` source still reads the older Hugging Face snapshot
  (`wikimedia/wikipedia`, `20231101.or`).
- The text is already normalised with `normalize_odia`, so a pipeline that applies it again
  (and line dedup) barely touches it.

## Origin

Built from 2026-09-24 to 2026-10-01 inside odia-llm-trainer, a project on Odia language models, as
its `data/odia-wikipedia/` folder, then moved here with everything needed to rebuild it: the
rendered HTML, the annotation inputs, every Sarvam-1 score, the translations, the curated junk
paragraphs and the review decisions. edaapp, the web app that recorded those decisions, followed on
2026-10-02. `METHODOLOGY.md` and `LEARNINGS.md` keep that project's other names: `src/`, `cpt.py`
and `odia-build-cpt` (its training-data builder), the eval harness and experiments (E01, E03, …).

## License

Everything in this repository, the data and the code, is licensed
[CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/) (the full text is in `LICENSE`).
The text is by Odia Wikipedia contributors: anything derived from it must keep that license and
credit Wikipedia. Each record's `revid` names its exact source revision, whose history lists the
authors.

## Rebuild

```bash
uv run pipeline.py           # build, annotate, scores, checks, from raw/ (a few minutes)
ODIA_WIKI_CONTACT=you@example.org uv run pipeline.py --fetch   # from scratch (~2 h)
```

The steps one by one:

```bash
uv run prepare.py download   # newest complete dump, or --dump YYYYMMDD
ODIA_WIKI_CONTACT=you@example.org uv run prepare.py render  # ~2 h, resumable
uv run prepare.py build      # about a minute; --min-chars N, --markdown
uv run check.py              # exit 1 if any check fails
```

`render` needs contact details in the user-agent (`ODIA_WIKI_CONTACT`). Wikimedia throttles
anonymous bulk clients to about one request a minute per connection. With contact details and 6
parallel requests it ran at about 3 pages/s (21,095 pages in about 2 hours), backing off on the
occasional 429 as `Retry-After` asks. Rendered chunks are cached in `raw/html/<date>/`, so a rerun fetches only what is
missing. The script writes only inside this directory. uv keeps its environment in its own cache.

## Publishing

GitHub ({GITHUB}) holds this repository with its history. The Hugging Face dataset is the same files
without `edaapp/`, one commit per publish: commit and push first, then `uv run publish_hub.py`
(`--dry-run` lists what would change). It deletes on the Hub what git no longer tracks and checks every
file afterwards.
"""
    (ROOT / "README.md").write_text(dataset_card(stats, stem, pretty) + readme, encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("step", choices=["download", "render", "build", "all"])
    ap.add_argument("--dump", help="dump date YYYYMMDD (default: latest)")
    ap.add_argument("--workers", type=int, default=6, help="parallel HTTP requests (render)")
    ap.add_argument("--limit-chunks", type=int, help="render at most this many chunks")
    ap.add_argument("--keep-dump", action="store_true",
                    help="download: keep the pages-articles dump after indexing it")
    ap.add_argument("--markdown", action="store_true", help="build: also write markdown/<title>.md files")
    ap.add_argument("--min-chars", type=int, default=0,
                    help="build: drop articles shorter than this many characters (0: off; see METHODOLOGY.md)")
    ap.add_argument("--min-words", type=int, default=5,
                    help="drop articles with fewer Odia words than this after cleaning")
    ap.add_argument("--partial", action="store_true", help="build from the chunks rendered so far")
    ap.add_argument("--reviews", type=Path, default=REVIEWS,
                    help="review decisions, JSON lines (default: reviews/reviews.jsonl)")
    ap.add_argument("--no-reviews", action="store_true", help="ignore review decisions")
    args = ap.parse_args()
    if args.step in ("download", "all"):
        args.dump = download(args)
    if args.step in ("render", "all"):
        render(args)
    if args.step in ("build", "all"):
        build(args)


if __name__ == "__main__":
    main()
