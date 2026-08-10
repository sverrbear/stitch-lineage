"""SHA-keyed local graph history: `.stitch/history/<commit-sha>.json.gz` (issue #87).

Every `stitch build` on a clean working tree keeps a gzipped copy of the graph it just
wrote, keyed by the repo's HEAD commit, so `stitch impact --base <ref>` can diff against
a baseline that was never committed -- SPEC.md section 12.2's "CI artifact keyed by
commit SHA" fallback, in local form, inside the gitignored `.stitch/`.

Dirty trees are not stored: the graph would describe the working tree, not the commit,
and a baseline that quietly contains your own uncommitted changes reports no impact at
all. `index.json` owns ordering (append order, oldest first) so retention prunes
deterministically -- mtimes do not survive a copy and cannot be trusted anyway.
"""

import gzip
import json
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

from stitch_lineage.graph.schema import Graph
from stitch_lineage.io import git
from stitch_lineage.io.graph_store import serialize_graph

HISTORY_DIRNAME = "history"
INDEX_FILENAME = "index.json"
SNAPSHOT_SUFFIX = ".json.gz"
# how far back an ancestor walk looks for a stored snapshot before giving up
DEFAULT_WALK_LIMIT = 200


class HistoryEntry(BaseModel):
    """One stored baseline. Counts are carried so `stitch history` need not decompress."""

    sha: str
    stored_at: str
    nodes: int = 0
    edges: int = 0

    @property
    def short_sha(self) -> str:
        return self.sha[:7]

    @property
    def filename(self) -> str:
        return f"{self.sha}{SNAPSHOT_SUFFIX}"


class HistoryIndex(BaseModel):
    entries: list[HistoryEntry] = Field(default_factory=list)


class BaselineHit(BaseModel):
    """A stored snapshot chosen as the baseline for `--base <ref>`, and why."""

    sha: str
    ref: str
    path: Path
    # commits walked back from the starting commit before a snapshot was found
    distance: int = 0
    # whether the starting commit was the merge-base of <ref> and HEAD rather than <ref>
    via_merge_base: bool = False

    @property
    def provenance(self) -> str:
        """The line `stitch impact` prints so the baseline is never a guess."""
        short = self.sha[:7]
        if self.distance == 0 and not self.via_merge_base and self.sha.startswith(self.ref):
            # --base was that very sha; naming it twice reads like two different things
            return f"baseline: local history snapshot for {short}"
        start = (
            f"the merge-base of '{self.ref}' and HEAD" if self.via_merge_base else f"'{self.ref}'"
        )
        if self.distance == 0:
            return f"baseline: local history snapshot for {short} ({start})"
        commits = "commit" if self.distance == 1 else "commits"
        return (
            f"baseline: local history snapshot for {short} -- nearest stored ancestor "
            f"of {start}, {self.distance} {commits} back"
        )


def history_dir(out_dir: Path) -> Path:
    return out_dir / HISTORY_DIRNAME


def read_entries(directory: Path) -> list[HistoryEntry]:
    """Stored baselines, oldest first. Entries whose file vanished are ignored.

    An unreadable index reads as empty history: this is a cache, and a rebuild is how
    you recover from one, so it must never turn into an error.
    """
    index = directory / INDEX_FILENAME
    if not index.is_file():
        return []
    try:
        parsed = HistoryIndex.model_validate_json(index.read_text(encoding="utf-8"))
    except ValueError:
        return []
    return [entry for entry in parsed.entries if (directory / entry.filename).is_file()]


def load_snapshot(directory: Path, sha: str) -> Graph:
    """Read one stored baseline. Raises ValueError if it does not parse."""
    path = directory / f"{sha}{SNAPSHOT_SUFFIX}"
    return Graph.model_validate_json(gzip.decompress(path.read_bytes()).decode("utf-8"))


def clear_snapshots(directory: Path) -> None:
    """Delete every stored baseline -- what turning history off in stitch.yml does."""
    _prune(directory, [])


def store_snapshot(
    directory: Path,
    sha: str,
    graph: Graph,
    *,
    retention: int,
    stored_at: str | None = None,
) -> HistoryEntry | None:
    """Store `graph` as the baseline for `sha`, then prune to `retention` snapshots.

    Re-storing a sha replaces it and moves it to the newest slot -- rebuilding the same
    commit should refresh its baseline, not spend two retention slots on it.
    retention <= 0 disables history and clears whatever is already stored, so turning
    the feature off in stitch.yml actually reclaims the disk.
    """
    if retention <= 0:
        clear_snapshots(directory)
        return None
    directory.mkdir(parents=True, exist_ok=True)
    entry = HistoryEntry(
        sha=sha,
        stored_at=stored_at or datetime.now(UTC).isoformat(timespec="seconds"),
        nodes=len(graph.nodes),
        edges=len(graph.edges),
    )
    payload = serialize_graph(graph).encode("utf-8")
    # mtime=0: same graph -> same bytes, so a snapshot is comparable across machines
    (directory / entry.filename).write_bytes(gzip.compress(payload, mtime=0))
    kept = [existing for existing in read_entries(directory) if existing.sha != sha]
    kept.append(entry)
    _prune(directory, kept[-retention:])
    return entry


def _prune(directory: Path, kept: list[HistoryEntry]) -> None:
    """Make the directory match `kept` exactly: rewrite the index, delete every other
    snapshot file (including orphans left by an interrupted write)."""
    if not directory.is_dir():
        return
    keep_files = {entry.filename for entry in kept}
    for path in sorted(directory.glob(f"*{SNAPSHOT_SUFFIX}")):
        if path.name not in keep_files:
            path.unlink(missing_ok=True)
    index = directory / INDEX_FILENAME
    if not kept:
        index.unlink(missing_ok=True)
        return
    payload = HistoryIndex(entries=kept).model_dump(mode="json")
    index.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def resolve_baseline(
    repo_dir: Path,
    directory: Path,
    ref: str,
    *,
    walk_limit: int = DEFAULT_WALK_LIMIT,
) -> BaselineHit | None:
    """Find the stored snapshot that best answers "what did I change against <ref>".

    <ref> -> its merge-base with HEAD (the commit the branch actually diverged from,
    which is what a PR diffs against) -> the nearest ancestor of that with a snapshot.
    None when git cannot resolve the ref or nothing in the ancestry was ever stored;
    the caller then falls back to the committed baseline.
    """
    stored = {entry.sha for entry in read_entries(directory)}
    if not stored:
        return None
    tip = git.resolve_commit(repo_dir, ref)
    if tip is None:
        return None
    start = git.merge_base(repo_dir, ref) or tip
    walk = git.ancestry(repo_dir, start, walk_limit) or [start]
    for distance, sha in enumerate(walk):
        if sha in stored:
            return BaselineHit(
                sha=sha,
                ref=ref,
                path=directory / f"{sha}{SNAPSHOT_SUFFIX}",
                distance=distance,
                via_merge_base=start != tip,
            )
    return None
