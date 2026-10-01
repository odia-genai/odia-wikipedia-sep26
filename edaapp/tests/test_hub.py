"""The Hugging Face source: hub.build_root links each dataset's snapshot into a data root, with
snapshot_download and dataset_info mocked (no network). The fake snapshots are laid out like the
Hugging Face cache: real folders, and every file a symlink into the repository's blobs/."""

import gzip
import hashlib
import os

import httpx2
import pytest
from huggingface_hub.errors import LocalEntryNotFoundError, RevisionNotFoundError
from test_joins import STORE, build_joined_root

from edaapp import cli, discovery, hub
from edaapp.paths import SafeWriter

REPO = "fastpixels/odia-wikipedia-sep26"
NAME = "odia-wikipedia"
OLD, NEW = "a" * 40, "b" * 40


def hf_snapshot(src, cache, commit):
    """`src` (a dataset folder) as the snapshot `commit` of REPO in a Hugging Face cache."""
    repo = cache / f"datasets--{REPO.replace('/', '--')}"
    snap = repo / "snapshots" / commit
    for f in sorted(src.rglob("*")):
        if f.is_file():
            data = f.read_bytes()
            blob = repo / "blobs" / hashlib.sha256(data).hexdigest()
            blob.parent.mkdir(parents=True, exist_ok=True)
            blob.write_bytes(data)
            link = snap / f.relative_to(src)
            link.parent.mkdir(parents=True, exist_ok=True)
            link.symlink_to(os.path.relpath(blob, link.parent))
    return snap


@pytest.fixture
def cache(tmp_path):
    """A Hugging Face cache holding two snapshots of the dataset: the JSON-lines fixture with its
    corpus gzipped (as published) and a sidecar join to raw/bpb/scores.jsonl.gz, and a newer one."""
    root, _ = build_joined_root(tmp_path / "src")
    ds = root / "jwiki"
    corpus = ds / "jw-20260901.jsonl"
    (ds / "jw-20260901.jsonl.gz").write_bytes(gzip.compress(corpus.read_bytes(), mtime=0))
    corpus.unlink()
    c = tmp_path / "hf"
    hf_snapshot(ds, c, OLD)
    (ds / "README.md").write_text("# Newer\n", encoding="utf-8")
    hf_snapshot(ds, c, NEW)
    return c


class FakeHub:
    """Stands in for huggingface_hub: `online` False makes every network call fail like a dropped
    connection; `commit` is what the Hub's revision points at; `cached` what the cache's refs say."""

    def __init__(self, cache):
        self.cache = cache
        self.online = True
        self.commit = OLD
        self.cached = OLD
        self.calls = []

    def snap(self, commit):
        return self.cache / f"datasets--{REPO.replace('/', '--')}" / "snapshots" / commit

    def dataset_info(self, repo_id, *, revision=None, timeout=None):
        self.calls.append(("info", repo_id, revision))
        if not self.online:
            raise httpx2.ConnectError("[Errno 8] nodename nor servname provided, or not known")
        if revision not in ("main", OLD, NEW):
            raise RevisionNotFoundError(
                f"404: {revision}", response=httpx2.Response(404, request=httpx2.Request("GET", "x"))
            )

    def snapshot_download(self, repo_id, **kw):
        self.calls.append(("download", repo_id, kw))
        if kw.get("local_files_only"):
            if self.cached is None:
                raise LocalEntryNotFoundError("Cannot find an appropriate cached snapshot folder")
            return str(self.snap(self.cached))
        if not self.online:
            raise httpx2.ConnectError("connection lost")
        self.cached = self.commit
        return str(self.snap(self.commit))


@pytest.fixture
def fake(cache, monkeypatch):
    f = FakeHub(cache)
    monkeypatch.setattr(hub, "dataset_info", f.dataset_info)
    monkeypatch.setattr(hub, "snapshot_download", f.snapshot_download)
    return f


def build(writer, revision="main"):
    return hub.build_root(revision, root=writer.root / "cache" / "hub-root", writer=writer)


def test_the_map_names_the_published_dataset():
    assert hub.HUB_DATASETS == {NAME: REPO}
    assert hub.HUB_ROOT.parts[-2:] == (".cache", "hub-root")


