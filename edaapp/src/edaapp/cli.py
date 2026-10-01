"""edaapp command line: `uv run edaapp [--port 8765] [--data PATH | --hub-revision REV] [--state DIR]`.

Without --data the datasets come from the Hugging Face Hub (see hub.py); --data serves a local data
root instead, e.g. a checkout of a dataset you are editing.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

HOST = "127.0.0.1"  # local only, by design; there is no option to change it


def resolve_state(path: Path | str) -> Path:
    """The review state folder: relative paths are taken from the current directory, and the
    result must lie inside edaapp/ (UnsafePathError otherwise)."""
    from .paths import APP_ROOT, SafeWriter

    p = Path(path).expanduser()
    if not p.is_absolute():
        p = Path.cwd() / p
    return SafeWriter(APP_ROOT).guard(p)


def hub_data_root(revision: str) -> tuple[Path, list]:
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
    return root, sources


def main(argv: list[str] | None = None) -> None:
    from .paths import STATE_DIR, UnsafePathError

    ap = argparse.ArgumentParser(
        prog="edaapp",
        description="Explore and improve datasets: by default the Hugging Face ones, or those in a local data folder.",
    )
    ap.add_argument("--port", type=int, default=8765, help="port on 127.0.0.1 (default 8765)")
    ap.add_argument(
        "--data",
        type=Path,
        default=None,
        help="a local data root holding one folder per dataset (e.g. a checkout of the dataset you are editing); "
        "default: the Hugging Face datasets",
    )
    ap.add_argument(
        "--hub-revision",
        metavar="REV",
        default=None,
        help="branch, tag or commit of the Hugging Face datasets (default main); not with --data",
    )
    ap.add_argument(
        "--state",
        type=Path,
        default=STATE_DIR,
        help=f"folder for reviews.jsonl; must be inside edaapp/ (default {STATE_DIR}). "
        "Use a scratch folder such as .cache/try-state for test runs, so real decisions are not touched.",
    )
    ap.add_argument("--log-level", default="info", choices=["debug", "info", "warning"])
    args = ap.parse_args(argv)
    if args.data is not None and args.hub_revision is not None:
        ap.error("--hub-revision picks a revision of the Hugging Face datasets; it can't be used with --data")

    try:
        state = resolve_state(args.state)
    except UnsafePathError as e:
        sys.exit(f"edaapp: --state {args.state}: {e}")
    logging.basicConfig(level=getattr(logging, args.log_level.upper()), format="%(levelname)s %(name)s: %(message)s")
    if args.log_level != "debug":  # the Hub client logs every HTTP request of a download at INFO
        for name in ("httpx", "httpx2"):
            logging.getLogger(name).setLevel(logging.WARNING)

    sources = []
    if args.data is None:
        data, sources = hub_data_root(args.hub_revision or "main")
    else:
        data = args.data.expanduser().resolve()
        if not data.is_dir():
            sys.exit(f"edaapp: data root {data} is not a directory")
        print(f"edaapp: data from a local folder (--data): {data}")

    import uvicorn

    from .server import create_app

    hosts = {f"{HOST}:{args.port}", f"localhost:{args.port}"}
    app = create_app(data, state_dir=state, allowed_hosts=hosts, sources=[s.public() for s in sources])
    names = sorted(app.state.catalog.datasets())
    print(f"edaapp: data root {data} ({len(names)} dataset{'s' if len(names) != 1 else ''}: {', '.join(names)})")
    print(f"edaapp: reviews go to {app.state.reviews.path}")
    print(f"edaapp: open http://{HOST}:{args.port}/", flush=True)
    uvicorn.run(app, host=HOST, port=args.port, log_level=args.log_level, access_log=False)


if __name__ == "__main__":
    main()
