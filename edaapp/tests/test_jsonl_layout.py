"""The JSON-lines layout: a JSONL-only dataset, excluded.jsonl, removed-blocks.jsonl, and a
conversion in progress (the same table as both Parquet and JSONL)."""

import json
import os

from conftest import BLOCKS, EXCLUDED, counts, sha1, write_jsonl, write_parquet

from edaapp import discovery

J = "/api/d/jwiki"
W = "/api/d/wiki"


def set_mtime(path, seconds):
    os.utime(path, ns=(int(seconds * 1e9), int(seconds * 1e9)))


# ---- discovery ------------------------------------------------------------------------------


def test_jsonl_only_dataset_is_found(jdata_root):
    ds = discovery.discover(jdata_root)["jwiki"]
    # excluded.jsonl and removed-blocks.jsonl (which has id and text) are not corpus versions
    assert [v.stem for v in ds.versions] == ["jw-20260901"]
    assert ds.versions[0].fmt == "jsonl" and ds.versions[0].not_read == [] and ds.notes == []
    anns = {a.key: a for a in ds.annotations}
    assert {k: a.fmt for k, a in anns.items()} == {"bpb": "jsonl", "bpb.paragraphs": "jsonl", "topics": "jsonl"}
    assert anns["bpb.paragraphs"].sidecar.path.name == "bpb.paragraphs.json"
    assert anns["bpb"].sidecar.path.name == "bpb.json" and anns["topics"].sidecar is None
    assert ds.excluded.path.name == "excluded.jsonl" and ds.removed_blocks.path.name == "removed-blocks.jsonl"
    assert ds.ignored == []


def test_newer_format_is_read_and_the_other_named(data_root):
    """Mid-conversion: an annotation (or corpus) exists as both .parquet and .jsonl."""
    ann = data_root / "wiki" / "annotations"
    write_jsonl(ann / "topics.jsonl", [{"id": 101, "topics": ["new"], "primary_topic": "new", "school_relevant": True}])
    set_mtime(ann / "topics.parquet", 1_700_000_000)
    set_mtime(ann / "topics.jsonl", 1_700_000_180)
    ds = discovery.discover(data_root)["wiki"]
    t = next(a for a in ds.annotations if a.key == "topics")
    assert t.fmt == "jsonl" and t.not_read == ["topics.parquet"]
    assert "annotations/topics.parquet is not read: topics.jsonl is used (newer by 3 min)" in ds.notes
    assert not any("topics" in m for m in ds.ignored)  # a note, not an unusable file
    # the Parquet file rewritten later wins
    set_mtime(ann / "topics.parquet", 1_700_000_300)
    t = next(a for a in discovery.discover(data_root)["wiki"].annotations if a.key == "topics")
    assert t.fmt == "parquet" and t.not_read == ["topics.jsonl"]
    # same mtime: JSONL, the canonical format
    set_mtime(ann / "topics.jsonl", 1_700_000_300)
    ds = discovery.discover(data_root)["wiki"]
    assert next(a for a in ds.annotations if a.key == "topics").fmt == "jsonl"
    assert any("same mtime, jsonl preferred" in n for n in ds.notes)
    # the corpus version the same way
    wiki = data_root / "wiki"
    set_mtime(wiki / "corp-20260101.parquet", 1_700_000_000)
    set_mtime(wiki / "corp-20260101.jsonl", 1_700_000_005)
    ds = discovery.discover(data_root)["wiki"]
    v = ds.version("corp-20260101")
    assert v.fmt == "jsonl" and v.primary.path.name == "corp-20260101.jsonl" and v.not_read == ["corp-20260101.parquet"]
    assert "corp-20260101.parquet is not read: corp-20260101.jsonl is used (newer by 5 s)" in ds.notes


