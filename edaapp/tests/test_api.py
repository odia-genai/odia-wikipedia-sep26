import json

import pytest
from conftest import sha1

from edaapp.reviews import FIELDS

D = "/api/d/wiki"


def test_home_lists_datasets(client):
    r = client.get("/api/datasets").json()
    names = [d["name"] for d in r["datasets"]]
    assert names == ["small", "wiki"]
    wiki = r["datasets"][1]
    assert wiki["articles"] == 6 and wiki["built"] == "2026-01-02"
    assert [v["stem"] for v in wiki["versions"]] == ["corp-20260101", "corp-20250101"]
    anns = {a["key"]: a for a in wiki["annotations"]}
    assert anns["bpb"]["stale"] == 1 and anns["topics"]["stale"] is None
    assert "notads" in r["other_dirs"]


def test_overview(client):
    o = client.get(f"{D}/overview").json()
    assert o["articles"] == 6
    assert o["dropped"] == {"disambiguation": 2}
    keys = {d["key"] for d in o["distributions"]}
    assert {"words", "odia_ratio", "bpb.bpb", "bpb.review_rank", "paragraphs"} <= keys
    assert any(d["key"] == "bpb.paragraphs.bpb" for d in o["para_distributions"])
    cats = {c["key"]: c for c in o["categories"]}
    assert dict(map(tuple, cats["topics.topics"]["options"]))["odisha"] == 2
    assert "bot_created" in cats and "topics.school_relevant" in cats
    text = " ".join(w["text"] for w in o["warnings"])
    assert "1 articles were scored on a different text" in text
    assert "1 corpus articles have no row" in text  # topics covers 5 of 6
    assert "1 ids are not in this corpus version" in text  # topics id 999
    assert "broken.parquet" in text
    st = {a["key"]: a for a in o["annotations"]}
    assert st["bpb"]["stale"] == 1 and st["bpb.paragraphs"]["stale"] == 0
    assert client.get(f"{D}/dropped", params={"reason": "disambiguation"}).json()["titles"] == ["କ (ବହୁବିକଳ୍ପ)", "ଖ"]


def test_schema_kinds(client):
    cols = {c["key"]: c for c in client.get(f"{D}/schema").json()["columns"]}
    assert cols["bot_created"]["kind"] == "bool"
    assert cols["words"]["kind"] == "numeric"
    assert cols["topics.topics"]["kind"] == "list"
    assert cols["topics.primary_topic"]["kind"] == "category"
    assert cols["timestamp"]["kind"] == "date"
    assert cols["title"]["kind"] == "text"
    assert cols["bpb.text_sha1"]["filterable"] is False
    assert cols["bpb._stale"]["kind"] == "bool"
    assert cols["text"]["filterable"] is False
    assert cols["bpb.bpb"]["description"] == "bits per byte"


def ids(client, params):
    r = client.get(f"{D}/browse", params=params)
    assert r.status_code == 200, r.text
    return [row["id"] for row in r.json()["rows"]]


def test_browse_filters(client):
    assert ids(client, [("sort", "id")]) == [101, 102, 103, 104, 105, 106]
    assert ids(client, [("is.bot_created", "true"), ("sort", "id")]) == [103, 105, 106]
    assert ids(client, [("any.topics.topics", "odisha"), ("sort", "id")]) == [101, 102]
    assert ids(client, [("in.topics.primary_topic", "math")]) == [104]
    assert ids(client, [("is.topics.school_relevant", "true"), ("sort", "id")]) == [101, 104, 106]
    assert ids(client, [("null.topics.primary_topic", "1"), ("sort", "id")]) == [103]
    assert ids(
        client, [("in.topics.primary_topic", "history"), ("in.topics.primary_topic", "__null__"), ("sort", "id")]
    ) == [103, 105, 106]
    assert ids(client, [("is.bpb._stale", "true")]) == [104]
    assert ids(client, [("max.odia_ratio", "0.5")]) == [103]
    assert ids(client, [("q", "ପୁରୀ")]) == [102]
    assert ids(client, [("sort", "bpb.review_rank"), ("null.bpb.review_rank", "0")]) == [103, 101]


