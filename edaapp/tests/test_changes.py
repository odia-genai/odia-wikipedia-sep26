"""Files appear, disappear and are replaced (atomically) while the app runs."""

import os

from conftest import corpus_rows, sha1, write_parquet


def replace_atomically(path, rows):
    tmp = path.with_name(path.name + ".tmp")
    write_parquet(tmp, rows)
    os.replace(tmp, path)


def test_new_annotation_appears(catalog, data_root):
    snap = catalog.snapshot("wiki")
    assert "school.grade" not in snap.columns
    write_parquet(data_root / "wiki" / "annotations" / "school.parquet", [{"id": 101, "grade": 7}])
    catalog.refresh()
    snap2 = catalog.snapshot("wiki")
    assert snap2 is not snap and "school.grade" in snap2.columns
    got = (
        catalog.cursor()
        .execute(f'SELECT id, "school.grade" FROM {snap2.view} WHERE "school.grade" IS NOT NULL')
        .fetchall()
    )
    assert got == [(101, 7)]


def test_annotation_disappears(catalog, data_root):
    assert "topics.topics" in catalog.snapshot("wiki").columns
    (data_root / "wiki" / "annotations" / "topics.parquet").unlink()
    catalog.refresh()
    snap = catalog.snapshot("wiki")
    assert "topics.topics" not in snap.columns
    assert not any(a.ann.name == "topics" for a in snap.anns)


def test_annotation_replaced_updates_values_and_staleness(catalog, data_root):
    snap = catalog.snapshot("wiki")
    stale = {s["key"]: s["stale"] for s in snap.ann_status}
    assert stale["bpb"] == 1
    rows = corpus_rows()
    fixed = [
        {
            "id": r["id"],
            "bpb": 1.5,
            "review_rank": None,
            "review_reasons": [],
            "tokens": 1,
            "text_sha1": sha1(r["text"]),
        }
        for r in rows
    ]
    replace_atomically(data_root / "wiki" / "annotations" / "bpb.parquet", fixed)
    catalog.refresh()
    snap2 = catalog.snapshot("wiki")
    assert {s["key"]: s["stale"] for s in snap2.ann_status}["bpb"] == 0
    assert catalog.cursor().execute(f'SELECT DISTINCT "bpb.bpb" FROM {snap2.view}').fetchall() == [(1.5,)]


def test_corpus_rebuilt_makes_annotations_stale(catalog, data_root):
    snap = catalog.snapshot("wiki")
    assert {s["key"]: s["stale"] for s in snap.ann_status}["bpb.paragraphs"] == 0
    rows = corpus_rows()
    rows[0]["text"] += "\n\nନୂଆ ଅନୁଚ୍ଛେଦ ।"  # article 101 changes
    replace_atomically(data_root / "wiki" / "corp-20260101.parquet", rows)
    catalog.refresh()
    snap2 = catalog.snapshot("wiki")
    assert snap2.corpus is not snap.corpus
    st = {s["key"]: s for s in snap2.ann_status}
    assert st["bpb.paragraphs"]["stale"] == 1  # scored on the old text of 101
    assert st["bpb"]["stale"] == 2  # 104 as before, plus 101
    assert catalog.cursor().execute(f'SELECT "bpb._stale" FROM {snap2.view} WHERE id = 101').fetchone() == (True,)
    # the old snapshot's tables are gone, the new one answers
    assert catalog.cursor().execute(f"SELECT count(*) FROM {snap2.corpus.table}").fetchone() == (6,)


def test_new_version_and_dataset_appear_and_vanish(catalog, data_root):
    assert [v.stem for v in catalog.dataset("wiki").versions] == ["corp-20260101", "corp-20250101"]
    write_parquet(data_root / "wiki" / "corp-20270101.parquet", corpus_rows()[:2])
    (data_root / "fresh").mkdir()
    write_parquet(data_root / "fresh" / "x.parquet", [{"id": 1, "text": "# a"}])
    catalog.refresh()
    assert catalog.dataset("wiki").versions[0].stem == "corp-20270101"
    assert catalog.snapshot("wiki").corpus.n_rows == 2
    assert "fresh" in catalog.datasets()
    (data_root / "fresh" / "x.parquet").unlink()
    catalog.refresh()
    assert "fresh" not in catalog.datasets()


