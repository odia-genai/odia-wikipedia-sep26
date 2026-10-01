"""Gzipped JSON lines tables: a `<stem>.jsonl.gz` corpus (as published on Hugging Face) and a
`.jsonl.gz` annotation are found, versioned by their stem, loaded and shown like `.jsonl` ones."""

import gzip
import json
import os

import pytest
from conftest import BLOCKS, build_jsonl_root, corpus_rows, sha1, write_jsonl

from edaapp import discovery

J = "/api/d/jwiki"
STEM = "jw-20260901"


def gzip_file(path, mtime=None):
    """Replace x.jsonl by x.jsonl.gz (the same bytes, gzipped); returns the new path."""
    gz = path.with_name(path.name + ".gz")
    gz.write_bytes(gzip.compress(path.read_bytes(), mtime=0))
    path.unlink()
    if mtime is not None:
        os.utime(gz, ns=(mtime, mtime))
    return gz


def write_gz(path, rows):
    data = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows).encode("utf-8")
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(gzip.compress(data, mtime=0))
    os.replace(tmp, path)


def build_gz_root(root):
    """The JSON-lines fixture with its corpus and the topics annotation gzipped."""
    build_jsonl_root(root)
    ds = root / "jwiki"
    gzip_file(ds / f"{STEM}.jsonl")
    gzip_file(ds / "annotations" / "topics.jsonl")
    (ds / "METHODOLOGY.md").write_text(
        "# Methodology\n\nTopics are in annotations/topics.jsonl.gz, scores in `annotations/bpb.jsonl`.\n",
        encoding="utf-8",
    )
    return root


@pytest.fixture
def gz_root(tmp_path):
    return build_gz_root(tmp_path / "gzdata")


@pytest.fixture
def gzclient(gz_root, writer):
    from fastapi.testclient import TestClient

    from edaapp.server import create_app

    app = create_app(gz_root, writer=writer, state_dir=writer.root / "state", cache_dir=writer.root / "cache")
    app.state.catalog.throttle = 0
    with TestClient(app) as c:
        yield c


def test_table_ext():
    assert discovery.table_ext("x.jsonl.gz") == ".jsonl.gz"
    assert discovery.table_ext("x.jsonl") == ".jsonl" and discovery.table_ext("x.parquet") == ".parquet"
    assert discovery.table_ext("x.json") is None and discovery.table_ext("x.gz") is None
    assert discovery.table_ext("x.jsonl.gz.tmp") is None and discovery.table_ext(".jsonl.gz") is None


# ---- discovery --------------------------------------------------------------------------------


def test_gzipped_corpus_is_a_version_with_its_build_file(gz_root):
    ds = discovery.discover(gz_root)["jwiki"]
    (v,) = ds.versions
    assert v.stem == STEM and v.fmt == "jsonl.gz" and v.primary.path.name == f"{STEM}.jsonl.gz"
    assert v.date == "20260901" and v.not_read == [] and ds.notes == []
    # <stem>-build.json next to <stem>.jsonl.gz
    assert v.build_json is not None and v.build_json.path.name == f"{STEM}-build.json"
    assert {"id", "text", "title"} <= set(discovery.table_columns(v.primary))  # the first line, gunzipped
    anns = {a.key: a for a in ds.annotations}
    assert anns["topics"].fmt == "jsonl.gz" and anns["topics"].file.path.name == "topics.jsonl.gz"
    assert anns["bpb"].fmt == "jsonl" and ds.ignored == []


def test_plain_and_gzipped_copies_of_one_version(gz_root):
    d = gz_root / "jwiki"
    gz = d / f"{STEM}.jsonl.gz"
    write_jsonl(d / f"{STEM}.jsonl", [{"id": 1, "text": "# a"}])
    t = 1_700_000_000_000_000_000
    for p in (gz, d / f"{STEM}.jsonl"):
        os.utime(p, ns=(t, t))
    v = discovery.discover(gz_root)["jwiki"].version(STEM)
    assert sorted(v.files) == ["jsonl", "jsonl.gz"] and v.fmt == "jsonl"  # a tie: the plain file
    assert v.note == f"{STEM}.jsonl.gz is not read: {STEM}.jsonl is used (same mtime, jsonl preferred)"
    os.utime(gz, ns=(t + 60 * 10**9, t + 60 * 10**9))
    v = discovery.discover(gz_root)["jwiki"].version(STEM)
    assert v.fmt == "jsonl.gz" and v.not_read == [f"{STEM}.jsonl"]