def test_browse_text_search_with_counts_and_snippets(client):
    r = client.get(f"{D}/browse", params={"text": "ଅକ୍ଷାଂଶ"}).json()
    assert r["total"] == 3 and r["total_matches"] == 3
    assert all(row["snippets"][0]["match"] == "ଅକ୍ଷାଂଶ" for row in r["rows"])
    r = client.get(f"{D}/browse", params={"text": r"\d+\.\d+ ଉତ୍ତର", "mode": "regex"}).json()
    assert r["total"] == 3
    assert r["rows"][0]["snippets"][0]["match"].endswith("ଉତ୍ତର")
    r = client.get(f"{D}/browse", params={"text": "india", "icase": "1"}).json()
    assert [x["id"] for x in r["rows"]] == [103]
    bad = client.get(f"{D}/browse", params={"text": "(", "mode": "regex"})
    assert bad.status_code == 400 and "error" in bad.json()


@pytest.mark.parametrize(
    "params",
    [
        {"min.nope": "1"},
        {'min.words" OR 1=1 --': "1"},
        {"sort": "words; DROP TABLE x"},
        {"cols": "x"},
    ],
)
def test_browse_rejects_injection(client, params):
    r = client.get(f"{D}/browse", params=params)
    assert r.status_code == 400


def test_browse_versions(client):
    r = client.get(f"{D}/browse", params={"v": "corp-20250101", "sort": "id"}).json()
    assert [x["id"] for x in r["rows"]] == [101, 102, 103]
    assert client.get(f"{D}/browse", params={"v": "nope"}).status_code == 404
    assert client.get("/api/d/nope/browse").status_code == 404


def test_article(client):
    a = client.get(f"{D}/article/102").json()
    assert a["title"] == "ପୁରୀ" and a["text_sha1"] == sha1(a["text"])
    assert [p["kind"] for p in a["paragraphs"]] == ["heading", "para", "para", "table"]
    assert "<table>" in a["paragraphs"][3]["html"] and "math inline" in a["paragraphs"][3]["html"]
    assert a["annotations"]["topics"]["primary_topic"] == "religion"
    assert a["ann_stale"] == {"bpb": False}
    pa = a["para_annotations"]["bpb"]
    assert set(pa["rows"]) == {"0", "1", "2", "3"} and pa["stale"] is False
    assert "bpb" in pa["scales"]
    assert a["links"]["revision"] == "https://or.wikipedia.org/w/index.php?oldid=1102"
    assert a["links"]["file_exists"] is True
    assert client.get(f"{D}/article/104").json()["ann_stale"] == {"bpb": True}
    assert client.get(f"{D}/article/424242").status_code == 404


def read_log(client):
    path = client.app.state.reviews.path
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]


def test_review_write_undo_and_file_format(client):
    a = client.get(f"{D}/article/101").json()
    body = {"id": 101, "verdict": "fix", "note": "ଟିପ୍ପଣୀ", "drop_paras": [3, 1], "text_sha1": a["text_sha1"]}
    r = client.post(f"{D}/review", json=body)
    assert r.status_code == 200 and r.json()["saved"] is True
    log = read_log(client)
    assert len(log) == 1
    e = log[0]
    assert list(e) == list(FIELDS)
    assert e["dataset"] == "wiki" and e["id"] == 101 and e["title"] == "କଟକ" and e["verdict"] == "fix"
    assert e["note"] == "ଟିପ୍ପଣୀ" and e["text_sha1"] == sha1(a["text"])
    paras = a["text"].split("\n\n")
    assert e["drop_paragraphs"] == [{"para": 1, "sha1": sha1(paras[1])}, {"para": 3, "sha1": sha1(paras[3])}]
    raw = client.app.state.reviews.path.read_text(encoding="utf-8")
    assert "ଟିପ୍ପଣୀ" in raw  # UTF-8, not \u escapes
    # saving the same decision again appends nothing
    assert client.post(f"{D}/review", json=body).json()["saved"] is False
    assert len(read_log(client)) == 1
    # browse shows the verdict; the article shows the state
    rows = client.get(f"{D}/browse", params={"in.review.verdict": "fix"}).json()["rows"]
    assert [x["id"] for x in rows] == [101] and rows[0]["review.verdict"] == "fix"
    assert client.get(f"{D}/article/101").json()["review"]["event"]["verdict"] == "fix"
    # undo appends an event restoring "no decision"
    u = client.post(f"{D}/undo", json={"id": 101}).json()
    assert u["event"]["verdict"] is None and u["event"]["drop_paragraphs"] == []
    log = read_log(client)
    assert len(log) == 2 and log[1]["verdict"] is None
    assert client.get(f"{D}/browse", params={"in.review.verdict": "fix"}).json()["total"] == 0
    d = client.get(f"{D}/decisions", params={"verdict": "cleared"}).json()
    assert d["counts"]["cleared"] == 1 and d["rows"][0]["id"] == 101