def test_unreadable_replacement_keeps_previous_copy(catalog, data_root):
    snap = catalog.snapshot("wiki")
    p = data_root / "wiki" / "annotations" / "topics.parquet"
    good = p.read_bytes()
    tmp = p.with_name("topics.tmp")
    tmp.write_bytes(good[: len(good) // 2])  # a truncated file, as if written in place
    os.replace(tmp, p)
    catalog.refresh()
    snap2 = catalog.snapshot("wiki")
    # discovery can't read the schema, so the file is listed as ignored; the app keeps working
    assert "topics.topics" not in snap2.columns
    assert any("topics.parquet" in m for m in catalog.dataset("wiki").ignored)
    tmp.write_bytes(good)
    os.replace(tmp, p)
    catalog.refresh()
    assert "topics.topics" in catalog.snapshot("wiki").columns
    del snap


def test_reviews_change_is_seen_by_the_view(catalog, reviews):
    from edaapp.reviews import make_event

    snap = catalog.snapshot("wiki")
    sha = catalog.cursor().execute(f"SELECT __sha1 FROM {snap.corpus.table} WHERE id = 102").fetchone()[0]
    reviews.append([make_event("wiki", 102, "ପୁରୀ", "drop", "", [], sha)])
    snap2 = catalog.snapshot("wiki")
    got = (
        catalog.cursor().execute(f'SELECT "review.verdict", "review.stale" FROM {snap2.view} WHERE id = 102').fetchone()
    )
    assert got == ("drop", False)


def test_paragraph_staleness_from_para_sha1_rises_then_falls(catalog, data_root):
    """A rebuild makes paragraph scores stale; re-scoring the changed paragraphs clears it."""
    ann = data_root / "wiki" / "annotations" / "ps.paragraphs.parquet"

    def score(rows):
        out = [
            {"id": r["id"], "para": i, "bpb": 0.5, "para_sha1": sha1(p)}
            for r in rows
            for i, p in enumerate(r["text"].split("\n\n"))
        ]
        replace_atomically(ann, out)

    rows = corpus_rows()
    score(rows)
    catalog.refresh()

    def status():
        st = {s["key"]: s for s in catalog.snapshot("wiki").ann_status}["ps.paragraphs"]
        return st["stale"], st.get("stale_paragraphs")

    assert status() == (0, 0)
    # the rebuild drops paragraph 1 of article 101 (everything after it shifts) and edits 102
    paras = rows[0]["text"].split("\n\n")
    rows[0]["text"] = "\n\n".join(p for i, p in enumerate(paras) if i != 1)
    rows[1]["text"] = rows[1]["text"].replace("ତୀର୍ଥ", "ତୀର୍ଥସ୍ଥାନ")
    replace_atomically(data_root / "wiki" / "corp-20260101.parquet", rows)
    catalog.refresh()
    stale, stale_paras = status()
    assert stale == 2 and stale_paras == len(paras) - 1 + 1  # 101: every row from ¶1 on; 102: one row
    # re-scoring the changed paragraphs brings it back down
    score(rows)
    catalog.refresh()
    assert status() == (0, 0)


def test_markdown_only_edits_do_not_count_as_table_changes(catalog, data_root):
    import os

    ds = data_root / "wiki"
    catalog.refresh()
    c0, t0 = catalog.changes, catalog.table_changes
    (ds / "METHODOLOGY.md").write_text("# edited\n", encoding="utf-8")
    os.utime(ds / "METHODOLOGY.md", (1e9, 1e9))
    (ds / "quality" / "new-report.md").write_text("# new\n", encoding="utf-8")
    catalog.refresh()
    assert catalog.changes > c0 and catalog.table_changes == t0
    write_parquet(ds / "annotations" / "extra.parquet", [{"id": 101, "x": 1}])
    catalog.refresh()
    assert catalog.table_changes == t0 + 1