def test_mixed_formats_over_the_api(client, data_root):
    ann = data_root / "wiki" / "annotations"
    write_jsonl(ann / "topics.jsonl", [{"id": 101, "topics": ["new"], "primary_topic": "new", "school_relevant": True}])
    set_mtime(ann / "topics.parquet", 1_700_000_000)
    set_mtime(ann / "topics.jsonl", 1_700_000_060)
    o = client.get(f"{W}/overview").json()
    st = {a["key"]: a for a in o["annotations"]}
    assert st["topics"]["file"] == "topics.jsonl" and st["topics"]["not_read"] == ["topics.parquet"]
    assert any("topics.parquet is not read" in w["text"] for w in o["warnings"])
    assert client.get(f"{W}/article/101").json()["annotations"]["topics"]["primary_topic"] == "new"
    home = {d["name"]: d for d in client.get("/api/datasets").json()["datasets"]}
    assert any("topics.parquet is not read" in n for n in home["wiki"]["notes"])


def test_tmp_files_are_not_watched(jdata_root):
    d = jdata_root / "jwiki"
    sig = discovery.dir_signature(d)
    (d / "jw-20260901.jsonl.tmp").write_text('{"id": 1, "text": "# half"}\n')  # a build writing
    (d / "annotations" / "bpb.jsonl.tmp").write_text("{")
    assert discovery.dir_signature(d) == sig
    ds = discovery.discover(jdata_root)["jwiki"]
    assert [v.stem for v in ds.versions] == ["jw-20260901"] and ds.ignored == []


# ---- loading JSON lines ------------------------------------------------------------------------


def test_jsonl_columns_keep_the_parquet_types(jclient):
    cols = {c["key"]: c for c in jclient.get(f"{J}/schema").json()["columns"]}
    # ISO strings stay strings (DuckDB would guess TIMESTAMP and drop the "T" and "Z")
    assert cols["timestamp"]["dtype"] == "VARCHAR" and cols["timestamp"]["kind"] == "date"
    assert cols["bpb.scored_at"]["dtype"] == "VARCHAR"
    assert cols["bpb.note"]["dtype"] == "VARCHAR"  # all null: JSON, read as a string
    assert cols["topics.topics"]["kind"] == "list" and cols["bpb.review_reasons"]["kind"] == "list"
    assert cols["templated_share"]["kind"] == "numeric" and cols["words"]["kind"] == "numeric"
    assert "file" not in cols
    assert "templated_share" in jclient.get(f"{J}/schema").json()["default_cols"]
    a = jclient.get(f"{J}/article/102").json()
    assert a["meta"]["timestamp"] == "2026-03-01T00:00:00Z"
    assert a["links"]["file"] is None and a["links"]["file_exists"] is False
    assert a["links"]["revision"] == "https://or.wikipedia.org/w/index.php?oldid=1102"
    assert a["annotations"]["topics"]["topics"] == ["religion", "odisha"]
    assert set(a["para_annotations"]["bpb"]["rows"]) == {"0", "1", "2", "3"}
    r = jclient.get(f"{J}/browse", params=[("min.timestamp", "2026-03-01"), ("sort", "id")]).json()
    assert [x["id"] for x in r["rows"]] == [102, 103, 104]


def test_jsonl_annotation_status_and_staleness(jclient):
    o = jclient.get(f"{J}/overview").json()
    st = {a["key"]: a for a in o["annotations"]}
    assert st["bpb"]["file"] == "bpb.jsonl" and st["bpb"]["stale"] == 1 and st["bpb"]["not_read"] == []
    assert st["bpb.paragraphs"]["stale"] == 0 and st["bpb.paragraphs"]["sidecar"] == "bpb.paragraphs.json"
    assert st["bpb.paragraphs"]["meta"]["description"] == "bits per byte per paragraph"
    assert st["topics"]["missing"] == 3
    assert any(d["key"] == "templated_share" for d in o["distributions"])
    assert o["file"] == "jw-20260901.jsonl" and o["file_not_read"] == []
    assert not any("not read" in w["text"] for w in o["warnings"])