def test_dropped_paragraphs_survive_a_rebuild(client, data_root):
    """The build re-renders from source and re-applies the latest event's drops by sha1, so a
    later decision must keep drops whose paragraph is no longer in the current text."""
    from conftest import corpus_rows, write_parquet

    a = client.get(f"{D}/article/101").json()
    paras = a["text"].split("\n\n")
    client.post(f"{D}/review", json={"id": 101, "verdict": "keep", "drop_paras": [3], "text_sha1": a["text_sha1"]})
    # a rebuild applies the drop: paragraph 3 is gone from the new corpus file
    rows = corpus_rows()
    rows[0]["text"] = "\n\n".join(p for i, p in enumerate(paras) if i != 3)
    tmp = data_root / "wiki" / "tmp.parquet.tmp"
    write_parquet(tmp, rows)
    tmp.replace(data_root / "wiki" / "corp-20260101.parquet")
    b = client.get(f"{D}/article/101").json()
    assert b["review"]["stale"] is True and b["review"]["drops"] == []
    assert b["review"]["missing"] == [{"para": 3, "sha1": sha1(paras[3])}]
    # a new verdict keeps the recorded drop
    r = client.post(f"{D}/review", json={"id": 101, "verdict": "fix", "text_sha1": b["text_sha1"]}).json()
    assert r["event"]["drop_paragraphs"] == [{"para": 3, "sha1": sha1(paras[3])}]
    assert r["event"]["text_sha1"] == b["text_sha1"]
    # unless the reviewer explicitly forgets it
    r = client.post(
        f"{D}/review", json={"id": 101, "verdict": "fix", "text_sha1": b["text_sha1"], "discard_missing": True}
    ).json()
    assert r["event"]["drop_paragraphs"] == []


def test_identical_paragraphs_are_one_entry(client, data_root):
    from conftest import corpus_rows, write_parquet

    rows = corpus_rows()
    rows[3]["text"] += "\n\nଶେଷ ଅନୁଚ୍ଛେଦ ।"  # article 104 now ends with the same paragraph twice
    tmp = data_root / "wiki" / "tmp.parquet.tmp"
    write_parquet(tmp, rows)
    tmp.replace(data_root / "wiki" / "corp-20260101.parquet")
    a = client.get(f"{D}/article/104").json()
    n = len(a["paragraphs"])
    r = client.post(f"{D}/review", json={"id": 104, "drop_paras": [n - 1], "text_sha1": a["text_sha1"]}).json()
    assert len(r["event"]["drop_paragraphs"]) == 1
    assert [d["para"] for d in r["review"]["drops"]] == [n - 2, n - 1]  # both copies show as dropped


def test_review_validation_and_conflict(client):
    assert client.post(f"{D}/review", json={"id": 101, "drop_paras": [0]}).status_code == 400  # the title heading
    assert client.post(f"{D}/review", json={"id": 101, "verdict": "maybe"}).status_code == 400
    assert client.post(f"{D}/review", json={"id": 101, "drop_paras": [99]}).status_code == 400
    assert client.post(f"{D}/review", json={"id": 101, "text_sha1": "0" * 40, "verdict": "keep"}).status_code == 409
    assert client.post(f"{D}/review", json={"id": 424242, "verdict": "keep"}).status_code == 404
    assert client.post(f"{D}/undo", json={"id": 102}).status_code == 409  # nothing to undo
    assert not client.app.state.reviews.path.exists()


