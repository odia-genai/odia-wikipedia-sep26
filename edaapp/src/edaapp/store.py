"""DuckDB state: corpus and annotation tables, the joined view, and change detection.

Each corpus version and each annotation file is loaded into an in-memory DuckDB table (the
whole corpus is ~90 MB of text and loads in ~0.2 s; queries on the table are several times
faster than on the Parquet file, and a table is a consistent snapshot while the file is being
replaced). A view per dataset version joins the corpus to every article-level annotation (by
`id`) and to the current review decisions.

An annotation whose sidecar declares "joins" (columns kept once in another file, e.g. a score
store keyed by para_sha1) gets those columns joined into its table when it is loaded, so every
view sees them as the annotation's own. The joined file is loaded once per file version and
shared by the annotations that name it; a missing or unreadable one is a warning, and the
annotation is shown without its columns.

Change detection: before serving a request the catalog stats the files discovery looks at
(throttled to once every 0.5 s), joined files included. A file whose (size, mtime, inode)
changed is re-read, a new file is picked up, a vanished one is dropped, and the view is rebuilt.
Queries running at that moment finish on the old tables (DuckDB's MVCC).
"""

from __future__ import annotations

import hashlib
import itertools
import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa

from . import discovery, paragraphs
from .discovery import Annotation, Dataset, FileStat, Join, Version
from .filters import Column, qi
from .paths import CACHE_DIR, SafeWriter
from .reviews import ReviewStore

log = logging.getLogger("edaapp")

SHA1_COL = "__sha1"
PARAS_COL = "__paras"
CATEGORY_MAX = 40  # string columns with at most this many distinct values are categories
LIST_OPTIONS = 400
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")
NUMERIC = (
    "TINYINT",
    "SMALLINT",
    "INTEGER",
    "BIGINT",
    "HUGEINT",
    "UTINYINT",
    "USMALLINT",
    "UINTEGER",
    "UBIGINT",
    "UHUGEINT",
    "FLOAT",
    "DOUBLE",
    "REAL",
)


def docs_only(old: tuple, new: tuple) -> bool:
    """Whether two folder signatures differ only in Markdown files."""
    changed = set(old) ^ set(new)
    return bool(changed) and all(entry[1].endswith(".md") for entry in changed)


def lit(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def is_numeric(dtype: str) -> bool:
    d = dtype.upper()
    return d in NUMERIC or d.startswith("DECIMAL")


# JSON has no date, time or UUID type, but DuckDB's JSON reader guesses them from strings: a
# "2026-07-17T18:50:18Z" string would become a TIMESTAMP and lose its "T" and "Z". The Parquet files
# had plain strings, so these stay strings. So do JSON-typed columns (all null, or mixed types).
AS_STRING = {
    "DATE",
    "TIME",
    "TIME WITH TIME ZONE",
    "TIMESTAMP",
    "TIMESTAMP WITH TIME ZONE",
    "TIMESTAMP_S",
    "TIMESTAMP_MS",
    "TIMESTAMP_NS",
    "INTERVAL",
    "UUID",
    "JSON",
    "NULL",
}


def reader_sql(fs: FileStat, types: dict[str, str] | None = None) -> str:
    """A table function reading the file. JSON lines (plain or gzipped) are read with the given
    column types (see json_types); without them DuckDB infers types from every record (sample_size=-1)."""
    if fs.path.suffix == ".parquet":
        return f"read_parquet({lit(str(fs.path))})"
    # said explicitly, not left to DuckDB's guess from the name
    opts = f"format='newline_delimited', compression='{'gzip' if discovery.is_gzip(fs.path) else 'uncompressed'}'"
    if not types:
        return f"read_json({lit(str(fs.path))}, {opts}, sample_size=-1)"
    spec = ", ".join(f"{lit(k)}: {lit(v)}" for k, v in types.items())
    return f"read_json({lit(str(fs.path))}, {opts}, columns={{{spec}}})"


def json_types(cur, fs: FileStat, overrides: dict[str, str] | None = None) -> dict[str, str]:
    """Column types of a JSON lines file: DuckDB's inference over every record, with guessed
    dates, times and UUIDs kept as strings (AS_STRING), then `overrides` for known columns."""
    out = {}
    for name, dtype, *_ in cur.execute(f"DESCRIBE SELECT * FROM {reader_sql(fs)}").fetchall():
        t = str(dtype)
        if t in AS_STRING:
            t = "VARCHAR"
        elif t.endswith("[]") and t[:-2] in AS_STRING:
            t = "VARCHAR[]"
        out[name] = t
    for k, t in (overrides or {}).items():
        if k in out:
            out[k] = t
    return out


def source_sql(cur, fs: FileStat, overrides: dict[str, str] | None = None) -> str:
    """reader_sql with explicit types for JSON lines (two passes over the file; the first,
    inference, takes ~0.1 s for the 96 MB corpus, and ~0.5 s when it is gzipped)."""
    if fs.path.suffix == ".parquet":
        return reader_sql(fs)
    return reader_sql(fs, json_types(cur, fs, overrides))


# The side files at a dataset's top level, their key columns' types, and the columns a view needs
# (added as NULL when a file lacks them, which is reported as a problem).
SIDE_TYPES = {
    "excluded": {"id": "BIGINT", "revid": "BIGINT", "title": "VARCHAR", "reason": "VARCHAR", "detail": "VARCHAR"},
    "removed_blocks": {"id": "BIGINT", "title": "VARCHAR", "kind": "VARCHAR", "reason": "VARCHAR", "text": "VARCHAR"},
}


class DataError(RuntimeError):
    pass


@dataclass
class CorpusTable:
    dataset: str
    version: Version
    sig: tuple
    table: str
    columns: list[tuple[str, str]]  # (name, type) of the source file
    n_rows: int
    loaded_at: float
    para_table: str | None = None
    groups_table: str | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)

    def has(self, col: str) -> bool:
        return any(c == col for c, _ in self.columns)


