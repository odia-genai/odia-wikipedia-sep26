"""Documents (Methodology, reports, docs): anchors, table of contents, math, reference links."""

import os

import pytest
from conftest import corpus_rows, sha1, write_parquet

from edaapp import reviewfirst
from edaapp.render import LinkContext, render_doc, slugify
from edaapp.service import locate

D = "/api/d/wiki"

CTX = LinkContext(
    "wiki",
    ids=frozenset({101, 102}),
    reports=frozenset({"report.md", "review-first.md"}),
    annotations=frozenset({"bpb", "bpb.paragraphs"}),
    docs=frozenset({"README.md", "LEARNINGS.md", "METHODOLOGY.md"}),
    review_first="review-first.md",
    side=frozenset({"excluded.jsonl"}),
)


def html_of(text):
    return render_doc(text, CTX)[0]


@pytest.mark.parametrize(
    "text,href",
    [
        ("see id 101.", "#/d/wiki/a/101"),
        ("see page id 102", "#/d/wiki/a/102"),
        ("`quality/report.md`", "#/d/wiki/reports?name=report.md&amp;group=quality"),
        ("quality/review-first.md", "#/d/wiki/review-first"),
        ("`annotations/bpb.parquet`", "#/d/wiki?ann=bpb"),
        ("annotations/bpb.paragraphs.parquet", "#/d/wiki?ann=bpb.paragraphs"),
        ("the sidecar annotations/bpb.json", "#/d/wiki?ann=bpb"),
        ("README.md", "#/d/wiki/reports?name=README.md&amp;group=docs"),
        ("data/wiki/LEARNINGS.md", "#/d/wiki/reports?name=LEARNINGS.md&amp;group=docs"),
        ("METHODOLOGY.md", "#/d/wiki/methodology"),
        ("`annotations/bpb.jsonl`", "#/d/wiki?ann=bpb"),
        ("pages left out: `excluded.jsonl`", "#/d/wiki/excluded"),
        ("data/wiki/excluded.jsonl", "#/d/wiki/excluded"),
    ],
)
def test_references_become_links(text, href):
    assert f'href="{href}"' in html_of(text)


@pytest.mark.parametrize(
    "text",
    [
        "id 999 is not in the corpus",
        "`quality/missing.md`",
        "annotations/nope.parquet",
        "translations/README.md",
        "src/README.md",
        "valid 12 and paid 101",
        "removed-blocks.jsonl",  # not in this dataset
        "old-excluded.jsonl",
        "[id 101](https://example.org)",  # already a link: left alone
        "`see id 101 here`",  # a code span is linked only if it is exactly a reference
    ],
)
def test_non_references_stay_text(text):
    html = html_of(text)
    assert 'class="ref"' not in html


def test_headings_get_unique_ids_and_toc():
    html, toc = render_doc("# T\n\n## ଓଡ଼ିଆ ଲେଖା: rules (v2)\n\n### a\n\n## ଓଡ଼ିଆ ଲେଖା: rules (v2)\n", CTX)
    assert [(t["level"], t["id"]) for t in toc] == [
        (1, "t"),
        (2, "ଓଡ଼ିଆ-ଲେଖା-rules-v2"),
        (3, "a"),
        (2, "ଓଡ଼ିଆ-ଲେଖା-rules-v2-1"),
    ]
    assert 'id="ଓଡ଼ିଆ-ଲେଖା-rules-v2-1"' in html
    assert slugify("  !!! ") == "section"
    assert slugify("ଡ଼") == "ଡ଼"  # no normalisation: the nukta stays a separate mark


def test_doc_math_is_strict_about_dollars():
    html = html_of("It cost $0.53/h for 0.5 h, $0.26 in total. Formula: $x^2 + 1$, and $$E = mc^2$$ inline.")
    assert "0.53/h for 0.5 h" in html and html.count('class="math inline"') == 1
    assert '<span class="math display">E = mc^2</span>' in html
    assert "<script>" not in html_of("<script>alert(1)</script>")


