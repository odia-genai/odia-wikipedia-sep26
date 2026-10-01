"""Parse quality/review-first.md into actionable items.

An item is a numbered list entry whose first line looks like

    12. **<title>** (id 49282, para 2, `garbled`)
    12. **<title>** (id 98338, whole article, `article`)

followed by its indented body (reasons, a quoted excerpt). Everything else (the intro, the
legend, headings between items) is kept as Markdown segments in document order. If no line
has that shape the caller falls back to rendering the whole file.
"""

from __future__ import annotations

import re
import textwrap
from dataclasses import dataclass

ITEM = re.compile(r"^(?P<rank>\d+)[.)]\s+\*\*(?P<title>.+?)\*\*\s*\((?P<meta>[^()]*(?:\([^()]*\)[^()]*)*)\)\s*$")
META_ID = re.compile(r"(?:^|[\s,(])(?:page )?id (\d+)\b")
META_PARA = re.compile(r"\bpara(?:graph)? (\d+)\b")
META_WHOLE = re.compile(r"\bwhole article\b")
META_TYPE = re.compile(r"`([^`]+)`")


@dataclass
class Item:
    rank: int
    title: str
    id: int
    para: int | None  # the paragraph index given in the file (None: whole article)
    type: str | None
    body: str  # the item's Markdown body, dedented
    line: int  # 1-based line of the item in the file


def parse_meta(meta: str) -> dict | None:
    m = META_ID.search(meta)
    if not m:
        return None
    p = META_PARA.search(meta)
    t = META_TYPE.search(meta)
    return {
        "id": int(m.group(1)),
        "para": int(p.group(1)) if p else None,
        "whole": bool(META_WHOLE.search(meta)) or p is None,
        "type": t.group(1).strip() if t else None,
    }


def parse(text: str) -> list[dict]:
    """Segments in order: {"kind": "md", "text"} and {"kind": "item", "item": Item}."""
    lines = text.split("\n")
    segs: list[dict] = []
    buf: list[str] = []

    def flush():
        if any(x.strip() for x in buf):
            segs.append({"kind": "md", "text": "\n".join(buf).strip("\n")})
        buf.clear()

    i = 0
    while i < len(lines):
        m = ITEM.match(lines[i])
        meta = parse_meta(m.group("meta")) if m else None
        if not meta:
            buf.append(lines[i])
            i += 1
            continue
        flush()
        j = i + 1
        # the body: blank or indented lines; the first unindented line ends the item
        while j < len(lines) and (not lines[j].strip() or lines[j][:1] in (" ", "\t")):
            j += 1
        body = textwrap.dedent("\n".join(lines[i + 1 : j])).strip("\n")
        item = Item(int(m.group("rank")), m.group("title").strip(), meta["id"], meta["para"], meta["type"], body, i + 1)
        segs.append({"kind": "item", "item": item})
        i = j
    flush()
    return segs
