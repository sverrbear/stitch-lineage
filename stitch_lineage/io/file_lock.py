"""Reentrant transactions for local stores shared by the server and CLI.

Atomic rename protects readers from partial files. These locks additionally protect
read-modify-write operations from other threads and cooperating processes. Lock files
are stable siblings of the data file: replacing or deleting one would split the lock.
"""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock, RLock
from typing import BinaryIO


@dataclass
class _State:
    lock: RLock = field(default_factory=RLock)
    depth: int = 0


_WINDOWS = os.name == "nt"
_states: dict[Path, _State] = {}
_states_guard = Lock()


def _reset_after_fork() -> None:
    global _states, _states_guard
    _states = {}
    _states_guard = Lock()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_after_fork)


@contextmanager
def _process_lock(handle: BinaryIO) -> Iterator[None]:
    if _WINDOWS:
        import msvcrt

        # Windows locks a byte range, including on a newly created lock file.
        if handle.seek(0, os.SEEK_END) == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def file_lock(path: Path) -> Iterator[None]:
    """Serialize a whole transaction, allowing nested writes to the same store."""
    path = Path(path).resolve()
    with _states_guard:
        state = _states.setdefault(path, _State())
    with state.lock:
        if state.depth:
            # The owning thread already holds both locks; a second file descriptor
            # could otherwise deadlock on the process lock even with an RLock.
            yield
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = path.with_name(f".{path.name}.lock")
        with lock_path.open("a+b") as handle, _process_lock(handle):
            state.depth = 1
            try:
                yield
            finally:
                state.depth = 0
