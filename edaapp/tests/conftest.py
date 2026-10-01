"""Fixture datasets, built in a temporary directory (pytest's --basetemp, inside edaapp/.cache)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from edaapp.paths import SafeWriter
from edaapp.reviews import ReviewStore
from edaapp.server import create_app
from edaapp.store import Catalog


def sha1(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


TOWN = "{t} {n}.{n} ଉତ୍ତର ଅକ୍ଷାଂଶ ଓ {n}.{n} ପୂର୍ବ ଦ୍ରାଘିମାରେ ଅବସ୍ଥିତ ।"

ARTICLES = [
    (
        101,
        "କଟକ",
        "# କଟକ\n\nକଟକ ଓଡ଼ିଶାର ଏକ ସହର ।\n\n## ଇତିହାସ\n\n" + TOWN.format(t="କଟକ", n=20) + "\n\n- ପ୍ରଥମ\n- ଦ୍ୱିତୀୟ\n  - ଉପ",
        False,
        False,
    ),
    (
        102,
        "ପୁରୀ",
        "# ପୁରୀ\n\nପୁରୀ ଏକ ତୀର୍ଥ ।\n\n"
        + TOWN.format(t="ପୁରୀ", n=19)
        + "\n\n| ନାମ | ସଂଖ୍ୟା |\n|---|---|\n| କ | 1 |\n| ଖ | $a \\| b$ |",
        False,
        True,
    ),
    (
        103,
        "ବାଲେଶ୍ୱର (ଜିଲ୍ଲା)",
        "# ବାଲେଶ୍ୱର (ଜିଲ୍ଲା)\n\n" + TOWN.format(t="ବାଲେଶ୍ୱର", n=21) + "\n\nThe English text is here. India.",
        True,
        False,
    ),
    (104, "ଗଣିତ", "# ଗଣିତ\n\nଗଣିତରେ $x^2 + y^2 = z^2$ ଏକ ସୂତ୍ର ।\n\n$$E = mc^2$$\n\nଶେଷ ଅନୁଚ୍ଛେଦ ।", False, False),
    (105, "1999", "# 1999\n\n1999 ଗ୍ରେଗୋରି ପାଞ୍ଜି ଅନୁସାରେ ଏକ ସାଧାରଣ ବର୍ଷ ଅଟେ ।", True, True),
    (106, "2001", "# 2001\n\n2001 ଗ୍ରେଗୋରି ପାଞ୍ଜି ଅନୁସାରେ ଏକ ସାଧାରଣ ବର୍ଷ ଅଟେ ।\n\n\n\nଖାଲି ପରେ ।", True, True),
]


def corpus_rows(articles=ARTICLES):
    rows = []
    for i, title, text, bot, stub in articles:
        rows.append(
            {
                "id": i,
                "title": title,
                "url": f"https://or.wikipedia.org/wiki/{title}",
                "revid": 1000 + i,
                "timestamp": f"2026-0{1 + i % 5}-01T00:00:00Z",
                "text": text,
                "words": len(text.split()),
                "chars": len(text),
                "odia_ratio": 0.9 if i != 103 else 0.4,
                "tables": 1 if i == 102 else 0,
                "bot_created": bot,
                "stub": stub,
                "file": f"markdown/{title}.md",
            }
        )
    return rows


def write_parquet(path: Path, rows: list[dict], schema: pa.Schema | None = None) -> None:
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), path)


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


METHODOLOGY = """# Methodology

How the corpus was improved, step by step.

## 1. Build

See `quality/report.md`, the scores in `annotations/bpb.parquet`, README.md and LEARNINGS.md.
Example: id 101 and page id 102; id 777 is not in the corpus. Cost $0.53/h, $0.26 in total.

## 2. Scoring

Bits per byte: $b = \\frac{bits}{bytes}$.

| step | rule |
|---|---|
| drop | id 103 |

### 2.1 Details

## 2. Scoring
"""

REVIEW_FIRST = """# Review first

Intro: see `quality/report.md` and `annotations/bpb.parquet`.

## Top 3

1. **କଟକ** (id 101, para 3, `templated`)
   - para 3 (para, 60 B): bpb 0.2
   > କଟକ 20.20 ଉତ୍ତର ଅକ୍ଷାଂଶ …

