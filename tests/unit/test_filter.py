"""ChatFilter matching rules (T043)."""

from __future__ import annotations

from tgexport.core.config import ChatFilter


def test_empty_filter_matches_everything() -> None:
    f = ChatFilter()
    assert f.matches(1, "Anything", "private")
    assert f.matches(-100500, "Group", "supergroup")


def test_include_by_integer_id() -> None:
    f = ChatFilter(include=(123,))
    assert f.matches(123, "X", "private")
    assert not f.matches(456, "Y", "private")


def test_include_by_numeric_string_id() -> None:
    f = ChatFilter(include=("-100123",))
    assert f.matches(-100123, "Ch", "channel")


def test_include_by_exact_title() -> None:
    f = ChatFilter(include=("My Group",))
    assert f.matches(1, "My Group", "group")
    assert not f.matches(1, "My Group 2", "group")
    assert not f.matches(1, "my group", "group")  # case-sensitive exact match


def test_exclude_overrides_include() -> None:
    f = ChatFilter(include=(1, 2), exclude=(2,))
    assert f.matches(1, "A", "private")
    assert not f.matches(2, "B", "private")


def test_type_filter() -> None:
    f = ChatFilter(types=("private",))
    assert f.matches(1, "A", "private")
    assert not f.matches(2, "B", "group")
    assert not f.matches(3, "C", "channel")


def test_type_filter_combined_with_include() -> None:
    f = ChatFilter(include=(1, 2), types=("group",))
    assert not f.matches(1, "A", "private")  # right ID, wrong type
    assert f.matches(2, "B", "group")
