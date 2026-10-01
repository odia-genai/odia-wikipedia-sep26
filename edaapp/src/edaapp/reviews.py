"""Review decisions: an append-only JSONL log, one event per line.

    {"ts": ISO-8601, "dataset": str, "id": int, "title": str,
     "verdict": "keep" | "drop" | "fix" | null, "note": str,
     "drop_paragraphs": [{"para": int, "sha1": str}], "text_sha1": str}

Each event is the full decision for (dataset, id) at that moment; the latest event for a pair
wins, in file order. Nothing is ever rewritten: undo appends an event that restores the
previous state. An event with verdict null, an empty note and no dropped paragraphs means
"no decision". Dropped paragraphs are identified by the sha1 of their text (the build matches
them by content, never paragraph 0); `para` is where the paragraph was when it was recorded.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path

from .paths import SafeWriter

VERDICTS = ("keep", "drop", "fix")
FIELDS = ("ts", "dataset", "id", "title", "verdict", "note", "drop_paragraphs", "text_sha1")
SHA1_RE = re.compile(r"^[0-9a-f]{40}$")


class InvalidEvent(ValueError):
    pass


def now_iso() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def make_event(
    dataset: str,
    id: int,
    title: str,
    verdict: str | None,
    note: str,
    drop_paragraphs: list[dict],
    text_sha1: str,
    ts: str | None = None,
) -> dict:
    """Build and validate an event, with keys in the documented order."""
    ev = {
        "ts": ts or now_iso(),
        "dataset": dataset,
        "id": id,
        "title": title,
        "verdict": verdict,
        "note": note,
        "drop_paragraphs": sorted(
            ({"para": int(d["para"]), "sha1": d["sha1"]} for d in drop_paragraphs),
            key=lambda d: (d["para"], d["sha1"]),
        ),
        "text_sha1": text_sha1,
    }
    validate(ev)
    return ev


def validate(ev: dict) -> None:
    if not isinstance(ev, dict) or list(ev.keys()) != list(FIELDS):
        raise InvalidEvent(f"event keys must be exactly {FIELDS}")
    if not isinstance(ev["ts"], str):
        raise InvalidEvent("ts must be an ISO-8601 string")
    try:
        dt.datetime.fromisoformat(ev["ts"].replace("Z", "+00:00"))
    except ValueError as e:
        raise InvalidEvent(f"ts is not ISO-8601: {ev['ts']!r}") from e
    if not isinstance(ev["dataset"], str) or not ev["dataset"]:
        raise InvalidEvent("dataset must be a non-empty string")
    if not isinstance(ev["id"], int) or isinstance(ev["id"], bool):
        raise InvalidEvent("id must be an integer")
    if not isinstance(ev["title"], str):
        raise InvalidEvent("title must be a string")
    if ev["verdict"] is not None and ev["verdict"] not in VERDICTS:
        raise InvalidEvent(f"verdict must be one of {VERDICTS} or null")
    if not isinstance(ev["note"], str):
        raise InvalidEvent("note must be a string")
    if not isinstance(ev["drop_paragraphs"], list):
        raise InvalidEvent("drop_paragraphs must be a list")
    seen = set()
    for d in ev["drop_paragraphs"]:
        if not isinstance(d, dict) or set(d) != {"para", "sha1"}:
            raise InvalidEvent("each dropped paragraph is {para, sha1}")
        if not isinstance(d["para"], int) or isinstance(d["para"], bool) or d["para"] < 0:
            raise InvalidEvent("para must be a non-negative integer")
        if not isinstance(d["sha1"], str) or not SHA1_RE.match(d["sha1"]):
            raise InvalidEvent("sha1 must be 40 lowercase hex characters")
        # the build matches dropped paragraphs by content, so the sha1 is the key
        if d["sha1"] in seen:
            raise InvalidEvent(f"paragraph sha1 {d['sha1'][:12]}… listed twice")
        seen.add(d["sha1"])
    if not isinstance(ev["text_sha1"], str) or not SHA1_RE.match(ev["text_sha1"]):
        raise InvalidEvent("text_sha1 must be 40 lowercase hex characters")


def is_empty(ev: dict) -> bool:
    return ev["verdict"] is None and not ev["note"] and not ev["drop_paragraphs"]


def same_decision(a: dict | None, b: dict | None) -> bool:
    """Whether two events record the same decision (ignoring ts and title)."""
    if a is None or b is None:
        return (a is None or is_empty(a)) and (b is None or is_empty(b))
    return (
        a["verdict"] == b["verdict"]
        and a["note"] == b["note"]
        and a["drop_paragraphs"] == b["drop_paragraphs"]
        and a["text_sha1"] == b["text_sha1"]
    )


@dataclass
class _State:
    ino: int | None = None
    sig: tuple | None = None  # (ino, size, mtime) when the file was fully parsed
    offset: int = 0  # bytes of the file already parsed (always at a line end)
    history: dict[tuple[str, int], list[dict]] = field(default_factory=dict)
    bad_lines: int = 0
    events: int = 0
    version: int = 0  # bumps whenever the replayed state changes


class ReviewStore:
    """Replays the log (incrementally, since it only grows) and appends to it."""

    def __init__(self, path: Path, writer: SafeWriter):
        self.path = writer.guard(path)
        self.writer = writer
        self._lock = threading.RLock()
        self._s = _State()

    # -- reading ---------------------------------------------------------------------------
    def _refresh(self) -> None:
        try:
            st = os.stat(self.path)
        except FileNotFoundError:
            if self._s.ino is not None or self._s.events:
                self._s = _State(version=self._s.version + 1)
            return
        sig = (st.st_ino, st.st_size, st.st_mtime_ns)
        s = self._s
        if sig == s.sig:
            return
        # Append-only: if the same file only grew, parse just the new bytes. Anything else
        # (replaced by another file, truncated) is replayed from the start.
        if s.ino is not None and (st.st_ino != s.ino or st.st_size < s.offset):
            s = self._s = _State(version=s.version + 1)
        s.ino = st.st_ino
        with open(self.path, "rb") as f:
            f.seek(s.offset)
            data = f.read()
        end = data.rfind(b"\n")
        if end >= 0:  # parse complete lines only; a line still being written waits
            s.offset += end + 1
            for raw in data[: end + 1].split(b"\n"):
                if not raw.strip():
                    continue
                try:
                    ev = json.loads(raw)
                    validate(ev)
                except Exception:
                    s.bad_lines += 1
                    continue
                s.history.setdefault((ev["dataset"], ev["id"]), []).append(ev)
                s.events += 1
            s.version += 1
        s.sig = sig if s.offset == st.st_size else None

    def version(self) -> int:
        with self._lock:
            self._refresh()
            return self._s.version

    def stats(self) -> dict:
        with self._lock:
            self._refresh()
            return {
                "events": self._s.events,
                "bad_lines": self._s.bad_lines,
                "path": str(self.path),
                "version": self._s.version,
            }

    def current(self, dataset: str) -> dict[int, dict]:
        """id -> latest event, for every article of the dataset that has one."""
        with self._lock:
            self._refresh()
            return {i: h[-1] for (d, i), h in self._s.history.items() if d == dataset}

    def get(self, dataset: str, id: int) -> dict | None:
        with self._lock:
            self._refresh()
            h = self._s.history.get((dataset, id))
            return h[-1] if h else None

    def history(self, dataset: str, id: int) -> list[dict]:
        with self._lock:
            self._refresh()
            return list(self._s.history.get((dataset, id), []))

    # -- writing ---------------------------------------------------------------------------
    def append(self, events: list[dict]) -> list[dict]:
        for ev in events:
            validate(ev)
        if not events:
            return []
        lines = [json.dumps(ev, ensure_ascii=False) for ev in events]
        with self._lock:
            self._refresh()
            self.writer.append_lines(self.path, lines)
            self._refresh()
        return events

    def undo_event(self, dataset: str, id: int, title: str, current_text_sha1: str) -> dict | None:
        """The event that restores the decision before the latest one (None if nothing to undo)."""
        h = self.history(dataset, id)
        if not h:
            return None
        if len(h) >= 2:
            prev = h[-2]
            return make_event(
                dataset, id, title, prev["verdict"], prev["note"], prev["drop_paragraphs"], prev["text_sha1"]
            )
        return make_event(dataset, id, title, None, "", [], current_text_sha1)
