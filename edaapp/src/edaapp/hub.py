"""The datasets edaapp serves by default, from the Hugging Face Hub.

HUB_DATASETS maps a dataset's name to the Hub dataset repository it comes from. The name is the
dataset's folder name in the data root, which keys the review log (`"dataset": "odia-wikipedia"`),
so it stays the same wherever the data comes from.

`build_root` downloads each repository with `snapshot_download` into the normal Hugging Face cache
(~/.cache/huggingface/hub, or wherever HF_HOME / HF_HUB_CACHE point) and returns a data root,
.cache/hub-root/, holding one symlink per dataset name that points at its snapshot folder. The app
then sees the usual layout (data root / dataset folder / files), and this repository keeps no copy
of the data. Only the links are written here, through the SafeWriter.

A download checks the Hub for the revision first and fetches only files that changed. When the Hub
can't be reached, the cached snapshot of the revision is used (local_files_only), and the result
says so; without one, HubError.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from huggingface_hub import constants, dataset_info, snapshot_download
from huggingface_hub.errors import LocalEntryNotFoundError, RepositoryNotFoundError, RevisionNotFoundError

from .paths import CACHE_DIR, SafeWriter, UnsafePathError

HUB_DATASETS = {"odia-wikipedia": "fastpixels/odia-wikipedia-sep26"}
HUB_ROOT = CACHE_DIR / "hub-root"
DEFAULT_REVISION = "main"
# raw/html/ is the rendered HTML of every page (149 MB of gzipped JSON lines), the build's input.
# Nothing in the app reads it: discovery looks at the top level, annotations/ and quality/, and the
# only file a sidecar joins is raw/bpb/scores.jsonl.gz.
IGNORE_PATTERNS = ["raw/html/*"]


class HubError(RuntimeError):
    """A dataset could not be downloaded and there is no usable cached copy."""


@dataclass(frozen=True)
class Source:
    """Where a dataset of the data root comes from."""

    name: str  # the dataset's folder name in the data root
    repo_id: str
    revision: str  # as asked for: a branch, a tag or a commit
    snapshot: Path  # the snapshot folder in the Hugging Face cache
    offline: str | None = None  # why the Hub could not be reached (the cached snapshot is used)

    @property
    def commit(self) -> str:
        return self.snapshot.name  # the cache names snapshot folders by commit

    @property
    def url(self) -> str:
        return f"https://huggingface.co/datasets/{self.repo_id}"

    def describe(self) -> str:
        where = f"{self.repo_id} at {self.revision} (commit {self.commit[:7]})"
        if self.offline:
            return (
                f"{self.name}: the Hugging Face Hub could not be reached ({self.offline}); using the cached "
                f"snapshot of {where}, which may be out of date"
            )
        return f"{self.name}: Hugging Face dataset {where}, in {self.snapshot}"

    def public(self) -> dict:
        return {
            "name": self.name,
            "repo_id": self.repo_id,
            "url": self.url,
            "revision": self.revision,
            "commit": self.commit,
            "snapshot": str(self.snapshot),
            "offline": self.offline,
        }


def _first_line(e: BaseException) -> str:
    msg = str(e).strip().split("\n")[0]
    return f"{type(e).__name__}: {msg}" if msg else type(e).__name__


def fetch(name: str, repo_id: str, revision: str = DEFAULT_REVISION) -> Source:
    """Download (or update) one dataset into the Hugging Face cache; its snapshot folder.

    The Hub is asked for the revision first, because snapshot_download on its own falls back to
    the cache without saying so when the Hub can't be reached."""
    kw = {"repo_type": "dataset", "revision": revision, "ignore_patterns": IGNORE_PATTERNS}
    try:
        dataset_info(repo_id, revision=revision, timeout=20)
        return Source(name, repo_id, revision, Path(os.path.abspath(snapshot_download(repo_id, **kw))))
    except RepositoryNotFoundError as e:  # the Hub answered: no such dataset (or not visible with your token)
        raise HubError(f"{repo_id} is not a dataset on the Hugging Face Hub ({_first_line(e)})") from e
    except RevisionNotFoundError as e:
        raise HubError(f"{repo_id} has no revision {revision!r} on the Hugging Face Hub") from e
    except Exception as e:  # no network, a timeout, the Hub down, HF_HUB_OFFLINE=1, a download cut off
        reason = _first_line(e)
        try:
            path = snapshot_download(repo_id, local_files_only=True, **kw)
        except Exception as e2:
            # "not in the cache" says nothing more; anything else (a partial snapshot, ...) is worth showing
            why = "" if type(e2) is LocalEntryNotFoundError else f" ({_first_line(e2)})"
            raise HubError(
                f"could not download {repo_id} from the Hugging Face Hub ({reason}), and the cache "
                f"({constants.HF_HUB_CACHE}) has no complete copy of revision {revision!r}{why}. "
                "Connect to the internet once to download it, or serve a local folder with --data PATH."
            ) from e
        return Source(name, repo_id, revision, Path(os.path.abspath(path)), offline=reason)


def build_root(
    revision: str = DEFAULT_REVISION,
    *,
    root: Path | None = None,
    datasets: dict[str, str] | None = None,
    writer: SafeWriter | None = None,
) -> tuple[Path, list[Source]]:
    """Fetch every dataset of `datasets` (default HUB_DATASETS) and point `root/<name>` (default
    HUB_ROOT) at its snapshot. Links of names no longer in the map are removed; anything else in the
    folder is left alone. Returns (the data root, the sources)."""
    writer = writer or SafeWriter()
    root = HUB_ROOT if root is None else root
    datasets = HUB_DATASETS if datasets is None else datasets
    sources = [fetch(name, repo_id, revision) for name, repo_id in datasets.items()]
    root = writer.mkdir(root)
    for s in sources:
        try:
            writer.symlink(root / s.name, s.snapshot)
        except UnsafePathError as e:  # e.g. a real folder where the link goes
            raise HubError(f"could not link {root / s.name} to the snapshot: {e}") from e
    for e in sorted(os.scandir(root), key=lambda e: e.name):
        if e.is_symlink() and e.name not in datasets:
            writer.remove_link(root / e.name)
    return root, sources