2. **ବାଲେଶ୍ୱର (ଜିଲ୍ଲା)** (id 103, para 2, `english`)
   - para 2: mostly English

3. **ଗଣିତ** (id 104, whole article, `article`)
   - article: bpb 0.9

4. **gone** (id 424242, para 1, `garbled`)

A closing note at the end.
"""


def build_data_root(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    ds = root / "wiki"
    (ds / "annotations").mkdir(parents=True)
    (ds / "quality").mkdir()
    (ds / "markdown").mkdir()
    rows = corpus_rows()
    write_parquet(ds / "corp-20260101.parquet", rows)
    write_jsonl(ds / "corp-20260101.jsonl", rows)
    old = [dict(r, text=r["text"] + "\n\nପୁରୁଣା ।") for r in rows[:3]]
    write_jsonl(ds / "corp-20250101.jsonl", old)
    (ds / "corp-20260101-build.json").write_text(
        json.dumps(
            {
                "built": "2026-01-02",
                "articles": len(rows),
                "dropped": {"disambiguation": 2},
                "dropped_titles": {"disambiguation": ["କ (ବହୁବିକଳ୍ପ)", "ଖ"]},
            },
            ensure_ascii=False,
        )
    )
    (ds / "README.md").write_text("# Wiki fixture\n\n| a | b |\n|---|---|\n| 1 | 2 |\n", encoding="utf-8")
    (ds / "LEARNINGS.md").write_text("# Learnings\n\n- one\n", encoding="utf-8")
    (ds / "quality" / "report.md").write_text(
        "# Quality\n\nSome **findings** and $x$.\n<script>x</script>\n", encoding="utf-8"
    )
    for r in rows:
        (ds / r["file"]).write_text(f"---\ntitle: {r['title']}\n---\n\n{r['text']}", encoding="utf-8")
    # article-level annotations
    write_parquet(
        ds / "annotations" / "topics.parquet",
        [
            {"id": 101, "topics": ["geography", "odisha"], "primary_topic": "geography", "school_relevant": True},
            {"id": 102, "topics": ["religion", "odisha"], "primary_topic": "religion", "school_relevant": False},
            {"id": 104, "topics": ["math"], "primary_topic": "math", "school_relevant": True},
            {"id": 105, "topics": ["history"], "primary_topic": "history", "school_relevant": False},
            {"id": 106, "topics": ["history"], "primary_topic": "history", "school_relevant": True},
            {"id": 999, "topics": ["orphan"], "primary_topic": "none", "school_relevant": False},
        ],
    )
    text = {r["id"]: r["text"] for r in rows}
    bpb = []
    for r in rows:
        stale = r["id"] == 104  # scored on an older text
        bpb.append(
            {
                "id": r["id"],
                "bpb": 0.3 + r["id"] % 7 / 10,
                "tokens": 10 * r["id"] % 97,
                "review_rank": {103: 1, 101: 2}.get(r["id"]),
                "review_reasons": ["high bpb"] if r["id"] in (101, 103) else [],
                "text_sha1": sha1("old text") if stale else sha1(text[r["id"]]),
            }
        )
    write_parquet(ds / "annotations" / "bpb.parquet", bpb)
    (ds / "annotations" / "bpb.json").write_text(
        json.dumps(
            {
                "name": "bpb",
                "description": "Sarvam-1 bits per byte",
                "source": "test",
                "created": "2026-01-03",
                "columns": {"bpb": "bits per byte", "review_rank": "1 = review first"},
                "depends_on_text": True,
            }
        )
    )
    paras = []
    for r in rows:
        for i, p in enumerate(r["text"].split("\n\n")):
            paras.append(
                {
                    "id": r["id"],
                    "para": i,
                    "bpb": 0.2 + (i % 4) / 5,
                    "bytes": len(p.encode()),
                    "kind": "heading" if p.startswith("#") else "para",
                    "text_sha1": sha1(r["text"]),
                    "para_sha1": sha1(p),
                }
            )
    write_parquet(ds / "annotations" / "bpb.paragraphs.parquet", paras)
    write_parquet(ds / "annotations" / "broken.parquet", [{"page": 1, "x": 2}])  # no id column
    (ds / "METHODOLOGY.md").write_text(METHODOLOGY, encoding="utf-8")
    (ds / "quality" / "review-first.md").write_text(REVIEW_FIRST, encoding="utf-8")
    # a folder that is not a dataset (no text column), and a JSONL-only dataset
    (root / "notads").mkdir()
    write_parquet(root / "notads" / "table.parquet", [{"id": 1, "body": "x"}])
    (root / "small").mkdir()
    write_jsonl(root / "small" / "tiny.jsonl", [{"id": 1, "text": "# a\n\nb"}, {"id": 2, "text": "# c"}])
    return root


# ---- the JSON-lines layout: no Parquet, excluded.jsonl and removed-blocks.jsonl ----------------

EXCLUDED = [
    {
        "id": 201,
        "revid": 5201,
        "title": "1998",
        "reason": "year page",
        "detail": "3 Odia words outside template sentences",
    },
    {
        "id": 202,
        "revid": 5202,
        "title": "1997",
        "reason": "year page",
        "detail": "0 Odia words outside template sentences",
    },
    {"id": 203, "revid": 5203, "title": "ଜାନୁଆରୀ 5", "reason": "date page without events", "detail": ""},
    {"id": 204, "revid": 5204, "title": "ଓଡ଼ିଆ (ବହୁବିକଳ୍ପ)", "reason": "disambiguation", "detail": ""},
    {"id": 205, "revid": 5205, "title": "କଟକ ସହର", "reason": "duplicate text", "detail": "same text as id 101 (କଟକ)"},
    {"id": 206, "revid": 5206, "title": "ପୁରୁଣା", "reason": "duplicate text", "detail": "same text as id 999 (ନାହିଁ)"},
    {
        "id": 207,
        "revid": 5207,
        "title": "English page",
        "reason": "mostly English",
        "detail": "what is left after removing English is under 25 Odia words",
    },
    {"id": 208, "revid": 5208, "title": "ମୂଳ ପୃଷ୍ଠା", "reason": "main page", "detail": ""},
    {"id": 209, "revid": 5209, "title": "ଖରାପ", "reason": "reviewer: drop", "detail": "spam"},
]

BLOCKS = [
    {
        "id": 101,
        "title": "କଟକ",
        "kind": "list item",
        "reason": "citation",
        "text": "- Smith (2001). A history.",
        "article_kept": True,
    },
    {
        "id": 101,
        "title": "କଟକ",
        "kind": "paragraph",
        "reason": "junk",
        "text": "{{cite web | url = x}}",
        "article_kept": True,
    },
    {
        "id": 103,
        "title": "ବାଲେଶ୍ୱର (ଜିଲ୍ଲା)",
        "kind": "paragraph",
        "reason": "awaiting translation",
        "text": "Balasore is a district.",
        "article_kept": True,
    },
    {
        "id": 207,
        "title": "English page",
        "kind": "paragraph",
        "reason": "awaiting translation",
        "text": "An English paragraph.",
        "article_kept": False,
    },
]


def counts(rows, key):
    out: dict[str, int] = {}
    for r in rows:
        out[r[key]] = out.get(r[key], 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def build_jsonl_root(root: Path) -> Path:
    """A dataset in the JSON-lines layout: JSONL corpus (no `file` column, with templated_share),
    JSONL annotations with sidecars, excluded.jsonl, removed-blocks.jsonl, a build file without
    dropped_titles, and no Markdown export."""
    ds = root / "jwiki"
    (ds / "annotations").mkdir(parents=True)
    rows = []
    for r in corpus_rows():
        r = {k: v for k, v in r.items() if k != "file"}
        r["templated_share"] = round((r["id"] % 4) / 4, 3)
        rows.append(r)
    write_jsonl(ds / "jw-20260901.jsonl", rows)
    write_jsonl(ds / "excluded.jsonl", EXCLUDED)
    write_jsonl(ds / "removed-blocks.jsonl", BLOCKS)
    (ds / "jw-20260901-build.json").write_text(
        json.dumps(
            {
                "dump": "orwiki-20260901-pages-articles.xml.bz2",
                "built": "2026-09-25",
                "pages_in_dump": len(rows) + len(EXCLUDED),
                "articles": len(rows),
                "excluded": len(EXCLUDED),
                "dropped": counts(EXCLUDED, "reason"),
            },
            ensure_ascii=False,
        )
    )
    text = {r["id"]: r["text"] for r in rows}
    write_jsonl(
        ds / "annotations" / "topics.jsonl",
        [
            {"id": 101, "topics": ["geography", "odisha"], "primary_topic": "geography"},
            {"id": 102, "topics": ["religion", "odisha"], "primary_topic": "religion"},
            {"id": 104, "topics": ["math"], "primary_topic": "math"},
        ],
    )
    write_jsonl(
        ds / "annotations" / "bpb.jsonl",
        [
            {
                "id": r["id"],
                "bpb": 0.3 + r["id"] % 7 / 10,
                "review_rank": {103: 1, 101: 2}.get(r["id"]),
                "review_reasons": ["high bpb"] if r["id"] in (101, 103) else [],
                "scored_at": "2026-09-20T10:00:00Z",
                "note": None,  # all null: JSON has no type for it
                "text_sha1": sha1("old text") if r["id"] == 104 else sha1(text[r["id"]]),
            }
            for r in rows
        ],
    )
    (ds / "annotations" / "bpb.json").write_text(
        json.dumps({"description": "bits per byte", "columns": {"bpb": "bits per byte"}, "depends_on_text": True})
    )
    write_jsonl(
        ds / "annotations" / "bpb.paragraphs.jsonl",
        [
            {"id": r["id"], "para": i, "bpb": 0.2 + (i % 4) / 5, "para_sha1": sha1(p)}
            for r in rows
            for i, p in enumerate(r["text"].split("\n\n"))
        ],
    )
    (ds / "annotations" / "bpb.paragraphs.json").write_text(json.dumps({"description": "bits per byte per paragraph"}))
    (ds / "METHODOLOGY.md").write_text(
        "# Methodology\n\nLeft out: `excluded.jsonl`; cut blocks: removed-blocks.jsonl; "
        "scores in annotations/bpb.jsonl.\n",
        encoding="utf-8",
    )
    return root


@pytest.fixture
def jdata_root(tmp_path) -> Path:
    return build_jsonl_root(tmp_path / "jdata")


@pytest.fixture
def jclient(jdata_root, writer):
    from fastapi.testclient import TestClient

    app = create_app(jdata_root, writer=writer, state_dir=writer.root / "state", cache_dir=writer.root / "cache")
    app.state.catalog.throttle = 0
    with TestClient(app) as c:
        yield c


def reviews_snapshot(folder: Path):
    """Every file in the folder with its size, mtime and content hash (None: no folder)."""
    if not folder.exists():
        return None
    return sorted(
        (p.name, p.stat().st_size, p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())
        for p in folder.iterdir()
        if p.is_file()
    )


@pytest.fixture(scope="session", autouse=True)
def real_reviews_untouched():
    """No test may touch the real review log, the repository's reviews/reviews.jsonl that the build
    reads (nor anything else in reviews/): every test uses a temporary state folder."""
    from edaapp.paths import REVIEWS_DIR

    before = reviews_snapshot(REVIEWS_DIR)
    yield
    assert reviews_snapshot(REVIEWS_DIR) == before, f"a test changed {REVIEWS_DIR}"


@pytest.fixture
def data_root(tmp_path) -> Path:
    return build_data_root(tmp_path / "data")


@pytest.fixture
def writer(tmp_path) -> SafeWriter:
    (tmp_path / "app").mkdir(exist_ok=True)
    return SafeWriter(tmp_path / "app")


@pytest.fixture
def reviews(writer) -> ReviewStore:
    return ReviewStore(writer.root / "state" / "reviews.jsonl", writer)


@pytest.fixture
def catalog(data_root, writer, reviews) -> Catalog:
    return Catalog(data_root, writer, reviews, cache_dir=writer.root / "cache", throttle=0)


@pytest.fixture
def client(data_root, writer):
    from fastapi.testclient import TestClient

    app = create_app(data_root, writer=writer, state_dir=writer.root / "state", cache_dir=writer.root / "cache")
    app.state.catalog.throttle = 0
    with TestClient(app) as c:
        yield c


@pytest.fixture
def no_server(monkeypatch):
    """cli.main up to the point where it would serve: uvicorn.run records its app instead."""
    import uvicorn

    ran = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: ran.update(app=app, **kw))
    return ran
