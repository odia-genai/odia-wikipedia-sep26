"""The repository mode, edaapp's default: the dataset's own repository (the folder above edaapp/)
served as the dataset "odia-wikipedia", whatever the clone is called, with decisions appended to
its reviews/reviews.jsonl. The fake repository here has the app's folder inside it, as the real one
has edaapp/, and discovery.APP_DIR points at it."""

import gzip
import json
import os
import re

import pytest
from conftest import build_jsonl_root, write_jsonl

from edaapp import cli, discovery, roots
from edaapp.paths import REPO_DATASET, REPO_ROOT, SafeWriter, UnsafePathError

NAME = "odia-wikipedia"
EVENT = {
    "ts": "2026-09-24T14:10:51.528Z",
    "dataset": NAME,
    "id": 101,
    "title": "କଟକ",
    "verdict": "keep",
    "note": "",
    "drop_paragraphs": [],
    "text_sha1": "0" * 40,
}


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A clone named odia-wikipedia-sep26: the corpus gzipped at the top level (as committed), the
    annotations, reviews/reviews.jsonl with one event, a .git folder, and app/ standing for
    edaapp/, with a table that has id and text at its top level and an annotations/ folder."""
    build_jsonl_root(tmp_path)
    r = tmp_path / "odia-wikipedia-sep26"
    (tmp_path / "jwiki").rename(r)
    corpus = r / "jw-20260901.jsonl"
    (r / "jw-20260901.jsonl.gz").write_bytes(gzip.compress(corpus.read_bytes(), mtime=0))
    corpus.unlink()
    (r / "reviews").mkdir()
    write_jsonl(r / "reviews" / "reviews.jsonl", [EVENT])
    (r / ".git").mkdir()
    (r / ".git" / "config").write_text("not a repository\n")
    (r / ".gitignore").write_text("/jw-*.jsonl\n")
    app = r / "app"
    (app / "annotations").mkdir(parents=True)
    write_jsonl(app / "x-20270101.jsonl", [{"id": 1, "text": "# not data"}])
    write_jsonl(app / "annotations" / "y.jsonl", [{"id": 101, "y": 1}])
    (app / ".cache").mkdir()
    monkeypatch.setattr(discovery, "APP_DIR", app)
    return r


def writer_for(repo):
    """The app's writer in the fake repository: app/ and reviews/ only."""
    return SafeWriter(repo / "app", repo / "reviews")


def build(repo):
    return roots.repo_root(repo=repo, root=repo / "app" / ".cache" / "repo-root", writer=writer_for(repo))


def test_the_dataset_is_named_odia_wikipedia_not_after_the_folder(repo):
    root, source = build(repo)
    assert os.listdir(root) == [NAME] and (root / NAME).resolve() == repo.resolve()
    assert source["kind"] == "repository" and source["name"] == NAME and source["path"] == str(repo.resolve())
    assert "this repository" in roots.describe_repo(source) and NAME in roots.describe_repo(source)
    ds = discovery.discover(root)
    assert list(ds) == [NAME]
    (v,) = ds[NAME].versions
    assert v.fmt == "jsonl.gz" and v.build_json.path.name == "jw-20260901-build.json"
    # nothing under app/ (edaapp/) is taken for a table or an annotation
    assert {a.key for a in ds[NAME].annotations} == {"bpb", "bpb.paragraphs", "topics"}
    assert ds[NAME].ignored == []


def test_the_name_matches_the_build():
    prepare = REPO_ROOT / "prepare.py"
    if not prepare.exists():
        pytest.skip("not inside the dataset's repository")
    m = re.search(r'^DATASET = "([^"]+)"', prepare.read_text(encoding="utf-8"), re.M)
    assert m and m.group(1) == REPO_DATASET == NAME


def test_the_app_folder_is_never_a_dataset(repo, monkeypatch):
    # the repository itself as a data root (--data): app/ has a table with id and text at its top level
    assert discovery.discover(repo) == {}
    assert "app" not in discovery.other_dirs(repo) and ".git" not in discovery.other_dirs(repo)
    # ... which would be a dataset if it were not the app's folder
    monkeypatch.setattr(discovery, "APP_DIR", repo / "elsewhere")
    assert list(discovery.discover(repo)) == ["app"]


def test_files_view_leaves_out_the_app_and_dot_folders(repo):
    root, _ = build(repo)
    tree = discovery.file_tree(root / NAME)
    names = {c["name"] for c in tree["children"]}
    assert "app" not in names and ".git" not in names
    assert {".gitignore", "reviews", "annotations", "jw-20260901.jsonl.gz", "excluded.jsonl"} <= names
    assert tree["files"] == sum(1 for p in repo.rglob("*") if p.is_file() and not {".git", "app"} & set(p.parts))


