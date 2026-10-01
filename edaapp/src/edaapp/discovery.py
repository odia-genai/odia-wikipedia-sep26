"""Find datasets, their versions, annotations and reports by looking at the data root's structure.

Conventions (nothing here names a particular dataset):

- A dataset is an immediate subdirectory of the data root with, at its top level, a JSONL
  (`.jsonl`, or gzipped `.jsonl.gz`) or Parquet table that has `id` and `text` columns. Each such
  table is a *version*, keyed by its file stem (`x` for `x.jsonl.gz`). Files of one stem in several
  formats are the same version: the newer file (by mtime; on a tie JSONL, then JSONL.gz) is read and
  the others are reported as not read. Versions are ordered newest first by the first 8-digit date
  in the stem, then by mtime.
- `<stem>-build.json` next to a version holds its build statistics.
- `excluded.jsonl` at the top level lists the pages left out of the corpus (`id`, `revid`,
  `title`, `reason`, `detail`); `removed-blocks.jsonl` lists blocks cut out of kept articles
  (`id`, `title`, `kind`, `reason`, `text`). Both are reserved names, never versions.
- `annotations/<name>.jsonl` (or .jsonl.gz, .parquet): one row per article, with an `id` column.
  `annotations/<name>.paragraphs.jsonl` (or .jsonl.gz, .parquet): rows keyed by (`id`, `para`). When
  several formats of one annotation exist, the newer file is read (JSONL on a tie).
  Optional sidecar `annotations/<name>.json` (for paragraph files `<name>.paragraphs.json`
  is tried first): {"name", "description", "source", "created", "columns", "depends_on_text",
  "joins"}. `joins` lists columns kept once in another file instead of copied into every row:
  [{"path": <relative to the dataset folder>, "on": <key column in both files>, "columns":
  {name: description}, "description"}]. The file (JSONL, JSONL.gz or Parquet, one row per key)
  is watched like the dataset's own files.
- `quality/*.md` are reports; `README.md` and `LEARNINGS.md` are the dataset's docs.
- `METHODOLOGY.md` at the top level gets a Methodology page; `quality/review-first.md` gets a
  Review first page (and stays in the reports list).
- Files ending in `.tmp` (a build's atomic write in progress) and dotfiles are not looked at.
- Symlinked files are followed: in a Hugging Face cache snapshot every file is a symlink into the
  cache's blobs/ folder.
"""

from __future__ import annotations

import gzip
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import pyarrow.parquet as pq

TABLE_EXTS = (".jsonl.gz", ".jsonl", ".parquet")  # a table file's format is its extension, without the dot
# on an mtime tie: JSONL is the canonical format, then the same gzipped (as published on Hugging Face)
FMT_PREFERENCE = {"jsonl": 2, "jsonl.gz": 1, "parquet": 0}
EXCLUDED = "excluded.jsonl"  # at the dataset's top level: pages left out of the corpus
REMOVED_BLOCKS = "removed-blocks.jsonl"  # at the top level: blocks cut out of kept articles
SIDE_FILES = {"excluded": EXCLUDED, "removed_blocks": REMOVED_BLOCKS}
SIDE_STEMS = {n.removesuffix(".jsonl") for n in SIDE_FILES.values()}  # never versions, in any format
METHODOLOGY = "METHODOLOGY.md"  # at the dataset's top level: its own page
REVIEW_FIRST = "review-first.md"  # in quality/: its own, actionable page
DATE_RE = re.compile(r"(?<!\d)(\d{8})(?!\d)")


@dataclass(frozen=True)
class FileStat:
    path: Path
    size: int
    mtime_ns: int
    ino: int

    @property
    def sig(self) -> tuple:
        return (str(self.path), self.size, self.mtime_ns, self.ino)


def table_ext(name: str) -> str | None:
    """A table file's extension (".jsonl.gz", ".jsonl" or ".parquet"), or None for other files."""
    return next((ext for ext in TABLE_EXTS if name.endswith(ext) and len(name) > len(ext)), None)


def is_gzip(path: Path) -> bool:
    return path.name.endswith(".gz")


def stat(path: Path) -> FileStat | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return FileStat(path, st.st_size, st.st_mtime_ns, st.st_ino)


# Column lists of table files, cached by (path, size, mtime, inode): a file replaced in place
# gets a new inode or mtime and is read again.
_COLUMNS_CACHE: dict[tuple, list[str] | None] = {}


