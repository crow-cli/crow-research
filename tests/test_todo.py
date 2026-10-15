"""Unit tests for the shared todo list and its report parsers.

The todo list is the only data structure the three agents share, and the
parsers are the only protocol for getting state back out of agent prose. These
tests pin the small surface of :mod:`crow_research.todo`: the mutation and query
methods, the render format that is the wire contract, and the deliberately dumb
parsers (dumb on purpose, so an agent that rambles cannot corrupt the list).
"""

from __future__ import annotations

import pytest

from crow_research.todo import (
    BLOCKED,
    DONE,
    OPEN,
    WORKING,
    Item,
    Todo,
    parse_new_items,
    parse_updates,
    parse_verdict,
)


# -- add ---------------------------------------------------------------


def test_add_assigns_incrementing_ids_and_strips_text() -> None:
    todo = Todo()
    first = todo.add("  ship it  ")
    second = todo.add("write tests")
    assert first.id == 1
    assert first.text == "ship it"
    assert second.id == 2
    assert second.text == "write tests"
    assert [i.id for i in todo.items] == [1, 2]


def test_add_records_author() -> None:
    todo = Todo()
    item = todo.add("ship it", by="worker")
    assert item.added_by == "worker"


def test_add_refuses_empty_text() -> None:
    todo = Todo()
    with pytest.raises(ValueError):
        todo.add("")
    with pytest.raises(ValueError):
        todo.add("   ")
    with pytest.raises(ValueError):
        todo.add(None)
    assert len(todo) == 0


# -- extend ------------------------------------------------------------


def test_extend_adds_only_nonempty_texts() -> None:
    todo = Todo()
    items = todo.extend(["a", "", "  ", "b"], by="explorer")
    assert [i.text for i in items] == ["a", "b"]
    assert [i.id for i in items] == [1, 2]
    assert all(i.added_by == "explorer" for i in items)
    assert len(todo) == 2


def test_extend_returns_empty_list_for_all_blank() -> None:
    todo = Todo()
    assert todo.extend(["", None, "   "]) == []
    assert len(todo) == 0


# -- set ---------------------------------------------------------------


def test_set_updates_state_and_strips_note() -> None:
    todo = Todo()
    item = todo.add("build the parser")
    todo.set(item.id, DONE, "  tests pass  ")
    assert item.state == DONE
    assert item.note == "tests pass"


def test_set_empty_note_leaves_existing_note() -> None:
    todo = Todo()
    item = todo.add("build the parser")
    todo.set(item.id, WORKING, "half done")
    todo.set(item.id, DONE)
    assert item.state == DONE
    assert item.note == "half done"


def test_set_whitespace_note_clears_note() -> None:
    todo = Todo()
    item = todo.add("build the parser")
    todo.set(item.id, WORKING, "half done")
    todo.set(item.id, DONE, "   ")
    assert item.note == ""


def test_set_unknown_id_raises() -> None:
    todo = Todo()
    todo.add("only item")
    with pytest.raises(KeyError):
        todo.set(99, DONE)


# -- remove ------------------------------------------------------------


def test_remove_deletes_only_that_item() -> None:
    todo = Todo()
    a = todo.add("a")
    b = todo.add("b")
    c = todo.add("c")
    removed = todo.remove(b.id)
    assert removed is b
    assert [i.id for i in todo.items] == [a.id, c.id]
    assert len(todo) == 2
    with pytest.raises(KeyError):
        todo[b.id]


def test_remove_works_on_any_state() -> None:
    todo = Todo()
    a = todo.add("a")
    b = todo.add("b")
    todo.set(a.id, DONE, "finished")
    todo.set(b.id, BLOCKED, "dead end")
    todo.remove(a.id)
    todo.remove(b.id)
    assert len(todo) == 0
    assert todo.render() == "(the todo list is empty)"


def test_remove_unknown_id_raises() -> None:
    todo = Todo()
    item = todo.add("only item")
    with pytest.raises(KeyError):
        todo.remove(99)
    todo.remove(item.id)
    with pytest.raises(KeyError):
        todo.remove(item.id)  # already gone


def test_remove_does_not_reuse_ids() -> None:
    todo = Todo()
    first = todo.add("a")
    todo.add("b")
    todo.remove(first.id)
    assert todo.add("c").id == 3  # ids never walk backwards


# -- pending / claimed / complete -------------------------------------


def test_pending_and_claimed() -> None:
    todo = Todo()
    a = todo.add("a")
    b = todo.add("b")
    c = todo.add("c")
    todo.set(a.id, WORKING)
    todo.set(b.id, DONE)
    todo.set(c.id, BLOCKED)
    assert todo.pending() == [a]
    assert todo.claimed() == [a]
    assert not todo.complete


def test_complete_when_nothing_pending() -> None:
    todo = Todo()
    a = todo.add("a")
    b = todo.add("b")
    todo.set(a.id, DONE)
    todo.set(b.id, BLOCKED)
    assert todo.complete
    assert todo.pending() == []


# -- contains ----------------------------------------------------------