def test_gzipped_side_files_and_broken_gz_are_not_versions(gz_root):
    d = gz_root / "jwiki"
    write_gz(d / "removed-blocks.jsonl.gz", BLOCKS)  # has id and text, but the name is reserved
    (d / "jw-20270101.jsonl.gz").write_bytes(b"not gzip at all")
    ds = discovery.discover(gz_root)["jwiki"]
    assert [v.stem for v in ds.versions] == [STEM]


def test_signature_sees_a_replaced_gz(gz_root):
    d = gz_root / "jwiki"
    sig = discovery.dir_signature(d)
    write_gz(d / f"{STEM}.jsonl.gz", corpus_rows()[:2])
    assert discovery.dir_signature(d) != sig


# ---- loading and the views ----------------------------------------------------------------------


def test_gzipped_corpus_over_the_api(gzclient):
    o = gzclient.get(f"{J}/overview").json()
    assert o["articles"] == 6 and o["file"] == f"{STEM}.jsonl.gz" and o["file_not_read"] == []
    assert o["build_file"] == f"{STEM}-build.json" and o["build"]["built"] == "2026-09-25"
    st = {a["key"]: a for a in o["annotations"]}
    assert st["topics"]["file"] == "topics.jsonl.gz" and st["topics"]["matched"] == 3
    assert not [w for w in o["warnings"] if "could not" in w["text"]]
    cols = {c["key"]: c for c in gzclient.get(f"{J}/schema").json()["columns"]}
    assert cols["timestamp"]["dtype"] == "VARCHAR" and cols["topics.topics"]["kind"] == "list"
    home = gzclient.get("/api/datasets").json()["datasets"][0]
    assert home["versions"][0]["fmt"] == "jsonl.gz" and home["versions"][0]["formats"] == ["jsonl.gz"]
    assert home["articles"] == 6 and home["built"] == "2026-09-25"
    r = gzclient.get(f"{J}/browse", params={"text": "ତୀର୍ଥ"}).json()
    assert [x["id"] for x in r["rows"]] == [102]


def test_article_from_a_gzipped_corpus(gzclient):
    want = {r["id"]: r for r in corpus_rows()}[102]
    a = gzclient.get(f"{J}/article/102").json()
    assert a["text"] == want["text"] and a["text_sha1"] == sha1(want["text"])
    assert a["meta"]["timestamp"] == "2026-03-01T00:00:00Z" and a["title"] == want["title"]
    assert a["annotations"]["topics"]["topics"] == ["religion", "odisha"]  # from topics.jsonl.gz
    assert a["links"]["revision"] == "https://or.wikipedia.org/w/index.php?oldid=1102"
    assert len(a["paragraphs"]) == len(want["text"].split("\n\n"))
    # a review is keyed by the dataset's name, as for any corpus
    r = gzclient.post(f"{J}/review", json={"id": 102, "verdict": "keep", "text_sha1": a["text_sha1"]})
    assert r.status_code == 200 and r.json()["event"]["dataset"] == "jwiki"


def test_a_replaced_gzipped_corpus_is_reloaded(gzclient, gz_root):
    assert gzclient.get(f"{J}/overview").json()["articles"] == 6
    write_gz(gz_root / "jwiki" / f"{STEM}.jsonl.gz", corpus_rows()[:3])
    assert gzclient.get(f"{J}/overview").json()["articles"] == 3


def test_documents_link_gzipped_annotations(gzclient):
    html = gzclient.get(f"{J}/methodology").json()["html"]
    assert 'href="#/d/jwiki?ann=topics"' in html and 'href="#/d/jwiki?ann=bpb"' in html
    assert ">annotations/topics.jsonl.gz</a>" in html  # the whole name is the link