def table_columns(fs: FileStat) -> list[str] | None:
    """Column names of a Parquet or JSON lines file, gzipped or not (JSON lines: the keys of its first record)."""
    key = fs.sig
    if key in _COLUMNS_CACHE:
        return _COLUMNS_CACHE[key]
    cols: list[str] | None
    try:
        if fs.path.suffix == ".parquet":
            cols = list(pq.read_schema(fs.path).names)
        else:
            with (gzip.open if is_gzip(fs.path) else open)(fs.path, "rt", encoding="utf-8") as f:
                first = f.readline()
            rec = json.loads(first) if first.strip() else None
            cols = list(rec.keys()) if isinstance(rec, dict) else None
    except Exception:
        cols = None
    if len(_COLUMNS_CACHE) > 2000:
        _COLUMNS_CACHE.clear()
    _COLUMNS_CACHE[key] = cols
    return cols


def newest(files: dict[str, FileStat]) -> str:
    """The format to read when one table exists in several: the newest file by mtime (a
    conversion or rebuild writes the new one last), JSONL on a tie."""
    return max(files, key=lambda fmt: (files[fmt].mtime_ns, FMT_PREFERENCE.get(fmt, -1)))


def not_read_note(files: dict[str, FileStat], used: str, prefix: str = "") -> str | None:
    """Which file is read when one table exists in two formats, e.g.
    "annotations/x.parquet is not read: x.jsonl is used (newer by 3 min)"."""
    others = [f for f in files if f != used]
    if not others:
        return None
    u = files[used]
    parts = []
    for f in others:
        o = files[f]
        dt = (u.mtime_ns - o.mtime_ns) / 1e9
        age = f"same mtime, {used} preferred" if dt == 0 else f"newer by {_duration(dt)}"
        parts.append(f"{prefix}{o.path.name} is not read: {u.path.name} is used ({age})")
    return "; ".join(parts)


def _duration(s: float) -> str:
    s = abs(s)
    if s < 1:
        return f"{s * 1000:.0f} ms"
    if s < 120:
        return f"{s:.0f} s"
    if s < 7200:
        return f"{s / 60:.0f} min"
    if s < 172800:
        return f"{s / 3600:.0f} h"
    return f"{s / 86400:.0f} days"


@dataclass
class Version:
    stem: str
    files: dict[str, FileStat]  # fmt ("jsonl", "jsonl.gz" or "parquet") -> file
    build_json: FileStat | None = None

    @property
    def fmt(self) -> str:
        return newest(self.files)

    @property
    def primary(self) -> FileStat:
        return self.files[self.fmt]

    @property
    def note(self) -> str | None:
        """Which file is read when the version exists in two formats."""
        return not_read_note(self.files, self.fmt)

    @property
    def not_read(self) -> list[str]:
        return [f.path.name for fmt, f in self.files.items() if fmt != self.fmt]

    @property
    def date(self) -> str:
        m = DATE_RE.search(self.stem)
        return m.group(1) if m else ""

    def sort_key(self) -> tuple:
        return (self.date, self.primary.mtime_ns, self.stem)


JOIN_EXTS = (".jsonl", ".jsonl.gz", ".parquet")  # formats a sidecar's "joins" may point to


@dataclass(frozen=True)
class Join:
    """Columns of an annotation that live in another file, declared by its sidecar's "joins": each
    row gets `columns` from the row of `path` whose `on` equals its own."""

    path: str  # as declared: relative to the dataset folder
    on: str
    columns: dict[str, str]  # name -> description
    description: str = ""
    file: FileStat | None = None  # None: missing, outside the dataset folder, or not a supported format
    problem: str | None = None

    def label(self) -> str:
        cols = ", ".join(self.columns)
        return f"{self.path} (joined on {self.on} for {cols})"


def join_target(ds_dir: Path, rel: str) -> Path | None:
    """The file a join names (`rel`, relative to the dataset folder), or None when it is outside the
    folder: an absolute path, or one whose folders lead out of it (after "..", and after following
    symlinked folders). The file itself may be a symlink, as every file of a Hugging Face cache
    snapshot is (into the cache's blobs/ folder, outside the snapshot)."""
    if Path(rel).is_absolute():
        return None
    target = ds_dir / rel
    return target if target.parent.resolve().is_relative_to(ds_dir.resolve()) else None