def test_build_root_links_the_snapshot_under_the_dataset_name(fake, writer):
    root, (s,) = build(writer)
    link = root / NAME
    assert link.is_symlink() and link.resolve() == fake.snap(OLD).resolve()
    assert (s.name, s.repo_id, s.revision, s.commit, s.offline) == (NAME, REPO, "main", OLD, None)
    assert f"{REPO} at main (commit aaaaaaa)" in s.describe() and "could not be reached" not in s.describe()
    # downloaded as a dataset, at the revision asked for, without raw/html (nothing in the app reads it)
    kind, repo, kw = fake.calls[-1]
    assert (kind, repo) == ("download", REPO)
    assert kw["repo_type"] == "dataset" and kw["revision"] == "main" and kw["ignore_patterns"] == ["raw/html/*"]
    assert not kw.get("local_files_only")
    assert os.listdir(root) == [NAME]


def test_the_linked_snapshot_is_a_dataset(fake, writer):
    root, _ = build(writer)
    ds = discovery.discover(root)[NAME]  # the folder name, not the repository's
    (v,) = ds.versions
    assert v.fmt == "jsonl.gz" and v.build_json.path.name == "jw-20260901-build.json"
    assert {a.key for a in ds.annotations} == {"bpb", "bpb.paragraphs", "topics"} and ds.ignored == []
    # the join's file is a symlink into blobs/, outside the snapshot folder: still inside the dataset
    (j,) = next(a for a in ds.annotations if a.key == "bpb.paragraphs").joins
    assert j.problem is None and j.file.path == root / NAME / STORE and j.file.size > 0
    assert any(e[:2] == ("join", STORE) and e[2] > 0 for e in discovery.dir_signature(root / NAME))
    assert ds.excluded is not None and ds.methodology is not None


def test_the_app_over_the_hub_root(fake, writer):
    from fastapi.testclient import TestClient

    from edaapp.server import create_app

    root, sources = build(writer)
    app = create_app(
        root,
        writer=writer,
        state_dir=writer.root / "state",
        cache_dir=writer.root / "cache",
        sources=[s.public() for s in sources],
    )
    app.state.catalog.throttle = 0
    d = f"/api/d/{NAME}"
    with TestClient(app) as c:
        home = c.get("/api/datasets").json()
        assert [x["name"] for x in home["datasets"]] == [NAME] and home["datasets"][0]["articles"] == 6
        assert home["sources"] == [
            {
                "name": NAME,
                "repo_id": REPO,
                "url": f"https://huggingface.co/datasets/{REPO}",
                "revision": "main",
                "commit": OLD,
                "snapshot": str(fake.snap(OLD)),
                "offline": None,
            }
        ]
        o = c.get(f"{d}/overview").json()
        assert o["articles"] == 6 and o["file"] == "jw-20260901.jsonl.gz"
        st = next(a for a in o["annotations"] if a["key"] == "bpb.paragraphs")
        assert st["joins"][0]["problems"] == [] and st["joins"][0]["matched"] > 0
        a = c.get(f"{d}/article/102").json()
        assert a["dataset"] == NAME and a["text"].startswith("# ପୁରୀ")
        assert {"bits", "tokens"} <= set(a["para_annotations"]["bpb"]["rows"]["1"])  # joined from the store
        assert c.get(f"{d}/methodology").json()["html"].startswith("<h1")
        assert c.get(f"{d}/excluded").json()["total"] > 0
        # the Files view shows the snapshot's files (all symlinks) with their sizes
        tree = c.get(f"{d}/files").json()["tree"]
        kids = {k["name"]: k for k in tree["children"]}
        assert kids["jw-20260901.jsonl.gz"]["type"] == "file" and kids["jw-20260901.jsonl.gz"]["size"] > 0
        assert {k["name"] for k in kids["annotations"]["children"]} >= {"bpb.jsonl", "topics.jsonl"}
        assert kids["raw"]["files"] == 1 and tree["files"] > 10
        # reviews are written to the state folder, never through the link into the cache
        r = c.post(f"{d}/review", json={"id": 102, "verdict": "keep", "text_sha1": a["text_sha1"]})
        assert r.status_code == 200 and r.json()["event"]["dataset"] == NAME
        assert (writer.root / "state" / "reviews.jsonl").exists()


def test_a_newer_revision_moves_the_link(fake, writer):
    root, _ = build(writer)
    fake.commit = NEW
    root2, (s,) = build(writer)
    assert root2 == root and s.commit == NEW and (root / NAME).resolve() == fake.snap(NEW).resolve()
    assert (root / NAME / "README.md").read_text() == "# Newer\n"
    assert [n for n in os.listdir(root) if n != NAME] == []  # no temporary links left behind


def test_offline_falls_back_to_the_cached_snapshot(fake, writer):
    fake.cached = OLD
    fake.online = False
    root, (s,) = build(writer)
    assert (root / NAME).resolve() == fake.snap(OLD).resolve()
    assert s.offline and "ConnectError" in s.offline and s.commit == OLD
    assert "could not be reached" in s.describe() and "cached snapshot" in s.describe()
    kind, _, kw = fake.calls[-1]
    assert kind == "download" and kw["local_files_only"] is True and kw["revision"] == "main"
    assert kw["ignore_patterns"] == ["raw/html/*"]


