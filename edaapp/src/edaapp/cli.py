"""edaapp command line: `uv run edaapp [--port 8765] [--hub [--hub-revision REV] | --data PATH] [--state DIR]`.

edaapp lives in the dataset's repository. Without options it serves that repository (the folder
above edaapp/) as the dataset "odia-wikipedia" (roots.py); --hub serves the published copy on
Hugging Face (hub.py); --data serves any other data root. Decisions go to the repository's
reviews/reviews.jsonl, the log the build reads, unless --state names a trial folder.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

HOST = "127.0.0.1"  # local only, by design; there is no option to change it


def resolve_state(path: Path | str) -> Path:
    """The review state folder: relative paths are taken from the current directory, and the
    result must lie inside edaapp/ or the repository's reviews/ folder (UnsafePathError otherwise)."""
    from .paths import SafeWriter

    p = Path(path).expanduser()
    if not p.is_absolute():
        p = Path.cwd() / p
    return SafeWriter().guard(p)


def repo_data_root() -> tuple[Path, list[dict]]:
    """The repository as the dataset "odia-wikipedia", through the repository data root."""
    from . import roots

    try:
        root, source = roots.repo_root()
    except roots.RootError as e:
        sys.exit(f"edaapp: {e}")
    print(f"edaapp: {roots.describe_repo(source)}")
    return root, [source]


def hub_data_root(revision: str) -> tuple[Path, list[dict]]:
    """Download (or update) the Hugging Face datasets and link them into the hub data root; exits
    with a message when that is not possible."""
    from . import hub

    for name, repo_id in hub.HUB_DATASETS.items():
        print(f"edaapp: {name}: checking Hugging Face dataset {repo_id} at {revision} ...", flush=True)
    try:
        root, sources = hub.build_root(revision)
    except hub.HubError as e:
        sys.exit(f"edaapp: {e}")
    for s in sources:
        print(f"edaapp: {s.describe()}")
    return root, [s.public() for s in sources]


def main(argv: list[str] | None = None) -> None:
    from .paths import REPO_DATASET, REVIEWS_DIR, UnsafePathError

    ap = argparse.ArgumentParser(
        prog="edaapp",
        description=f"Explore and review the {REPO_DATASET} dataset: this repository by default, its published "
        "copy on Hugging Face (--hub), or any data folder (--data).",
    )
    ap.add_argument("--port", type=int, default=8765, help="port on 127.0.0.1 (default 8765)")
    src = ap.add_mutually_exclusive_group()
    src.add_argument(
        "--hub",
        action="store_true",
        help="serve the published copy on Hugging Face instead of this repository",
    )
    src.add_argument(
        "--data",
        type=Path,
        default=None,
        help="serve another data root: a folder holding one folder per dataset",
    )
    ap.add_argument(
        "--hub-revision",
        metavar="REV",
        default=None,
        help="branch, tag or commit of the Hugging Face copy (default main); implies --hub",
    )
    ap.add_argument(
        "--state",
        type=Path,
        default=REVIEWS_DIR,
        help=f"folder for reviews.jsonl (default {REVIEWS_DIR}, the log the build reads). A trial folder "
        "must be inside edaapp/, e.g. .cache/try-state, so real decisions are not touched.",
    )
    ap.add_argument("--log-level", default="info", choices=["debug", "info", "warning"])
    args = ap.parse_args(argv)
    if args.data is not None and args.hub_revision is not None:
        ap.error("--hub-revision picks a revision of the Hugging Face copy; it can't be used with --data")

    try:
        state = resolve_state(args.state)
    except UnsafePathError as e:
        sys.exit(f"edaapp: --state {args.state}: {e}")
    logging.basicConfig(level=getattr(logging, args.log_level.upper()), format="%(levelname)s %(name)s: %(message)s")
    if args.log_level != "debug":  # the Hub client logs every HTTP request of a download at INFO
        for name in ("httpx", "httpx2"):
            logging.getLogger(name).setLevel(logging.WARNING)

    sources: list[dict] = []
    if args.data is not None:
        data = args.data.expanduser().resolve()
        if not data.is_dir():
            sys.exit(f"edaapp: data root {data} is not a directory")
        print(f"edaapp: data from a local folder (--data): {data}")
    elif args.hub or args.hub_revision is not None:
        data, sources = hub_data_root(args.hub_revision or "main")
    else:
        data, sources = repo_data_root()

    import uvicorn

    from .server import create_app

    hosts = {f"{HOST}:{args.port}", f"localhost:{args.port}"}
    app = create_app(data, state_dir=state, allowed_hosts=hosts, sources=sources)
    names = sorted(app.state.catalog.datasets())
    print(f"edaapp: data root {data} ({len(names)} dataset{'s' if len(names) != 1 else ''}: {', '.join(names)})")
    print(f"edaapp: reviews go to {app.state.reviews.path}")
    print(f"edaapp: open http://{HOST}:{args.port}/", flush=True)
    uvicorn.run(app, host=HOST, port=args.port, log_level=args.log_level, access_log=False)


if __name__ == "__main__":
    main()
