"""Sidecar joins: an annotation's columns kept once in another file (a score store keyed by
para_sha1, gzipped JSON lines) are joined in when it loads, and shown everywhere as its own."""

import gzip
import json
import os

import pytest
from conftest import build_jsonl_root, sha1, write_jsonl

from edaapp import discovery

J = "/api/d/jwiki"
STORE = "raw/bpb/scores.jsonl.gz"
JOINED = ["score_run", "bytes", "tokens", "bits", "pieces"]
UNSCORED = (101, 1)  # this paragraph's text has no row in the store: bpb null


def store_rows(rows):
    """One store row per distinct non-title paragraph text, except UNSCORED's."""
    out, seen = [], set()
    for r in rows:
        for i, p in enumerate(r["text"].split("\n\n")):
            h = sha1(p)
            if i == 0 or (r["id"], i) == UNSCORED or h in seen:
                continue
            seen.add(h)
            n = len(p.encode()) or 1  # (article 106 has an empty paragraph; the real corpus has none)
            out.append(
                {
                    "para_sha1": h,
                    "score_run": 1 + (n % 2),
                    "bytes": n,
                    "tokens": n // 5 + 1,
                    "pieces": 1,
                    "bits": n * 0.5 + 0.125,
                }
            )
    return out


def write_store(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    data = "".join(json.dumps(r) + "\n" for r in rows).encode()
    path.write_bytes(gzip.compress(data, mtime=0))


def build_joined_root(root):
    """The JSON-lines fixture, with bpb.paragraphs.jsonl holding only bpb (and para_sha1) and its sidecar
    joining the store's columns, as score_bpb.py writes them; article 104 has no score at all (bpb null)."""
    build_jsonl_root(root)
    ds = root / "jwiki"
    rows = [json.loads(x) for x in (ds / "jw-20260901.jsonl").read_text(encoding="utf-8").splitlines()]
    store = store_rows(rows)
    write_store(ds / STORE, store)
    by = {s["para_sha1"]: s for s in store}
    paras = []
    for r in rows:
        for i, p in enumerate(r["text"].split("\n\n")):
            if i:
                s = by.get(sha1(p)) if (r["id"], i) != UNSCORED else None
                paras.append(
                    {"id": r["id"], "para": i, "para_sha1": sha1(p), "bpb": s["bits"] / s["bytes"] if s else None}
                )
    write_jsonl(ds / "annotations" / "bpb.paragraphs.jsonl", paras)
    side = {
        "description": "bits per byte per paragraph",
        "columns": {"bpb": "bits per byte; null = not scored yet"},
        "depends_on_text": True,
        "joins": [
            {
                "path": STORE,
                "on": "para_sha1",
                "description": "the score store: one row per paragraph text",
                "columns": {c: f"the store's {c}" for c in JOINED},
            }
        ],
    }
    (ds / "annotations" / "bpb.paragraphs.json").write_text(json.dumps(side))
    art = [json.loads(x) for x in (ds / "annotations" / "bpb.jsonl").read_text().splitlines()]
    for a in art:
        if a["id"] == 104:
            a["bpb"] = None  # an article with nothing scored
    write_jsonl(ds / "annotations" / "bpb.jsonl", art)
    return root, store


@pytest.fixture
def joined(tmp_path, writer):
    from fastapi.testclient import TestClient

    from edaapp.server import create_app

    root, store = build_joined_root(tmp_path / "jdata")
    app = create_app(root, writer=writer, state_dir=writer.root / "state", cache_dir=writer.root / "cache")
    app.state.catalog.throttle = 0
    with TestClient(app) as c:
        yield c, root / "jwiki", {s["para_sha1"]: s for s in store}


def para_ann(client, id):
    return client.get(f"{J}/article/{id}").json()["para_annotations"]["bpb"]


def bump(path):
    """A new mtime, so change detection sees a rewrite that kept the size."""
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))


# ---- discovery --------------------------------------------------------------------------------


def test_discovery_parses_joins_and_watches_the_joined_file(tmp_path):
    root, _ = build_joined_root(tmp_path / "d")
    ds = discovery.discover(root)["jwiki"]
    ann = next(a for a in ds.annotations if a.key == "bpb.paragraphs")
    (j,) = ann.joins
    assert (j.path, j.on, list(j.columns), j.problem) == (STORE, "para_sha1", JOINED, None)
    assert j.file is not None and j.file.path == (root / "jwiki" / STORE).resolve()
    sig = discovery.dir_signature(root / "jwiki")
    assert any(e[0] == "join" and e[1] == STORE and e[2] > 0 for e in sig)
    (root / "jwiki" / STORE).unlink()  # a missing joined file is part of the signature too
    assert any(e[:3] == ("join", STORE, -1) for e in discovery.dir_signature(root / "jwiki"))