def parse_joins(meta: dict, ds_dir: Path) -> list[Join]:
    """The sidecar's "joins", each checked: a path inside the dataset folder, a supported format, a
    key and at least one column. A join that can't be used carries its problem (and no file)."""
    raw = meta.get("joins") if isinstance(meta, dict) else None
    if raw is None:
        return []
    if not isinstance(raw, list):
        return [Join("?", "?", {}, problem="the sidecar's joins is not a list; ignored")]
    out = []
    for j in raw:
        if not isinstance(j, dict) or not isinstance(j.get("path"), str) or not isinstance(j.get("on"), str):
            out.append(Join(str(j)[:60], "?", {}, problem=f"a join needs a path and an on column: {str(j)[:80]}"))
            continue
        path, on = j["path"], j["on"]
        cols = j.get("columns")
        if isinstance(cols, list):
            cols = {str(c): "" for c in cols}
        if not isinstance(cols, dict) or not cols:
            out.append(Join(path, on, {}, problem=f"the join of {path} names no columns"))
            continue
        cols = {str(k): str(v) if v is not None else "" for k, v in cols.items()}
        desc = str(j.get("description") or "")
        target = join_target(ds_dir, path)
        if target is None:
            out.append(Join(path, on, cols, desc, problem=f"{path} is outside the dataset folder; not joined"))
        elif not path.endswith(JOIN_EXTS):
            out.append(Join(path, on, cols, desc, problem=f"{path}: only {', '.join(JOIN_EXTS)} files can be joined"))
        elif (fs := stat(target)) is None or not target.is_file():
            gone = f"{path} not found, so {', '.join(cols)} are not shown (joined on {on})"
            out.append(Join(path, on, cols, desc, problem=gone))
        else:
            out.append(Join(path, on, cols, desc, file=fs))
    return out


# Join paths per sidecar, cached by the sidecar's (path, size, mtime, inode): dir_signature stats
# the joined files every half second without parsing the sidecars again.
_JOIN_PATHS_CACHE: dict[tuple, list[str]] = {}


def join_paths(sidecar: FileStat) -> list[str]:
    key = sidecar.sig
    if key not in _JOIN_PATHS_CACHE:
        meta = _read_json(sidecar)
        raw = meta.get("joins")
        paths = (
            [j["path"] for j in raw if isinstance(j, dict) and isinstance(j.get("path"), str)]
            if isinstance(raw, list)
            else []
        )
        if len(_JOIN_PATHS_CACHE) > 2000:
            _JOIN_PATHS_CACHE.clear()
        _JOIN_PATHS_CACHE[key] = paths
    return _JOIN_PATHS_CACHE[key]


@dataclass
class Annotation:
    name: str
    level: str  # "article" | "paragraph"
    file: FileStat
    fmt: str
    columns: list[str]
    sidecar: FileStat | None = None
    meta: dict = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)
    not_read: list[str] = field(default_factory=list)  # the same annotation in another format, older
    joins: list[Join] = field(default_factory=list)  # the sidecar's "joins"

    @property
    def key(self) -> str:
        """Unique within a dataset: the name, plus ".paragraphs" for paragraph files."""
        return self.name if self.level == "article" else f"{self.name}.paragraphs"


@dataclass
class Dataset:
    name: str
    dir: Path
    versions: list[Version]  # newest first
    annotations: list[Annotation]
    reports: list[FileStat]  # quality/*.md
    docs: list[FileStat]  # README.md, LEARNINGS.md
    ignored: list[str]  # annotation files that could not be used, with the reason
    signature: tuple
    methodology: FileStat | None = None  # METHODOLOGY.md at the top level: the Methodology page
    review_first: FileStat | None = None  # quality/review-first.md: the Review first page
    excluded: FileStat | None = None  # excluded.jsonl: pages left out of the corpus
    removed_blocks: FileStat | None = None  # removed-blocks.jsonl: blocks cut out of kept articles
    notes: list[str] = field(default_factory=list)  # a table in two formats: which file is read

    def side(self, kind: str) -> FileStat | None:
        """excluded.jsonl ("excluded") or removed-blocks.jsonl ("removed_blocks"), if present."""
        return {"excluded": self.excluded, "removed_blocks": self.removed_blocks}.get(kind)

    def doc_names(self) -> list[str]:
        return [d.path.name for d in self.docs] + (["METHODOLOGY.md"] if self.methodology else [])

    def version(self, stem: str | None) -> Version:
        if stem:
            for v in self.versions:
                if v.stem == stem:
                    return v
            raise KeyError(stem)
        return self.versions[0]


def _listdir(path: Path) -> list[os.DirEntry]:
    try:
        with os.scandir(path) as it:
            return sorted(it, key=lambda e: e.name)
    except OSError:
        return []


def _entry_stat(e: os.DirEntry) -> FileStat | None:
    try:
        st = e.stat()
    except OSError:
        return None
    return FileStat(Path(e.path), st.st_size, st.st_mtime_ns, st.st_ino)


