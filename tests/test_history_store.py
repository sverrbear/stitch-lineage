"""SHA-keyed local graph history: storage, retention, and baseline resolution (issue #87)."""

import gzip
import json
import subprocess

import pytest

from stitch_lineage.io import git
from stitch_lineage.io.graph_store import graphs_semantically_equal
from stitch_lineage.io.history_store import (
    INDEX_FILENAME,
    load_snapshot,
    read_entries,
    resolve_baseline,
    store_snapshot,
)

SHA_A = "a" * 40
SHA_B = "b" * 40
SHA_C = "c" * 40


def _git(cwd, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


@pytest.fixture
def history(tmp_path):
    return tmp_path / ".stitch" / "history"


@pytest.fixture
def repo(tmp_path):
    """A three-commit linear repo on main, returning [c1, c2, c3] shas."""
    _git(tmp_path, "init", "-b", "main")
    shas = []
    for index in range(3):
        (tmp_path / f"file{index}.txt").write_text(str(index))
        _git(tmp_path, "add", ".")
        _git(tmp_path, "commit", "-m", f"commit {index}")
        shas.append(_git(tmp_path, "rev-parse", "HEAD"))
    return shas


# --- storage ----------------------------------------------------------------


def test_store_and_load_round_trip(history, sample_graph):
    entry = store_snapshot(history, SHA_A, sample_graph, retention=20)
    assert entry is not None
    assert entry.short_sha == SHA_A[:7]
    assert (entry.nodes, entry.edges) == (len(sample_graph.nodes), len(sample_graph.edges))
    assert (history / f"{SHA_A}.json.gz").is_file()
    assert graphs_semantically_equal(load_snapshot(history, SHA_A), sample_graph)


def test_snapshot_bytes_are_deterministic(history, tmp_path, sample_graph):
    store_snapshot(history, SHA_A, sample_graph, retention=20)
    first = (history / f"{SHA_A}.json.gz").read_bytes()
    shuffled = sample_graph.model_copy(update={"nodes": list(reversed(sample_graph.nodes))})
    store_snapshot(history, SHA_B, shuffled, retention=20)
    # same graph, same bytes: no timestamp in the gzip header, canonical json inside
    assert (history / f"{SHA_B}.json.gz").read_bytes() == first
    assert json.loads(gzip.decompress(first))["schema_version"] == sample_graph.schema_version


def test_retention_prunes_the_oldest_in_insertion_order(history, sample_graph):
    for sha in (SHA_A, SHA_B, SHA_C):
        store_snapshot(history, sha, sample_graph, retention=2)
    assert [entry.sha for entry in read_entries(history)] == [SHA_B, SHA_C]
    assert not (history / f"{SHA_A}.json.gz").exists()
    assert sorted(path.name for path in history.glob("*.json.gz")) == [
        f"{SHA_B}.json.gz",
        f"{SHA_C}.json.gz",
    ]


def test_restoring_the_same_sha_refreshes_it_without_spending_a_slot(history, sample_graph):
    store_snapshot(history, SHA_A, sample_graph, retention=2)
    store_snapshot(history, SHA_B, sample_graph, retention=2)
    store_snapshot(history, SHA_A, sample_graph, retention=2, stored_at="2026-08-10T10:00:00+00:00")
    entries = read_entries(history)
    assert [entry.sha for entry in entries] == [SHA_B, SHA_A]  # A moved to newest
    assert entries[-1].stored_at == "2026-08-10T10:00:00+00:00"


def test_retention_zero_disables_history_and_clears_it(history, sample_graph):
    store_snapshot(history, SHA_A, sample_graph, retention=20)
    assert store_snapshot(history, SHA_B, sample_graph, retention=0) is None
    assert read_entries(history) == []
    assert list(history.glob("*.json.gz")) == []
    assert not (history / INDEX_FILENAME).exists()


def test_missing_files_and_orphans_are_reconciled(history, sample_graph):
    store_snapshot(history, SHA_A, sample_graph, retention=20)
    (history / f"{SHA_A}.json.gz").unlink()
    (history / f"{SHA_C}.json.gz").write_bytes(b"orphan")
    assert read_entries(history) == []  # indexed but the file is gone

    store_snapshot(history, SHA_B, sample_graph, retention=20)
    assert [entry.sha for entry in read_entries(history)] == [SHA_B]
    assert not (history / f"{SHA_C}.json.gz").exists()  # orphan swept


def test_unreadable_index_reads_as_no_history(history, sample_graph):
    store_snapshot(history, SHA_A, sample_graph, retention=20)
    (history / INDEX_FILENAME).write_text("{not json")
    assert read_entries(history) == []


def test_reading_an_absent_directory_is_empty_not_an_error(history):
    assert read_entries(history) == []


# --- resolution -------------------------------------------------------------


def test_resolves_the_ref_itself_when_it_has_a_snapshot(tmp_path, repo, history, sample_graph):
    store_snapshot(history, repo[2], sample_graph, retention=20)
    hit = resolve_baseline(tmp_path, history, "main")
    assert hit is not None
    assert (hit.sha, hit.distance, hit.via_merge_base) == (repo[2], 0, False)
    assert hit.provenance == f"baseline: local history snapshot for {repo[2][:7]} ('main')"


def test_a_sha_passed_as_the_ref_is_not_named_twice(tmp_path, repo, history, sample_graph):
    store_snapshot(history, repo[2], sample_graph, retention=20)
    hit = resolve_baseline(tmp_path, history, repo[2])
    assert hit is not None
    assert hit.provenance == f"baseline: local history snapshot for {repo[2][:7]}"


def test_walks_back_to_the_nearest_stored_ancestor(tmp_path, repo, history, sample_graph):
    store_snapshot(history, repo[0], sample_graph, retention=20)
    hit = resolve_baseline(tmp_path, history, "main")
    assert hit is not None
    assert (hit.sha, hit.distance) == (repo[0], 2)
    assert "nearest stored ancestor of 'main', 2 commits back" in hit.provenance


def test_uses_the_merge_base_when_the_branch_diverged(tmp_path, repo, history, sample_graph):
    # branch off commit 2, so the merge-base with main (at commit 3) is commit 2
    _git(tmp_path, "checkout", "-b", "feature", repo[1])
    (tmp_path / "branch.txt").write_text("x")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "branch work")
    store_snapshot(history, repo[1], sample_graph, retention=20)

    hit = resolve_baseline(tmp_path, history, "main")
    assert hit is not None
    assert (hit.sha, hit.distance, hit.via_merge_base) == (repo[1], 0, True)
    assert "the merge-base of 'main' and HEAD" in hit.provenance


