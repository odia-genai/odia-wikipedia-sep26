"""What the API does, independent of HTTP: browse, article, reviews, patterns, bulk actions."""

from __future__ import annotations

import bisect
import hashlib
import json
import math
import re
import threading
import time
from collections import OrderedDict
from typing import Any
from urllib.parse import urlsplit

import duckdb

from . import paragraphs as P
from . import render, reviewfirst
from .discovery import REVIEW_FIRST
from .filters import FilterError, Params, build_browse, get, qi
from .render import LinkContext, render_doc
from .reviews import FIELDS, ReviewStore, is_empty, make_event, now_iso, same_decision
from .store import SHA1_COL, Catalog, DataError, SideTable, Snapshot, _jsonable, column_descriptions, is_numeric

SNIPPET_CONTEXT = 70
SNIPPETS_PER_ROW = 3
MAX_BULK = 25_000
# "same text as id 1234 (title)": a detail that names a page id (the same form documents use)
ID_REF = re.compile(r"(?<![\w])id (\d+)")
# sort keys of the Excluded view's tables: (whitelisted) key -> ORDER BY columns after the first
EXCLUDED_SORTS = {"reason": ["title", "id"], "title": ["id"], "id": [], "revid": ["id"]}
BLOCK_SORTS = {
    "id": ["reason", "kind"],
    "title": ["id"],
    "reason": ["kind", "id"],
    "kind": ["reason", "id"],
    "chars": ["id"],
}
ID_QUERY = re.compile(r"[0-9]{1,18}")  # a search that is a page id (fits a BIGINT)
DUMP_WIKI = re.compile(r"^([a-z][a-z0-9_-]*?)wiki-")  # "orwiki-20260901-pages-articles.xml.bz2"


def paging(items: list[tuple[str, str]], default: int = 100) -> tuple[int, int]:
    try:
        page = max(1, int(get(items, "page", "1") or 1))
        size = min(500, max(1, int(get(items, "size", str(default)) or default)))
    except ValueError:
        raise FilterError("page and size must be integers") from None
    return page, size


def order_by(
    items: list[tuple[str, str]], sorts: dict[str, list[str]], default: str, exprs: dict[str, str] | None = None
) -> str:
    """ORDER BY for a whitelisted sort key (`exprs` maps a key to its SQL, else the quoted
    column); the direction applies to the key, ties go ascending."""
    key = get(items, "sort") or default
    if key not in sorts:
        raise FilterError(f"sort must be one of {', '.join(sorts)}")
    d = "DESC" if (get(items, "dir") or "").lower() == "desc" else "ASC"

    def col(c: str) -> str:
        return (exprs or {}).get(c) or qi(c)

    return ", ".join([f"{col(key)} {d} NULLS LAST", *(f"{col(c)} ASC" for c in sorts[key])])


class NotFound(LookupError):
    pass


class Conflict(RuntimeError):
    pass


def jsonable_row(names: list[str], row: tuple) -> dict:
    out = {}
    for n, v in zip(names, row, strict=True):
        if isinstance(v, float):
            v = v if math.isfinite(v) else None
        elif isinstance(v, (list, tuple)):
            v = [_jsonable(x) for x in v]
        elif isinstance(v, dict):
            v = {str(k): _jsonable(x) for k, x in v.items()}
        elif v is not None and not isinstance(v, (int, str, bool)):
            v = str(v)
        out[n] = v
    return out


def _duck_error(e: Exception) -> FilterError:
    msg = str(e).split("\n")[0]
    return FilterError(msg)


