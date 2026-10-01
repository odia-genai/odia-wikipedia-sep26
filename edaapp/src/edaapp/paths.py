"""The one place edaapp writes files.

edaapp lives in the dataset's own repository (edaapp/ next to prepare.py). It writes in two places
only: its own folder (caches, DuckDB spill files, the links of its data roots, `--state` trials)
and the repository's reviews/ folder, which holds reviews/reviews.jsonl, the review log the build
reads. Every write goes through a SafeWriter, which resolves the target path (following symlinks
and "..") and refuses anything outside its roots. Tests root their own writers in a temporary
directory. Nothing else in the package opens a file for writing or makes a link
(tests/test_paths.py checks the source for that). A link may point out of the roots (the
repository, the Hugging Face cache); writes through it are refused unless they land in a root.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

# .../edaapp/src/edaapp/paths.py -> .../edaapp
APP_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = APP_ROOT.parent  # the dataset's repository: the folder above edaapp/
# The repository's dataset name: DATASET in ../prepare.py, the `dataset` of its review events. Never
# the folder's name, which is whatever a clone is called.
REPO_DATASET = "odia-wikipedia"
REVIEWS_DIR = REPO_ROOT / "reviews"  # reviews/reviews.jsonl: the review log the build reads
CACHE_DIR = APP_ROOT / ".cache"
WRITE_ROOTS = (APP_ROOT, REVIEWS_DIR)  # where the app's writer may write


class UnsafePathError(PermissionError):
    """A write was aimed outside the writer's roots."""


def inside(path: Path | str, roots: Path | str | tuple | list) -> Path:
    """Resolve `path` (relative paths are taken relative to the first root) and return it if it
    lies inside one of `roots` (a folder, or several). Symlinks are followed, so a link pointing out
    of the roots is refused."""
    roots = [Path(r).resolve() for r in (roots if isinstance(roots, (tuple, list)) else [roots])]
    p = Path(path)
    if not p.is_absolute():
        p = roots[0] / p
    p = p.resolve()
    if not any(p == r or r in p.parents for r in roots):
        where = roots[0] if len(roots) == 1 else " or ".join(map(str, roots))
        raise UnsafePathError(f"refusing to write outside {where}: {p}")
    return p


class SafeWriter:
    """All file writes, each behind the path guard. `SafeWriter()` is the app's: edaapp/ and the
    repository's reviews/ folder (WRITE_ROOTS); `SafeWriter(folder, ...)` writes only in those."""

    def __init__(self, *roots: Path | str):
        self.roots = tuple(Path(r).resolve() for r in (roots or WRITE_ROOTS))
        self.root = self.roots[0]  # relative paths are taken from here
        self._lock = threading.Lock()

    def guard(self, path: Path | str) -> Path:
        return inside(path, self.roots)

    def mkdir(self, path: Path | str) -> Path:
        p = self.guard(path)
        p.mkdir(parents=True, exist_ok=True)
        # mkdir(parents=True) could have followed a symlinked parent; check the result again
        return self.guard(p)

    def append_lines(self, path: Path | str, lines: list[str]) -> Path:
        """Append whole lines (each without a newline) in one write, then fsync.

        One write() per call keeps a batch together; O_APPEND puts it at the end even if another
        process appended in between."""
        p = self.guard(path)
        self.mkdir(p.parent)
        data = "".join(line + "\n" for line in lines).encode("utf-8")
        with self._lock:
            fd = os.open(p, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
            try:
                os.write(fd, data)
                os.fsync(fd)
            finally:
                os.close(fd)
        return p

    def write_atomic(self, path: Path | str, data: bytes) -> Path:
        """Write to a temporary file next to the target, then rename it over the target."""
        p = self.guard(path)
        self.mkdir(p.parent)
        tmp = self.guard(p.with_name(f".{p.name}.{os.getpid()}.{threading.get_ident()}.tmp"))
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
        return p

    def unlink(self, path: Path | str) -> None:
        p = self.guard(path)
        if p.exists():
            p.unlink()

    def _link_path(self, link: Path | str) -> Path:
        """A symlink's own path: its folder must be inside the root (the link itself is not
        resolved, since it may point out of the root)."""
        link = Path(link)
        if link.name in ("", ".", ".."):
            raise UnsafePathError(f"not a link name: {link}")
        return self.guard(link.parent) / link.name

    def symlink(self, link: Path | str, target: Path | str) -> Path:
        """Make `link` (inside the root) point at `target`, which may be anywhere (e.g. a snapshot
        in the Hugging Face cache). Writes through the link are still refused: the guard resolves
        it. An existing symlink is replaced atomically; anything else in its place is refused."""
        p = self._link_path(link)
        if p.exists() and not p.is_symlink():
            raise UnsafePathError(f"refusing to replace {p} with a symlink: it is not one")
        self.mkdir(p.parent)
        tmp = p.with_name(f".{p.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        if tmp.is_symlink():
            tmp.unlink()
        os.symlink(target, tmp, target_is_directory=Path(target).is_dir())
        os.replace(tmp, p)
        return p

    def remove_link(self, link: Path | str) -> None:
        """Remove a symlink (never what it points at); anything that is not a symlink is refused."""
        p = self._link_path(link)
        if not p.is_symlink():
            raise UnsafePathError(f"refusing to remove {p}: it is not a symlink")
        p.unlink()