def test_a_download_cut_off_falls_back_too(fake, writer, monkeypatch):
    real = fake.snapshot_download

    def flaky(repo_id, **kw):
        if not kw.get("local_files_only"):
            raise httpx2.ReadTimeout("timed out")
        return real(repo_id, **kw)

    monkeypatch.setattr(hub, "snapshot_download", flaky)
    _, (s,) = build(writer)
    assert s.offline == "ReadTimeout: timed out" and s.commit == OLD


def test_offline_without_a_cached_copy_is_a_clear_error(fake, writer):
    fake.online = False
    fake.cached = None
    with pytest.raises(hub.HubError) as e:
        build(writer)
    msg = str(e.value)
    assert REPO in msg and "ConnectError" in msg and "no complete copy of revision 'main'" in msg and "--data" in msg
    assert not (writer.root / "cache" / "hub-root" / NAME).exists()


def test_an_unknown_revision_is_an_error_not_a_fallback(fake, writer):
    with pytest.raises(hub.HubError, match="has no revision 'v9'"):
        build(writer, "v9")
    assert not any(c[0] == "download" for c in fake.calls)


def test_stale_links_go_and_real_folders_stay(fake, writer):
    root = writer.mkdir(writer.root / "cache" / "hub-root")
    writer.symlink(root / "old-dataset", fake.snap(OLD))
    (root / "notes").mkdir()
    build(writer)
    assert sorted(os.listdir(root)) == ["notes", NAME]
    # a real folder where the link goes is never replaced
    (root / NAME).unlink()
    (root / NAME).mkdir()
    with pytest.raises(hub.HubError, match="not one"):
        build(writer)
    assert (root / NAME).is_dir() and not (root / NAME).is_symlink()


def test_writes_through_the_link_are_refused(fake, writer):
    root, _ = build(writer)
    from edaapp.paths import UnsafePathError

    with pytest.raises(UnsafePathError):
        writer.append_lines(root / NAME / "reviews" / "x.jsonl", ["{}"])
    with pytest.raises(UnsafePathError):
        SafeWriter(writer.root).write_atomic(root / NAME / "README.md", b"x")


# ---- the command line ---------------------------------------------------------------------------


@pytest.fixture
def no_server(monkeypatch):
    """cli.main up to the point where it would serve: uvicorn.run records its app instead."""
    import uvicorn

    ran = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: ran.update(app=app, **kw))
    return ran


def test_cli_serves_the_hub_by_default(fake, writer, monkeypatch, no_server, capsys, tmp_path):
    monkeypatch.setattr(hub, "HUB_ROOT", writer.root / "cache" / "hub-root")
    monkeypatch.setattr(hub, "SafeWriter", lambda: writer)
    cli.main(["--port", "8799", "--hub-revision", NEW, "--state", str(tmp_path / "state")])
    out = capsys.readouterr().out
    assert f"checking Hugging Face dataset {REPO} at {NEW}" in out
    assert f"Hugging Face dataset {REPO} at {NEW}" in out and "data root" in out and NAME in out
    app = no_server["app"]
    assert no_server["port"] == 8799 and app.state.svc.sources[0]["revision"] == NEW
    assert fake.calls[0] == ("info", REPO, NEW)


def test_cli_local_data_root(no_server, capsys, jdata_root, monkeypatch, tmp_path):
    def no_hub(*a, **kw):
        raise AssertionError("the Hub is not asked when --data is given")

    monkeypatch.setattr(hub, "build_root", no_hub)
    cli.main(["--data", str(jdata_root), "--state", str(tmp_path / "state")])
    out = capsys.readouterr().out
    assert f"data from a local folder (--data): {jdata_root.resolve()}" in out and "jwiki" in out
    assert no_server["app"].state.svc.sources == []


def test_cli_errors(fake, writer, monkeypatch, capsys, jdata_root):
    with pytest.raises(SystemExit):
        cli.main(["--data", str(jdata_root), "--hub-revision", "main"])
    assert "can't be used with --data" in capsys.readouterr().err
    fake.online, fake.cached = False, None
    monkeypatch.setattr(hub, "HUB_ROOT", writer.root / "cache" / "hub-root")
    monkeypatch.setattr(hub, "SafeWriter", lambda: writer)
    with pytest.raises(SystemExit) as e:
        cli.main([])
    assert "could not download" in str(e.value.code) and "--data PATH" in str(e.value.code)