class Service:
    def __init__(self, catalog: Catalog, reviews: ReviewStore, sources: list[dict] | None = None):
        self.cat = catalog
        self.reviews = reviews
        self.sources = list(sources or [])  # the Hugging Face datasets behind the data root (none: a local folder)
        self._id_cache: OrderedDict[tuple, list[tuple[int, bool]]] = OrderedDict()
        self._id_lock = threading.Lock()  # requests run on a thread pool

    # ---- helpers ---------------------------------------------------------------------------
    def snap(self, ds: str, v: str | None) -> Snapshot:
        try:
            return self.cat.snapshot(ds, v or None)
        except KeyError as e:
            raise NotFound(str(e)) from None

    def default_cols(self, snap: Snapshot) -> list[str]:
        cols = snap.columns
        base = [c for c in ("words", "odia_ratio", "templated_share", "paragraphs", "bot_created", "stub") if c in cols]
        # plus the first few numeric / category / bool / list columns of each annotation
        for a in snap.anns:
            picked = 0
            for n, _ in a.columns:
                c = cols.get(f"{a.ann.name}.{n}")
                if c and c.filterable and c.kind in ("numeric", "category", "bool", "list") and picked < 3:
                    base.append(c.key)
                    picked += 1
        return base

    def pattern_sql(self, snap: Snapshot) -> str:
        pt = self.cat.paragraph_table(snap.corpus)
        return f"SELECT DISTINCT id FROM {pt} WHERE nkey = {{pat}}"

    # ---- schema ----------------------------------------------------------------------------
    def schema(self, snap: Snapshot) -> dict:
        cols = [c.public() for c in snap.columns.values()]
        live = snap.live_review_options()
        for c in cols:
            if c["key"] == "review.verdict":
                c["options"] = [[v if v is not None else "__null__", n] for v, n in live]
        return {
            "columns": cols,
            "default_cols": self.default_cols(snap),
            "version": snap.version.stem,
            "versions": [v.stem for v in snap.ds.versions],
        }

    # ---- browse ----------------------------------------------------------------------------
    def browse(self, snap: Snapshot, items: list[tuple[str, str]]) -> dict:
        t0 = time.time()
        pat = get(items, "pat")
        bq = build_browse(
            items,
            snap.columns,
            default_cols=self.default_cols(snap),
            pattern_sql=self.pattern_sql(snap) if pat else None,
        )
        sel = ", ".join(qi(k) for k in bq.select_keys)
        sql = (
            f"SELECT {sel}, count(*) OVER () AS __total "
            f"FROM {snap.view} {bq.where_sql()} ORDER BY {bq.order} "
            f"LIMIT {bq.size} OFFSET {(bq.page - 1) * bq.size}"
        )
        if bq.matches:
            # the match count is computed once per row, in a subquery, then summed over all rows
            sql = (
                f"SELECT *, count(*) OVER () AS __total, sum(__matches) OVER () AS __total_matches FROM ("
                f"SELECT {sel}, {bq.matches} AS __matches FROM {snap.view} {bq.where_sql()}) "
                f"ORDER BY {bq.order} LIMIT {bq.size} OFFSET {(bq.page - 1) * bq.size}"
            )
        cur = snap.cursor()
        try:
            res = cur.execute(sql, bq.params.values)
            names = [d[0] for d in res.description]
            rows = res.fetchall()
        except duckdb.Error as e:
            raise _duck_error(e) from None
        total = rows[0][names.index("__total")] if rows else self._count(snap, bq)
        total_matches = rows[0][names.index("__total_matches")] if rows and bq.matches else None
        out_rows = []
        for r in rows:
            d = jsonable_row(names, r)
            d.pop("__total", None)
            d.pop("__total_matches", None)
            out_rows.append(d)
        if bq.search and out_rows:
            self._snippets(snap, out_rows, bq.search)
        return {
            "rows": out_rows,
            "total": total,
            "total_matches": total_matches,
            "page": bq.page,
            "size": bq.size,
            "columns": bq.select_keys,
            "search": bq.search,
            "elapsed_ms": round((time.time() - t0) * 1000, 1),
        }

    def _count(self, snap: Snapshot, bq) -> int:
        cur = snap.cursor()
        return cur.execute(f"SELECT count(*) FROM {snap.view} {bq.where_sql()}", bq.params.values).fetchone()[0]

    def _snippets(self, snap: Snapshot, rows: list[dict], search: dict) -> None:
        """Keyword-in-context snippets for the rows on this page. Regex matches come from DuckDB
        (RE2, the same engine that filtered the rows) and are then located in the text."""
        ids = [r["id"] for r in rows]
        cur = snap.cursor()
        params: dict[str, Any] = {"ids": ids}
        if search["mode"] == "regex":
            params.update(pat=search["text"], opts="i" if search["icase"] else "")
            res = cur.execute(
                f"SELECT id, text, regexp_extract_all(text, $pat, 0, $opts)[1:{SNIPPETS_PER_ROW * 4}] "
                f"FROM {snap.corpus.table} WHERE list_contains($ids, id)",
                params,
            ).fetchall()
        else:
            res = [
                (i, t, None)
                for i, t in cur.execute(
                    f"SELECT id, text FROM {snap.corpus.table} WHERE list_contains($ids, id)", params
                ).fetchall()
            ]
        by_id = {i: (t, m) for i, t, m in res}
        for r in rows:
            text, found = by_id.get(r["id"], ("", None))
            r["snippets"] = kwic(text, search, found)

    def id_list(self, snap: Snapshot, items: list[tuple[str, str]]) -> list[tuple[int, bool]]:
        """(id, has verdict) of every row of a browse query, in order (cached)."""
        keep = [(k, v) for k, v in items if k not in ("page", "size", "cols", "id", "auto", "v")]
        key = (snap.view, self.reviews.version(), tuple(sorted(keep)))
        with self._id_lock:
            if key in self._id_cache:
                self._id_cache.move_to_end(key)
                return self._id_cache[key]
        pat = get(items, "pat")
        bq = build_browse(keep, snap.columns, pattern_sql=self.pattern_sql(snap) if pat else None)
        m = f", {bq.matches} AS __matches" if bq.matches else ""
        sql = (
            f'SELECT id, "review.verdict" IS NOT NULL FROM (SELECT *{m} FROM {snap.view} '
            f"{bq.where_sql()}) ORDER BY {bq.order}"
        )
        try:
            ids = [(i, bool(s)) for i, s in snap.cursor().execute(sql, bq.params.values).fetchall()]
        except duckdb.Error as e:
            raise _duck_error(e) from None
        with self._id_lock:
            self._id_cache[key] = ids
            while len(self._id_cache) > 24:
                self._id_cache.popitem(last=False)
        return ids

    def position(self, snap: Snapshot, id: int, items: list[tuple[str, str]]) -> dict:
        ids = self.id_list(snap, items)
        idx = next((i for i, (x, _) in enumerate(ids) if x == id), None)
        out: dict[str, Any] = {
            "total": len(ids),
            "index": idx,
            "prev": None,
            "next": None,
            "next_unreviewed": None,
            "reviewed": sum(1 for _, s in ids if s),
        }
        if idx is not None:
            out["prev"] = ids[idx - 1][0] if idx > 0 else None
            out["next"] = ids[idx + 1][0] if idx + 1 < len(ids) else None
            nxt = [x for x, s in ids[idx + 1 :] if not s and x != id] or [x for x, s in ids[:idx] if not s]
            out["next_unreviewed"] = nxt[0] if nxt else None
        else:
            un = [x for x, s in ids if not s and x != id]
            out["next_unreviewed"] = un[0] if un else None
            out["next"] = ids[0][0] if ids else None
        return out

    # ---- article ---------------------------------------------------------------------------
    def article_row(self, snap: Snapshot, id: int) -> dict:
        cur = snap.cursor()
        res = cur.execute(f"SELECT * FROM {snap.view} WHERE id = $id", {"id": id})
        names = [d[0] for d in res.description]
        row = res.fetchone()
        if row is None:
            raise NotFound(f"no article with id {id} in {snap.ds.name}/{snap.version.stem}")
        d = jsonable_row(names, row)
        d["__sha1"] = cur.execute(f"SELECT {SHA1_COL} FROM {snap.corpus.table} WHERE id = $id", {"id": id}).fetchone()[
            0
        ]
        return d

    def article(self, snap: Snapshot, id: int) -> dict:
        row = self.article_row(snap, id)
        text = row.pop("text")
        text_sha1 = row.pop("__sha1")
        paras = P.split(text)
        blocks = [
            {"i": i, "kind": P.kind(p), "sha1": P.sha1(p), "chars": len(p), "html": render.render(p)}
            for i, p in enumerate(paras)
        ]
        meta, annotations = {}, {}
        for k, v in row.items():
            c = snap.columns.get(k)
            if c is None:
                continue
            if c.source in ("corpus", "derived"):
                meta[k] = v
            elif c.source == "review":
                continue
            else:
                annotations.setdefault(c.source, {})[k[len(c.source) + 1 :]] = v
        # paragraph-level annotations
        para_anns = {}
        scales = snap.para_scales()
        cur = snap.cursor()
        for a in snap.para_anns:
            res = cur.execute(f"SELECT * FROM {a.table} WHERE id = $id ORDER BY para", {"id": id})
            names = [d[0] for d in res.description]
            rows = [jsonable_row(names, r) for r in res.fetchall()]
            stale = None
            if any(n == "text_sha1" for n, _ in a.columns):
                stale = any(r.get("text_sha1") not in (None, text_sha1) for r in rows)
            elif any(n == "para_sha1" for n, _ in a.columns):
                # per paragraph: was this paragraph's current text the one scored?
                hashes = [b["sha1"] for b in blocks]
                for r in rows:
                    p = r.get("para")
                    r["_stale"] = r.get("para_sha1") is not None and (
                        not isinstance(p, int) or p >= len(hashes) or hashes[p] != r["para_sha1"]
                    )
                stale = any(r["_stale"] for r in rows)
            desc = column_descriptions(a)
            # the primary column is the numeric one named like the annotation (bpb.paragraphs -> bpb): a row
            # whose primary value is null was not scored (e.g. a text new since the last scoring run)
            primary = next((n for n, t in a.columns if n == a.ann.name and is_numeric(t)), None)
            para_anns[a.ann.name] = {
                "columns": [
                    {
                        "name": n,
                        "type": t,
                        "numeric": is_numeric(t),
                        "description": desc.get(n, ""),
                        **({"from": a.joined[n]} if n in a.joined else {}),
                    }
                    for n, t in a.columns
                ],
                "rows": {str(r["para"]): {k: v for k, v in r.items() if k not in ("id", "para")} for r in rows},
                "scales": scales.get(a.ann.name, {}),
                "stale": stale,
                "primary": primary,
                "unscored": sum(r.get(primary) is None for r in rows) if primary else 0,
                "description": a.ann.meta.get("description", "") if isinstance(a.ann.meta, dict) else "",
            }
        review = self.review_state(snap.ds.name, id, paras, text_sha1)
        ds_dir = snap.ds.dir
        md_file = meta.get("file")  # optional: the Markdown export (`file` column) may be absent
        md_path = str((ds_dir / md_file).resolve()) if isinstance(md_file, str) else None
        revid = meta.get("revid")
        origin = self.wiki_origin(snap)
        links = {
            "url": meta.get("url"),
            "revision": f"{origin}/w/index.php?oldid={revid}" if revid and origin else None,
            "file": md_file,
            "file_abs": md_path,
            "file_exists": bool(md_path) and (ds_dir / md_file).exists(),
        }
        return {
            "id": id,
            "title": meta.get("title"),
            "dataset": snap.ds.name,
            "version": snap.version.stem,
            "meta": meta,
            "annotations": annotations,
            "ann_stale": self._ann_stale(snap, annotations, text_sha1),
            # article-level annotations whose primary column (numeric, named like the annotation) is null
            "ann_unscored": [
                a.ann.name
                for a in snap.anns
                if any(n == a.ann.name and is_numeric(t) for n, t in a.columns)
                and annotations.get(a.ann.name, {}).get(a.ann.name) is None
            ],
            "text": text,
            "text_sha1": text_sha1,
            "paragraphs": blocks,
            "para_annotations": para_anns,
            "review": review,
            "links": links,
            "removed_blocks": self._blocks_of(snap, id),
        }

    def _blocks_of(self, snap: Snapshot, id: int) -> dict | None:
        """How many blocks the build cut out of this article (removed-blocks.jsonl), by reason."""
        st = self.cat.side_table(snap.ds, "removed_blocks")
        if st is None:
            return None
        by = (
            snap.cursor()
            .execute(
                f"SELECT reason, count(*) FROM {st.table} WHERE id = $id GROUP BY 1 ORDER BY 2 DESC, 1", {"id": id}
            )
            .fetchall()
        )
        return {"total": sum(n for _, n in by), "by_reason": [[r, n] for r, n in by]}

    def wiki_origin(self, snap: Snapshot) -> str | None:
        """The wiki the corpus came from ("https://or.wikipedia.org"): the origin of the corpus's
        `url` column, else from the dump name in the build file ("orwiki-..."), else None."""

        def build():
            if snap.corpus.has("url"):
                row = (
                    snap.cursor()
                    .execute(f"SELECT url FROM {snap.corpus.table} WHERE url LIKE 'http%' LIMIT 1")
                    .fetchone()
                )
                if row:
                    u = urlsplit(str(row[0]))
                    if u.scheme in ("http", "https") and u.netloc:
                        return f"{u.scheme}://{u.netloc}"
            bj = snap.version.build_json
            if bj:
                try:
                    dump = json.loads(bj.path.read_text(encoding="utf-8")).get("dump") or ""
                except Exception:
                    dump = ""
                m = DUMP_WIKI.match(str(dump))
                if m:
                    return f"https://{m.group(1).replace('_', '-')}.wikipedia.org"
            return None

        return snap.cached("wiki_origin", build)

    def _ann_stale(self, snap: Snapshot, annotations: dict, text_sha1: str) -> dict:
        out = {}
        for name, fields in annotations.items():
            if "text_sha1" in fields and fields["text_sha1"] is not None:
                out[name] = fields["text_sha1"] != text_sha1
        return out

    def review_state(self, ds: str, id: int, paras: list[str], text_sha1: str) -> dict:
        ev = self.reviews.get(ds, id)
        hist = self.reviews.history(ds, id)
        state = {"event": ev, "history": hist[-20:], "events": len(hist), "stale": False, "drops": [], "missing": []}
        if ev is None:
            return state
        state["stale"] = ev["text_sha1"] != text_sha1
        state["drops"], state["missing"] = rebase_drops(ev["drop_paragraphs"], paras)
        return state

    # ---- reviews ---------------------------------------------------------------------------
    def save_review(
        self,
        snap: Snapshot,
        id: int,
        verdict: str | None,
        note: str,
        drop_paras: list[int],
        seen_sha1: str | None,
        discard_missing: bool = False,
    ) -> dict:
        row = self.article_row(snap, id)
        text, text_sha1 = row["text"], row["__sha1"]
        if seen_sha1 and seen_sha1 != text_sha1:
            raise Conflict("the article's text changed since you opened it; reload it and review again")
        paras = P.split(text)
        new: dict[str, int] = {}
        for i in sorted(set(drop_paras)):
            if not isinstance(i, int) or isinstance(i, bool) or i < 0 or i >= len(paras):
                raise FilterError(f"paragraph {i} does not exist (the article has {len(paras)})")
            if i == 0:
                raise FilterError("paragraph 0 is the title heading; drop the article instead")
            new.setdefault(P.sha1(paras[i]), i)  # identical paragraphs share one entry
        drops = [{"para": i, "sha1": h} for h, i in new.items()]
        cur_ev = self.reviews.get(snap.ds.name, id)
        if cur_ev and not discard_missing:
            # recorded drops whose text is not in this version stay in the decision
            drops += [d for d in rebase_drops(cur_ev["drop_paragraphs"], paras)[1] if d["sha1"] not in new]
        ev = make_event(snap.ds.name, id, row.get("title") or "", verdict, note, drops, text_sha1)
        if same_decision(cur_ev, ev):
            return {"saved": False, "event": cur_ev, "review": self.review_state(snap.ds.name, id, paras, text_sha1)}
        self.reviews.append([ev])
        return {"saved": True, "event": ev, "review": self.review_state(snap.ds.name, id, paras, text_sha1)}

    def undo(self, snap: Snapshot, id: int) -> dict:
        row = self.article_row(snap, id)
        ev = self.reviews.undo_event(snap.ds.name, id, row.get("title") or "", row["__sha1"])
        if ev is None:
            raise Conflict("nothing to undo for this article")
        self.reviews.append([ev])
        paras = P.split(row["text"])
        return {"saved": True, "event": ev, "review": self.review_state(snap.ds.name, id, paras, row["__sha1"])}

    def decisions(self, snap: Snapshot, items: list[tuple[str, str]]) -> dict:
        """Current decision per article (latest event), with counts and a filter."""
        cur_all = self.reviews.current(snap.ds.name)
        verdict = get(items, "verdict")  # keep|drop|fix|none|cleared|paras|stale
        q = (get(items, "q") or "").lower()
        cur = snap.cursor()
        sha = dict(cur.execute(f"SELECT id, {SHA1_COL} FROM {snap.corpus.table}").fetchall())
        counts = {
            "keep": 0,
            "drop": 0,
            "fix": 0,
            "none": 0,
            "cleared": 0,
            "with_paragraphs": 0,
            "stale": 0,
            "not_in_version": 0,
            "total": 0,
        }
        rows = []
        for id_, ev in cur_all.items():
            empty = is_empty(ev)
            v = ev["verdict"] or ("cleared" if empty else "none")
            stale = id_ in sha and sha[id_] != ev["text_sha1"]
            missing = id_ not in sha
            counts["total"] += 1
            counts[v] += 1
            counts["with_paragraphs"] += bool(ev["drop_paragraphs"])
            counts["stale"] += stale
            counts["not_in_version"] += missing
            r = {
                "id": id_,
                "title": ev["title"],
                "verdict": ev["verdict"],
                "note": ev["note"],
                "dropped": len(ev["drop_paragraphs"]),
                "ts": ev["ts"],
                "stale": stale,
                "missing": missing,
                "events": len(self.reviews.history(snap.ds.name, id_)),
                "cleared": empty,
            }
            if verdict:
                if verdict in ("keep", "drop", "fix", "none", "cleared") and v != verdict:
                    continue
                if verdict == "decided" and empty:
                    continue
                if verdict == "paras" and not ev["drop_paragraphs"]:
                    continue
                if verdict == "stale" and not stale:
                    continue
            elif empty:
                continue  # cleared decisions are hidden unless asked for
            if q and q not in (ev["title"] or "").lower() and q not in (ev["note"] or "").lower():
                continue
            rows.append(r)
        rows.sort(key=lambda r: r["ts"], reverse=True)
        try:
            page = max(1, int(get(items, "page", "1") or 1))
            size = min(500, max(1, int(get(items, "size", "100") or 100)))
        except ValueError:
            raise FilterError("page and size must be integers") from None
        return {
            "counts": counts,
            "total": len(rows),
            "rows": rows[(page - 1) * size : page * size],
            "all_ids": [r["id"] for r in rows],
            "page": page,
            "size": size,
            "log": self.reviews.stats(),
        }

    def export(self, ds: str, include_cleared: bool = False) -> list[dict]:
        evs = [ev for ev in self.reviews.current(ds).values() if include_cleared or not is_empty(ev)]
        return sorted(evs, key=lambda e: e["id"])

    # ---- patterns --------------------------------------------------------------------------
    def templates(self, snap: Snapshot, items: list[tuple[str, str]]) -> dict:
        t0 = time.time()
        gt = self.cat.groups_table(snap.corpus)
        p = Params()
        where = []
        try:
            min_articles = max(2, int(get(items, "min", "5") or 5))
            page = max(1, int(get(items, "page", "1") or 1))
            size = min(200, max(1, int(get(items, "size", "50") or 50)))
        except ValueError:
            raise FilterError("min, page and size must be integers") from None
        where.append(f"articles >= {p.add(min_articles)}")
        kinds = [k for k in (get(items, "kinds") or "para,list,table,math").split(",") if k in P.KINDS]
        if kinds:
            where.append(f"list_contains(CAST({p.add(kinds)} AS VARCHAR[]), kind)")
        q = get(items, "q")
        if q:
            where.append(f"contains(norm, {p.add(q)})")
        w = " AND ".join(where)
        cur = snap.cursor()
        total, paras_total, arts = cur.execute(
            f"SELECT count(*), coalesce(sum(paragraphs), 0), coalesce(sum(articles), 0) FROM {gt} WHERE {w}", p.values
        ).fetchone()
        res = cur.execute(
            f"SELECT nkey, norm, kind, paragraphs, articles, chars, variants FROM {gt} WHERE {w} "
            f"ORDER BY articles DESC, paragraphs DESC, nkey LIMIT {size} OFFSET {(page - 1) * size}",
            p.values,
        )
        names = [d[0] for d in res.description]
        rows = [jsonable_row(names, r) for r in res.fetchall()]
        pt = self.cat.paragraph_table(snap.corpus)
        all_paras = cur.execute(
            f"SELECT count(*) FROM {pt} WHERE list_contains(CAST($k AS VARCHAR[]), kind)", {"k": kinds}
        ).fetchone()[0]
        return {
            "rows": rows,
            "total": total,
            "paragraphs_in_groups": paras_total,
            "paragraphs_total": all_paras,
            "page": page,
            "size": size,
            "min": min_articles,
            "kinds": kinds,
            "elapsed_ms": round((time.time() - t0) * 1000, 1),
        }

    def template(self, snap: Snapshot, key: str, items: list[tuple[str, str]]) -> dict:
        gt = self.cat.groups_table(snap.corpus)
        pt = self.cat.paragraph_table(snap.corpus)
        cur = snap.cursor()
        res = cur.execute(
            f"SELECT nkey, norm, kind, paragraphs, articles, chars, variants FROM {gt} WHERE nkey = $k", {"k": key}
        )
        names = [d[0] for d in res.description]
        g = res.fetchone()
        if g is None:
            raise NotFound(f"no repeated-paragraph group {key!r}")
        try:
            page = max(1, int(get(items, "page", "1") or 1))
        except ValueError:
            page = 1
        size = 50
        ex = cur.execute(
            f"SELECT p.id, c.title, p.para, p.p FROM {pt} p JOIN {snap.corpus.table} c USING (id) "
            f"WHERE p.nkey = $k ORDER BY c.title, p.para LIMIT {size} OFFSET {(page - 1) * size}",
            {"k": key},
        ).fetchall()
        variants = cur.execute(
            f"SELECT p, count(*) AS n FROM {pt} WHERE nkey = $k GROUP BY p ORDER BY n DESC LIMIT 5", {"k": key}
        ).fetchall()
        return {
            "group": jsonable_row(names, g),
            "examples": [{"id": i, "title": t, "para": pa, "text": tx} for i, t, pa, tx in ex],
            "top_variants": [{"text": t, "n": n} for t, n in variants],
            "page": page,
            "size": size,
        }

    def para_search(self, snap: Snapshot, items: list[tuple[str, str]]) -> dict:
        t0 = time.time()
        pattern = get(items, "re") or ""
        if not pattern:
            raise FilterError("give a regular expression (re=...)")
        icase = get(items, "icase") == "1"
        kinds = [k for k in (get(items, "kinds") or ",".join(P.KINDS)).split(",") if k in P.KINDS]
        try:
            page = max(1, int(get(items, "page", "1") or 1))
            size = min(200, max(1, int(get(items, "size", "50") or 50)))
        except ValueError:
            raise FilterError("page and size must be integers") from None
        pt = self.cat.paragraph_table(snap.corpus)
        params = {"re": pattern, "o": "i" if icase else "", "k": kinds}
        base = f"FROM {pt} p WHERE list_contains(CAST($k AS VARCHAR[]), p.kind) AND regexp_matches(p.p, $re, $o)"
        cur = snap.cursor()
        try:
            n_paras, n_arts, n_matches = cur.execute(
                f"SELECT count(*), count(DISTINCT p.id), coalesce(sum(len(regexp_extract_all(p.p, $re, 0, $o))), 0) "
                f"{base}",
                params,
            ).fetchone()
            res = cur.execute(
                f"SELECT p.id, c.title, p.para, p.kind, p.p, regexp_extract_all(p.p, $re, 0, $o)[1:12] AS found, "
                f"len(regexp_extract_all(p.p, $re, 0, $o)) AS n "
                f"{base.replace('FROM ' + pt + ' p', 'FROM ' + pt + ' p JOIN ' + snap.corpus.table + ' c USING (id)')} "
                f"ORDER BY c.title, p.para LIMIT {size} OFFSET {(page - 1) * size}",
                params,
            ).fetchall()
            by_kind = cur.execute(f"SELECT p.kind, count(*) {base} GROUP BY 1 ORDER BY 2 DESC", params).fetchall()
        except duckdb.Error as e:
            raise _duck_error(e) from None
        rows = []
        search = {"mode": "regex", "text": pattern, "icase": icase}
        for i, title, para, kind, text, found, n in res:
            rows.append(
                {
                    "id": i,
                    "title": title,
                    "para": para,
                    "kind": kind,
                    "matches": n,
                    "chars": len(text),
                    "snippets": kwic(text, search, found, max_snips=3),
                }
            )
        return {
            "rows": rows,
            "paragraphs": n_paras,
            "articles": n_arts,
            "matches": n_matches,
            "by_kind": by_kind,
            "page": page,
            "size": size,
            "elapsed_ms": round((time.time() - t0) * 1000, 1),
        }

    # ---- bulk actions ------------------------------------------------------------------------
    def _bulk_targets(self, snap: Snapshot, spec: dict) -> dict[int, set[int]]:
        """id -> paragraph indexes matched by the spec (an empty set for whole-article actions)."""
        match = spec.get("match") or {}
        typ = match.get("type")
        cur = snap.cursor()
        if typ == "ids":
            ids = match.get("ids") or []
            if not all(isinstance(i, int) for i in ids):
                raise FilterError("ids must be integers")
            return {i: set() for i in ids}
        pt = self.cat.paragraph_table(snap.corpus)
        kinds = [k for k in (match.get("kinds") or P.KINDS) if k in P.KINDS]
        if typ == "regex":
            pattern = match.get("pattern") or ""
            if not pattern:
                raise FilterError("empty pattern")
            params = {"re": pattern, "o": "i" if match.get("icase") else "", "k": kinds}
            sql = (
                f"SELECT id, para FROM {pt} WHERE list_contains(CAST($k AS VARCHAR[]), kind) "
                f"AND regexp_matches(p, $re, $o)"
            )
        elif typ == "template":
            params = {"key": str(match.get("key") or "")}
            sql = f"SELECT id, para FROM {pt} WHERE nkey = $key"
        elif typ == "browse":
            # the articles of a Browse query (filters as a list of [name, value] pairs)
            q = [(str(k), str(v)) for k, v in (match.get("params") or [])]
            return {i: set() for i, _ in self.id_list(snap, q)}
        else:
            raise FilterError("match.type must be regex, template, ids or browse")
        try:
            rows = cur.execute(sql, params).fetchall()
        except duckdb.Error as e:
            raise _duck_error(e) from None
        out: dict[int, set[int]] = {}
        for i, para in rows:
            out.setdefault(i, set()).add(para)
        return out

    def bulk_plan(self, snap: Snapshot, spec: dict) -> dict:
        action = spec.get("action")
        if action not in ("drop_paragraphs", "drop_articles", "undo", "clear"):
            raise FilterError("action must be drop_paragraphs, drop_articles, undo or clear")
        if action == "drop_paragraphs" and (spec.get("match") or {}).get("type") in ("ids", "browse"):
            raise FilterError("drop_paragraphs needs a regex or template match")
        note = str(spec.get("note") or "").strip()
        targets = self._bulk_targets(snap, spec)
        if len(targets) > MAX_BULK:
            raise FilterError(f"{len(targets):,} articles is more than one bulk action may touch ({MAX_BULK:,})")
        cur = snap.cursor()
        ids = sorted(targets)
        texts = {}
        if ids:
            texts = {
                i: (t, title, s)
                for i, t, title, s in cur.execute(
                    f"SELECT id, text, title, {SHA1_COL} FROM {snap.corpus.table} WHERE list_contains($ids, id)",
                    {"ids": ids},
                ).fetchall()
            }
        events, summary = [], {"articles": 0, "paragraphs": 0, "unchanged": 0, "not_in_version": 0, "carried_drops": 0}
        ds = snap.ds.name
        for i in ids:
            if i not in texts:
                summary["not_in_version"] += 1
                continue
            text, title, text_sha1 = texts[i]
            paras = P.split(text)
            cur_ev = self.reviews.get(ds, i)
            if action == "undo":
                ev = self.reviews.undo_event(ds, i, title or "", text_sha1)
                if ev is None:
                    summary["unchanged"] += 1
                    continue
            elif action == "clear":
                ev = make_event(ds, i, title or "", None, "", [], text_sha1)
            else:
                verdict = cur_ev["verdict"] if cur_ev else None
                old_note = cur_ev["note"] if cur_ev else ""
                # recorded drops stay exactly as recorded (including ones whose text is no longer
                # here, so the next build keeps dropping them); new matches are added by sha1
                recorded = list(cur_ev["drop_paragraphs"]) if cur_ev else []
                summary["carried_drops"] += len(rebase_drops(recorded, paras)[1])
                have = {d["sha1"] for d in recorded}
                added = []
                if action == "drop_paragraphs":
                    for para in sorted(targets[i]):
                        h = P.sha1(paras[para])
                        if para == 0 or h in have:  # never the title heading
                            continue
                        have.add(h)
                        added.append({"para": para, "sha1": h})
                else:
                    verdict = "drop"
                new_note = old_note
                if note and note not in old_note:
                    new_note = f"{old_note}\n{note}" if old_note else note
                ev = make_event(ds, i, title or "", verdict, new_note, recorded + added, text_sha1)
                summary["paragraphs"] += len(added)
            if same_decision(cur_ev, ev):
                summary["unchanged"] += 1
                continue
            summary["articles"] += 1
            events.append(ev)
        body = [{k: v for k, v in ev.items() if k != "ts"} for ev in events]
        token = hashlib.sha1(json.dumps(body, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        # a short excerpt of each paragraph that will be dropped, for the confirmation dialog
        excerpts = {}
        if action == "drop_paragraphs":
            for ev in events[:2000]:
                paras = P.split(texts[ev["id"]][0])
                hashes = {P.sha1(p): k for k, p in enumerate(paras)}
                excerpts[ev["id"]] = {
                    d["para"]: (paras[hashes[d["sha1"]]][:160] if d["sha1"] in hashes else None)
                    for d in ev["drop_paragraphs"]
                }
        return {
            "action": action,
            "events": body,
            "summary": summary,
            "token": token,
            "matched_articles": len(ids),
            "excerpts": excerpts,
        }

    def bulk_apply(self, snap: Snapshot, spec: dict, token: str) -> dict:
        plan = self.bulk_plan(snap, spec)
        if plan["token"] != token:
            raise Conflict("the data or the decisions changed since the preview; preview again")
        ts = now_iso()
        events = [{k: ({"ts": ts, **ev})[k] for k in FIELDS} for ev in plan["events"]]  # documented key order
        self.reviews.append(events)
        return {"written": len(events), "summary": plan["summary"]}

    # ---- overview --------------------------------------------------------------------------
    def overview(self, snap: Snapshot) -> dict:
        # keyed by the folder's signature too: a new build file or report changes the overview
        return snap.cached(f"overview:{hash(snap.ds.signature)}", lambda: self._overview(snap))

    def _overview(self, snap: Snapshot) -> dict:
        t0 = time.time()
        cur = snap.cursor()
        v = snap.view
        dists = []
        numeric = [c for c in snap.columns.values() if c.kind == "numeric" and c.source != "review" and c.key != "id"]
        for c in numeric:
            vals = [
                x
                for (x,) in cur.execute(f"SELECT {qi(c.key)} FROM {v} WHERE {qi(c.key)} IS NOT NULL").fetchall()
                if x is not None and (not isinstance(x, float) or math.isfinite(x))
            ]
            if not vals:
                continue
            # identifiers (a name ending in "id" whose values are all different, e.g. revid) have no
            # meaningful distribution
            if c.key.lower().endswith("id") and len(set(vals)) == len(vals):
                continue
            dists.append(
                {"key": c.key, "source": c.source, "description": c.description, **histogram(vals), "nulls": c.nulls}
            )
        cats = []
        for c in snap.columns.values():
            if c.source == "review" or not c.filterable:
                continue
            if c.kind in ("category", "bool", "list") and c.options is not None:
                total_values = None
                if c.kind == "list":
                    total_values, distinct = cur.execute(
                        f"SELECT count(*), count(DISTINCT x) FROM (SELECT unnest(CAST({qi(c.key)} AS VARCHAR[])) x "
                        f"FROM {v})"
                    ).fetchone()
                else:
                    distinct = len(c.options)
                cats.append(
                    {
                        "key": c.key,
                        "kind": c.kind,
                        "source": c.source,
                        "description": c.description,
                        "options": c.options[:30],
                        "distinct": distinct,
                        "values": total_values,
                        "nulls": c.nulls,
                    }
                )
        # paragraph-level numeric distributions
        para_dists = []
        for a in snap.para_anns:
            desc = column_descriptions(a)
            for n, t in a.columns:
                if is_numeric(t):
                    vals = [
                        x
                        for (x,) in cur.execute(f"SELECT {qi(n)} FROM {a.table} WHERE {qi(n)} IS NOT NULL").fetchall()
                        if not isinstance(x, float) or math.isfinite(x)
                    ]
                    if vals:
                        para_dists.append(
                            {
                                "key": f"{a.ann.name}.paragraphs.{n}",
                                "source": a.ann.name,
                                **histogram(vals),
                                # e.g. paragraphs not scored yet (a null bpb, and null joined columns)
                                "nulls": cur.execute(f"SELECT count(*) - count({qi(n)}) FROM {a.table}").fetchone()[0],
                                "description": desc.get(n, ""),
                                **({"from": a.joined[n]} if n in a.joined else {}),
                            }
                        )
                elif t.upper() == "VARCHAR":
                    opts = cur.execute(
                        f"SELECT {qi(n)}, count(*) FROM {a.table} GROUP BY 1 ORDER BY 2 DESC LIMIT 31"
                    ).fetchall()
                    if len(opts) <= 30 and n != "text_sha1":
                        cats.append(
                            {
                                "key": f"{a.ann.name}.paragraphs.{n}",
                                "kind": "category",
                                "source": a.ann.name,
                                "options": [[str(x), c] for x, c in opts],
                                "distinct": len(opts),
                                "description": "",
                                "nulls": 0,
                            }
                        )
        build = None
        bj = snap.version.build_json
        if bj:
            try:
                build = json.loads(bj.path.read_text(encoding="utf-8"))
            except Exception as e:
                build = {"error": f"could not read {bj.path.name}: {e}"}
        build_summary, dropped, has_titles = None, None, False
        if isinstance(build, dict):
            build_summary = {k: val for k, val in build.items() if not isinstance(val, (dict, list))}
            dt = build.get("dropped_titles")  # older builds: reason -> titles
            has_titles = isinstance(dt, dict)
            if isinstance(build.get("dropped"), dict):  # reason -> count
                dropped = build["dropped"]
            elif has_titles:
                dropped = {str(r): len(t) if isinstance(t, list) else t for r, t in dt.items()}
            for k, val in build.items():
                if isinstance(val, dict) and k != "dropped_titles" and k != "dropped":
                    build_summary[k] = val
        totals = cur.execute(
            f"SELECT count(*) {', sum(words)' if 'words' in snap.columns else ', NULL'} "
            f"{', sum(chars)' if 'chars' in snap.columns else ', NULL'} FROM {snap.corpus.table}"
        ).fetchone()
        warnings = []
        for st in snap.ann_status:
            for w in st["warnings"]:
                warnings.append({"annotation": st["key"], "text": w})
        if not snap.ann_status:
            warnings.append({"annotation": None, "text": "no annotations found (annotations/<name>.jsonl)"})
        for k, e in self.cat.errors().items():
            if k.startswith(snap.ds.name + "/"):
                warnings.append({"annotation": None, "text": f"{k}: {e}"})
        for msg in snap.ds.ignored:
            warnings.append({"annotation": None, "text": f"ignored annotations/{msg}"})
        for msg in snap.ds.notes:  # a table in two formats (a conversion in progress)
            warnings.append({"annotation": None, "text": msg})
        excluded = self.excluded_summary(snap, dropped)
        if excluded:
            for msg in excluded["problems"]:
                warnings.append({"annotation": None, "text": msg})
        return {
            "dataset": snap.ds.name,
            "version": snap.version.stem,
            "file": snap.version.primary.path.name,
            "file_size": snap.version.primary.size,
            "file_mtime": snap.version.primary.mtime_ns / 1e9,
            "file_not_read": snap.version.not_read,
            "articles": totals[0],
            "words": totals[1],
            "chars": totals[2],
            "build": build_summary,
            "dropped": dropped,
            "dropped_titles": has_titles and not excluded,  # the old per-reason title lists
            "excluded": excluded,
            "build_file": bj.path.name if bj else None,
            "distributions": dists,
            "para_distributions": para_dists,
            "categories": cats,
            "annotations": snap.ann_status,
            "warnings": warnings,
            "elapsed_ms": round((time.time() - t0) * 1000, 1),
        }

    def dropped_titles(self, snap: Snapshot, reason: str) -> dict:
        """Titles left out for a reason: from excluded.jsonl, else an older build file's
        `dropped_titles`."""
        st = self.cat.side_table(snap.ds, "excluded")
        if st is not None:
            titles = [
                t
                for (t,) in snap.cursor()
                .execute(f"SELECT title FROM {st.table} WHERE reason = $r ORDER BY title, id", {"r": reason})
                .fetchall()
            ]
            return {"reason": reason, "titles": titles}
        bj = snap.version.build_json
        if not bj:
            raise NotFound("no build file for this version")
        build = json.loads(bj.path.read_text(encoding="utf-8"))
        titles = (build.get("dropped_titles") or {}).get(reason)
        if titles is None:
            raise NotFound(f"no dropped titles for reason {reason!r}")
        return {"reason": reason, "titles": titles}

    # ---- excluded pages and removed blocks ------------------------------------------------------
    def excluded_summary(self, snap: Snapshot, build_dropped: dict | None = None) -> dict | None:
        """Counts per reason from excluded.jsonl (and removed-blocks.jsonl), checked against the
        corpus version and the build file's `dropped` counts. None without either file."""
        ex = self.cat.side_table(snap.ds, "excluded")
        bl = self.cat.side_table(snap.ds, "removed_blocks")
        if ex is None and bl is None:
            return None
        cur = snap.cursor()
        c = snap.corpus.table
        out: dict[str, Any] = {"pages": None, "blocks": None, "problems": [], "mismatch": []}
        if ex is not None:
            reasons = cur.execute(
                f"SELECT reason, count(*) FROM {ex.table} GROUP BY 1 ORDER BY 2 DESC, 1 NULLS LAST"
            ).fetchall()
            in_corpus = cur.execute(
                f"SELECT count(DISTINCT id) FROM {ex.table} WHERE id IN (SELECT id FROM {c})"
            ).fetchone()[0]
            out["pages"] = {
                "file": ex.file.path.name,
                "mtime": ex.file.mtime_ns / 1e9,
                "rows": ex.rows,
                "reasons": [[r, n] for r, n in reasons],
                "in_corpus": in_corpus,
            }
            out["problems"] += ex.problems
            if in_corpus:
                out["problems"].append(
                    f"{in_corpus:,} pages in {ex.file.path.name} are also in {snap.version.primary.path.name} "
                    "(the two files come from different builds)"
                )
            if isinstance(build_dropped, dict):
                have = {r: n for r, n in reasons}
                for r in sorted(set(have) | set(build_dropped), key=str):
                    if have.get(r, 0) != build_dropped.get(r, 0):
                        out["mismatch"].append([r, build_dropped.get(r, 0), have.get(r, 0)])
        if bl is not None:
            q = f"SELECT {{col}}, count(*) FROM {bl.table} GROUP BY 1 ORDER BY 2 DESC, 1 NULLS LAST"
            out["blocks"] = {
                "file": bl.file.path.name,
                "mtime": bl.file.mtime_ns / 1e9,
                "rows": bl.rows,
                "reasons": [[r, n] for r, n in cur.execute(q.format(col="reason")).fetchall()],
                "kinds": [[k, n] for k, n in cur.execute(q.format(col="kind")).fetchall()],
                "articles": cur.execute(f"SELECT count(DISTINCT id) FROM {bl.table}").fetchone()[0],
            }
            out["problems"] += bl.problems
        return out

    def excluded(self, snap: Snapshot, items: list[tuple[str, str]]) -> dict:
        """excluded.jsonl: filter by reason, search title/detail (or an exact id), sort, page.
        Rows carry links to the dump revision and the current page, and a detail that names an
        id ("same text as id 1234") says whether that page is in the corpus."""
        st = self._side(snap, "excluded")
        t0 = time.time()
        page, size = paging(items)
        p = Params()
        where = []
        reason = get(items, "reason")
        if reason == "__null__":
            where.append("reason IS NULL")
        elif reason:
            where.append(f"reason = {p.add(reason)}")
        q = (get(items, "q") or "").strip()
        if q:
            k = p.add(q.lower())
            conds = [f"contains(lower(coalesce(title, '')), {k})", f"contains(lower(coalesce(detail, '')), {k})"]
            if ID_QUERY.fullmatch(q):
                n = p.add(int(q))
                conds += [f"id = {n}", f"revid = {n}"]
            where.append("(" + " OR ".join(conds) + ")")
        w = f"WHERE {' AND '.join(where)}" if where else ""
        order = order_by(items, EXCLUDED_SORTS, "reason")
        cur = snap.cursor()
        total = cur.execute(f"SELECT count(*) FROM {st.table} {w}", p.values).fetchone()[0]
        res = cur.execute(
            f"SELECT id, revid, title, reason, detail, id IN (SELECT id FROM {snap.corpus.table}) AS in_corpus "
            f"FROM {st.table} {w} ORDER BY {order} LIMIT {size} OFFSET {(page - 1) * size}",
            p.values,
        )
        names = [d[0] for d in res.description]
        rows = [jsonable_row(names, r) for r in res.fetchall()]
        # ids named in the details (the kept page of a duplicate): link them if they are articles
        named = sorted({int(m) for r in rows for m in ID_REF.findall(r["detail"] or "")})
        found = {}
        if named:
            found = dict(
                cur.execute(
                    f"SELECT id, title FROM {snap.corpus.table} WHERE list_contains($ids, id)", {"ids": named}
                ).fetchall()
            )
        origin = self.wiki_origin(snap)
        for r in rows:
            r["links"] = {
                "revision": f"{origin}/w/index.php?oldid={r['revid']}" if origin and r["revid"] is not None else None,
                "current": f"{origin}/?curid={r['id']}" if origin and r["id"] is not None else None,
            }
            r["detail_ids"] = [
                {"id": int(m), "in_corpus": int(m) in found, "title": found.get(int(m))}
                for m in dict.fromkeys(ID_REF.findall(r["detail"] or ""))
            ]
        summary = self.excluded_summary(snap, self._build_dropped(snap))
        return {
            "file": st.file.path.name,
            "path": str(st.file.path),
            "mtime": st.file.mtime_ns / 1e9,
            "version": snap.version.stem,
            "corpus_file": snap.version.primary.path.name,
            "origin": origin,
            "summary": summary,
            "total": total,
            "rows": rows,
            "page": page,
            "size": size,
            "elapsed_ms": round((time.time() - t0) * 1000, 1),
        }

    def removed_blocks(self, snap: Snapshot, items: list[tuple[str, str]]) -> dict:
        """removed-blocks.jsonl: filter by reason, kind, article (id=) or whether the article was
        kept (kept=1|0), search title/text, sort, page. Rows say whether their article is in the
        corpus, or why it was left out."""
        st = self._side(snap, "removed_blocks")
        t0 = time.time()
        page, size = paging(items)
        cols = {n for n, _ in st.columns}
        p = Params()
        where = []
        for name in ("reason", "kind"):
            val = get(items, name)
            if val == "__null__":
                where.append(f"b.{name} IS NULL")
            elif val:
                where.append(f"b.{name} = {p.add(val)}")
        art = get(items, "id")
        if art:
            if not ID_QUERY.fullmatch(art):
                raise FilterError("id must be a page id (digits)")
            where.append(f"b.id = {p.add(int(art))}")
        kept = get(items, "kept")
        if kept in ("0", "1") and "article_kept" in cols:
            where.append(f"b.article_kept = {p.add(kept == '1')}")
        q = (get(items, "q") or "").strip()
        if q:
            k = p.add(q.lower())
            conds = [f"contains(lower(coalesce(b.title, '')), {k})", f"contains(lower(coalesce(b.text, '')), {k})"]
            if ID_QUERY.fullmatch(q):
                conds.append(f"b.id = {p.add(int(q))}")
            where.append("(" + " OR ".join(conds) + ")")
        w = f"WHERE {' AND '.join(where)}" if where else ""
        exprs = {k: f"b.{qi(k)}" for k in ("id", "title", "reason", "kind")} | {"chars": "length(b.text)"}
        order = order_by(items, BLOCK_SORTS, "id", exprs)
        ex = self.cat.side_table(snap.ds, "excluded")
        why = f"(SELECT any_value(e.reason) FROM {ex.table} e WHERE e.id = b.id)" if ex else "NULL"
        kept_col = "b.article_kept" if "article_kept" in cols else "NULL"
        cur = snap.cursor()
        total = cur.execute(f"SELECT count(*) FROM {st.table} b {w}", p.values).fetchone()[0]
        res = cur.execute(
            f"SELECT b.id, b.title, b.kind, b.reason, b.text, length(b.text) AS chars, {kept_col} AS article_kept, "
            f"b.id IN (SELECT id FROM {snap.corpus.table}) AS in_corpus, {why} AS excluded_reason "
            f"FROM {st.table} b {w} ORDER BY {order} LIMIT {size} OFFSET {(page - 1) * size}",
            p.values,
        )
        names = [d[0] for d in res.description]
        rows = [jsonable_row(names, r) for r in res.fetchall()]
        return {
            "file": st.file.path.name,
            "path": str(st.file.path),
            "mtime": st.file.mtime_ns / 1e9,
            "version": snap.version.stem,
            "has_kept": "article_kept" in cols,
            "summary": self.excluded_summary(snap, self._build_dropped(snap)),
            "total": total,
            "rows": rows,
            "page": page,
            "size": size,
            "elapsed_ms": round((time.time() - t0) * 1000, 1),
        }

    def _side(self, snap: Snapshot, kind: str) -> SideTable:
        """The side table, or 404 (no such file) / 503 (the file is there but can't be read)."""
        st = self.cat.side_table(snap.ds, kind)
        if st is not None:
            return st
        fs = snap.ds.side(kind)
        name = fs.path.name if fs else {"excluded": "excluded.jsonl", "removed_blocks": "removed-blocks.jsonl"}[kind]
        if fs is None:
            raise NotFound(f"{snap.ds.name} has no {name}")
        err = self.cat.errors().get(f"{snap.ds.name}/{name}", "unknown error")
        raise DataError(f"could not read {name}: {err}")

    def _build_dropped(self, snap: Snapshot) -> dict | None:
        bj = snap.version.build_json
        if not bj:
            return None
        try:
            d = json.loads(bj.path.read_text(encoding="utf-8")).get("dropped")
        except Exception:
            return None
        return d if isinstance(d, dict) else None

    # ---- documents: methodology, reports, docs --------------------------------------------------
    def link_context(self, snap: Snapshot) -> LinkContext:
        """What references in documents can link to, for this dataset version (cached)."""

        def build():
            ids = frozenset(i for (i,) in snap.cursor().execute(f"SELECT id FROM {snap.corpus.table}").fetchall())
            ds = snap.ds
            return LinkContext(
                dataset=ds.name,
                ids=ids,
                reports=frozenset(r.path.name for r in ds.reports),
                annotations=frozenset(a.key for a in ds.annotations),
                docs=frozenset(ds.doc_names()),
                review_first=ds.review_first.path.name if ds.review_first else None,
                side=frozenset(fs.path.name for fs in (ds.excluded, ds.removed_blocks) if fs),
            )

        # the snapshot is rebuilt when files change, but docs can appear without a table change
        key = f"links:{hash(snap.ds.signature)}"
        return snap.cached(key, build)

    def _read(self, fs) -> str:
        try:
            return fs.path.read_text(encoding="utf-8")
        except OSError as e:
            raise NotFound(f"could not read {fs.path.name}: {e}") from None

    def doc(self, snap: Snapshot, group: str, name: str) -> dict:
        """A rendered document: quality/<name> (group "quality") or a top-level doc (group "docs")."""
        ds = snap.ds
        if group == "quality":
            pool = ds.reports
        elif group == "docs":
            pool = ds.docs + ([ds.methodology] if ds.methodology else [])
        else:
            raise FilterError("group must be quality or docs")
        fs = next((r for r in pool if r.path.name == name), None)  # only discovered files, by name
        if fs is None:
            raise NotFound(f"no {'report' if group == 'quality' else 'document'} {name!r}")
        text = self._read(fs)
        html, toc = render_doc(text, self.link_context(snap))
        return {
            "name": name,
            "group": group,
            "path": str(fs.path),
            "html": html,
            "toc": toc,
            "mtime": fs.path.stat().st_mtime if fs.path.exists() else fs.mtime_ns / 1e9,
            "size": len(text.encode("utf-8")),
        }

    def methodology(self, snap: Snapshot) -> dict:
        if not snap.ds.methodology:
            raise NotFound(f"{snap.ds.name} has no METHODOLOGY.md")
        return self.doc(snap, "docs", snap.ds.methodology.path.name)

    def review_first(self, snap: Snapshot) -> dict:
        """quality/review-first.md as items with their article's current state and review."""
        ds = snap.ds
        fs = ds.review_first
        if fs is None:
            raise NotFound(f"{ds.name} has no quality/{REVIEW_FIRST}")
        text = self._read(fs)
        links = self.link_context(snap)
        segs = reviewfirst.parse(text)
        items = [s["item"] for s in segs if s["kind"] == "item"]
        queue = next(
            (c.key for c in snap.columns.values() if c.kind == "numeric" and c.key.endswith(".review_rank")), None
        )
        out: dict[str, Any] = {
            "name": fs.path.name,
            "path": str(fs.path),
            "mtime": fs.path.stat().st_mtime if fs.path.exists() else fs.mtime_ns / 1e9,
            "queue_sort": queue,
            "parsed": bool(items),
        }
        if not items:  # not in the expected shape: show it as a document, references still linked
            out["html"], out["toc"] = render_doc(text, links)
            return out
        cur = snap.cursor()
        ids = sorted({it.id for it in items})
        stale_cols = [c.key for c in snap.columns.values() if c.key.endswith("._stale")]
        sel = ", ".join(qi(k) for k in stale_cols)
        rows = {
            r[0]: r
            for r in cur.execute(
                f"SELECT id, title, text, {SHA1_COL}{', ' + sel if sel else ''} FROM {snap.view} "
                f"WHERE list_contains($ids, id)",
                {"ids": ids},
            ).fetchall()
        }
        # a paragraph annotation with para_sha1 lets a link follow its paragraph across rebuilds
        para_sha: dict[tuple[int, int], str] = {}
        for a in snap.para_anns:
            if any(n == "para_sha1" for n, _ in a.columns):
                para_sha = {
                    (i, p): h
                    for i, p, h in cur.execute(
                        f"SELECT id, para, para_sha1 FROM {a.table} WHERE list_contains($ids, id)", {"ids": ids}
                    ).fetchall()
                }
                break
        current = self.reviews.current(ds.name)
        segments, types, reviewed = [], {}, 0
        for seg in segs:
            if seg["kind"] == "md":
                segments.append({"kind": "md", "html": render_doc(seg["text"], links)[0]})
                continue
            it = seg["item"]
            row = rows.get(it.id)
            d: dict[str, Any] = {
                "kind": "item",
                "rank": it.rank,
                "title": it.title,
                "id": it.id,
                "para": it.para,
                "type": it.type,
                "line": it.line,
                "body_html": render_doc(it.body, links)[0] if it.body else "",
                "in_corpus": row is not None,
            }
            paras: list[str] = []
            if row is not None:
                paras = P.split(row[2])
                hashes = [P.sha1(x) for x in paras]
                d["title_now"] = row[1]
                d["text_sha1"] = row[3]
                d["stale"] = [k[: -len("._stale")] for k, v in zip(stale_cols, row[4:], strict=True) if v]
                d["paragraphs"] = len(paras)
                d["para_now"], d["para_status"] = locate(it.id, it.para, hashes, para_sha)
            else:
                d["para_now"], d["para_status"] = None, "article not in this version"
            ev = current.get(it.id)
            drops = rebase_drops(ev["drop_paragraphs"], paras)[0] if ev else []
            d["verdict"] = ev["verdict"] if ev else None
            d["note"] = ev["note"] if ev else ""
            d["drops"] = [x["para"] for x in drops]
            d["events"] = len(self.reviews.history(ds.name, it.id)) if ev else 0
            d["flagged_dropped"] = d["para_now"] is not None and d["para_now"] in d["drops"]
            d["reviewed"] = bool(d["verdict"]) or d["flagged_dropped"]
            reviewed += d["reviewed"]
            if it.type:
                types[it.type] = types.get(it.type, 0) + 1
            segments.append(d)
        out.update(segments=segments, types=list(types.items()), items=len(items), reviewed=reviewed)
        return out

    # ---- home --------------------------------------------------------------------------------
    def home(self) -> dict:
        out = []
        for name, ds in sorted(self.cat.datasets().items()):
            item: dict[str, Any] = {
                "name": name,
                "dir": str(ds.dir),
                "versions": [
                    {
                        "stem": v.stem,
                        "fmt": v.fmt,
                        "not_read": v.not_read,
                        "size": v.primary.size,
                        "mtime": v.primary.mtime_ns / 1e9,
                        "date": v.date,
                        "formats": sorted(v.files),
                    }
                    for v in ds.versions
                ],
                "reports": [r.path.name for r in ds.reports],
                "docs": [d.path.name for d in ds.docs],
                "methodology": bool(ds.methodology),
                "review_first": bool(ds.review_first),
                "excluded": bool(ds.excluded),
                "removed_blocks": bool(ds.removed_blocks),
                "notes": list(ds.notes),
            }
            try:
                snap = self.cat.snapshot(name)
                cur = snap.cursor()
                n, words = cur.execute(
                    f"SELECT count(*), {'sum(words)' if 'words' in snap.columns else 'NULL'} FROM {snap.corpus.table}"
                ).fetchone()
                reviewed = cur.execute(
                    f'SELECT count(*) FILTER (WHERE "review.verdict" IS NOT NULL), '
                    f"count(*) FILTER (WHERE \"review.verdict\" = 'keep'), "
                    f"count(*) FILTER (WHERE \"review.verdict\" = 'drop'), "
                    f"count(*) FILTER (WHERE \"review.verdict\" = 'fix'), "
                    f'count(*) FILTER (WHERE coalesce("review.dropped", 0) > 0) FROM {snap.view}'
                ).fetchone()
                sides = {k: self.cat.side_table(ds, k) for k in ("excluded", "removed_blocks")}
                item["excluded_rows"] = sides["excluded"].rows if sides["excluded"] else None
                item["removed_block_rows"] = sides["removed_blocks"].rows if sides["removed_blocks"] else None
                build = None
                if snap.version.build_json:
                    try:
                        b = json.loads(snap.version.build_json.path.read_text(encoding="utf-8"))
                        build = b.get("built") if isinstance(b, dict) else None
                    except Exception:
                        build = None
                item.update(
                    articles=n,
                    words=words,
                    built=build,
                    corpus_mtime=snap.version.primary.mtime_ns / 1e9,
                    annotations=[
                        {
                            "key": st["key"],
                            "level": st["level"],
                            "stale": st["stale"],
                            "matched": st["matched"],
                            "missing": st["missing"],
                            "warnings": len(st["warnings"]),
                            "mtime": st["mtime"],
                        }
                        for st in snap.ann_status
                    ],
                    reviews={
                        "reviewed": reviewed[0],
                        "keep": reviewed[1],
                        "drop": reviewed[2],
                        "fix": reviewed[3],
                        "with_paragraphs": reviewed[4],
                    },
                )
            except Exception as e:  # show the dataset even if it can't be loaded
                item["error"] = str(e)
            out.append(item)
        from .discovery import other_dirs

        not_ds = [d for d in other_dirs(self.cat.data_root) if d not in self.cat.datasets()]
        return {
            "data_root": str(self.cat.data_root),
            "sources": self.sources,
            "datasets": out,
            "other_dirs": not_ds,
            "reviews_file": self.reviews.stats(),
        }


# ---- pure helpers ---------------------------------------------------------------------------


def locate(id: int, para: int | None, hashes: list[str], para_sha: dict) -> tuple[int | None, str]:
    """Where a paragraph named by (id, index at scoring time) is in the current text.

    With the scored paragraph's sha1 (from a paragraph annotation's para_sha1) it is found by
    content: "same" place, or "moved" to another index. If that text is nowhere in the article,
    the paragraph was edited or removed: the same index is the best guess while it exists
    ("changed"), otherwise "gone". Without a sha1 the index is taken as is ("unverified")."""
    if para is None:
        return None, "whole article"
    want = para_sha.get((id, para))
    if want:
        if para < len(hashes) and hashes[para] == want:
            return para, "same"
        found = [i for i, h in enumerate(hashes) if h == want]
        if found:
            return min(found, key=lambda i: abs(i - para)), "moved"
        return (para, "changed") if para < len(hashes) else (None, "gone")
    if para < len(hashes):
        return para, "unverified"
    return None, "gone"


def rebase_drops(drops: list[dict], paras: list[str]) -> tuple[list[dict], list[dict]]:
    """Match recorded paragraph drops to the current text by sha1, the way the build does.

    Returns (present, missing). `present` lists every current paragraph (never paragraph 0, the
    title) whose sha1 is recorded, so identical paragraphs are dropped together, as the build
    drops them; an entry whose recorded index differs gets "moved_from". `missing` holds the
    recorded drops whose text is not in the current text: already removed by a rebuild that
    applied them, or the text changed. They must stay in later events, or the next build (which
    starts again from the source) would keep those paragraphs."""
    want = {d["sha1"]: d for d in drops}
    present, found = [], set()
    hashes = [P.sha1(p) for p in paras]
    for i, h in enumerate(hashes):
        if i == 0 or h not in want:
            continue
        rec = want[h]["para"]
        entry = {"para": i, "sha1": h}
        if rec != i and not (rec < len(hashes) and hashes[rec] == h):
            entry["moved_from"] = rec
        present.append(entry)
        found.add(h)
    missing = [d for d in drops if d["sha1"] not in found]
    return present, missing


def kwic(
    text: str,
    search: dict,
    found: list[str] | None = None,
    max_snips: int = SNIPPETS_PER_ROW,
    ctx: int = SNIPPET_CONTEXT,
) -> list[dict]:
    """Keyword-in-context: [{"pre", "match", "post"}] around the first matches."""
    spans: list[tuple[int, int]] = []
    if search.get("mode") == "regex":
        pos = 0
        for m in found or []:
            if not m:
                continue
            j = text.find(m, pos)
            if j < 0:
                j = text.find(m)
                if j < 0:
                    continue
            spans.append((j, j + len(m)))
            pos = j + len(m)
            if len(spans) >= max_snips:
                break
    else:
        needle = search.get("text") or ""
        flags = re.IGNORECASE if search.get("icase") else 0
        for m in re.finditer(re.escape(needle), text, flags):
            spans.append((m.start(), m.end()))
            if len(spans) >= max_snips:
                break
    out = []
    for a, b in spans:
        # widen the context to the nearest space, so a snippet never starts inside a word
        # (in Odia, cutting mid-word can leave a dangling vowel sign)
        s = max(0, a - ctx)
        if s > 0:
            sp = text.rfind(" ", max(0, s - 20), s)
            s = sp + 1 if sp >= 0 else s
        e = min(len(text), b + ctx)
        if e < len(text):
            sp = text.find(" ", e, e + 20)
            e = sp if sp >= 0 else e
        out.append(
            {
                "pre": ("…" if s > 0 else "") + text[s:a].replace("\n", " ⏎ "),
                "match": text[a:b].replace("\n", " ⏎ "),
                "post": text[b:e].replace("\n", " ⏎ ") + ("…" if e < len(text) else ""),
            }
        )
    return out


def histogram(vals: list, bins: int = 40) -> dict:
    """Bins for a numeric distribution; log-spaced when the data is positive and very skewed."""
    vals = sorted(float(x) for x in vals)
    n = len(vals)
    lo, hi = vals[0], vals[-1]

    def q(p):
        k = (n - 1) * p
        f = math.floor(k)
        c = min(f + 1, n - 1)
        return vals[f] + (vals[c] - vals[f]) * (k - f)

    stats = {
        "count": n,
        "min": lo,
        "max": hi,
        "mean": sum(vals) / n,
        "p01": q(0.01),
        "p10": q(0.1),
        "p25": q(0.25),
        "median": q(0.5),
        "p75": q(0.75),
        "p90": q(0.9),
        "p99": q(0.99),
    }
    integer = all(float(x).is_integer() for x in vals[:: max(1, n // 500)])
    log = lo > 0 and hi / max(lo, 1e-12) > 100 and stats["median"] > 0 and hi / stats["median"] > 20
    if hi == lo:
        edges = [lo, lo + 1]
    elif log:
        a, b = math.log10(lo), math.log10(hi)
        edges = [10 ** (a + (b - a) * i / bins) for i in range(bins + 1)]
    else:
        width = (hi - lo) / bins
        if integer and hi - lo + 1 <= bins:
            edges = [lo + i for i in range(int(hi - lo) + 2)]
        else:
            edges = [lo + width * i for i in range(bins + 1)]
    counts = [0] * (len(edges) - 1)
    for x in vals:
        i = bisect.bisect_right(edges, x) - 1
        counts[min(max(i, 0), len(counts) - 1)] += 1
    return {"edges": edges, "counts": counts, "log": log, "integer": integer, **stats}