def test_queue_position_unreviewed_first(client):
    q = {"sort": "bpb.review_rank", "dir": "asc", "unrev": "1", "null.bpb.review_rank": "0"}
    p = client.get(f"{D}/position", params={**q, "id": "-1"}).json()
    assert p["total"] == 2 and p["reviewed"] == 0 and p["next_unreviewed"] == 103
    a = client.get(f"{D}/article/103").json()
    client.post(f"{D}/review", json={"id": 103, "verdict": "drop", "text_sha1": a["text_sha1"]})
    p = client.get(f"{D}/position", params={**q, "id": "103"}).json()
    assert p["reviewed"] == 1 and p["index"] == 1 and p["next_unreviewed"] == 101 and p["prev"] == 101


def test_patterns_templates_and_search(client):
    r = client.get(f"{D}/patterns/templates", params={"min": "2"}).json()
    groups = {g["norm"]: g for g in r["rows"]}
    town = "⟨title⟩ #.# ଉତ୍ତର ଅକ୍ଷାଂଶ ଓ #.# ପୂର୍ବ ଦ୍ରାଘିମାରେ ଅବସ୍ଥିତ ।"
    assert groups[town]["articles"] == 3  # incl. "ବାଲେଶ୍ୱର (ଜିଲ୍ଲା)" via its base title
    assert groups["# ଗ୍ରେଗୋରି ପାଞ୍ଜି ଅନୁସାରେ ଏକ ସାଧାରଣ ବର୍ଷ ଅଟେ ।"]["articles"] == 2
    key = groups[town]["nkey"]
    g = client.get(f"{D}/patterns/template/{key}").json()
    assert {e["id"] for e in g["examples"]} == {101, 102, 103}
    assert ids(client, [("pat", key), ("sort", "id")]) == [101, 102, 103]
    s = client.get(f"{D}/patterns/search", params={"re": "ଅକ୍ଷାଂଶ|ଦ୍ରାଘିମା"}).json()
    assert s["paragraphs"] == 3 and s["articles"] == 3 and s["matches"] == 6
    assert client.get(f"{D}/patterns/search", params={"re": "("}).status_code == 400


def test_bulk_preview_then_apply(client):
    spec = {"action": "drop_paragraphs", "match": {"type": "regex", "pattern": "ଅକ୍ଷାଂଶ"}, "note": "bulk: town"}
    # an existing decision is kept and merged
    a = client.get(f"{D}/article/101").json()
    client.post(f"{D}/review", json={"id": 101, "verdict": "keep", "note": "fine", "text_sha1": a["text_sha1"]})
    plan = client.post(f"{D}/bulk/preview", json={"spec": spec}).json()
    assert plan["summary"]["articles"] == 3 and plan["summary"]["paragraphs"] == 3
    ev101 = next(e for e in plan["events"] if e["id"] == 101)
    assert ev101["verdict"] == "keep" and ev101["note"] == "fine\nbulk: town"
    assert ev101["drop_paragraphs"] == [{"para": 3, "sha1": sha1(a["text"].split("\n\n")[3])}]
    assert "ts" not in ev101
    # the title heading (paragraph 0) is never dropped, even if a pattern matches it
    heading = client.post(
        f"{D}/bulk/preview",
        json={"spec": {"action": "drop_paragraphs", "match": {"type": "regex", "pattern": "^# କଟକ$"}}},
    ).json()
    assert heading["events"] == [] and heading["matched_articles"] == 1
    # a wrong token (the preview no longer matches) is refused and writes nothing
    before = len(read_log(client))
    assert client.post(f"{D}/bulk/apply", json={"spec": spec, "token": "x"}).status_code == 409
    assert len(read_log(client)) == before
    r = client.post(f"{D}/bulk/apply", json={"spec": spec, "token": plan["token"]}).json()
    assert r["written"] == 3
    log = read_log(client)
    assert len(log) == before + 3 and len({e["ts"] for e in log[-3:]}) == 1
    assert all(list(e) == list(FIELDS) for e in log)
    # applying again changes nothing
    again = client.post(f"{D}/bulk/preview", json={"spec": spec}).json()
    assert again["events"] == [] and again["summary"]["unchanged"] == 3
    # bulk undo by ids
    undo = client.post(
        f"{D}/bulk/preview", json={"spec": {"action": "undo", "match": {"type": "ids", "ids": [101, 102]}}}
    ).json()
    assert {e["id"] for e in undo["events"]} == {101, 102}
    assert next(e for e in undo["events"] if e["id"] == 101)["drop_paragraphs"] == []


