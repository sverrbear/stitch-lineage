"""The few git questions stitch asks: HEAD, dirtiness, ref resolution, ancestry.

Every call is best-effort and total: not a repo, no commits, unknown ref -> None or
False, never an exception. Nothing here is required for stitch to work; git only ever
sharpens what the local history can answer (SPEC.md section 10).
"""

import subprocess
from pathlib import Path


def _run(cwd: Path, *args: str) -> str | None:
    """Run a git command, returning its stripped stdout, or None on any failure."""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def head_sha(cwd: Path) -> str | None:
    """Full sha of HEAD, or None outside a repo / before the first commit."""
    return resolve_commit(cwd, "HEAD")


def resolve_commit(cwd: Path, ref: str) -> str | None:
    """Full commit sha `ref` points at, or None when it does not resolve to one."""
    sha = _run(cwd, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    return sha or None


def is_dirty(cwd: Path) -> bool:
    """Whether the working tree has uncommitted changes (untracked files count).

    Outside a repo there is nothing to be dirty about -- but callers only reach this
    with a HEAD in hand, so False there means "not a repo", not "clean".
    """
    status = _run(cwd, "status", "--porcelain")
    return bool(status)


def merge_base(cwd: Path, ref: str, other: str = "HEAD") -> str | None:
    """Best common ancestor of two refs, or None when they have none / do not resolve."""
    return _run(cwd, "merge-base", ref, other) or None


def ancestry(cwd: Path, sha: str, limit: int) -> list[str]:
    """`sha` first, then its first-parent ancestors, newest first, at most `limit`."""
    listing = _run(cwd, "rev-list", "--first-parent", f"--max-count={limit}", sha)
    return listing.splitlines() if listing else []


def commit_subject(cwd: Path, sha: str) -> str | None:
    """One-line subject of a commit, for display only."""
    return _run(cwd, "log", "-1", "--format=%s", sha) or None
