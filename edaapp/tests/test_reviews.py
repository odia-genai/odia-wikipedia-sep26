import json
import os

import pytest

from edaapp.reviews import FIELDS, InvalidEvent, ReviewStore, make_event, same_decision
from edaapp.service import rebase_drops

SHA = "a" * 40
SHB = "b" * 40


def psha(p):
    return f"{p:040x}"[-40:] if p >= 0 else "c" * 40


def ev(id=1, verdict="keep", note="", drops=(), sha=SHA, ds="wiki"):
    return make_event(ds, id, f"title {id}", verdict, note, [{"para": p, "sha1": psha(p)} for p in drops], sha)


def test_event_has_exactly_the_contract_keys_in_order():
    e = ev(drops=[3, 1])
    assert list(e) == list(FIELDS)
    assert list(FIELDS) == ["ts", "dataset", "id", "title", "verdict", "note", "drop_paragraphs", "text_sha1"]
    assert [d["para"] for d in e["drop_paragraphs"]] == [1, 3]
    assert e["ts"].endswith("Z")


@pytest.mark.parametrize(
    "kw",
    [
        {"verdict": "maybe"},
        {"sha": "xyz"},
        {"sha": "A" * 40},
        {"drops": [1, 1]},
        {"drops": [-1]},
        {"ds": ""},
    ],
)
def test_invalid_events_are_refused(kw):
    with pytest.raises((InvalidEvent, ValueError)):
        ev(**kw)


def test_same_sha1_twice_is_refused():
    with pytest.raises(InvalidEvent):
        make_event("wiki", 1, "t", None, "", [{"para": 1, "sha1": SHB}, {"para": 2, "sha1": SHB}], SHA)


def test_append_and_replay_latest_wins(reviews):
    reviews.append([ev(1, "keep"), ev(2, "drop")])
    reviews.append([ev(1, "fix", note="needs work")])
    cur = reviews.current("wiki")
    assert cur[1]["verdict"] == "fix" and cur[1]["note"] == "needs work"
    assert cur[2]["verdict"] == "drop"
    assert len(reviews.history("wiki", 1)) == 2
    # the file is plain JSONL, one event per line, readable by anyone
    lines = reviews.path.read_text(encoding="utf-8").splitlines()
    assert [json.loads(x)["verdict"] for x in lines] == ["keep", "drop", "fix"]
    # a fresh store replays the same state
    again = ReviewStore(reviews.path, reviews.writer)
    assert again.current("wiki") == cur


def test_datasets_are_separate(reviews):
    reviews.append([ev(1, "keep", ds="a"), ev(1, "drop", ds="b")])
    assert reviews.get("a", 1)["verdict"] == "keep" and reviews.get("b", 1)["verdict"] == "drop"


def test_undo_restores_previous_state(reviews):
    reviews.append([ev(1, "keep", note="n1")])
    reviews.append([ev(1, "drop", drops=[2])])
    u = reviews.undo_event("wiki", 1, "title 1", SHA)
    assert u["verdict"] == "keep" and u["note"] == "n1" and u["drop_paragraphs"] == []
    reviews.append([u])
    assert reviews.get("wiki", 1)["verdict"] == "keep"
    assert len(reviews.history("wiki", 1)) == 3  # nothing rewritten
    # undoing the first-ever event clears the decision
    reviews.append([ev(9, "fix")])
    u9 = reviews.undo_event("wiki", 9, "title 9", SHA)
    assert u9["verdict"] is None and u9["note"] == "" and u9["drop_paragraphs"] == []
    assert reviews.undo_event("wiki", 12345, "x", SHA) is None


def test_incremental_replay_partial_and_bad_lines(reviews):
    reviews.append([ev(1, "keep")])
    assert reviews.get("wiki", 1)["verdict"] == "keep"
    # another process appends a complete line, a garbage line, and half a line
    with open(reviews.path, "a", encoding="utf-8") as f:
        f.write(json.dumps(ev(1, "drop")) + "\n")
        f.write("not json\n")
        f.write(json.dumps(ev(2, "fix"))[:20])
    assert reviews.get("wiki", 1)["verdict"] == "drop"
    assert reviews.get("wiki", 2) is None  # incomplete line waits
    assert reviews.stats()["bad_lines"] == 1
    with open(reviews.path, "a", encoding="utf-8") as f:
        f.write(json.dumps(ev(2, "fix"))[20:] + "\n")
    assert reviews.get("wiki", 2)["verdict"] == "fix"


def test_replaced_or_removed_file_is_replayed(reviews):
    reviews.append([ev(1, "keep"), ev(2, "keep")])
    tmp = reviews.path.with_name("new.jsonl")
    tmp.write_text(json.dumps(ev(3, "drop")) + "\n")
    os.replace(tmp, reviews.path)
    assert set(reviews.current("wiki")) == {3}
    reviews.path.unlink()
    assert reviews.current("wiki") == {}


def test_same_decision():
    assert same_decision(ev(1, "keep"), ev(1, "keep"))
    assert not same_decision(ev(1, "keep"), ev(1, "drop"))
    assert same_decision(None, ev(1, None))
    assert not same_decision(None, ev(1, None, note="x"))


def test_rebase_drops_by_sha1():
    import hashlib

    def h(s):
        return hashlib.sha1(s.encode()).hexdigest()

    paras = ["# t", "a", "b", "c", "a"]
    drops = [
        {"para": 1, "sha1": h("a")},
        {"para": 2, "sha1": h("c")},
        {"para": 3, "sha1": h("gone")},
        {"para": 0, "sha1": h("# t")},
    ]
    present, missing = rebase_drops(drops, paras)
    # "a" appears twice and both are dropped (as the build does); "c" moved; paragraph 0 never
    assert present == [
        {"para": 1, "sha1": h("a")},
        {"para": 3, "sha1": h("c"), "moved_from": 2},
        {"para": 4, "sha1": h("a")},
    ]
    assert missing == [{"para": 3, "sha1": h("gone")}, {"para": 0, "sha1": h("# t")}]