def skipped(name: str) -> bool:
    """Dotfiles, and files being written: a build writes `x.jsonl.tmp` and renames it over `x.jsonl`
    when done. Watching the growing temp file would reload the dataset every half second."""
    return name.startswith(".") or name.endswith(".tmp")


def dir_signature(ds_dir: Path) -> tuple:
    """(name, size, mtime, inode) of every file that discovery looks at, and of the files that the
    sidecars' "joins" name (a missing one as -1s). Cheap: a few dozen stats."""
    sig = []
    joined = set()
    for sub in ("", "annotations", "quality"):
        d = ds_dir / sub if sub else ds_dir
        for e in _listdir(d):
            if skipped(e.name) or not e.is_file():
                continue
            fs = _entry_stat(e)
            if fs:
                sig.append((sub, e.name, fs.size, fs.mtime_ns, fs.ino))
                if sub == "annotations" and e.name.endswith(".json"):
                    joined.update(join_paths(fs))
    for p in sorted(joined):
        target = join_target(ds_dir, p)
        fs = stat(target) if target is not None else None  # outside: never read (parse_joins says so)
        sig.append(("join", p, *((fs.size, fs.mtime_ns, fs.ino) if fs else (-1, -1, -1))))
    return tuple(sig)


def _read_json(fs: FileStat) -> dict:
    try:
        with open(fs.path, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def discover_dataset(ds_dir: Path) -> Dataset | None:
    signature = dir_signature(ds_dir)
    files = [e for e in _listdir(ds_dir) if e.is_file() and not skipped(e.name)]
    by_stem: dict[str, dict[str, FileStat]] = {}
    for e in files:
        ext = table_ext(e.name)
        if ext is None or e.name[: -len(ext)] in SIDE_STEMS:
            continue  # removed-blocks.jsonl has id and text columns, but it is not a corpus
        fs = _entry_stat(e)
        if fs is None or fs.size == 0:
            continue
        cols = table_columns(fs)
        if not cols or "id" not in cols or "text" not in cols:
            continue
        by_stem.setdefault(e.name[: -len(ext)], {})[ext[1:]] = fs
    if not by_stem:
        return None
    versions = []
    for stem, fmts in by_stem.items():
        build = stat(ds_dir / f"{stem}-build.json")
        versions.append(Version(stem, fmts, build))
    versions.sort(key=Version.sort_key, reverse=True)

    annotations, ignored, notes = discover_annotations(ds_dir / "annotations")
    notes = [n for v in versions if (n := not_read_note(v.files, v.fmt))] + notes
    reports = [
        fs for e in _listdir(ds_dir / "quality") if e.name.endswith(".md") and e.is_file() if (fs := _entry_stat(e))
    ]
    docs = [fs for n in ("README.md", "LEARNINGS.md") if (fs := stat(ds_dir / n))]
    methodology = stat(ds_dir / METHODOLOGY)
    review_first = next((r for r in reports if r.path.name == REVIEW_FIRST), None)
    excluded, removed = (fs if (fs := stat(ds_dir / n)) and fs.size else None for n in (EXCLUDED, REMOVED_BLOCKS))
    return Dataset(
        ds_dir.name,
        ds_dir,
        versions,
        annotations,
        reports,
        docs,
        ignored,
        signature,
        methodology,
        review_first,
        excluded,
        removed,
        notes,
    )


def discover_annotations(ann_dir: Path) -> tuple[list[Annotation], list[str], list[str]]:
    """(annotations, ignored files with the reason, notes). One annotation per (name, level): if it
    exists in several formats (.jsonl, .jsonl.gz, .parquet; a conversion in progress), the newer
    file is read and a note says which."""
    ignored: list[str] = []
    notes: list[str] = []
    entries = {e.name: e for e in _listdir(ann_dir) if e.is_file() and not skipped(e.name)}
    usable: dict[tuple[str, str], dict[str, Annotation]] = {}  # (name, level) -> fmt -> annotation
    for fname, e in entries.items():
        ext = table_ext(fname)
        if ext is None:
            continue
        base = fname[: -len(ext)]
        if base.endswith(".paragraphs"):
            name, level = base[: -len(".paragraphs")], "paragraph"
        else:
            name, level = base, "article"
        fs = _entry_stat(e)
        if fs is None:
            continue
        if fs.size == 0:
            ignored.append(f"{fname}: empty file")
            continue
        cols = table_columns(fs)
        if cols is None:
            ignored.append(f"{fname}: could not read its schema")
            continue
        need = ["id", "para"] if level == "paragraph" else ["id"]
        missing = [c for c in need if c not in cols]
        if missing:
            ignored.append(f"{fname}: no {', '.join(missing)} column")
            continue
        sidecar = None
        cands = [f"{name}.paragraphs.json", f"{name}.json"] if level == "paragraph" else [f"{name}.json"]
        for c in cands:
            if c in entries:
                sidecar = _entry_stat(entries[c])
                break
        meta = _read_json(sidecar) if sidecar else {}
        joins = parse_joins(meta, ann_dir.parent)
        usable.setdefault((name, level), {})[ext[1:]] = Annotation(
            name, level, fs, ext[1:], cols, sidecar, meta, joins=joins
        )
    out = []
    for fmts in usable.values():
        use = newest({f: a.file for f, a in fmts.items()})
        ann = fmts[use]
        note = not_read_note({f: a.file for f, a in fmts.items()}, use, "annotations/")
        if note:
            ann.not_read = [a.file.path.name for f, a in fmts.items() if f != use]
            notes.append(note)
        out.append(ann)
    out.sort(key=lambda a: (a.name, a.level))
    return out, ignored, notes


def discover(data_root: Path) -> dict[str, Dataset]:
    out = {}
    for e in _listdir(data_root):
        if e.name.startswith(".") or not e.is_dir():
            continue
        ds = discover_dataset(Path(e.path))
        if ds:
            out[ds.name] = ds
    return out


def other_dirs(data_root: Path) -> list[str]:
    """Subdirectories of the data root that are not datasets (shown on the home page)."""
    return [e.name for e in _listdir(data_root) if e.is_dir() and not e.name.startswith(".")]


# ---- the Files view -----------------------------------------------------------------------

BIG_DIR = 300  # a subdirectory with more entries than this is summarised, not listed
BIG_BYTES = 100 * 1024 * 1024  # ... and so is one holding more than this many bytes
MAX_DEPTH = 4


def _du(path: Path, limit: int = 200_000) -> tuple[int, int, int]:
    """(files, dirs, bytes) under path, stopping after `limit` entries. Symlinked files count with
    their target's size (a Hugging Face cache snapshot is made of them); symlinked folders are not
    followed."""
    files = dirs = size = seen = 0
    stack = [path]
    while stack and seen < limit:
        d = stack.pop()
        for e in _listdir(d):
            seen += 1
            try:
                if e.is_dir(follow_symlinks=False):
                    dirs += 1
                    stack.append(Path(e.path))
                elif e.is_file():
                    files += 1
                    size += e.stat().st_size
            except OSError:
                pass
    return files, dirs, size


def file_tree(root: Path, depth: int = 0) -> dict:
    """The directory as a tree of {name, type, size, files, children}. Subdirectories that are
    big (many entries or many bytes) or deep get counts and totals instead of children. Symlinked
    files are shown with their target's size and mtime; symlinked folders are left out."""
    node: dict = {"name": root.name, "type": "dir", "children": []}
    total_files = total_size = 0
    for e in _listdir(root):
        try:
            if e.is_dir(follow_symlinks=False):
                sub = Path(e.path)
                n_entries = len(_listdir(sub))
                f, d, s = _du(sub)
                # the dataset's own folders are always opened one level (if not huge in entries);
                # deeper folders that are big in entries or bytes get counts and totals only
                if n_entries > BIG_DIR or (s > BIG_BYTES and depth >= 1) or depth + 1 >= MAX_DEPTH:
                    exts: dict[str, int] = {}
                    for x in _listdir(sub)[:5000]:
                        if x.is_file():
                            ext = table_ext(x.name) or os.path.splitext(x.name)[1] or "(none)"
                            exts[ext] = exts.get(ext, 0) + 1
                    why = f"{n_entries:,} entries" if n_entries > BIG_DIR else "large" if s > BIG_BYTES else "deep"
                    child = {
                        "name": e.name,
                        "type": "dir",
                        "summarised": True,
                        "reason": why,
                        "entries": n_entries,
                        "files": f,
                        "dirs": d,
                        "size": s,
                        "extensions": exts,
                    }
                else:
                    child = file_tree(sub, depth + 1)
                total_files += child.get("files", 0)
                total_size += child.get("size", 0)
                node["children"].append(child)
            elif e.is_file():
                st = e.stat()
                node["children"].append({"name": e.name, "type": "file", "size": st.st_size, "mtime": st.st_mtime})
                total_files += 1
                total_size += st.st_size
        except OSError:
            continue
    node.update(files=total_files, size=total_size)
    return node