# ---- excluded.jsonl ----------------------------------------------------------------------------


def ex(client, **params):
    r = client.get(f"{J}/excluded", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def test_excluded_counts_filter_search_and_pages(jclient):
    r = ex(jclient)
    assert r["total"] == len(EXCLUDED) and r["file"] == "excluded.jsonl"
    pages = r["summary"]["pages"]
    assert dict(map(tuple, pages["reasons"])) == counts(EXCLUDED, "reason")
    assert pages["reasons"][0][1] == 2 and pages["in_corpus"] == 0
    assert r["summary"]["mismatch"] == []  # the build file's counts agree
    # default order: reason, then title
    assert [x["reason"] for x in r["rows"]] == sorted(x["reason"] for x in EXCLUDED)
    assert [x["title"] for x in ex(jclient, reason="year page")["rows"]] == ["1997", "1998"]
    assert ex(jclient, reason="nope")["total"] == 0
    # title search, detail search, an exact id or revid
    assert [x["id"] for x in ex(jclient, q="ଓଡ଼ିଆ")["rows"]] == [204]
    assert [x["id"] for x in ex(jclient, q="spam")["rows"]] == [209]
    assert [x["id"] for x in ex(jclient, q="english")["rows"]] == [207]  # case-insensitive
    assert [x["id"] for x in ex(jclient, q="205")["rows"]] == [205]
    assert [x["id"] for x in ex(jclient, q="5203")["rows"]] == [203]
    # pages and sorting
    p2 = ex(jclient, sort="id", size=4, page=2)
    assert [x["id"] for x in p2["rows"]] == [205, 206, 207, 208] and p2["total"] == 9 and p2["page"] == 2
    assert [x["id"] for x in ex(jclient, sort="id", dir="desc", size=2)["rows"]] == [209, 208]
    assert jclient.get(f"{J}/excluded", params={"sort": "title; DROP TABLE x"}).status_code == 400
    assert jclient.get(f"{J}/excluded", params={"page": "x"}).status_code == 400
    assert ex(jclient, q="²")["total"] == 0 and ex(jclient, q="9" * 30)["total"] == 0  # not ids: text only


def test_excluded_links(jclient):
    rows = {x["id"]: x for x in ex(jclient, reason="duplicate text")["rows"]}
    assert rows[205]["links"] == {
        "revision": "https://or.wikipedia.org/w/index.php?oldid=5205",
        "current": "https://or.wikipedia.org/?curid=205",
    }
    # the detail names the kept page: linked when it is an article of the corpus
    assert rows[205]["detail_ids"] == [{"id": 101, "in_corpus": True, "title": "କଟକ"}]
    assert rows[206]["detail_ids"] == [{"id": 999, "in_corpus": False, "title": None}]
    assert rows[205]["in_corpus"] is False
    assert ex(jclient, q="1998")["rows"][0]["detail_ids"] == []  # "3 Odia words ..." names no id


def test_origin_falls_back_to_the_dump_name(jclient, jdata_root):
    path = jdata_root / "jwiki" / "jw-20260901.jsonl"
    rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]
    write_jsonl(path, [{k: v for k, v in r.items() if k != "url"} for r in rows])
    assert ex(jclient)["origin"] == "https://or.wikipedia.org"  # from "orwiki-20260901-pages-..."


def test_overview_pages_left_out_come_from_excluded(jclient):
    o = jclient.get(f"{J}/overview").json()
    assert o["dropped"] == counts(EXCLUDED, "reason")  # the build file's counts
    assert o["dropped_titles"] is False  # no title lists in the build file: the Excluded view has them
    assert o["excluded"]["pages"]["rows"] == len(EXCLUDED)
    assert o["excluded"]["blocks"]["rows"] == len(BLOCKS) and o["excluded"]["blocks"]["articles"] == 3
    assert "dropped" not in o["build"] and o["build"]["excluded"] == len(EXCLUDED)
    # the old per-reason titles endpoint reads excluded.jsonl now
    assert jclient.get(f"{J}/dropped", params={"reason": "year page"}).json()["titles"] == ["1997", "1998"]


