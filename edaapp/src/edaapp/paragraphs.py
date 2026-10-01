"""Paragraphs, as the shared data contract defines them.

Paragraph i of an article is text.split("\\n\\n")[i]. Index 0 is the "# title" heading. A heading,
a list block (its items joined by single newlines) and a table each count as one paragraph.
DuckDB's string_split(text, '\\n\\n') gives exactly the same pieces (checked over the whole
corpus), so SQL and Python agree on paragraph numbers.
"""

from __future__ import annotations

import hashlib
import re

SEP = "\n\n"

# ASCII digits only: that is what CommonMark reads as an ordered-list marker (and what RE2 \d means)
_LIST = re.compile(r"^\s*(?:[-*+]|[0-9]+[.)])\s")
_HEADING = re.compile(r"#{1,6} ")


def split(text: str) -> list[str]:
    return text.split(SEP)


def sha1(s: str) -> str:
    """sha1 hex of the UTF-8 bytes, as used for text_sha1 and paragraph hashes."""
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


def kind(p: str) -> str:
    """A coarse block type: heading, table, list, math (display), or para."""
    if _HEADING.match(p):  # "# " … "###### ", not a "#!/usr/bin/perl" code line
        return "heading"
    if p.startswith("|"):
        return "table"
    if _LIST.match(p):
        return "list"
    if p.startswith("$$"):
        return "math"
    return "para"


# The same classification in SQL, for the paragraph table built in DuckDB.
KIND_SQL = """CASE
  WHEN regexp_matches(p, '^#{1,6} ') THEN 'heading'
  WHEN starts_with(p, '|') THEN 'table'
  WHEN regexp_matches(p, '^\\s*(?:[-*+]|[0-9]+[.)])\\s') THEN 'list'
  WHEN starts_with(p, '$$') THEN 'math'
  ELSE 'para' END"""

KINDS = ["heading", "para", "list", "table", "math"]