# ---- the joined columns everywhere --------------------------------------------------------------


def test_article_paragraphs_show_the_store_columns(joined):
    client, ds, store = joined
    pa = para_ann(client, 102)
    cols = {c["name"]: c for c in pa["columns"]}
    assert all(cols[c]["from"] == STORE and cols[c]["numeric"] for c in JOINED)
    assert "from" not in cols["bpb"] and "the store's bits" in cols["bits"]["description"]
    assert pa["primary"] == "bpb" and pa["unscored"] == 0
    for row in pa["rows"].values():
        s = store[row["para_sha1"]]
        assert {c: row[c] for c in JOINED} == {c: s[c] for c in JOINED}  # exact, floats included
        assert row["bpb"] == s["bits"] / s["bytes"]


def test_null_bpb_is_not_scored_not_zero(joined):
    client, _, _ = joined
    pa = para_ann(client, 101)
    row = pa["rows"][str(UNSCORED[1])]
    assert row["bpb"] is None and all(row[c] is None for c in JOINED)
    assert pa["unscored"] == 1 and not row["_stale"]
    # the other paragraphs of the article are scored
    assert all(r["bpb"] is not None for k, r in pa["rows"].items() if k != str(UNSCORED[1]))
    # shading scales ignore nulls
    q = pa["scales"]["bpb"]["q"]
    assert all(isinstance(x, float) for x in q) and q[0] <= q[4]
    # an article-level primary column that is null is named, for "not scored"
    art = client.get(f"{J}/article/104").json()
    assert art["annotations"]["bpb"]["bpb"] is None and art["ann_unscored"] == ["bpb"]
    assert client.get(f"{J}/article/102").json()["ann_unscored"] == []


def test_null_bpb_sorts_last_both_ways(joined):
    client, _, _ = joined
    for d in ("asc", "desc"):
        rows = client.get(f"{J}/browse?sort=bpb.bpb&dir={d}&cols=bpb.bpb").json()["rows"]
        vals = [r["bpb.bpb"] for r in rows]
        assert vals[-1] is None and None not in vals[:-1]
        assert vals[:-1] == sorted(vals[:-1], reverse=d == "desc")


def test_overview_reports_the_join(joined):
    client, _, store = joined
    o = client.get(f"{J}/overview").json()
    st = next(a for a in o["annotations"] if a["key"] == "bpb.paragraphs")
    (j,) = st["joins"]
    total = st["rows"]
    assert j["columns"] == JOINED and j["rows"] == len(store) and j["problems"] == []
    assert (j["matched"], j["unmatched"]) == (total - 1, 1)  # the unscored paragraph
    assert {c["name"] for c in st["columns"] if c.get("from") == STORE} == set(JOINED)
    assert st["stale"] == 0 and not st["warnings"]
    assert not [w for w in o["warnings"] if w["annotation"] == "bpb.paragraphs"]
    dists = {d["key"]: d for d in o["para_distributions"]}
    assert dists["bpb.paragraphs.bits"]["nulls"] == 1 and dists["bpb.paragraphs.bpb"]["nulls"] == 1
    assert dists["bpb.paragraphs.bits"]["count"] == total - 1 and dists["bpb.paragraphs.bits"]["from"] == STORE


def test_store_rewrite_is_picked_up(joined):
    client, ds, store = joined
    before = para_ann(client, 102)["rows"]["1"]
    rows = [dict(s, bits=s["bits"] + 1) for s in store.values()]
    write_store(ds / STORE, rows)
    bump(ds / STORE)
    after = para_ann(client, 102)["rows"]["1"]
    assert after["bits"] == before["bits"] + 1 and after["bpb"] == before["bpb"]  # bpb is the file's own


