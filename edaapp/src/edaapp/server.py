"""HTTP API and static page. Every endpoint is under /api; the page is static/index.html."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from fastapi import Body, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import discovery
from .filters import FilterError
from .paths import APP_ROOT, REVIEWS_DIR, SafeWriter, UnsafePathError
from .reviews import InvalidEvent, ReviewStore
from .service import Conflict, NotFound, Service
from .store import Catalog, DataError

log = logging.getLogger("edaapp")
STATIC = APP_ROOT / "static"


def J(obj, status: int = 200) -> JSONResponse:
    return JSONResponse(obj, status_code=status)


def create_app(
    data_root: Path,
    *,
    writer: SafeWriter | None = None,
    state_dir: Path = REVIEWS_DIR,
    allowed_hosts: set[str] | None = None,
    cache_dir: Path | None = None,
    sources: list[dict] | None = None,
) -> FastAPI:
    """The app over `data_root`. Decisions are appended to `state_dir`/reviews.jsonl (default: the
    repository's reviews/reviews.jsonl, the log the build reads). `sources` says where the datasets
    come from (this repository, or the Hugging Face snapshots), for the Home page; empty for a
    data root given with --data."""
    writer = writer or SafeWriter()
    reviews = ReviewStore(writer.guard(Path(state_dir) / "reviews.jsonl"), writer)
    kw = {"cache_dir": cache_dir} if cache_dir else {}
    catalog = Catalog(data_root, writer, reviews, **kw)
    svc = Service(catalog, reviews, sources)
    app = FastAPI(title="edaapp", docs_url="/api/docs", redoc_url=None, openapi_url="/api/openapi.json")
    app.state.svc = svc
    app.state.catalog = catalog
    app.state.reviews = reviews
    files_cache: dict[str, tuple[float, dict]] = {}

    # -- guards: only local hosts (no DNS rebinding), same-origin writes ------------------------
    @app.middleware("http")
    async def local_only(request: Request, call_next):
        if allowed_hosts is not None:
            host = request.headers.get("host", "")
            if host not in allowed_hosts:
                return J({"error": f"host {host!r} not allowed"}, 403)
            if request.method not in ("GET", "HEAD"):
                origin = request.headers.get("origin")
                if origin and origin.split("://", 1)[-1] not in allowed_hosts:
                    return J({"error": "cross-origin write refused"}, 403)
                if "application/json" not in request.headers.get("content-type", ""):
                    return J({"error": "send JSON"}, 415)
        t0 = time.time()
        resp = await call_next(request)
        if request.url.path.startswith("/api/"):
            resp.headers["x-elapsed-ms"] = f"{(time.time() - t0) * 1000:.1f}"
            resp.headers["cache-control"] = "no-store"
        elif request.url.path.startswith("/static/") or request.url.path == "/":
            resp.headers["cache-control"] = "no-cache"
        return resp

    @app.exception_handler(FilterError)
    async def _bad(request, exc):
        return J({"error": str(exc)}, 400)

    @app.exception_handler(InvalidEvent)
    async def _bad_event(request, exc):
        return J({"error": str(exc)}, 400)

    @app.exception_handler(NotFound)
    async def _nf(request, exc):
        return J({"error": str(exc)}, 404)

    @app.exception_handler(KeyError)
    async def _nf2(request, exc):
        return J({"error": f"not found: {exc}"}, 404)

    @app.exception_handler(Conflict)
    async def _conflict(request, exc):
        return J({"error": str(exc)}, 409)

    @app.exception_handler(DataError)
    async def _data(request, exc):
        return J({"error": str(exc)}, 503)

    @app.exception_handler(UnsafePathError)
    async def _unsafe(request, exc):
        log.error("blocked write: %s", exc)
        return J({"error": str(exc)}, 500)

    def items(request: Request) -> list[tuple[str, str]]:
        return list(request.query_params.multi_items())

    def snap(ds: str, request: Request):
        return svc.snap(ds, request.query_params.get("v"))

    # -- page ----------------------------------------------------------------------------------
    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html", media_type="text/html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    # -- api -----------------------------------------------------------------------------------
    @app.get("/api/health")
    def health():
        return J({"ok": True, "data_root": str(catalog.data_root), "datasets": sorted(catalog.datasets())})

    @app.get("/api/changes")
    def changes():
        catalog.refresh()
        return J(
            {
                "data": catalog.changes,  # any file discovery looks at
                "tables": catalog.table_changes,  # the same, minus Markdown-only edits
                "reviews": reviews.version(),
                "datasets": {n: str(hash(d.signature)) for n, d in catalog.datasets().items()},
            }
        )

    @app.get("/api/datasets")
    def datasets():
        return J(svc.home())

    @app.get("/api/d/{ds}/schema")
    def schema(ds: str, request: Request):
        return J(svc.schema(snap(ds, request)))

    @app.get("/api/d/{ds}/overview")
    def overview(ds: str, request: Request):
        return J(svc.overview(snap(ds, request)))

    @app.get("/api/d/{ds}/dropped")
    def dropped(ds: str, request: Request, reason: str):
        return J(svc.dropped_titles(snap(ds, request), reason))

    @app.get("/api/d/{ds}/excluded")
    def excluded(ds: str, request: Request):
        return J(svc.excluded(snap(ds, request), items(request)))

    @app.get("/api/d/{ds}/removed-blocks")
    def removed_blocks(ds: str, request: Request):
        return J(svc.removed_blocks(snap(ds, request), items(request)))

    @app.get("/api/d/{ds}/browse")
    def browse(ds: str, request: Request):
        return J(svc.browse(snap(ds, request), items(request)))

    @app.get("/api/d/{ds}/position")
    def position(ds: str, request: Request, id: int):
        return J(svc.position(snap(ds, request), id, items(request)))

    @app.get("/api/d/{ds}/article/{id}")
    def article(ds: str, id: int, request: Request):
        return J(svc.article(snap(ds, request), id))

    @app.post("/api/d/{ds}/review")
    def review(ds: str, request: Request, body: dict = Body(...)):  # noqa: B008
        s = svc.snap(ds, body.get("v"))
        try:
            id_ = int(body["id"])
        except (KeyError, TypeError, ValueError):
            raise FilterError("id is required") from None
        verdict = body.get("verdict")
        note = body.get("note") or ""
        drops = body.get("drop_paras") or []
        if not isinstance(note, str) or not isinstance(drops, list):
            raise FilterError("note must be a string and drop_paras a list of paragraph numbers")
        return J(
            svc.save_review(s, id_, verdict, note, drops, body.get("text_sha1"), bool(body.get("discard_missing")))
        )

    @app.post("/api/d/{ds}/undo")
    def undo(ds: str, request: Request, body: dict = Body(...)):  # noqa: B008
        s = svc.snap(ds, body.get("v"))
        return J(svc.undo(s, int(body["id"])))

    @app.get("/api/d/{ds}/decisions")
    def decisions(ds: str, request: Request):
        return J(svc.decisions(snap(ds, request), items(request)))

    @app.get("/api/d/{ds}/decisions/export")
    def export(ds: str, request: Request, format: str = "jsonl", cleared: int = 0):
        svc.snap(ds, None)  # 404 for unknown datasets
        evs = svc.export(ds, include_cleared=bool(cleared))
        stamp = time.strftime("%Y%m%d-%H%M%S")
        if format == "json":
            body = json.dumps(evs, ensure_ascii=False, indent=1)
            media, ext = "application/json", "json"
        elif format == "jsonl":
            body = "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in evs)
            media, ext = "application/x-ndjson", "jsonl"
        else:
            raise FilterError("format must be json or jsonl")
        return Response(
            body.encode("utf-8"),
            media_type=f"{media}; charset=utf-8",
            headers={"content-disposition": f'attachment; filename="{ds}-decisions-{stamp}.{ext}"'},
        )

    @app.get("/api/reviews/log")
    def review_log():
        if not reviews.path.exists():
            return Response(b"", media_type="application/x-ndjson")
        return FileResponse(reviews.path, media_type="application/x-ndjson", filename="reviews.jsonl")

    @app.get("/api/d/{ds}/patterns/templates")
    def templates(ds: str, request: Request):
        return J(svc.templates(snap(ds, request), items(request)))

    @app.get("/api/d/{ds}/patterns/template/{key}")
    def template(ds: str, key: str, request: Request):
        return J(svc.template(snap(ds, request), key, items(request)))

    @app.get("/api/d/{ds}/patterns/search")
    def para_search(ds: str, request: Request):
        return J(svc.para_search(snap(ds, request), items(request)))

    @app.post("/api/d/{ds}/bulk/preview")
    def bulk_preview(ds: str, body: dict = Body(...)):  # noqa: B008
        return J(svc.bulk_plan(svc.snap(ds, body.get("v")), body.get("spec") or {}))

    @app.post("/api/d/{ds}/bulk/apply")
    def bulk_apply(ds: str, body: dict = Body(...)):  # noqa: B008
        token = body.get("token")
        if not token:
            raise FilterError("apply needs the token from a preview")
        return J(svc.bulk_apply(svc.snap(ds, body.get("v")), body.get("spec") or {}, token))

    @app.get("/api/d/{ds}/reports")
    def reports(ds: str):
        d = catalog.dataset(ds)

        def entry(fs, group):
            return {"name": fs.path.name, "group": group, "size": fs.size, "mtime": fs.mtime_ns / 1e9}

        return J(
            {
                "reports": [entry(r, "quality") for r in d.reports],
                "docs": [entry(r, "docs") for r in d.docs],
                "methodology": entry(d.methodology, "docs") if d.methodology else None,
                "review_first": d.review_first.path.name if d.review_first else None,
            }
        )

    @app.get("/api/d/{ds}/report")
    def report(ds: str, request: Request, name: str, group: str = "quality"):
        return J(svc.doc(snap(ds, request), group, name))

    @app.get("/api/d/{ds}/methodology")
    def methodology(ds: str, request: Request):
        return J(svc.methodology(snap(ds, request)))

    @app.get("/api/d/{ds}/review-first")
    def review_first(ds: str, request: Request):
        return J(svc.review_first(snap(ds, request)))

    @app.get("/api/d/{ds}/files")
    def files(ds: str):
        d = catalog.dataset(ds)
        hit = files_cache.get(ds)
        if hit and time.time() - hit[0] < 10:
            return J(hit[1])
        tree = discovery.file_tree(d.dir)
        out = {"root": str(d.dir), "tree": tree}
        files_cache[ds] = (time.time(), out)
        return J(out)

    return app