def test_no_snapshot_in_the_ancestry_is_a_miss(tmp_path, repo, history, sample_graph):
    store_snapshot(history, SHA_A, sample_graph, retention=20)
    assert resolve_baseline(tmp_path, history, "main") is None


def test_unknown_ref_and_empty_history_are_misses(tmp_path, repo, history, sample_graph):
    assert resolve_baseline(tmp_path, history, "main") is None  # nothing stored
    store_snapshot(history, repo[2], sample_graph, retention=20)
    assert resolve_baseline(tmp_path, history, "nosuchref") is None


def test_outside_a_git_repo_everything_is_none(tmp_path, history, sample_graph):
    store_snapshot(history, SHA_A, sample_graph, retention=20)
    assert resolve_baseline(tmp_path, history, "main") is None
    assert git.head_sha(tmp_path) is None
    assert git.is_dirty(tmp_path) is False
    assert git.commit_subject(tmp_path, SHA_A) is None


def test_git_helpers_report_head_dirtiness_and_subject(tmp_path, repo):
    assert git.head_sha(tmp_path) == repo[2]
    assert git.is_dirty(tmp_path) is False
    assert git.commit_subject(tmp_path, repo[0]) == "commit 0"
    (tmp_path / "file0.txt").write_text("changed")
    assert git.is_dirty(tmp_path) is True
