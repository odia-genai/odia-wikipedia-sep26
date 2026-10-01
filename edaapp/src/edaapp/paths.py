"""The one place edaapp writes files.

Every write the app makes (review events, caches, DuckDB spill files, the links of the Hugging
Face data root) goes through a SafeWriter, which resolves the target path (following symlinks and
"..") and refuses anything outside its root. The app's writer is rooted at the edaapp/ directory;
tests root their own writers in a temporary directory. Nothing else in the package opens a file
for writing or makes a link (tests/test_paths.py checks the source for that). A link may point
out of the root (the Hugging Face cache); writes through it are refused, since the guard resolves it.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

# .../edaapp/src/edaapp/paths.py -> .../edaapp
APP_ROOT = Path(__file__).resolve().parents[2]
STATE_DIR = APP_ROOT / "state"
CACHE_DIR = APP_ROOT / ".cache"


class UnsafePathError(PermissionError):
    """A write was aimed outside the writer's root."""


def inside(path: Path | str, root: Path | str) -> Path:
    """Resolve `path` (relative paths are taken relative to `root`) and return it if it lies
    inside `root`. Symlinks are followed, so a link pointing out of the root is refused."""
    root = Path(root).resolve()
    p = Path(path)
    if not p.is_absolute():
        p = root / p
    p = p.resolve()
    if p != root and root not in p.parents:
        raise UnsafePathError(f"refusing to write outside {root}: {p}")
    return p


class SafeWriter:
    """All file writes, each behind the path guard."""

    def __init__(self, root: Path | str = APP_ROOT):
        self.root = Path(root).resolve()
        self._lock = threading.Lock()

    def guard(self, path: Path | str) -> Path:
        return inside(path, self.root)

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
