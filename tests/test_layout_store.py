"""`.stitch/layout.yml`: presentation state, shared with whatever the canvas stores."""

import pytest

from stitch_lineage.io.layout_store import (
    DISMISSED_KEY,
    LAYOUT_FILENAME,
    LayoutStoreError,
    add_dismissed,
    layout_path,
    read_dismissed,
    read_layout,
    write_layout,
)


@pytest.fixture
def path(tmp_path):
    return layout_path(tmp_path / ".stitch")


def test_layout_path_names_the_file(tmp_path):
    assert layout_path(tmp_path).name == LAYOUT_FILENAME


def test_a_missing_file_is_an_empty_layout(path):
    assert read_layout(path) == {}
    assert read_dismissed(path) == []


def test_add_dismissed_creates_the_file_and_reports_novelty(path):
    assert add_dismissed("abc123", path) is True
    assert read_dismissed(path) == ["abc123"]
    assert add_dismissed("abc123", path) is False
    assert read_dismissed(path) == ["abc123"]


def test_dismissals_accumulate_and_read_back_sorted(path):
    for entry_id in ("zzz", "aaa", "mmm"):
        add_dismissed(entry_id, path)
    assert read_dismissed(path) == ["aaa", "mmm", "zzz"]


def test_unrelated_keys_survive_a_dismissal(path):
    write_layout({"positions": {"model.demo.fct_orders": {"x": 10, "y": 20}}}, path)
    add_dismissed("abc123", path)
    layout = read_layout(path)
    assert layout["positions"] == {"model.demo.fct_orders": {"x": 10, "y": 20}}
    assert layout[DISMISSED_KEY] == ["abc123"]


def test_writes_are_deterministic(path, tmp_path):
    other = layout_path(tmp_path / "other")
    layout = {"positions": {"a": 1}, DISMISSED_KEY: ["zzz", "aaa"]}
    write_layout(layout, path)
    write_layout(dict(reversed(list(layout.items()))), other)
    assert path.read_text() == other.read_text()
    assert path.read_text().endswith("\n")


def test_a_corrupt_file_names_the_fix(path):
    path.parent.mkdir(parents=True)
    path.write_text("dismissed_suggestions: [\n")
    with pytest.raises(LayoutStoreError, match="delete the file"):
        read_layout(path)


@pytest.mark.parametrize("body", ["- just\n- a\n- list\n", "dismissed_suggestions: 3\n"])
def test_a_wrongly_shaped_document_is_rejected(path, body):
    path.parent.mkdir(parents=True)
    path.write_text(body)
    with pytest.raises(LayoutStoreError):
        read_dismissed(path)


def test_non_string_ids_are_rejected(path):
    path.parent.mkdir(parents=True)
    path.write_text("dismissed_suggestions:\n  - 42\n")
    with pytest.raises(LayoutStoreError, match="strings only"):
        read_dismissed(path)