def test_excluded_disagreements_are_reported(jclient, jdata_root):
    ds = jdata_root / "jwiki"
    build = json.loads((ds / "jw-20260901-build.json").read_text(encoding="utf-8"))
    build["dropped"]["year page"] = 5
    (ds / "jw-20260901-build.json").write_text(json.dumps(build, ensure_ascii=False), encoding="utf-8")
    # an excluded page that is also in the corpus: the two files come from different builds
    write_jsonl(ds / "excluded.jsonl", [*EXCLUDED, {"id": 101, "revid": 1, "title": "କଟକ", "reason": "year page"}])
    o = jclient.get(f"{J}/overview").json()
    assert o["excluded"]["mismatch"] == [["year page", 5, 3]]
    assert o["excluded"]["pages"]["in_corpus"] == 1
    text = " ".join(w["text"] for w in o["warnings"])
    assert "1 pages in excluded.jsonl are also in jw-20260901.jsonl" in text
    # an id search also finds the duplicate whose detail names that id
    rows = ex(jclient, q="101")["rows"]
    assert [(x["id"], x["in_corpus"], x["detail"]) for x in rows] == [
        (205, False, "same text as id 101 (କଟକ)"),
        (101, True, None),  # no detail field: null
    ]


def test_side_files_reload_and_keep_the_last_good_copy(jclient, jdata_root):
    path = jdata_root / "jwiki" / "excluded.jsonl"
    assert ex(jclient)["total"] == 9
    tmp = path.with_name("excluded.jsonl.tmp")
    write_jsonl(tmp, EXCLUDED[:3])
    os.replace(tmp, path)
    assert ex(jclient)["total"] == 3
    tmp.write_text(path.read_text(encoding="utf-8") + '{"id": 1, "rea', encoding="utf-8")  # truncated
    os.replace(tmp, path)
    assert ex(jclient)["total"] == 3  # the previous copy
    o = jclient.get(f"{J}/overview").json()
    assert any("jwiki/excluded.jsonl" in w["text"] for w in o["warnings"])
    path.unlink()
    assert jclient.get(f"{J}/excluded").status_code == 404
    assert jclient.get(f"{J}/overview").json()["excluded"]["pages"] is None


def test_unreadable_side_file_without_a_previous_copy(jclient, jdata_root):
    (jdata_root / "jwiki" / "removed-blocks.jsonl").write_text('{"id": 1, "title": "x", "kind', encoding="utf-8")
    r = jclient.get(f"{J}/removed-blocks")
    assert r.status_code == 503 and "could not read removed-blocks.jsonl" in r.json()["error"]
    assert ex(jclient)["total"] == 9  # the other file is fine
    o = jclient.get(f"{J}/overview").json()
    assert o["excluded"]["blocks"] is None
    assert any("jwiki/removed-blocks.jsonl" in w["text"] for w in o["warnings"])


def test_datasets_without_side_files(client):
    assert client.get(f"{W}/excluded").status_code == 404
    assert client.get(f"{W}/removed-blocks").status_code == 404
    o = client.get(f"{W}/overview").json()
    assert o["excluded"] is None and o["dropped_titles"] is True  # an older build file: titles in it
    assert client.get(f"{W}/article/101").json()["removed_blocks"] is None
    wiki = {d["name"]: d for d in client.get("/api/datasets").json()["datasets"]}["wiki"]
    assert wiki["excluded"] is False and wiki["removed_blocks"] is False and wiki["excluded_rows"] is None


# ---- removed-blocks.jsonl ----------------------------------------------------------------------


