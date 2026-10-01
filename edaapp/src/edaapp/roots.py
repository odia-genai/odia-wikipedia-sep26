"""Data roots made of symlinks, and the default one: this repository.

The app reads a data root: a folder holding one folder per dataset, whose name is the dataset's
name. edaapp lives in the dataset's own repository (edaapp/ next to prepare.py), so by default it
serves the folder above edaapp/ as one dataset named REPO_DATASET ("odia-wikipedia", DATASET in
../prepare.py), whatever the clone's folder is called: the name keys the review log. The data
root is .cache/repo-root/, holding one symlink, odia-wikipedia -> the repository. The Hugging Face
root (hub.py) is made the same way, with links to snapshots in the Hugging Face cache.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from . import paths
from .paths import SafeWriter, UnsafePathError

REPO_LINK_ROOT = paths.CACHE_DIR / "repo-root"


class RootError(RuntimeError):
    """A data root of links could not be made."""


def link_root(root: Path, links: dict[str, Path], writer: SafeWriter) -> Path:
    """Make `root` a folder of symlinks, one per dataset name (name -> target folder). Links of
    other names are removed; anything else in the folder is left alone. Returns the root."""
    root = writer.mkdir(root)
    for name, target in links.items():
        try:
            writer.symlink(root / name, target)
        except UnsafePathError as e:  # e.g. a real folder where the link goes
            raise RootError(f"could not link {root / name} to {target}: {e}") from e
    for e in sorted(os.scandir(root), key=lambda e: e.name):
        if e.is_symlink() and e.name not in links:
            writer.remove_link(root / e.name)
    return root


def git_commit(repo: Path) -> str | None:
    """The repository's checked-out commit (full hash), or None (no git, not a repository)."""
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10, check=True
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None


def repo_root(
    *, repo: Path | None = None, name: str | None = None, root: Path | None = None, writer: SafeWriter | None = None
) -> tuple[Path, dict]:
    """The data root serving the repository (default: the folder above edaapp/) as the dataset
    `name` (default REPO_DATASET). Returns (the data root, the source, for the Home page)."""
    repo = Path(paths.REPO_ROOT if repo is None else repo).resolve()
    name = paths.REPO_DATASET if name is None else name
    root = link_root(REPO_LINK_ROOT if root is None else root, {name: repo}, writer or SafeWriter())
    return root, {"kind": "repository", "name": name, "path": str(repo), "commit": git_commit(repo)}


def describe_repo(source: dict) -> str:
    commit = f" (commit {source['commit'][:7]})" if source.get("commit") else ""
    return f"{source['name']}: this repository, {source['path']}{commit}"
