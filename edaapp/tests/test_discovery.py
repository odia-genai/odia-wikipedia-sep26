import json

from conftest import write_parquet

from edaapp import discovery


def test_datasets_are_found_by_structure(data_root):
    found = discovery.discover(data_root)
    assert sorted(found) == ["small", "wiki"]  # notads has no text column
    assert "notads" in discovery.other_dirs(data_root)


def test_versions_newest_first_and_the_newer_format_read(data_root):
    import os

    wiki = data_root / "wiki"
    os.utime(wiki / "corp-20260101.jsonl", ns=(10**18, 10**18))
    os.utime(wiki / "corp-20260101.parquet", ns=(10**18 + 1, 10**18 + 1))
    ds = discovery.discover(data_root)["wiki"]
    assert [v.stem for v in ds.versions] == ["corp-20260101", "corp-20250101"]
    new, old = ds.versions
    assert new.fmt == "parquet" and sorted(new.files) == ["jsonl", "parquet"]
    assert new.not_read == ["corp-20260101.jsonl"] and "corp-20260101.jsonl is not read" in new.note
    assert old.fmt == "jsonl" and old.note is None
    assert new.build_json is not None and new.build_json.path.name == "corp-20260101-build.json"
    assert old.build_json is None
    assert ds.version(None) is new and ds.version("corp-20250101") is old


def test_annotations_reports_docs(data_root):
    ds = discovery.discover(data_root)["wiki"]
    keys = {a.key: a for a in ds.annotations}
    assert set(keys) == {"topics", "bpb", "bpb.paragraphs"}
    assert keys["bpb"].level == "article" and keys["bpb.paragraphs"].level == "paragraph"
    assert keys["bpb"].meta["depends_on_text"] is True
    # the paragraph file falls back to <name>.json for its sidecar
    assert keys["bpb.paragraphs"].sidecar.path.name == "bpb.json"
    assert keys["topics"].sidecar is None
    assert any("broken.parquet" in m and "no id" in m for m in ds.ignored)
    assert [r.path.name for r in ds.reports] == ["report.md", "review-first.md"]
    assert ds.methodology.path.name == "METHODOLOGY.md" and ds.review_first.path.name == "review-first.md"
    assert discovery.discover(data_root)["small"].methodology is None
    assert [d.path.name for d in ds.docs] == ["README.md", "LEARNINGS.md"]


def test_paragraph_sidecar_preferred(data_root):
    ann = data_root / "wiki" / "annotations"
    (ann / "bpb.paragraphs.json").write_text(json.dumps({"description": "per paragraph"}))
    ds = discovery.discover(data_root)["wiki"]
    p = next(a for a in ds.annotations if a.key == "bpb.paragraphs")
    assert p.sidecar.path.name == "bpb.paragraphs.json" and p.meta["description"] == "per paragraph"


def test_paragraph_annotation_needs_para(data_root):
    write_parquet(data_root / "wiki" / "annotations" / "x.paragraphs.parquet", [{"id": 1, "v": 2.0}])
    ds = discovery.discover(data_root)["wiki"]
    assert "x.paragraphs" not in {a.key for a in ds.annotations}
    assert any("x.paragraphs.parquet" in m and "para" in m for m in ds.ignored)


def test_signature_changes_with_files(data_root):
    d = data_root / "wiki"
    s1 = discovery.dir_signature(d)
    assert discovery.dir_signature(d) == s1
    write_parquet(d / "annotations" / "new.parquet", [{"id": 101, "x": 1}])
    s2 = discovery.dir_signature(d)
    assert s2 != s1
    (d / "annotations" / "new.parquet").unlink()
    assert discovery.dir_signature(d) == s1


def test_file_tree_summarises_big_dirs(data_root, monkeypatch):
    monkeypatch.setattr(discovery, "BIG_DIR", 3)
    tree = discovery.file_tree(data_root / "wiki")
    by_name = {c["name"]: c for c in tree["children"]}
    md = by_name["markdown"]
    assert md["summarised"] and md["files"] == 6 and md["size"] > 0 and "children" not in md
    assert not by_name["quality"].get("summarised")
    assert tree["files"] >= 6 + 5