def test_review_first_parser():
    text = (
        "# Review first\n\nIntro.\n\n## Top\n\n"
        "1. **A (b)** (id 5, para 2, `garbled`)\n   - reason\n   > quote\n\n"
        "2. **B** (id 6, whole article, `article`)\n\n"
        "3. **C** (page id 7, paragraph 4)\n   - no type\n\n"
        "4. **D** (no id here)\n\n"
        "## Next\n\nClosing text.\n"
    )
    segs = reviewfirst.parse(text)
    kinds = [s["kind"] for s in segs]
    assert kinds == ["md", "item", "item", "item", "md"]
    a, b, c = [s["item"] for s in segs if s["kind"] == "item"]
    assert (a.rank, a.title, a.id, a.para, a.type) == (1, "A (b)", 5, 2, "garbled")
    assert a.body == "- reason\n> quote"
    assert (b.id, b.para, b.type) == (6, None, "article")
    assert (c.id, c.para, c.type) == (7, 4, None)
    assert "4. **D** (no id here)" in segs[-1]["text"] and "## Next" in segs[-1]["text"]
    assert reviewfirst.parse("# Just a report\n\nNo items.\n") == [
        {"kind": "md", "text": "# Just a report\n\nNo items."}
    ]


def test_locate_follows_the_paragraph_by_sha1():
    hashes = ["h0", "h1", "h2", "h3"]
    assert locate(1, 2, hashes, {(1, 2): "h2"}) == (2, "same")
    assert locate(1, 2, hashes, {(1, 2): "h3"}) == (3, "moved")
    assert locate(1, 2, hashes, {(1, 2): "zz"}) == (2, "changed")  # edited in place, or earlier ones removed
    assert locate(1, 7, hashes, {(1, 7): "zz"}) == (None, "gone")
    assert locate(1, 2, hashes, {}) == (2, "unverified")
    assert locate(1, 9, hashes, {}) == (None, "gone")
    assert locate(1, None, hashes, {}) == (None, "whole article")


# ---- API ----------------------------------------------------------------------------------------


def test_methodology_endpoint(client):
    m = client.get(f"{D}/methodology").json()
    assert m["name"] == "METHODOLOGY.md" and m["mtime"] > 0
    assert [t["id"] for t in m["toc"]] == ["methodology", "1-build", "2-scoring", "21-details", "2-scoring-1"]
    html = m["html"]
    assert 'href="#/d/wiki/a/101"' in html and 'href="#/d/wiki/a/102"' in html and "/a/777" not in html
    assert 'href="#/d/wiki/reports?name=report.md&amp;group=quality"' in html
    assert 'href="#/d/wiki?ann=bpb"' in html
    assert 'href="#/d/wiki/reports?name=README.md&amp;group=docs"' in html
    assert "<table>" in html and '<span class="math inline">' in html and "$0.53/h" in html
    assert client.get("/api/d/small/methodology").status_code == 404
    home = {d["name"]: d for d in client.get("/api/datasets").json()["datasets"]}
    assert home["wiki"]["methodology"] is True and home["small"]["methodology"] is False
    assert home["wiki"]["review_first"] is True and home["small"]["review_first"] is False


def test_reports_are_linked_too(client):
    r = client.get(f"{D}/report", params={"name": "review-first.md"}).json()
    assert 'href="#/d/wiki/a/101"' in r["html"] and r["toc"][0]["text"] == "Review first"
    doc = client.get(f"{D}/report", params={"name": "METHODOLOGY.md", "group": "docs"})
    assert doc.status_code == 200
    assert client.get(f"{D}/report", params={"name": "x.md", "group": "other"}).status_code == 400


def test_methodology_appears_changes_and_disappears(client, data_root):
    path = data_root / "wiki" / "METHODOLOGY.md"
    path.unlink()
    assert client.get(f"{D}/methodology").status_code == 404
    home = {d["name"]: d for d in client.get("/api/datasets").json()["datasets"]}
    assert home["wiki"]["methodology"] is False
    tmp = path.with_name("METHODOLOGY.md.tmp")
    tmp.write_text("# New\n\n## Step 3: re-score\n\nSee id 101.\n", encoding="utf-8")
    os.replace(tmp, path)
    m = client.get(f"{D}/methodology").json()
    assert [t["text"] for t in m["toc"]] == ["New", "Step 3: re-score"]
    tmp.write_text("# Newer\n", encoding="utf-8")
    os.utime(tmp, (m["mtime"] + 5, m["mtime"] + 5))
    os.replace(tmp, path)
    m2 = client.get(f"{D}/methodology").json()
    assert m2["toc"][0]["text"] == "Newer" and m2["mtime"] > m["mtime"]


