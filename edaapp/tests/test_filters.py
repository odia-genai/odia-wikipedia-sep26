import duckdb
import pytest

from edaapp.filters import Column, FilterError, Params, build_browse, filter_clauses, qi


@pytest.fixture
def con():
    c = duckdb.connect()
    c.execute("""CREATE TABLE t AS SELECT * FROM (VALUES
        (1, 'ଅ', 10, 0.5, true, 'a', ['x', 'y'], 'hello world', '2026-01-01T00:00:00Z', 7),
        (2, 'ଆ', 20, 0.9, false, 'b', ['y'], 'another', '2025-06-01T00:00:00Z', NULL),
        (3, 'ଇ', 30, NULL, NULL, NULL, [], 'x''; DROP TABLE t; --', '2024-01-01T00:00:00Z', 3)
    ) v(id, title, words, ratio, flag, cat, tags, note, ts, "we""ird.col")""")
    return c


COLS = {
    c.key: c
    for c in [
        Column("id", "BIGINT", "numeric", "corpus"),
        Column("title", "VARCHAR", "text", "corpus"),
        Column("words", "BIGINT", "numeric", "corpus"),
        Column("ratio", "DOUBLE", "numeric", "corpus"),
        Column("flag", "BOOLEAN", "bool", "corpus"),
        Column("cat", "VARCHAR", "category", "corpus"),
        Column("tags", "VARCHAR[]", "list", "ann"),
        Column("note", "VARCHAR", "text", "corpus"),
        Column("ts", "VARCHAR", "date", "corpus"),
        Column('we"ird.col', "BIGINT", "numeric", "ann"),
        Column("text", "VARCHAR", "text", "corpus", filterable=False, sortable=False),
    ]
}


def ids(con, items):
    p = Params()
    where = filter_clauses(items, COLS, p)
    sql = "SELECT id FROM t" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY id"
    return [r[0] for r in con.execute(sql, p.values).fetchall()]


def test_each_operator(con):
    assert ids(con, [("min.words", "15")]) == [2, 3]
    assert ids(con, [("min.words", "15"), ("max.words", "25")]) == [2]
    assert ids(con, [("is.flag", "true")]) == [1]
    assert ids(con, [("is.flag", "false")]) == [2]
    assert ids(con, [("in.cat", "a"), ("in.cat", "b")]) == [1, 2]
    assert ids(con, [("in.cat", "__null__")]) == [3]
    assert ids(con, [("in.cat", "a"), ("in.cat", "__null__")]) == [1, 3]
    assert ids(con, [("any.tags", "x")]) == [1]
    assert ids(con, [("any.tags", "x"), ("any.tags", "y")]) == [1, 2]
    assert ids(con, [("has.note", "WORLD")]) == [1]
    assert ids(con, [("null.ratio", "1")]) == [3]
    assert ids(con, [("null.ratio", "0")]) == [1, 2]
    assert ids(con, [("min.ts", "2025-01-01")]) == [1, 2]
    assert ids(con, [('max.we"ird.col', "5")]) == [3]
    assert ids(con, [("unrelated", "x"), ("page", "2"), ("min.words", "")]) == [1, 2, 3]


@pytest.mark.parametrize(
    "items",
    [
        [("min.nope", "1")],
        [('min.words" OR 1=1 --', "1")],
        [("min.words", "abc")],
        [("min.words", "nan")],
        [("is.flag", "maybe")],
        [("any.cat", "a")],  # any-of is only for lists
        [("min.text", "1")],  # text is not filterable
        [("null.nope", "1")],
    ],
)
def test_bad_filters_are_rejected(con, items):
    with pytest.raises(FilterError):
        ids(con, items)


def test_values_are_bound_not_spliced(con):
    evil = "x'; DROP TABLE t; --"
    assert ids(con, [("has.note", evil)]) == [3]  # found as a plain string
    assert ids(con, [("in.cat", "a' OR '1'='1")]) == []
    assert con.execute("SELECT count(*) FROM t").fetchone()[0] == 3  # table intact
    p = Params()
    where = filter_clauses([("has.note", evil), ("in.cat", "' OR 1=1 --")], COLS, p)
    assert evil not in " ".join(where) and "OR 1=1" not in " ".join(where)
    assert evil in p.values.values()


def test_identifiers_are_quoted():
    assert qi('we"ird.col') == '"we""ird.col"'
    assert qi("review.verdict") == '"review.verdict"'


def run_browse(con, items):
    cols = dict(COLS)
    cols["review.verdict"] = Column("review.verdict", "VARCHAR", "category", "review")
    con.execute(
        "CREATE OR REPLACE VIEW v AS SELECT *, CASE WHEN id = 2 THEN 'keep' END AS \"review.verdict\", "
        "note AS text FROM t"
    )
    bq = build_browse(items, cols, default_cols=["words"])
    sel = ", ".join(qi(k) for k in bq.select_keys)
    m = f", {bq.matches} AS __matches" if bq.matches else ""
    sql = f"SELECT {sel}{m} FROM v {bq.where_sql()} ORDER BY {bq.order}"
    return bq, con.execute(sql, bq.params.values).fetchall()


def test_browse_sort_search_and_unreviewed_first(con):
    bq, rows = run_browse(con, [("sort", "words"), ("dir", "desc")])
    assert [r[0] for r in rows] == [3, 2, 1]
    assert bq.select_keys == ["id", "title", "words", "review.verdict"]
    _, rows = run_browse(con, [("sort", "words"), ("dir", "desc"), ("unrev", "1")])
    assert [r[0] for r in rows] == [3, 1, 2]  # 2 has a verdict, so it goes last
    _, rows = run_browse(con, [("text", "o"), ("mode", "substr")])
    assert {r[0]: r[-1] for r in rows} == {1: 2, 2: 1}  # match counts (3 has only an upper-case O)
    _, rows = run_browse(con, [("text", "L+"), ("mode", "regex"), ("icase", "1")])
    assert [r[0] for r in rows] == [1, 3]  # hello, TABLE
    _, rows = run_browse(con, [("text", "L+"), ("mode", "regex")])
    assert [r[0] for r in rows] == [3]
    _, rows = run_browse(con, [("q", "ଆ")])
    assert [r[0] for r in rows] == [2]


@pytest.mark.parametrize(
    "items",
    [
        [("sort", "nope")],
        [("sort", "text")],
        [("dir", "sideways")],
        [("cols", "words,nope")],
        [("text", "x"), ("mode", "fuzzy")],
        [("page", "x")],
    ],
)
def test_browse_rejects_bad_params(con, items):
    with pytest.raises(FilterError):
        build_browse(items, COLS)


def test_page_size_is_capped():
    bq = build_browse([("size", "100000"), ("page", "0")], COLS)
    assert bq.size == 500 and bq.page == 1