def test_bulk_drop_articles_and_bad_specs(client):
    spec = {"action": "drop_articles", "match": {"type": "template", "key": "nope"}}
    assert client.post(f"{D}/bulk/preview", json={"spec": spec}).json()["events"] == []
    bad = [
        {"action": "explode"},
        {"action": "drop_paragraphs", "match": {"type": "ids", "ids": [1]}},
        {"action": "drop_articles", "match": {"type": "regex", "pattern": ""}},
    ]
    for s in bad:
        assert client.post(f"{D}/bulk/preview", json={"spec": s}).status_code == 400


def test_decisions_export(client):
    a = client.get(f"{D}/article/105").json()
    client.post(f"{D}/review", json={"id": 105, "verdict": "drop", "text_sha1": a["text_sha1"]})
    r = client.get(f"{D}/decisions/export", params={"format": "jsonl"})
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    rows = [json.loads(x) for x in r.text.splitlines()]
    assert [x["id"] for x in rows] == [105] and list(rows[0]) == list(FIELDS)
    assert json.loads(client.get(f"{D}/decisions/export", params={"format": "json"}).text)[0]["verdict"] == "drop"
    assert client.get("/api/reviews/log").text.count("\n") == 1


def test_reports_and_files(client):
    r = client.get(f"{D}/reports").json()
    assert [x["name"] for x in r["reports"]] == ["report.md", "review-first.md"]
    assert r["methodology"]["name"] == "METHODOLOGY.md" and r["review_first"] == "review-first.md"
    rep = client.get(f"{D}/report", params={"name": "report.md"}).json()
    assert "<strong>findings</strong>" in rep["html"] and "<script>" not in rep["html"]
    assert client.get(f"{D}/report", params={"name": "../../etc/passwd"}).status_code == 404
    doc = client.get(f"{D}/report", params={"name": "README.md", "group": "docs"}).json()
    assert "<table>" in doc["html"]
    tree = client.get(f"{D}/files").json()["tree"]
    assert {c["name"] for c in tree["children"]} >= {"annotations", "markdown", "quality", "corp-20260101.parquet"}


def test_static_page_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "<main" in r.text
    assert client.get("/static/js/main.js").status_code == 200


def test_host_and_origin_guard(data_root, writer):
    from fastapi.testclient import TestClient

    from edaapp.server import create_app

    app = create_app(
        data_root,
        writer=writer,
        state_dir=writer.root / "state",
        cache_dir=writer.root / "cache",
        allowed_hosts={"127.0.0.1:8765"},
    )
    c = TestClient(app, base_url="http://127.0.0.1:8765")
    assert c.get("/api/health").status_code == 200
    evil = TestClient(app, base_url="http://evil.example")
    assert evil.get("/api/health").status_code == 403
    r = c.post(f"{D}/review", json={"id": 101, "verdict": "keep"}, headers={"origin": "http://evil.example"})
    assert r.status_code == 403
    r = c.post(f"{D}/review", content="id=101", headers={"content-type": "application/x-www-form-urlencoded"})
    assert r.status_code == 415
    assert (
        c.post(
            f"{D}/review", json={"id": 101, "verdict": "keep"}, headers={"origin": "http://127.0.0.1:8765"}
        ).status_code
        == 200
    )