def test_contains_is_true_for_ids_on_the_list() -> None:
    todo = Todo()
    a = todo.add("a")
    b = todo.add("b")
    assert a.id in todo
    assert b.id in todo
    assert len(todo) == 2


def test_contains_is_false_for_unknown_or_removed_ids() -> None:
    todo = Todo()
    a = todo.add("a")
    todo.remove(a.id)
    assert a.id not in todo  # removed ids stop being members immediately
    assert 99 not in todo  # unknown ids are absent, never a KeyError
    assert "1" not in todo  # wrong type is just not there


def test_contains_tracks_later_adds() -> None:
    todo = Todo()
    assert 1 not in todo
    item = todo.add("a")
    assert item.id in todo


# -- render ------------------------------------------------------------


def test_render_empty() -> None:
    assert Todo().render() == "(the todo list is empty)"


def test_item_render_marks_and_note() -> None:
    assert Item(1, "a", OPEN).render() == "1. [ ] a"
    assert Item(2, "b", WORKING).render() == "2. [~] b"
    assert Item(3, "c", DONE, "verified").render() == "3. [x] c — verified"
    assert Item(4, "d", BLOCKED).render() == "4. [!] d"


def test_render_shows_all_items_with_head() -> None:
    todo = Todo()
    todo.add("a")
    todo.add("b")
    todo.set(1, WORKING)
    assert todo.render() == (
        "TODO (2 pending / 2 total)\n1. [~] a\n2. [ ] b"
    )


def test_render_only_pending() -> None:
    todo = Todo()
    todo.add("a")
    todo.add("b")
    todo.set(1, DONE, "done")
    assert todo.render(only_pending=True) == (
        "TODO (1 pending / 2 total)\n2. [ ] b"
    )


# -- parse_updates -----------------------------------------------------


def test_parse_updates_returns_id_state_note_triples() -> None:
    report = "TODO 3 done built the parser, tests pass\nTODO 5 open still crashes on empty input"
    assert parse_updates(report) == [
        (3, DONE, "built the parser, tests pass"),
        (5, OPEN, "still crashes on empty input"),
    ]


def test_parse_updates_note_is_optional() -> None:
    assert parse_updates("TODO 4 blocked") == [(4, BLOCKED, "")]
    assert parse_updates("TODO 4 working\n") == [(4, WORKING, "")]


def test_parse_updates_is_case_insensitive() -> None:
    assert parse_updates("todo 7 Done x") == [(7, DONE, "x")]


def test_parse_updates_ignores_prose_and_malformed_lines() -> None:
    report = "\n".join(
        [
            "I did the thing and here is my reasoning",
            "TODO not-a-number done x",
            "TODO 2 frobnicate x",
            "TODO 3",  # missing state
            "DONE 4 stuff",
            "TODO 5 done",  # the only valid line
        ]
    )
    assert parse_updates(report) == [(5, DONE, "")]


# -- parse_new_items ---------------------------------------------------


def test_parse_new_items_in_order() -> None:
    report = "ADD write the parser\nADD ship it\nADD"
    assert parse_new_items(report) == ["write the parser", "ship it"]


def test_parse_new_items_is_case_insensitive_and_strips() -> None:
    assert parse_new_items("add  leading space  ") == ["leading space"]


def test_parse_new_items_ignores_prose_and_blank() -> None:
    report = "here are some thoughts\nADD\nADD   \nnot an add line"
    assert parse_new_items(report) == []


# -- parse_verdict -----------------------------------------------------


def test_parse_verdict_returns_word() -> None:
    assert parse_verdict("VERDICT: PASS") == "PASS"
    assert parse_verdict("VERDICT: fail") == "FAIL"
    assert parse_verdict("VERDICT: unverified") == "UNVERIFIED"


def test_parse_verdict_none_when_absent() -> None:
    assert parse_verdict("no verdict here") is None
    assert parse_verdict("") is None
    assert parse_verdict(None) is None


def test_parse_verdict_last_one_wins() -> None:
    assert parse_verdict("VERDICT: FAIL\nVERDICT: PASS") == "PASS"
    assert parse_verdict("VERDICT: PASS\nVERDICT: FAIL") == "FAIL"


def test_parse_verdict_accepts_markdown_heading_prefixes() -> None:
    assert parse_verdict("## Verdict: PASS") == "PASS"
    assert parse_verdict("#* Verdict: FAIL") == "FAIL"
    assert parse_verdict("# Verdict: UNVERIFIED") == "UNVERIFIED"


def test_parse_verdict_ignores_unknown_or_empty_words() -> None:
    assert parse_verdict("VERDICT: MAYBE") is None
    assert parse_verdict("VERDICT:") is None
    assert parse_verdict("VERDICT: PASS\nVERDICT:") == "PASS"


def _main() -> int:
    failures = 0
    for name, test in sorted(globals().items()):
        if not name.startswith("test_"):
            continue
        print(f"=== {name}")
        try:
            test()
        except Exception as error:  # pragma: no cover - reporting only
            failures += 1
            import traceback

            traceback.print_exc()
            print(f"FAIL {name}: {error}")
        else:
            print(f"ok   {name}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_main())