@dataclass
class SideTable:
    """excluded.jsonl or removed-blocks.jsonl, loaded once per file version."""

    kind: str
    file: FileStat
    table: str
    columns: list[tuple[str, str]]
    rows: int
    problems: list[str]


@dataclass
class JoinTable:
    """A file named by a sidecar's "joins", loaded once per file version and shared by every
    annotation of the dataset that joins it."""

    file: FileStat
    table: str
    types: dict[str, str]  # column -> DuckDB type
    rows: int
    dups: dict[str, int] = field(default_factory=dict)  # key column -> rows that repeat a key (computed lazily)


@dataclass
class AnnTable:
    ann: Annotation
    sig: tuple
    table: str
    columns: list[tuple[str, str]]  # (name, type) excluding id (and para); joined columns included
    rows: int
    distinct: int
    problems: list[str]
    joined: dict[str, str] = field(default_factory=dict)  # joined column -> the file it comes from
    joins: list[dict] = field(default_factory=list)  # each declared join: path, on, columns, rows, matched, problem


class Snapshot:
    """One dataset version with its annotations: a view, its column schema and summary stats."""

    def __init__(
        self,
        cat: Catalog,
        ds: Dataset,
        corpus: CorpusTable,
        anns: list[AnnTable],
        para_anns: list[AnnTable],
        view: str,
        review_table: str,
    ):
        self.cat = cat
        self.ds = ds
        self.corpus = corpus
        self.anns = anns
        self.para_anns = para_anns
        self.view = view
        self.review_table = review_table
        self.created = time.time()
        self._cache: dict[str, Any] = {}
        self._lock = threading.Lock()
        self.columns: dict[str, Column] = self._columns()
        self._resolve_columns()  # string kinds, options and ranges come from the data
        self.ann_status = self._ann_status()

    @property
    def version(self) -> Version:
        return self.corpus.version

    def cursor(self):
        return self.cat.cursor()

    def cached(self, key: str, fn):
        with self._lock:
            if key not in self._cache:
                self._cache[key] = fn()
            return self._cache[key]

    # ---- schema ---------------------------------------------------------------------------
    def _columns(self) -> dict[str, Column]:
        cols: dict[str, Column] = {}
        for name, dtype in self.corpus.columns:
            if name == "text":
                cols[name] = Column(
                    name,
                    dtype,
                    "text",
                    "corpus",
                    name,
                    "article text (use the search box)",
                    filterable=False,
                    sortable=False,
                )
                continue
            cols[name] = Column(name, dtype, "", "corpus", name)
        pkey = "paragraphs" if "paragraphs" not in cols else "_paragraphs"
        cols[pkey] = Column(
            pkey, "BIGINT", "numeric", "derived", "paragraphs", 'number of paragraphs, text.split("\\n\\n")'
        )
        for a in self.anns:
            desc = column_descriptions(a)
            for name, dtype in a.columns:
                key = f"{a.ann.name}.{name}"
                cols[key] = Column(
                    key,
                    dtype,
                    "hash" if name == "text_sha1" else "",
                    a.ann.name,
                    key,
                    str(desc.get(name, "")) if isinstance(desc, dict) else "",
                )
            cols[f"{a.ann.name}._present"] = Column(
                f"{a.ann.name}._present",
                "BOOLEAN",
                "bool",
                a.ann.name,
                f"{a.ann.name}._present",
                f"the article has a row in annotations/{a.ann.file.path.name}",
            )
            if any(n == "text_sha1" for n, _ in a.columns):
                cols[f"{a.ann.name}._stale"] = Column(
                    f"{a.ann.name}._stale",
                    "BOOLEAN",
                    "bool",
                    a.ann.name,
                    f"{a.ann.name}._stale",
                    "text_sha1 differs from the current text: scored on an older version",
                )
        for key, dtype, kind, desc in (
            ("review.verdict", "VARCHAR", "category", "latest review verdict"),
            ("review.note", "VARCHAR", "text", "latest review note"),
            ("review.dropped", "INTEGER", "numeric", "paragraphs marked to drop"),
            ("review.ts", "VARCHAR", "date", "time of the latest review event"),
            ("review.stale", "BOOLEAN", "bool", "the review was made on a different text"),
        ):
            cols[key] = Column(key, dtype, kind, "review", key, desc)
        for c in cols.values():
            if not c.kind:
                c.kind = self._kind(c)
            if c.kind in ("hash", "other"):
                c.filterable = False
        return cols

    def _kind(self, c: Column) -> str:
        d = c.dtype.upper()
        if d == "BOOLEAN":
            return "bool"
        if is_numeric(d):
            return "numeric"
        if d in ("DATE", "TIMESTAMP", "TIMESTAMP WITH TIME ZONE", "TIMESTAMP_S", "TIMESTAMP_MS", "TIMESTAMP_NS"):
            return "date"
        if d.endswith("[]"):
            return "list" if d[:-2] == "VARCHAR" else "other"
        if d == "VARCHAR":
            return "string"  # decided by the data in schema()
        return "other"

    def _resolve_columns(self) -> None:
        """Decide string columns' kinds from the data; collect filter options and ranges."""
        cur = self.cursor()
        v = self.view
        for c in self.columns.values():
            k = qi(c.key)
            if c.source == "review":
                continue
            if c.kind == "string":
                # ISO-8601 dates get a range filter; other strings with few distinct values are
                # categories (multi-select); the rest get a "contains" box
                n, sample, not_iso, nonnull = cur.execute(
                    f"SELECT count(DISTINCT {k}), min({k}), count(*) FILTER (WHERE {k} IS NOT NULL AND NOT "
                    f"regexp_matches({k}, '^\\d{{4}}-\\d{{2}}-\\d{{2}}')), count({k}) FROM {v}"
                ).fetchone()
                if sample and ISO_DATE.match(str(sample)) and not_iso == 0:
                    c.kind = "date"
                elif n is not None and n <= CATEGORY_MAX and (n < nonnull or nonnull > 2 * CATEGORY_MAX):
                    # few distinct values that repeat (a unique column like a title never is one)
                    c.kind = "category"
                else:
                    c.kind = "text"
            if c.kind == "category" or c.kind == "bool":
                c.options = [
                    [val, cnt]
                    for val, cnt in cur.execute(
                        f"SELECT CAST({k} AS VARCHAR) AS val, count(*) AS n FROM {v} GROUP BY 1 "
                        f"ORDER BY n DESC, val LIMIT 200"
                    ).fetchall()
                ]
            elif c.kind == "list":
                c.options = [
                    [val, cnt]
                    for val, cnt in cur.execute(
                        f"SELECT val, count(*) AS n FROM (SELECT unnest(CAST({k} AS VARCHAR[])) AS val FROM {v}) "
                        f"GROUP BY 1 ORDER BY n DESC, val LIMIT {LIST_OPTIONS}"
                    ).fetchall()
                ]
            if c.kind in ("numeric", "date"):
                lo, hi, nulls = cur.execute(f"SELECT min({k}), max({k}), count(*) - count({k}) FROM {v}").fetchone()
                c.min, c.max, c.nulls = _jsonable(lo), _jsonable(hi), nulls
            elif c.filterable:
                c.nulls = cur.execute(f"SELECT count(*) - count({k}) FROM {v}").fetchone()[0]

    def live_review_options(self) -> list:
        cur = self.cursor()
        return [
            [val, cnt]
            for val, cnt in cur.execute(
                f'SELECT "review.verdict", count(*) FROM {self.view} GROUP BY 1 ORDER BY 2 DESC'
            ).fetchall()
        ]

    # ---- annotation status --------------------------------------------------------------------
    def _ann_status(self) -> list[dict]:
        cur = self.cursor()
        out = []
        corpus_mtime = self.corpus.version.primary.mtime_ns
        for a in self.anns + self.para_anns:
            ann = a.ann
            has_sha = any(n == "text_sha1" for n, _ in a.columns)
            st: dict[str, Any] = {
                "name": ann.name,
                "key": ann.key,
                "level": ann.level,
                "file": ann.file.path.name,
                "size": ann.file.size,
                "mtime": ann.file.mtime_ns / 1e9,
                "rows": a.rows,
                "articles": a.distinct,
                # a joined column says which file it comes from (the sidecar's "joins")
                "columns": [
                    {"name": n, "type": t, **({"from": a.joined[n]} if n in a.joined else {})} for n, t in a.columns
                ],
                "joins": a.joins,
                "meta": ann.meta,
                "sidecar": ann.sidecar.path.name if ann.sidecar else None,
                "not_read": list(ann.not_read),
                "has_text_sha1": has_sha,
                "problems": list(a.problems),
                "warnings": [],
            }
            depends = bool(ann.meta.get("depends_on_text")) if isinstance(ann.meta, dict) else False
            st["depends_on_text"] = depends
            c = self.corpus.table
            matched, orphans = cur.execute(
                f"SELECT count(DISTINCT a.id) FILTER (WHERE c.id IS NOT NULL), "
                f"count(DISTINCT a.id) FILTER (WHERE c.id IS NULL) "
                f"FROM {a.table} a LEFT JOIN {c} c ON c.id = a.id"
            ).fetchone()
            st["matched"], st["orphans"] = matched, orphans
            st["missing"] = self.corpus.n_rows - matched
            if has_sha:
                st["stale"] = cur.execute(
                    f"SELECT count(DISTINCT a.id) FROM {a.table} a JOIN {c} c ON c.id = a.id "
                    f"WHERE a.text_sha1 IS NOT NULL AND a.text_sha1 <> c.{SHA1_COL}"
                ).fetchone()[0]
                st["fresh"] = matched - st["stale"]
            elif ann.level == "paragraph" and any(n == "para_sha1" for n, _ in a.columns):
                # per paragraph: the scored paragraph's sha1 against the paragraph now at that index
                sep = "chr(10) || chr(10)"
                st["stale"], st["stale_paragraphs"] = cur.execute(
                    f"WITH s AS (SELECT id, string_split(text, {sep}) AS ps FROM {c} "
                    f"WHERE id IN (SELECT DISTINCT id FROM {a.table})), "
                    f"now AS (SELECT id, unnest(ps) AS p, generate_subscripts(ps, 1) - 1 AS para FROM s) "
                    f"SELECT count(DISTINCT a.id), count(*) FROM {a.table} a JOIN {c} c ON c.id = a.id "
                    f"LEFT JOIN now ON now.id = a.id AND now.para = a.para "
                    f"WHERE a.para_sha1 IS NOT NULL AND (now.p IS NULL OR sha1(now.p) <> a.para_sha1)"
                ).fetchone()
                has_sha = True
                st["fresh"] = matched - st["stale"]
            else:
                st["stale"] = None
            if ann.level == "paragraph":
                st["out_of_range"] = cur.execute(
                    f"SELECT count(*) FROM {a.table} a JOIN {c} c ON c.id = a.id "
                    f"WHERE a.para < 0 OR a.para >= c.{PARAS_COL}"
                ).fetchone()[0]
            w = st["warnings"]
            if st["stale"] and st.get("stale_paragraphs") is not None:
                w.append(
                    f"{st['stale_paragraphs']:,} paragraphs in {st['stale']:,} articles were scored on a different "
                    "text (para_sha1 mismatch)"
                )
            elif st["stale"]:
                w.append(f"{st['stale']:,} articles were scored on a different text (text_sha1 mismatch)")
            if depends and not has_sha:
                older = ann.file.mtime_ns < corpus_mtime
                w.append(
                    "depends on the text but has no text_sha1 column, so staleness can't be checked"
                    + ("; the file is older than the corpus" if older else "")
                )
            if st["missing"] and ann.level == "article":
                w.append(f"{st['missing']:,} corpus articles have no row")
            if orphans:
                w.append(f"{orphans:,} ids are not in this corpus version")
            if st.get("out_of_range"):
                w.append(f"{st['out_of_range']:,} rows point past the article's last paragraph")
            w.extend(a.problems)
            out.append(st)
        return out

    # ---- paragraph annotations: value scales for shading -------------------------------------
    def para_scales(self) -> dict:
        def build():
            cur = self.cursor()
            out = {}
            for a in self.para_anns:
                scales = {}
                for n, t in a.columns:
                    if is_numeric(t) and n not in ("para",):
                        r = cur.execute(
                            f"SELECT quantile_cont({qi(n)}, [0.02, 0.25, 0.5, 0.75, 0.98]), min({qi(n)}), "
                            f"max({qi(n)}) FROM {a.table}"
                        ).fetchone()
                        if r[0] is not None:
                            scales[n] = {
                                "q": [_jsonable(x) for x in r[0]],
                                "min": _jsonable(r[1]),
                                "max": _jsonable(r[2]),
                            }
                out[a.ann.name] = scales
            return out

        return self.cached("para_scales", build)


