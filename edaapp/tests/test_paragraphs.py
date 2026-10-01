import duckdb
import pytest

from edaapp import paragraphs as P
from edaapp.render import render

CASES = [
    "# t\n\nfirst\n\nsecond",
    "# t",
    "",
    "a\n\n\n\nb",  # an empty paragraph between
    "a\n\n\nb",  # three newlines: the third belongs to the next paragraph
    "# t\n\n- one\n- two\n  - sub\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n$$x$$",
    "ends with a blank\n\n",
]


@pytest.mark.parametrize("text", CASES)
def test_split_is_str_split_and_matches_duckdb(text):
    assert P.split(text) == text.split("\n\n")
    con = duckdb.connect()
    got = con.execute("SELECT string_split($t, chr(10) || chr(10))", {"t": text}).fetchone()[0]
    assert got == text.split("\n\n")


def test_unnest_with_subscripts_numbers_paragraphs_from_zero():
    con = duckdb.connect()
    rows = con.execute(
        "WITH s AS (SELECT string_split($t, chr(10) || chr(10)) AS ps) "
        "SELECT unnest(ps), generate_subscripts(ps, 1) - 1 FROM s",
        {"t": CASES[0]},
    ).fetchall()
    assert rows == [("# t", 0), ("first", 1), ("second", 2)]


def test_sha1_is_utf8_hex():
    import hashlib

    assert P.sha1("ଓଡ଼ିଆ") == hashlib.sha1("ଓଡ଼ିଆ".encode()).hexdigest()
    con = duckdb.connect()
    assert con.execute("SELECT sha1($t)", {"t": "ଓଡ଼ିଆ ପାଠ"}).fetchone()[0] == P.sha1("ଓଡ଼ିଆ ପାଠ")


@pytest.mark.parametrize(
    "p,kind",
    [
        ("# t", "heading"),
        ("## h", "heading"),
        ("| a |", "table"),
        ("- x\n- y", "list"),
        ("1. x", "list"),
        ("  - x", "list"),
        ("$$x$$", "math"),
        ("text", "para"),
        ("1\\. not a list", "para"),
        ("-not a list", "para"),
        # a heading is 1-6 "#" and a space, as CommonMark reads it
        ("###### six", "heading"),
        ("####### seven", "para"),
        ("#!/usr/bin/perl", "para"),
        ("#hashtag", "para"),
    ],
)
def test_kind_python_and_sql_agree(p, kind):
    assert P.kind(p) == kind
    con = duckdb.connect()
    assert con.execute(f"SELECT {P.KIND_SQL} FROM (SELECT $p AS p)", {"p": p}).fetchone()[0] == kind


def test_render_blocks():
    assert "<table>" in render("| a | b |\n|---|---|\n| 1 | $x \\| y$ |")
    assert '<span class="math inline">x | y</span>' in render("| a | b |\n|---|---|\n| 1 | $x \\| y$ |")
    assert '<div class="math block">' in render("$$E = mc^2$$")
    assert "&lt;script&gt;" in render("<script>alert(1)</script>")
    assert "$5" in render("price \\$5 and \\$6") and "math" not in render("price \\$5 and \\$6")
    assert 'href="javascript' not in render("[x](javascript:alert(1))")
