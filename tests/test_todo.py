"""Unit tests for the shared todo list and the file it lives in.

The list is the only data structure the three agents share, and the JSON file is
the only protocol between them: the agents write it, the orchestrator reads it,
and nothing parses anybody's prose. These tests pin the small surface of
:mod:`crow_research.todo` — the mutation and query methods, the render format
the agents are shown, the document shape, and what a document has to get wrong
to be refused.

The refusal cases matter more than they look. A file that does not parse is
announced upstream and the last good copy goes back; a file that parses but
means something else is a list the loop can never finish.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from crow_research.todo import (
    BLOCKED,
    DONE,
    OPEN,
    STATE_FILE,
    WORKING,
    Item,
    Todo,
    state_path,
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


def test_set_refuses_a_state_that_is_not_one_of_the_four() -> None:
    """The in-memory API holds the contract the file already enforces on load:
    an invented state is a ``ValueError`` at the source instead of surfacing
    later as a KeyError out of ``Item.render`` when the list is drawn."""
    todo = Todo()
    item = todo.add("build the parser")
    for state in ("wip", "finished", "", "open ", 3, None):
        with pytest.raises(ValueError):
            todo.set(item.id, state)
    assert item.state == OPEN  # a refused set leaves the item untouched
    assert item.render() == "1. [ ] build the parser"  # render never sees it


def test_set_forgives_case_but_not_spelling() -> None:
    """One contract for both paths: whatever loads also sets. Case is a typing
    habit; spelling is the thing that has to be right."""
    todo = Todo()
    item = todo.add("build the parser")
    todo.set(item.id, "Done")
    assert item.state == DONE
    with pytest.raises(ValueError):
        todo.set(item.id, "COMPLETE")


def test_set_says_what_is_wrong() -> None:
    """The refusal names the bad state and the four that exist — it is read by
    whoever is watching the run, not by a log parser."""
    todo = Todo()
    todo.add("a")
    with pytest.raises(ValueError, match="state 'wip', expected one of open/working/done/blocked"):
        todo.set(1, "wip")


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


# -- get ---------------------------------------------------------------


def test_get_returns_the_item_for_a_known_id() -> None:
    todo = Todo()
    a = todo.add("a")
    b = todo.add("b")
    assert todo.get(a.id) is a
    assert todo.get(b.id) is b
    todo.set(a.id, DONE, "finished")
    assert todo.get(a.id) is a  # any state, not just pending


def test_get_is_none_for_unknown_or_removed_ids() -> None:
    todo = Todo()
    item = todo.add("a")
    todo.remove(item.id)
    assert todo.get(item.id) is None  # removed ids stop being findable immediately
    assert todo.get(99) is None  # unknown ids are absent, never a KeyError
    assert todo.get("1") is None  # wrong type is just not there


def test_get_is_none_before_the_id_exists() -> None:
    todo = Todo()
    assert todo.get(1) is None
    item = todo.add("a")
    assert todo.get(item.id) is item


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


# -- the document ------------------------------------------------------


def test_to_dict_is_the_document() -> None:
    todo = Todo()
    todo.add("make the thing", by="prompt")
    todo.set(1, DONE, "built it")
    todo.verdict, todo.verdict_note = "PASS", "every claim reproduced"
    assert todo.to_dict() == {
        "task": None,
        "verdict": "PASS",
        "verdict_note": "every claim reproduced",
        "items": [
            {"id": 1, "text": "make the thing", "state": DONE, "note": "built it", "added_by": "prompt"}
        ],
        "ideas": [],
        "attempts": [],
        "best": None,
        "conclusions": {"text": "", "trigger": "none", "reason": ""},
    }


def test_round_trip_preserves_the_list() -> None:
    todo = Todo()
    todo.add("a", by="prompt")
    todo.add("b", by="explorer")
    todo.set(2, BLOCKED, "needs a human")
    todo.verdict, todo.verdict_note = "FAIL", "one finding"
    assert Todo.from_dict(todo.to_dict()) == todo


def test_from_dict_fills_in_what_an_agent_omitted() -> None:
    """Only `id` and `text` are not optional. An agent editing the file by hand
    should not have to write the three keys that are empty anyway."""
    todo = Todo.from_dict({"items": [{"id": 3, "text": "  a thing  "}]})
    assert todo.items == [Item(3, "a thing", OPEN, "", "")]
    assert todo.verdict is None
    assert todo.verdict_note == ""


def test_from_dict_keeps_counting_past_the_highest_id() -> None:
    """Ids are never reused, and a loaded list has to know that: the next item
    must not collide with one an agent is still holding a reference to."""
    assert Todo.from_dict({"items": [{"id": 7, "text": "a"}]}).add("b").id == 8
    assert Todo.from_dict({"items": []}).add("b").id == 1


def test_from_dict_normalizes_case_but_not_spelling() -> None:
    """Case is a typing habit and an agent's write is worth more than one; an
    invented state or verdict is a different contract, and that is refused."""
    assert Todo.from_dict({"items": [{"id": 1, "text": "a", "state": "Done"}]}).items[0].state == DONE
    assert Todo.from_dict({"verdict": "pass", "items": []}).verdict == "PASS"
    assert Todo.from_dict({"verdict": "Unverified", "items": []}).verdict == "UNVERIFIED"
    assert Todo.from_dict({"verdict": None, "items": []}).verdict is None


def test_an_agent_written_document_loads() -> None:
    """What an agent actually leaves behind: keys in its own order, a verdict it
    set, an item it appended with a hand-computed id, and none of the optional
    keys it did not feel like writing."""
    doc = """{
  "items": [
    {"id": 1, "text": "make the thing", "state": "done", "note": "built it", "added_by": "prompt"},
    {"id": 2, "text": "write the docs", "added_by": "explorer"}
  ],
  "verdict": "PASS",
  "verdict_note": "every claim reproduced"
}"""
    todo = Todo.from_dict(json.loads(doc))
    assert todo.verdict == "PASS"
    assert todo.complete is False
    assert todo.render(only_pending=True) == "TODO (1 pending / 2 total)\n2. [ ] write the docs"
    assert todo.add("next").id == 3


# -- refusing a document -----------------------------------------------


def test_from_dict_refuses_anything_that_is_not_the_contract() -> None:
    for doc in (
        [],
        "todo",
        3,
        None,
        {"items": {}},
        {"items": "1. a thing"},
        {"items": [3]},
        {"items": [{"text": "no id"}]},
        {"items": [{"id": "1", "text": "a"}]},
        {"items": [{"id": True, "text": "a"}]},
        {"items": [{"id": 1.5, "text": "a"}]},
        {"items": [{"id": 1}]},
        {"items": [{"id": 1, "text": ""}]},
        {"items": [{"id": 1, "text": "   "}]},
        {"items": [{"id": 1, "text": "a", "state": "wip"}]},
        {"items": [{"id": 1, "text": "a", "state": 3}]},
        {"items": [{"id": 1, "text": "a", "note": 3}]},
        {"items": [{"id": 1, "text": "a", "added_by": None}]},
        {"items": [{"id": 1, "text": "a"}, {"id": 1, "text": "b"}]},
        {"verdict": "MAYBE", "items": []},
        {"verdict": "", "items": []},
        {"verdict": 1, "items": []},
        {"verdict_note": 3, "items": []},
    ):
        with pytest.raises(ValueError):
            Todo.from_dict(doc)


def test_from_dict_says_what_is_wrong() -> None:
    """The message is announced upstream to whoever is watching the run, so it
    names the item and the field instead of reporting a shape."""
    with pytest.raises(ValueError, match="item 2 has state 'wip'"):
        Todo.from_dict({"items": [{"id": 2, "text": "a", "state": "wip"}]})
    with pytest.raises(ValueError, match="two items claim id 1"):
        Todo.from_dict({"items": [{"id": 1, "text": "a"}, {"id": 1, "text": "b"}]})
    with pytest.raises(ValueError, match="verdict is 'MAYBE'"):
        Todo.from_dict({"verdict": "MAYBE", "items": []})
    with pytest.raises(ValueError, match="item 3 has no text"):
        Todo.from_dict({"items": [{"id": 3, "text": "a"}, {"id": 3, "text": ""}]})


# -- save / load -------------------------------------------------------


def test_save_then_load_round_trips() -> None:
    todo = Todo()
    todo.add("make the thing", by="prompt")
    todo.set(1, WORKING, "claimed")
    todo.verdict = "UNVERIFIED"
    with tempfile.TemporaryDirectory() as tmp:
        path = state_path(tmp)
        assert not path.exists()
        assert todo.save(path) == path
        assert Todo.load(path) == todo


def test_save_creates_the_directory_and_leaves_no_temporary_behind() -> None:
    """Atomic means a temporary and a rename, and the rename means the reader
    never sees a half-written document — nor the file it came from."""
    with tempfile.TemporaryDirectory() as tmp:
        path = state_path(tmp)
        Todo().save(path)
        assert [p.name for p in path.parent.iterdir()] == ["todo.json"]


def test_save_overwrites_in_place() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = state_path(tmp)
        todo = Todo()
        todo.add("a")
        todo.save(path)
        todo.set(1, DONE)
        todo.save(path)
        assert Todo.load(path).complete


def test_save_writes_indented_json_an_agent_can_patch() -> None:
    """``indent=2`` is not cosmetics. The agents edit this file with the same
    tool they edit source with, and a document on one line is a document an
    agent has to regenerate from memory to change one field of."""
    todo = Todo()
    todo.add("a thing")
    with tempfile.TemporaryDirectory() as tmp:
        path = state_path(tmp)
        todo.save(path)
        text = path.read_text()
        assert text.startswith('{\n  "task": null,\n  "verdict": null,')
        assert '"text": "a thing"' in text
        assert text.endswith("}\n")


def test_load_raises_on_a_truncated_file() -> None:
    """``JSONDecodeError`` is a ``ValueError``, which is what lets the pipeline
    catch it, announce it, and put the last good copy back — instead of reading
    an empty list and starting the run over."""
    with tempfile.TemporaryDirectory() as tmp:
        path = state_path(tmp)
        path.parent.mkdir(parents=True)
        path.write_text('{"items": [{"id": 1, ')
        with pytest.raises(ValueError):
            Todo.load(path)


def test_load_raises_on_a_missing_file() -> None:
    """Not an empty list. Whether the file is absent is the caller's business:
    the pipeline seeds it, and anything else is a wrong path."""
    with tempfile.TemporaryDirectory() as tmp:
        with pytest.raises(FileNotFoundError):
            Todo.load(state_path(tmp))


def test_load_refuses_a_well_formed_document_that_breaks_the_contract() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = state_path(tmp)
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"items": [{"id": 1, "text": "a", "state": "finished"}]}))
        with pytest.raises(ValueError, match="state 'finished'"):
            Todo.load(path)


# -- state_path --------------------------------------------------------


def test_state_path_is_under_the_working_directory() -> None:
    assert STATE_FILE == ".crow-research/todo.json"
    assert state_path("/work") == Path("/work/.crow-research/todo.json")
    assert state_path(Path("/work")) == state_path("/work")


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