def column_descriptions(a: AnnTable) -> dict[str, str]:
    """An annotation's column descriptions: its sidecar's "columns", and for joined columns the
    join's, with the file they come from."""
    meta = a.ann.meta if isinstance(a.ann.meta, dict) else {}
    own = meta.get("columns") if isinstance(meta.get("columns"), dict) else {}
    out = {}
    for j in a.ann.joins:
        for c, d in j.columns.items():
            if a.joined.get(c) == j.path:
                out[c] = f"{d} (from {j.path}, joined on {j.on})" if d else f"from {j.path}, joined on {j.on}"
    out.update({str(k): str(v) for k, v in own.items() if k not in a.joined})
    return out


def _jsonable(x):
    if x is None:
        return None
    if isinstance(x, float):
        return x if x == x and abs(x) != float("inf") else None
    if isinstance(x, (int, str, bool)):
        return x
    return str(x)


class Catalog:
    def __init__(
        self,
        data_root: Path,
        writer: SafeWriter,
        reviews: ReviewStore,
        cache_dir: Path = CACHE_DIR,
        throttle: float = 0.5,
    ):
        self.data_root = Path(data_root).resolve()
        self.writer = writer
        self.reviews = reviews
        self.throttle = throttle
        tmp = writer.mkdir(Path(cache_dir) / "duckdb-tmp")
        self.con = duckdb.connect(
            config={
                "autoinstall_known_extensions": False,
                "autoload_known_extensions": False,
                "temp_directory": str(tmp),
                "extension_directory": str(writer.guard(Path(cache_dir) / "duckdb-ext")),
            }
        )
        self._lock = threading.RLock()
        self._datasets: dict[str, Dataset] = {}
        self._corpora: dict[tuple[str, str], CorpusTable] = {}
        self._anns: dict[tuple[str, str], AnnTable] = {}
        self._joins: dict[tuple[str, str], JoinTable] = {}  # (dataset, joined file's path)
        self._snaps: dict[tuple[str, str], Snapshot] = {}
        self._sides: dict[tuple[str, str], SideTable] = {}  # (dataset, "excluded"|"removed_blocks")
        self._review_tables: dict[str, tuple[str, int, int]] = {}  # ds -> (table, reviews version, gen)
        self._gen = itertools.count(1)
        self._last_check = 0.0
        self._errors: dict[str, str] = {}
        # bump when refresh() sees files change on disk (not when tables load lazily); the page polls
        # them. table_changes leaves out edits that touch only Markdown documents (METHODOLOGY.md,
        # quality/*.md, ...), so an often-edited document doesn't make every list view reload.
        self.changes = 0
        self.table_changes = 0
        self.refresh(force=True)

    # ---- plumbing ------------------------------------------------------------------------------
    def cursor(self):
        return self.con.cursor()

    def _name(self, prefix: str) -> str:
        return f"{prefix}{next(self._gen)}"

    def _drop(self, kind: str, name: str | None) -> None:
        if name:
            try:
                self.cursor().execute(f"DROP {kind} IF EXISTS {name}")
            except duckdb.Error as e:  # pragma: no cover - best effort
                log.warning("could not drop %s %s: %s", kind, name, e)

    # ---- discovery & change detection ----------------------------------------------------------
    def refresh(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_check < self.throttle:
            return
        with self._lock:
            if not force and time.monotonic() - self._last_check < self.throttle:
                return
            self._last_check = time.monotonic()
            names = set()
            for e in sorted(self.data_root.iterdir()) if self.data_root.is_dir() else []:
                if not e.is_dir() or e.name.startswith("."):
                    continue
                sig = discovery.dir_signature(e)
                old = self._datasets.get(e.name)
                if old is not None and old.signature == sig:
                    names.add(e.name)
                    continue
                ds = discovery.discover_dataset(e)
                if ds is None:
                    if old is not None:
                        self._forget(e.name)
                    continue
                names.add(e.name)
                self._datasets[e.name] = ds
                self.changes += 1
                if old is None or not docs_only(old.signature, sig):
                    self.table_changes += 1
                if old is not None:
                    log.info("dataset %s changed on disk; reloading what changed", e.name)
            for gone in set(self._datasets) - names:
                self._forget(gone)

    def _forget(self, name: str) -> None:
        self._datasets.pop(name, None)
        self.changes += 1
        self.table_changes += 1
        for key in [k for k in self._snaps if k[0] == name]:
            snap = self._snaps.pop(key)
            self._drop("VIEW", snap.view)
        for key in [k for k in self._corpora if k[0] == name]:
            ct = self._corpora.pop(key)
            for t in (ct.table, ct.para_table, ct.groups_table):
                self._drop("TABLE", t)
        for key in [k for k in self._anns if k[0] == name]:
            self._drop("TABLE", self._anns.pop(key).table)
        for key in [k for k in self._joins if k[0] == name]:
            self._drop("TABLE", self._joins.pop(key).table)
        for key in [k for k in self._sides if k[0] == name]:
            self._drop("TABLE", self._sides.pop(key).table)

    def datasets(self) -> dict[str, Dataset]:
        self.refresh()
        return dict(self._datasets)

    def dataset(self, name: str) -> Dataset:
        self.refresh()
        ds = self._datasets.get(name)
        if ds is None:
            raise KeyError(name)
        return ds

    def errors(self) -> dict[str, str]:
        return dict(self._errors)

    # ---- loading -------------------------------------------------------------------------------
    def _corpus(self, ds: Dataset, v: Version) -> CorpusTable:
        key = (ds.name, v.stem)
        fs = v.primary
        sig = fs.sig
        ct = self._corpora.get(key)
        if ct is not None and ct.sig == sig:
            return ct
        table = self._name("c")
        t0 = time.time()
        sep = "chr(10) || chr(10)"
        try:
            self.cursor().execute(
                f"CREATE TABLE {table} AS SELECT * REPLACE (CAST(id AS BIGINT) AS id), "
                f"sha1(text) AS {SHA1_COL}, len(string_split(text, {sep})) AS {PARAS_COL} "
                f"FROM {source_sql(self.cursor(), fs, {'text': 'VARCHAR'})} WHERE id IS NOT NULL AND text IS NOT NULL"
            )
        except duckdb.Error as e:
            self._drop("TABLE", table)
            self._errors[f"{ds.name}/{fs.path.name}"] = str(e)
            if ct is not None:
                log.warning("could not reload %s (%s); keeping the previous copy", fs.path, e)
                return ct
            raise DataError(f"could not read {fs.path.name}: {e}") from e
        self._errors.pop(f"{ds.name}/{fs.path.name}", None)
        cols = [
            (r[0], r[1])
            for r in self.cursor().execute(f"DESCRIBE {table}").fetchall()
            if r[0] not in (SHA1_COL, PARAS_COL)
        ]
        n = self.cursor().execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        new = CorpusTable(ds.name, v, sig, table, cols, n, time.time())
        log.info("loaded %s (%d rows) in %.2fs", fs.path.name, n, time.time() - t0)
        if ct is not None:
            for t in (ct.table, ct.para_table, ct.groups_table):
                self._drop("TABLE", t)
        self._corpora[key] = new
        return new

    def _join_table(self, ds: Dataset, j: Join) -> JoinTable | None:
        """The file a join names, as a table, loaded once per file version. A file that can't be
        read keeps its previous copy (the error is listed); None without one."""
        fs = j.file
        assert fs is not None
        key = (ds.name, str(fs.path))
        jt = self._joins.get(key)
        if jt is not None and jt.file.sig == fs.sig:
            return jt
        table = self._name("j")
        err_key = f"{ds.name}/{j.path}"
        t0 = time.time()
        try:
            self.cursor().execute(f"CREATE TABLE {table} AS SELECT * FROM {source_sql(self.cursor(), fs)}")
            types = {r[0]: r[1] for r in self.cursor().execute(f"DESCRIBE {table}").fetchall()}
            rows = self.cursor().execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        except duckdb.Error as e:
            self._drop("TABLE", table)
            self._errors[err_key] = str(e).split("\n")[0]
            log.warning("could not read %s: %s", fs.path, e)
            return jt
        self._errors.pop(err_key, None)
        if jt is not None:
            self._drop("TABLE", jt.table)
        new = JoinTable(fs, table, types, rows)
        self._joins[key] = new
        log.info("loaded %s (%d rows) in %.2fs", j.path, rows, time.time() - t0)
        return new

    def _join_parts(self, ds: Dataset, ann: Annotation, own: dict[str, str]) -> tuple[str, str, list, list]:
        """SQL for an annotation's sidecar joins: (select list, LEFT JOINs, status per declared join,
        problems). `own` holds the annotation file's column types; its own columns win over joined
        ones, and a join is skipped (with a problem) when its file or key is missing. A used join's
        status gets "_table", "_key" and "_cols" for counting matches once the table exists."""
        sel, frm, status, problems = "", "", [], []
        taken = set(own)
        for i, j in enumerate(ann.joins):
            st: dict[str, Any] = {
                "path": j.path,
                "on": j.on,
                "columns": [],
                "declared": list(j.columns),
                "description": j.description,
                "problems": [],
            }
            status.append(st)
            if j.file is None:
                st["problems"].append(j.problem or f"{j.path} can't be joined")
                continue
            jt = self._join_table(ds, j)
            if jt is None:
                st["problems"].append(f"{j.path} could not be read, so {', '.join(j.columns)} are not shown")
                continue
            st.update(rows=jt.rows, size=jt.file.size, mtime=jt.file.mtime_ns / 1e9)
            if j.on not in own or j.on not in jt.types:
                where = ann.file.path.name if j.on not in own else j.path
                st["problems"].append(f"{where} has no {j.on} column to join {j.path} on; not joined")
                continue
            missing = [c for c in j.columns if c not in jt.types]
            if missing:
                st["problems"].append(f"{j.path} has no {', '.join(missing)} column")
            dup = [c for c in j.columns if c in taken and c not in missing]
            if dup:
                st["problems"].append(
                    f"{', '.join(dup)} {'is' if len(dup) == 1 else 'are'} in {ann.file.path.name} itself; its "
                    f"values are shown, not those of {j.path}"
                )
            cols = [c for c in j.columns if c not in missing and c not in dup]
            if not cols:
                continue
            if j.on not in jt.dups:
                jt.dups[j.on] = (
                    self.cursor()
                    .execute(
                        f"SELECT count(*) - count(DISTINCT {qi(j.on)}) FROM {jt.table} WHERE {qi(j.on)} IS NOT NULL"
                    )
                    .fetchone()[0]
                )
            src = jt.table
            if jt.dups[j.on]:
                st["problems"].append(
                    f"{j.path} has {jt.dups[j.on]:,} repeated {j.on} values; the first row of each is used"
                )
                first = f"row_number() OVER (PARTITION BY {qi(j.on)} ORDER BY rowid) = 1"  # in file order
                src = f"(SELECT * FROM {jt.table} QUALIFY {first})"
            alias = f"j{i}"
            cast = own[j.on].upper() != jt.types[j.on].upper()  # e.g. an integer key written as a string elsewhere
            key = (lambda x: f"CAST({x} AS VARCHAR)") if cast else (lambda x: x)
            frm += f" LEFT JOIN {src} {alias} ON {key(f'a.{qi(j.on)}')} = {key(f'{alias}.{qi(j.on)}')}"
            sel += "".join(f", {alias}.{qi(c)} AS {qi(c)}" for c in cols)
            st.update(columns=cols, _table=jt.table, _key=key)
            taken.update(cols)
        for st in status:
            problems.extend(st["problems"])
        return sel, frm, status, problems

    def _ann(self, ds: Dataset, ann: Annotation) -> AnnTable | None:
        key = (ds.name, ann.key)
        sig = (
            ann.file.sig,
            ann.sidecar.sig if ann.sidecar else None,
            tuple((j.path, j.file.sig if j.file else None) for j in ann.joins),
        )
        at = self._anns.get(key)
        if at is not None and at.sig == sig:
            at.ann = ann
            return at
        table = self._name("a")
        keys = ["id", "para"] if ann.level == "paragraph" else ["id"]
        try:
            src = source_sql(self.cursor(), ann.file)
            own = {r[0]: r[1] for r in self.cursor().execute(f"DESCRIBE SELECT * FROM {src}").fetchall()}
            cols = list(own)
            repl = ["CAST(a.id AS BIGINT) AS id"] + (
                ["CAST(a.para AS INTEGER) AS para"] if ann.level == "paragraph" else []
            )
            part = ", ".join(keys)
            jsel, jfrom, jstatus, problems = self._join_parts(ds, ann, own) if ann.joins else ("", "", [], [])
            self.cursor().execute(
                f"CREATE TABLE {table} AS SELECT a.* REPLACE ({', '.join(repl)}){jsel} FROM {src} a{jfrom} "
                "WHERE a.id IS NOT NULL"
            )
            rows = self.cursor().execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            distinct_keys = (
                self.cursor().execute(f"SELECT count(*) FROM (SELECT DISTINCT {part} FROM {table})").fetchone()[0]
            )
            if distinct_keys < rows:
                problems.append(f"{rows - distinct_keys:,} duplicate rows by ({part}); one of each is used")
                dedup = self._name("a")
                self.cursor().execute(
                    f"CREATE TABLE {dedup} AS SELECT * FROM {table} QUALIFY row_number() OVER (PARTITION BY {part}) = 1"
                )
                self._drop("TABLE", table)
                table = dedup
            distinct = self.cursor().execute(f"SELECT count(DISTINCT id) FROM {table}").fetchone()[0]
            joined = {}
            for st in jstatus:  # rows that found their key in the joined file (the rest have its columns null)
                if "_table" in st:
                    k, on, jt_table = st.pop("_key"), qi(st["on"]), st.pop("_table")
                    st["matched"] = (
                        self.cursor()
                        .execute(
                            f"SELECT count(*) FROM {table} a WHERE {k(f'a.{on}')} IN (SELECT {k(on)} FROM {jt_table})"
                        )
                        .fetchone()[0]
                    )
                    st["unmatched"] = distinct_keys - st["matched"]  # the table's rows, after any dedup
                    joined.update(dict.fromkeys(st["columns"], st["path"]))
            if "text_sha1" in cols:
                t = dict((r[0], r[1]) for r in self.cursor().execute(f"DESCRIBE {table}").fetchall())
                if t.get("text_sha1", "").upper() != "VARCHAR":
                    problems.append("text_sha1 is not a string column")
            types = [(r[0], r[1]) for r in self.cursor().execute(f"DESCRIBE {table}").fetchall() if r[0] not in keys]
        except duckdb.Error as e:
            self._drop("TABLE", table)
            self._errors[f"{ds.name}/annotations/{ann.file.path.name}"] = str(e)
            if at is not None:
                return at
            log.warning("skipping annotation %s: %s", ann.file.path, e)
            return None
        self._errors.pop(f"{ds.name}/annotations/{ann.file.path.name}", None)
        if at is not None:
            self._drop("TABLE", at.table)
        new = AnnTable(ann, sig, table, types, rows, distinct, problems, joined, jstatus)
        self._anns[key] = new
        return new

    def side_table(self, ds: Dataset, kind: str) -> SideTable | None:
        """excluded.jsonl ("excluded") or removed-blocks.jsonl ("removed_blocks") as a table,
        loaded once per file version. A file that can't be read keeps its previous copy (the
        error is listed); None when the dataset has no such file."""
        key = (ds.name, kind)
        fs = ds.side(kind)
        with self._lock:
            st = self._sides.get(key)
            if fs is None:
                if st is not None:
                    self._drop("TABLE", self._sides.pop(key).table)
                return None
            if st is not None and st.file.sig == fs.sig:
                return st
            want = SIDE_TYPES[kind]
            table = self._name("s")
            err_key = f"{ds.name}/{fs.path.name}"
            try:
                types = json_types(self.cursor(), fs, want)
                missing = [c for c in want if c not in types]
                fill = "".join(f", CAST(NULL AS {t}) AS {qi(c)}" for c, t in want.items() if c in missing)
                self.cursor().execute(f"CREATE TABLE {table} AS SELECT *{fill} FROM {reader_sql(fs, types)}")
                cols = [(r[0], r[1]) for r in self.cursor().execute(f"DESCRIBE {table}").fetchall()]
                rows = self.cursor().execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            except duckdb.Error as e:
                self._drop("TABLE", table)
                self._errors[err_key] = str(e).split("\n")[0]
                log.warning("could not read %s: %s", fs.path, e)
                return st
            self._errors.pop(err_key, None)
            problems = [f"{fs.path.name} has no {', '.join(missing)} field"] if missing else []
            if st is not None:
                self._drop("TABLE", st.table)
            new = SideTable(kind, fs, table, cols, rows, problems)
            self._sides[key] = new
            log.info("loaded %s (%d rows)", fs.path.name, rows)
            return new

    def _review_table(self, ds_name: str) -> str:
        """The table of current decisions for a dataset, rebuilt when the log changes."""
        version = self.reviews.version()
        cur = self._review_tables.get(ds_name)
        if cur is not None and cur[1] == version:
            return cur[0]
        table = cur[0] if cur else self._name("r")
        events = self.reviews.current(ds_name)
        ids, verdicts, notes, dropped, ts, sha = [], [], [], [], [], []
        for i, ev in events.items():
            ids.append(i)
            verdicts.append(ev["verdict"])
            notes.append(ev["note"] or None)
            dropped.append(len(ev["drop_paragraphs"]))
            ts.append(ev["ts"])
            sha.append(ev["text_sha1"])
        arrow = pa.table(
            {
                "id": pa.array(ids, pa.int64()),
                "verdict": pa.array(verdicts, pa.string()),
                "note": pa.array(notes, pa.string()),
                "dropped": pa.array(dropped, pa.int32()),
                "ts": pa.array(ts, pa.string()),
                "text_sha1": pa.array(sha, pa.string()),
            }
        )
        with self._lock:
            c = self.con.cursor()
            c.register("__reviews_in", arrow)
            c.execute(f"CREATE OR REPLACE TABLE {table} AS SELECT * FROM __reviews_in")
            c.unregister("__reviews_in")
            self._review_tables[ds_name] = (table, version, 0)
        return table

    def snapshot(self, ds_name: str, stem: str | None = None) -> Snapshot:
        ds = self.dataset(ds_name)
        try:
            v = ds.version(stem)
        except KeyError:
            raise KeyError(f"{ds_name} has no version {stem!r}") from None
        with self._lock:
            corpus = self._corpus(ds, v)
            anns, para_anns = [], []
            for ann in ds.annotations:
                at = self._ann(ds, ann)
                if at is not None:
                    (para_anns if ann.level == "paragraph" else anns).append(at)
            # drop tables of annotation files that disappeared
            live = {(ds.name, a.key) for a in ds.annotations}
            for key in [k for k in self._anns if k[0] == ds.name and k not in live]:
                self._drop("TABLE", self._anns.pop(key).table)
            # and of joined files that no sidecar names any more
            joined = {(ds.name, str(j.file.path)) for a in ds.annotations for j in a.joins if j.file}
            for key in [k for k in self._joins if k[0] == ds.name and k not in joined]:
                self._drop("TABLE", self._joins.pop(key).table)
            rtable = self._review_table(ds.name)
            snap_key = (ds.name, v.stem)
            sig = (corpus.table, tuple(a.table for a in anns), tuple(a.table for a in para_anns), rtable)
            snap = self._snaps.get(snap_key)
            if snap is not None and getattr(snap, "_sig", None) == sig:
                snap.ds = ds  # same tables, but docs and reports may have changed
                return snap
            view = self._name("v")
            self._create_view(view, corpus, anns, rtable)
            snap = Snapshot(self, ds, corpus, anns, para_anns, view, rtable)
            snap._sig = sig  # type: ignore[attr-defined]
            old = self._snaps.get(snap_key)
            self._snaps[snap_key] = snap
            if old is not None:
                self._drop("VIEW", old.view)
            return snap

    def _create_view(self, view: str, corpus: CorpusTable, anns: list[AnnTable], rtable: str) -> None:
        sel = ["c.*"]
        pkey = "paragraphs" if not corpus.has("paragraphs") else "_paragraphs"
        sel.append(f"c.{PARAS_COL} AS {qi(pkey)}")
        joins = []
        for i, a in enumerate(anns):
            alias = f"a{i}"
            for n, _ in a.columns:
                sel.append(f"{alias}.{qi(n)} AS {qi(a.ann.name + '.' + n)}")
            sel.append(f"({alias}.id IS NOT NULL) AS {qi(a.ann.name + '._present')}")
            if any(n == "text_sha1" for n, _ in a.columns):
                sel.append(
                    f"({alias}.text_sha1 IS NOT NULL AND {alias}.text_sha1 <> c.{SHA1_COL}) "
                    f"AS {qi(a.ann.name + '._stale')}"
                )
            joins.append(f"LEFT JOIN {a.table} {alias} ON {alias}.id = c.id")
        sel += [
            'r.verdict AS "review.verdict"',
            'r.note AS "review.note"',
            'r.dropped AS "review.dropped"',
            'r.ts AS "review.ts"',
            f'(r.text_sha1 IS NOT NULL AND r.text_sha1 <> c.{SHA1_COL}) AS "review.stale"',
        ]
        joins.append(f"LEFT JOIN {rtable} r ON r.id = c.id")
        self.cursor().execute(
            f"CREATE OR REPLACE VIEW {view} AS SELECT {', '.join(sel)} FROM {corpus.table} c {' '.join(joins)}"
        )

    # ---- paragraphs (lazy; for Patterns and bulk actions) --------------------------------------
    def paragraph_table(self, corpus: CorpusTable) -> str:
        """id, para, kind, p (the text), chars, nkey (group key of the normalised text), norm."""
        with corpus.lock:
            if corpus.para_table:
                return corpus.para_table
            table = self._name("pp")
            sep = "chr(10) || chr(10)"
            digits = "'[0-9୦-୯]+'"
            t0 = time.time()
            # masked title (digits -> #) and the title without a trailing "(...)" qualifier;
            # a title is only masked if it has at least two characters that are not digits,
            # spaces or punctuation (otherwise "#" would be replaced everywhere)
            self.cursor().execute(f"""
                CREATE TABLE {table} AS
                WITH s AS (
                    SELECT id,
                           regexp_replace(title, {digits}, '#', 'g') AS t1,
                           regexp_replace(regexp_replace(title, '\\s*\\([^)]*\\)\\s*$', ''), {digits}, '#', 'g')
                             AS t2,
                           string_split(text, {sep}) AS ps
                    FROM {corpus.table}),
                u AS (
                    SELECT id, t1, t2, unnest(ps) AS p, generate_subscripts(ps, 1) - 1 AS para FROM s),
                m AS (
                    SELECT id, para, p, t1, t2, regexp_replace(p, {digits}, '#', 'g') AS n0 FROM u),
                n AS (
                    SELECT id, para, p,
                      CASE WHEN length(regexp_replace(t2, '[#\\s[:punct:]]', '', 'g')) >= 2 AND t2 <> t1
                           THEN replace(
                             CASE WHEN length(regexp_replace(t1, '[#\\s[:punct:]]', '', 'g')) >= 2
                                  THEN replace(n0, t1, '⟨title⟩') ELSE n0 END, t2, '⟨title⟩')
                           ELSE CASE WHEN length(regexp_replace(t1, '[#\\s[:punct:]]', '', 'g')) >= 2
                                  THEN replace(n0, t1, '⟨title⟩') ELSE n0 END
                      END AS norm
                    FROM m)
                SELECT id, para, {paragraphs.KIND_SQL} AS kind, p, length(p) AS chars,
                       substr(sha1(norm), 1, 16) AS nkey, norm
                FROM n""")
            log.info("paragraph table for %s built in %.2fs", corpus.version.stem, time.time() - t0)
            corpus.para_table = table
            return table

    def groups_table(self, corpus: CorpusTable) -> str:
        """Repeated paragraphs: one row per normalised text found in at least two articles."""
        pt = self.paragraph_table(corpus)
        with corpus.lock:
            if corpus.groups_table:
                return corpus.groups_table
            table = self._name("pg")
            t0 = time.time()
            self.cursor().execute(f"""
                CREATE TABLE {table} AS
                SELECT nkey, any_value(norm) AS norm, mode(kind) AS kind, count(*) AS paragraphs,
                       count(DISTINCT id) AS articles, avg(chars)::INTEGER AS chars,
                       count(DISTINCT p) AS variants
                FROM {pt} GROUP BY nkey HAVING count(DISTINCT id) >= 2""")
            log.info("repeated-paragraph groups for %s built in %.2fs", corpus.version.stem, time.time() - t0)
            corpus.groups_table = table
            return table


def text_sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def dumps(o) -> str:
    return json.dumps(o, ensure_ascii=False)