def test_missing_store_is_a_warning_not_a_crash(joined):
    client, ds, store = joined
    (ds / STORE).unlink()
    o = client.get(f"{J}/overview").json()
    st = next(a for a in o["annotations"] if a["key"] == "bpb.paragraphs")
    assert any("not found" in w and STORE in w for w in st["warnings"])
    assert any(w["annotation"] == "bpb.paragraphs" and "not found" in w["text"] for w in o["warnings"])
    assert {c["name"] for c in st["columns"]} == {"para_sha1", "bpb"}
    pa = para_ann(client, 101)  # the article view still works, without the store's columns
    assert {c["name"] for c in pa["columns"]} == {"para_sha1", "bpb"} and pa["unscored"] == 1
    assert "bpb.paragraphs.bits" not in {d["key"] for d in o["para_distributions"]}
    home = {d["name"]: d for d in client.get("/api/datasets").json()["datasets"]}
    assert next(a for a in home["jwiki"]["annotations"] if a["key"] == "bpb.paragraphs")["warnings"] >= 1
    # the store comes back: so do its columns
    write_store(ds / STORE, list(store.values()))
    assert {c["name"] for c in para_ann(client, 101)["columns"]} >= set(JOINED)


def test_unreadable_store_keeps_the_last_good_copy(joined):
    client, ds, store = joined
    para_ann(client, 102)  # loaded
    (ds / STORE).write_bytes(b"\x1f\x8b not gzip")
    pa = para_ann(client, 102)
    assert pa["rows"]["1"]["bits"] == store[pa["rows"]["1"]["para_sha1"]]["bits"]
    o = client.get(f"{J}/overview").json()
    assert any(STORE in w["text"] for w in o["warnings"])


# ---- generic: any annotation, any key; problems are warnings -------------------------------------


def test_article_level_join_on_id(joined):
    client, ds, _ = joined
    write_jsonl(ds / "raw" / "views.jsonl", [{"id": i, "views": i * 10, "extra": "x"} for i in (101, 102, 103)])
    (ds / "annotations" / "topics.json").write_text(
        json.dumps(
            {
                "description": "topics",
                "joins": [{"path": "raw/views.jsonl", "on": "id", "columns": {"views": "page views"}}],
            }
        )
    )
    schema = {c["key"]: c for c in client.get(f"{J}/schema").json()["columns"]}
    assert schema["topics.views"]["kind"] == "numeric" and "page views" in schema["topics.views"]["description"]
    assert "topics.extra" not in schema  # only the declared columns
    # topics.jsonl has rows for 101, 102 and 104: 104 has no views row (null), 103 no topics row
    rows = client.get(f"{J}/browse?min.topics.views=1015&cols=topics.views&sort=id").json()["rows"]
    assert [(r["id"], r["topics.views"]) for r in rows] == [(102, 1020)]
    assert client.get(f"{J}/article/101").json()["annotations"]["topics"]["views"] == 1010
    assert client.get(f"{J}/article/104").json()["annotations"]["topics"]["views"] is None
    st = next(a for a in client.get(f"{J}/overview").json()["annotations"] if a["key"] == "topics")
    assert (st["joins"][0]["matched"], st["joins"][0]["unmatched"]) == (2, 1)
    assert not [w for w in st["warnings"] if "views" in w]  # (3 articles have no topics row: the fixture's)


def test_join_problems_are_warnings(joined):
    client, ds, _ = joined
    write_jsonl(
        ds / "raw" / "dup.jsonl", [{"id": 101, "views": 1}, {"id": 101, "views": 2}, {"id": 102, "primary_topic": "x"}]
    )
    (ds / "annotations" / "topics.json").write_text(
        json.dumps(
            {
                "joins": [
                    {"path": "raw/dup.jsonl", "on": "id", "columns": ["views", "primary_topic", "nope"]},
                    {"path": "../../outside.jsonl", "on": "id", "columns": ["x"]},
                    {"path": "raw/dup.jsonl", "on": "no_such_key", "columns": ["views"]},
                    {"path": "raw/views.csv", "on": "id", "columns": ["x"]},
                    "not a join",
                ]
            }
        )
    )
    o = client.get(f"{J}/overview").json()
    st = next(a for a in o["annotations"] if a["key"] == "topics")
    w = " | ".join(st["warnings"])
    assert "1 repeated id values; the first row of each is used" in w
    assert "primary_topic is in topics.jsonl itself" in w and "has no nope column" in w
    assert "outside the dataset folder" in w and "no no_such_key column" in w
    assert "only .jsonl, .jsonl.gz, .parquet files can be joined" in w and "needs a path and an on column" in w
    art = client.get(f"{J}/article/101").json()["annotations"]["topics"]
    assert art["views"] == 1 and art["primary_topic"] == "geography"  # first row; the file's own column wins
