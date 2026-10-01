"""Schema-driven filters and the Browse query, as parameterised SQL.

Filter parameters (the same names in the page URL and in the API query string):

    is.<col>=true|false        boolean (absent = either)
    min.<col>=x, max.<col>=y   numeric or date range, inclusive
    in.<col>=a&in.<col>=b      one of these values (low-cardinality strings); "__null__" = missing
    any.<col>=a&any.<col>=b    list column contains any of these values
    has.<col>=s                string contains s (case-insensitive)
    null.<col>=1|0             value missing / present

    q=<s>                      title contains s (case-insensitive)
    text=<s>&mode=substr|regex&icase=1   full-text search over `text` (regex is RE2 syntax)
    pat=<key>                  articles containing a paragraph of that repeated-paragraph group
    sort=<col>&dir=asc|desc    ("_matches" sorts by the number of text matches)
    unrev=1                    unreviewed articles (no verdict) first
    cols=a,b,c                 columns to show; page=1&size=50

Column names only ever come from the discovered schema (a whitelist) and are quoted; every value
is a bound parameter. Anything else is a FilterError (HTTP 400).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

OPS = ("is", "min", "max", "in", "any", "has", "null")
NULL_TOKEN = "__null__"
MAX_PAGE_SIZE = 500


class FilterError(ValueError):
    pass


def qi(ident: str) -> str:
    """Quote an SQL identifier."""
    return '"' + ident.replace('"', '""') + '"'


@dataclass
class Column:
    key: str  # the name used in URLs and the API ("words", "bpb.bpb", "review.verdict")
    dtype: str  # DuckDB type
    kind: str  # bool | numeric | category | list | text | date | hash | other
    source: str  # "corpus", an annotation name, "review" or "derived"
    label: str = ""
    description: str = ""
    filterable: bool = True
    sortable: bool = True
    options: list | None = None  # [[value, count], ...] for category / list / bool
    min: Any = None
    max: Any = None
    nulls: int = 0

    def public(self) -> dict:
        return {
            k: getattr(self, k)
            for k in (
                "key",
                "dtype",
                "kind",
                "source",
                "label",
                "description",
                "filterable",
                "sortable",
                "options",
                "min",
                "max",
                "nulls",
            )
        }


class Params:
    """Named query parameters ($p0, $p1, ...), so their order in the SQL never matters."""

    def __init__(self):
        self.values: dict[str, Any] = {}

    def add(self, v: Any) -> str:
        name = f"p{len(self.values)}"
        self.values[name] = v
        return "$" + name


def _num(v: str, key: str) -> float:
    try:
        x = float(v)
    except ValueError:
        raise FilterError(f"{key}: {v!r} is not a number") from None
    if math.isnan(x):
        raise FilterError(f"{key}: NaN is not a bound")
    return x


def _column(columns: dict[str, Column], key: str, kinds: tuple[str, ...], op: str) -> Column:
    col = columns.get(key)
    if col is None:
        raise FilterError(f"unknown column {key!r}")
    if not col.filterable or col.kind not in kinds:
        raise FilterError(f"{op}.{key}: not available for a {col.kind} column")
    return col


def filter_clauses(items: list[tuple[str, str]], columns: dict[str, Column], p: Params) -> list[str]:
    """SQL conditions for the filter parameters in `items` (other parameters are ignored)."""
    grouped: dict[tuple[str, str], list[str]] = {}
    for k, v in items:
        op, dot, key = k.partition(".")
        if not dot or op not in OPS:
            continue
        if v is None or v == "":
            continue
        grouped.setdefault((op, key), []).append(v)
    where: list[str] = []
    for (op, key), vals in grouped.items():
        if op == "is":
            col = _column(columns, key, ("bool",), op)
            v = vals[-1].lower()
            if v not in ("true", "false"):
                raise FilterError(f"is.{key}: expected true or false, got {vals[-1]!r}")
            where.append(f"{qi(col.key)} = {p.add(v == 'true')}")
        elif op in ("min", "max"):
            col = _column(columns, key, ("numeric", "date"), op)
            cmp = ">=" if op == "min" else "<="
            v = vals[-1]
            if col.kind == "numeric":
                where.append(f"{qi(col.key)} {cmp} {p.add(_num(v, key))}")
            elif col.dtype.upper() == "VARCHAR":  # ISO-8601 strings compare as text
                where.append(f"{qi(col.key)} {cmp} {p.add(v)}")
            else:
                where.append(f"CAST({qi(col.key)} AS VARCHAR) {cmp} {p.add(v)}")
        elif op == "in":
            col = _column(columns, key, ("category", "text", "bool"), op)
            real = [v for v in vals if v != NULL_TOKEN]
            parts = []
            if real:
                parts.append(f"CAST({qi(col.key)} AS VARCHAR) IN ({', '.join(p.add(v) for v in real)})")
            if len(real) != len(vals):
                parts.append(f"{qi(col.key)} IS NULL")
            where.append("(" + " OR ".join(parts) + ")")
        elif op == "any":
            col = _column(columns, key, ("list",), op)
            where.append(f"list_has_any(CAST({qi(col.key)} AS VARCHAR[]), CAST({p.add(vals)} AS VARCHAR[]))")
        elif op == "has":
            col = _column(columns, key, ("text", "category", "date", "list"), op)
            where.append(f"contains(lower(CAST({qi(col.key)} AS VARCHAR)), lower({p.add(vals[-1])}))")
        elif op == "null":
            col = columns.get(key)
            if col is None:
                raise FilterError(f"unknown column {key!r}")
            v = vals[-1]
            if v not in ("0", "1"):
                raise FilterError(f"null.{key}: expected 1 or 0")
            where.append(f"{qi(col.key)} IS {'' if v == '1' else 'NOT '}NULL")
    return where


@dataclass
class TextSearch:
    where: str
    matches: str  # expression counting matches in one row


def text_search(query: str, mode: str, icase: bool, p: Params, column: str = "text") -> TextSearch:
    c = qi(column)
    if mode == "regex":
        opts = "i" if icase else ""
        pat = p.add(query)
        o = p.add(opts)
        return TextSearch(f"regexp_matches({c}, {pat}, {o})", f"len(regexp_extract_all({c}, {pat}, 0, {o}))")
    if mode != "substr":
        raise FilterError(f"mode must be substr or regex, not {mode!r}")
    s = p.add(query.lower() if icase else query)
    hay = f"lower({c})" if icase else c
    return TextSearch(f"contains({hay}, {s})", f"((length({hay}) - length(replace({hay}, {s}, ''))) // length({s}))")


def get(items: list[tuple[str, str]], name: str, default: str | None = None) -> str | None:
    for k, v in reversed(items):
        if k == name:
            return v
    return default


@dataclass
class BrowseQuery:
    where: list[str]
    params: Params
    order: str
    matches: str | None
    select_keys: list[str]
    page: int
    size: int
    search: dict = field(default_factory=dict)

    def where_sql(self) -> str:
        return ("WHERE " + " AND ".join(self.where)) if self.where else ""


def build_browse(
    items: list[tuple[str, str]],
    columns: dict[str, Column],
    *,
    id_col: str = "id",
    title_col: str | None = "title",
    verdict_col: str | None = "review.verdict",
    default_cols: list[str] | None = None,
    pattern_sql: str | None = None,
) -> BrowseQuery:
    """Turn the browse parameters into WHERE/ORDER BY pieces for SELECT ... FROM <view>.

    `pattern_sql`, if given, is an SQL expression with one $pat parameter placeholder written as
    "{pat}" that yields the ids of articles containing a paragraph of the requested group."""
    p = Params()
    where = filter_clauses(items, columns, p)

    q = get(items, "q")
    if q and title_col:
        where.append(f"contains(lower({qi(title_col)}), lower({p.add(q)}))")

    text = get(items, "text")
    matches = None
    search = {}
    if text:
        mode = get(items, "mode", "substr") or "substr"
        icase = get(items, "icase") == "1"
        ts = text_search(text, mode, icase, p)
        where.append(ts.where)
        matches = ts.matches
        search = {"text": text, "mode": mode, "icase": icase}

    pat = get(items, "pat")
    if pat and pattern_sql:
        where.append(f"{qi(id_col)} IN ({pattern_sql.format(pat=p.add(pat))})")

    sort = get(items, "sort") or ("_matches" if matches else (title_col or id_col))
    direction = (get(items, "dir") or ("desc" if sort == "_matches" else "asc")).lower()
    if direction not in ("asc", "desc"):
        raise FilterError("dir must be asc or desc")
    if sort == "_matches":
        if not matches:
            sort, sort_sql = id_col, qi(id_col)
        else:
            sort_sql = "__matches"
    else:
        col = columns.get(sort)
        if col is None or not col.sortable:
            raise FilterError(f"cannot sort by {sort!r}")
        sort_sql = qi(col.key)
    order = [f"{sort_sql} {direction.upper()} NULLS LAST"]
    if get(items, "unrev") == "1" and verdict_col and verdict_col in columns:
        order.insert(0, f"({qi(verdict_col)} IS NOT NULL) ASC")
    if sort != id_col:
        order.append(f"{qi(id_col)} ASC")

    cols_param = get(items, "cols")
    if cols_param:
        keys = [k for k in cols_param.split(",") if k]
    else:
        keys = list(default_cols or [])
    for k in keys:
        if k not in columns:
            raise FilterError(f"unknown column {k!r}")
    select = [id_col] + ([title_col] if title_col else [])
    select += [k for k in keys if k not in select]
    if verdict_col and verdict_col in columns and verdict_col not in select:
        select.append(verdict_col)

    try:
        page = max(1, int(get(items, "page", "1") or 1))
        size = min(MAX_PAGE_SIZE, max(1, int(get(items, "size", "50") or 50)))
    except ValueError:
        raise FilterError("page and size must be integers") from None

    return BrowseQuery(where, p, ", ".join(order), matches, select, page, size, search)