def test_decisions_go_to_the_repositorys_review_log(repo):
    from fastapi.testclient import TestClient

    from edaapp.server import create_app

    root, source = build(repo)
    w = writer_for(repo)
    app = create_app(root, writer=w, state_dir=repo / "reviews", cache_dir=repo / "app" / ".cache", sources=[source])
    app.state.catalog.throttle = 0
    log = repo / "reviews" / "reviews.jsonl"
    with TestClient(app) as c:
        assert app.state.reviews.path == log.resolve()
        home = c.get("/api/datasets").json()
        assert home["sources"][0]["kind"] == "repository" and home["reviews_file"]["path"] == str(log.resolve())
        d = f"/api/d/{NAME}"
        assert c.get(f"{d}/decisions").json()["counts"]["keep"] == 1  # the event already in the log
        changes = c.get("/api/changes").json()
        a = c.get(f"{d}/article/102").json()
        r = c.post(f"{d}/review", json={"id": 102, "verdict": "drop", "text_sha1": a["text_sha1"]})
        assert r.status_code == 200
        lines = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()]
        assert [(e["dataset"], e["id"], e["verdict"]) for e in lines] == [(NAME, 101, "keep"), (NAME, 102, "drop")]
        # the log is in the dataset's folder, but appending to it is not a change of the data
        after = c.get("/api/changes").json()
        assert after["data"] == changes["data"] and after["reviews"] != changes["reviews"]
    # the app writes nowhere else in the repository
    for bad in (repo / "annotations" / "x.jsonl", repo / "prepare.py", repo / "reviews" / ".." / "x"):
        with pytest.raises(UnsafePathError):
            w.append_lines(bad, ["{}"])


def test_cli_default_is_this_repository(repo, monkeypatch, no_server, capsys, tmp_path):
    from edaapp import hub, paths

    def no_hub(*a, **kw):
        raise AssertionError("the Hub is not asked by default")

    monkeypatch.setattr(hub, "build_root", no_hub)
    monkeypatch.setattr(paths, "REPO_ROOT", repo)
    monkeypatch.setattr(roots, "REPO_LINK_ROOT", tmp_path / "repo-root")
    cli.main(["--port", "8797", "--state", str(tmp_path / "state")])
    out = capsys.readouterr().out
    assert f"edaapp: {NAME}: this repository, {repo.resolve()}" in out
    assert f"data root {(tmp_path / 'repo-root').resolve()} (1 dataset: {NAME})" in out
    app = no_server["app"]
    assert sorted(app.state.catalog.datasets()) == [NAME] and app.state.svc.sources[0]["kind"] == "repository"
    assert app.state.reviews.path == (tmp_path / "state" / "reviews.jsonl").resolve()


def test_cli_default_state_is_the_log_the_build_reads(repo, monkeypatch, no_server, tmp_path):
    """Without --state, decisions go to the real repository's reviews/reviews.jsonl (nothing is
    written here: the session guard checks that file)."""
    from edaapp import paths

    monkeypatch.setattr(paths, "REPO_ROOT", repo)
    monkeypatch.setattr(roots, "REPO_LINK_ROOT", tmp_path / "repo-root")
    cli.main([])
    assert no_server["app"].state.reviews.path == (paths.REVIEWS_DIR / "reviews.jsonl").resolve()


def test_git_commit_of_the_real_repository():
    if not (REPO_ROOT / ".git").exists():
        pytest.skip("not inside a git checkout")
    assert re.fullmatch(r"[0-9a-f]{40}", roots.git_commit(REPO_ROOT) or "")


def test_the_real_repository_in_repository_mode(tmp_path):
    """The real clone (read only): a dataset named odia-wikipedia, without building (the corpus is
    the committed .jsonl.gz), and edaapp/ and .git/ out of its Files view."""
    if not (REPO_ROOT / "prepare.py").exists():
        pytest.skip("not inside the dataset's repository")
    root, source = roots.repo_root(root=tmp_path / "repo-root", writer=SafeWriter(tmp_path))
    ds = discovery.discover(root)[NAME]
    v = ds.version(None)
    assert "jsonl.gz" in v.files and v.stem.startswith("orwiki-")
    assert {"bpb", "bpb.paragraphs", "topics", "translation"} <= {a.key for a in ds.annotations}
    names = {c["name"] for c in discovery.file_tree(root / NAME)["children"]}
    assert "edaapp" not in names and ".git" not in names and {"reviews", "annotations"} <= names