def test_review_first_endpoint(client):
    r = client.get(f"{D}/review-first").json()
    assert r["parsed"] is True and r["items"] == 4 and r["queue_sort"] == "bpb.review_rank"
    items = [s for s in r["segments"] if s["kind"] == "item"]
    assert [i["rank"] for i in items] == [1, 2, 3, 4]
    assert dict(r["types"]) == {"templated": 1, "english": 1, "article": 1, "garbled": 1}
    one, two, three, four = items
    assert (one["id"], one["para_now"], one["para_status"], one["verdict"]) == (101, 3, "same", None)
    assert 'href="#/d/wiki?ann=bpb"' in r["segments"][0]["html"]
    assert three["para_now"] is None and three["para_status"] == "whole article"
    assert three["stale"] == ["bpb"]  # the fixture's bpb row for 104 was scored on older text
    assert four["in_corpus"] is False and four["para_status"] == "article not in this version"
    assert r["segments"][-1]["kind"] == "md" and "closing note" in r["segments"][-1]["html"]
    # a verdict shows up on the item; dropping the flagged paragraph counts as reviewed
    client.post(f"{D}/review", json={"id": 103, "verdict": "keep", "text_sha1": two["text_sha1"]})
    client.post(f"{D}/review", json={"id": 101, "drop_paras": [3], "text_sha1": one["text_sha1"]})
    items = [s for s in client.get(f"{D}/review-first").json()["segments"] if s["kind"] == "item"]
    assert items[1]["verdict"] == "keep" and items[1]["reviewed"] is True
    assert items[0]["verdict"] is None and items[0]["flagged_dropped"] is True and items[0]["reviewed"] is True
    assert client.get("/api/d/small/review-first").status_code == 404


def test_review_first_follows_paragraphs_across_a_rebuild(client, data_root):
    rows = corpus_rows()
    paras = rows[0]["text"].split("\n\n")
    # the rebuild removes paragraph 1 of article 101: its flagged paragraph 3 is now paragraph 2
    rows[0]["text"] = "\n\n".join(p for i, p in enumerate(paras) if i != 1)
    tmp = data_root / "wiki" / "c.parquet.tmp"
    write_parquet(tmp, rows)
    os.replace(tmp, data_root / "wiki" / "corp-20260101.parquet")
    items = [s for s in client.get(f"{D}/review-first").json()["segments"] if s["kind"] == "item"]
    assert (items[0]["para"], items[0]["para_now"], items[0]["para_status"]) == (3, 2, "moved")
    assert sha1(rows[0]["text"].split("\n\n")[2]) == sha1(paras[3])
    assert items[0]["stale"] == ["bpb"]


def test_review_first_falls_back_to_plain_rendering(client, data_root):
    (data_root / "wiki" / "quality" / "review-first.md").write_text(
        "# Review first\n\n- ବାଲେଶ୍ୱର: id 103, look at paragraph 2\n", encoding="utf-8"
    )
    r = client.get(f"{D}/review-first").json()
    assert r["parsed"] is False and 'href="#/d/wiki/a/103"' in r["html"]


def test_state_option_must_stay_inside_edaapp(tmp_path):
    from edaapp.cli import resolve_state
    from edaapp.paths import APP_ROOT, UnsafePathError

    assert resolve_state(APP_ROOT / ".cache" / "try-state") == APP_ROOT / ".cache" / "try-state"
    for bad in ["/tmp/state", APP_ROOT.parent / "data" / "state", APP_ROOT / "state" / ".." / ".." / "x"]:
        with pytest.raises(UnsafePathError):
            resolve_state(bad)