def rb(client, **params):
    r = client.get(f"{J}/removed-blocks", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def test_removed_blocks_filters_and_article_links(jclient):
    r = rb(jclient)
    assert r["total"] == 4 and r["has_kept"] is True
    blocks = r["summary"]["blocks"]
    assert dict(map(tuple, blocks["reasons"])) == counts(BLOCKS, "reason")
    assert dict(map(tuple, blocks["kinds"])) == counts(BLOCKS, "kind")
    assert [(x["id"], x["reason"]) for x in r["rows"]] == [
        (101, "citation"),
        (101, "junk"),
        (103, "awaiting translation"),
        (207, "awaiting translation"),
    ]
    by = {(x["id"], x["reason"]): x for x in r["rows"]}
    assert by[(101, "junk")]["in_corpus"] is True and by[(101, "junk")]["excluded_reason"] is None
    # the block of a page left out as mostly English: not in the corpus, and why
    assert by[(207, "awaiting translation")]["in_corpus"] is False
    assert by[(207, "awaiting translation")]["excluded_reason"] == "mostly English"
    assert by[(207, "awaiting translation")]["article_kept"] is False
    assert [x["id"] for x in rb(jclient, reason="awaiting translation")["rows"]] == [103, 207]
    assert [x["reason"] for x in rb(jclient, kind="list item")["rows"]] == ["citation"]
    assert [x["id"] for x in rb(jclient, kept="0")["rows"]] == [207]
    assert [x["reason"] for x in rb(jclient, id="101")["rows"]] == ["citation", "junk"]
    assert [x["id"] for x in rb(jclient, q="balasore")["rows"]] == [103]  # text, case-insensitive
    assert [x["id"] for x in rb(jclient, q="ବାଲେଶ୍ୱର")["rows"]] == [103]  # title
    longest = max(len(b["text"]) for b in BLOCKS)
    assert [x["chars"] for x in rb(jclient, sort="chars", dir="desc", size=1)["rows"]] == [longest]
    assert jclient.get(f"{J}/removed-blocks", params={"id": "x"}).status_code == 400
    assert jclient.get(f"{J}/removed-blocks", params={"id": "9" * 30}).status_code == 400
    assert jclient.get(f"{J}/removed-blocks", params={"sort": "text"}).status_code == 400


def test_article_counts_its_removed_blocks(jclient):
    a = jclient.get(f"{J}/article/101").json()
    assert a["removed_blocks"] == {"total": 2, "by_reason": [["citation", 1], ["junk", 1]]}
    assert jclient.get(f"{J}/article/102").json()["removed_blocks"] == {"total": 0, "by_reason": []}


# ---- the nav, home and document links --------------------------------------------------------------


def test_home_and_links_to_the_excluded_view(jclient):
    d = jclient.get("/api/datasets").json()["datasets"][0]
    assert d["name"] == "jwiki"
    assert d["excluded"] is True and d["removed_blocks"] is True
    assert d["excluded_rows"] == 9 and d["removed_block_rows"] == 4
    assert d["versions"][0]["fmt"] == "jsonl" and d["versions"][0]["not_read"] == []
    html = jclient.get(f"{J}/methodology").json()["html"]
    assert 'href="#/d/jwiki/excluded"' in html
    assert 'href="#/d/jwiki/excluded?tab=blocks"' in html
    assert 'href="#/d/jwiki?ann=bpb"' in html  # annotations/bpb.jsonl


def test_parquet_annotation_still_works_next_to_jsonl_ones(jclient, jdata_root):
    write_parquet(jdata_root / "jwiki" / "annotations" / "school.parquet", [{"id": 101, "grade": 7}])
    cols = {c["key"] for c in jclient.get(f"{J}/schema").json()["columns"]}
    assert {"school.grade", "bpb.bpb"} <= cols
    assert jclient.get(f"{J}/article/101").json()["annotations"]["school"]["grade"] == 7


def test_text_sha1_of_jsonl_corpus_matches_python(jclient):
    a = jclient.get(f"{J}/article/104").json()
    assert a["text_sha1"] == sha1(a["text"])
