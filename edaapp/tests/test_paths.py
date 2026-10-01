import os
import re
from pathlib import Path

import pytest

from edaapp.paths import APP_ROOT, SafeWriter, UnsafePathError, inside


def test_guard_accepts_paths_inside(tmp_path):
    w = SafeWriter(tmp_path)
    assert w.guard("state/reviews.jsonl") == (tmp_path / "state" / "reviews.jsonl").resolve()
    assert w.guard(tmp_path / "a" / ".." / "b") == (tmp_path / "b").resolve()
    assert w.guard(tmp_path) == tmp_path.resolve()


@pytest.mark.parametrize("bad", ["../x", "../../etc/passwd", "/etc/passwd", "state/../../x"])
def test_guard_rejects_paths_outside(tmp_path, bad):
    root = tmp_path / "root"
    root.mkdir()
    with pytest.raises(UnsafePathError):
        SafeWriter(root).guard(bad)


def test_guard_rejects_sibling_with_common_prefix(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app-evil").mkdir()
    with pytest.raises(UnsafePathError):
        inside(tmp_path / "app-evil" / "x", tmp_path / "app")


def test_guard_follows_symlinks(tmp_path):
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "link").symlink_to(outside)
    (root / "file.jsonl").symlink_to(outside / "target.jsonl")
    w = SafeWriter(root)
    with pytest.raises(UnsafePathError):
        w.guard("link/x.txt")
    with pytest.raises(UnsafePathError):
        w.append_lines("file.jsonl", ["{}"])
    assert not (outside / "target.jsonl").exists()


def test_symlinks_inside_may_point_out_but_not_be_written_through(tmp_path):
    root, outside = tmp_path / "root", tmp_path / "outside"
    root.mkdir()
    (outside / "a").mkdir(parents=True)
    (outside / "b").mkdir()
    w = SafeWriter(root)
    link = w.symlink("links/ds", outside / "a")
    assert link == root.resolve() / "links" / "ds" and link.is_symlink() and link.resolve() == (outside / "a").resolve()
    w.symlink(root / "links" / "ds", outside / "b")  # replaced atomically
    assert link.resolve() == (outside / "b").resolve() and os.listdir(root / "links") == ["ds"]
    with pytest.raises(UnsafePathError):
        w.append_lines("links/ds/x.jsonl", ["{}"])  # the guard resolves the link
    assert os.listdir(outside / "b") == []
    with pytest.raises(UnsafePathError):
        w.symlink("../escape", outside / "a")  # the link itself must be inside
    with pytest.raises(UnsafePathError):
        w.symlink("links/..", outside / "a")
    (root / "real").mkdir()
    with pytest.raises(UnsafePathError):
        w.symlink("real", outside / "a")  # never replaces what is not a link
    with pytest.raises(UnsafePathError):
        w.remove_link("real")
    w.remove_link("links/ds")
    assert not link.exists() and not link.is_symlink() and (outside / "b").is_dir()


def test_writes_land_inside_and_are_whole_lines(tmp_path):
    w = SafeWriter(tmp_path)
    p = w.append_lines("state/log.jsonl", ["a", "b"])
    w.append_lines("state/log.jsonl", ["c"])
    assert p.read_text() == "a\nb\nc\n"
    q = w.write_atomic("cache/x.bin", b"123")
    assert q.read_bytes() == b"123"
    assert not [f for f in os.listdir(q.parent) if f.endswith(".tmp")]
    w.unlink("cache/x.bin")
    assert not q.exists()


def test_app_writer_writes_in_edaapp_and_the_repositorys_reviews_only():
    from edaapp.paths import REPO_ROOT, REVIEWS_DIR

    w = SafeWriter()
    assert w.roots == (APP_ROOT, REVIEWS_DIR.resolve()) and w.root == APP_ROOT
    assert APP_ROOT.name == "edaapp" and REPO_ROOT == APP_ROOT.parent
    assert w.guard(REVIEWS_DIR / "reviews.jsonl") == (REVIEWS_DIR / "reviews.jsonl").resolve()  # only checked
    assert w.guard(".cache/x") == APP_ROOT / ".cache" / "x"  # relative: from edaapp/
    for bad in [
        REPO_ROOT / "LEARNINGS.md",
        REPO_ROOT / "annotations" / "bpb.jsonl",
        REPO_ROOT / "orwiki-20260901-trainingready.jsonl.gz",
        REVIEWS_DIR / ".." / "prepare.py",
        APP_ROOT.parent.parent / "x",
    ]:
        with pytest.raises(UnsafePathError):
            w.guard(bad)


def test_a_writer_with_two_roots(tmp_path):
    a, b, out = tmp_path / "a", tmp_path / "b", tmp_path / "out"
    for d in (a, b, out):
        d.mkdir()
    w = SafeWriter(a, b)
    assert w.append_lines("x.jsonl", ["1"]) == a.resolve() / "x.jsonl"  # relative paths: the first root
    assert w.append_lines(b / "log.jsonl", ["2"]).read_text() == "2\n"
    (a / "to-b").symlink_to(b)
    (a / "to-out").symlink_to(out)
    w.append_lines(a / "to-b" / "via.jsonl", ["3"])  # lands in a root: fine
    with pytest.raises(UnsafePathError):
        w.append_lines(a / "to-out" / "x.jsonl", ["4"])
    with pytest.raises(UnsafePathError):
        w.guard(b / ".." / "out" / "x")
    assert os.listdir(out) == []


# Everything that can create, change or remove a file or a link. Only paths.py may use these.
WRITE_PRIMITIVES = re.compile(
    r"open\([^)]*['\"][wax+]b?['\"]|\.write_text\(|\.write_bytes\(|os\.replace\(|os\.rename\(|os\.remove\(|"
    r"\.unlink\(|shutil\.|\.mkdir\(|os\.makedirs\(|os\.open\(|\bCOPY\b|\bEXPORT DATABASE\b|\.touch\(|rmtree|"
    r"os\.symlink\(|\.symlink_to\(|os\.link\(|\.hardlink_to\("
)


def test_only_paths_module_writes_files():
    src = Path(__file__).resolve().parents[1] / "src" / "edaapp"
    offenders = []
    for f in sorted(src.glob("*.py")):
        if f.name == "paths.py":
            continue
        for n, line in enumerate(f.read_text().splitlines(), 1):
            code = line.split("#", 1)[0]
            if WRITE_PRIMITIVES.search(code) and "writer." not in code:
                offenders.append(f"{f.name}:{n}: {line.strip()}")
    assert offenders == []
