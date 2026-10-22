"""Transactions protect acknowledged changes across threads, processes, and apply."""

import importlib
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest

from stitch_lineage import apply as apply_service
from stitch_lineage.graph.schema import Graph, Node, NodeType
from stitch_lineage.io import graph_store, staged_store
from stitch_lineage.io.file_lock import file_lock
from stitch_lineage.io.staged_store import StagedRelationship


def entry(column):
    return StagedRelationship(
        from_model="orders", from_column=column, to_model="customers", to_column="id"
    )


@pytest.mark.parametrize("store_kind", ["relationships", "descriptions", "graph"])
def test_concurrent_transactions_wait_until_the_first_read_and_write_finish(
    tmp_path, monkeypatch, store_kind
):
    path = tmp_path / "store.yml"
    first_read, release, second_started = Event(), Event(), Event()
    if store_kind == "relationships":
        module, name = staged_store, "read_staged"

        def write(key):
            return staged_store.add_staged(entry(key), path)

        def read():
            return [item.from_column for item in staged_store.read_staged(path)]
    elif store_kind == "descriptions":
        module, name = staged_store, "read_descriptions"

        def write(key):
            return staged_store.upsert_description(
                staged_store.StagedDescription(entity=key, new_description=key), path
            )

        def read():
            return [item.entity for item in staged_store.read_descriptions(path)]
    else:
        graph_store.write_graph(
            Graph(
                nodes=[
                    Node(node_id=f"model.demo.{key}", name=key, node_type=NodeType.MODEL)
                    for key in ("first", "second")
                ]
            ),
            path,
        )
        module, name = apply_service, "read_graph"

        def write(key):
            patch = apply_service.patch_graph(
                path,
                [staged_store.StagedDescription(entity=key, new_description=key)],
                "relationships_test",
            )
            assert patch.descriptions_updated == 1

        def read():
            return [node.description for node in graph_store.read_graph(path).nodes]

    original = getattr(module, name)
    calls = 0

    def pause_first_read(path):
        nonlocal calls
        value = original(path)
        calls += 1
        if calls == 1:
            first_read.set()
            assert release.wait(5)
        return value

    monkeypatch.setattr(module, name, pause_first_read)

    def second_write():
        second_started.set()
        return write("second")

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(write, "first")
        try:
            assert first_read.wait(5)
            second = pool.submit(second_write)
            assert second_started.wait(5)
            with pytest.raises(TimeoutError):
                second.result(timeout=0.1)
        finally:
            release.set()
        first.result(timeout=5)
        second.result(timeout=5)
    assert sorted(read()) == ["first", "second"]


def test_nested_lock_is_reentrant_and_lockfile_survives_replacement(tmp_path):
    path = tmp_path / "store.yml"
    with file_lock(path):
        staged_store.add_staged(entry("first"), path)
        with file_lock(path):
            staged_store.add_staged(entry("second"), path)
    assert len(staged_store.read_staged(path)) == 2
    lock_path = tmp_path / ".store.yml.lock"
    inode = lock_path.stat().st_ino
    staged_store.remove_staged(entry("first").id, path)
    assert lock_path.stat().st_ino == inode


def test_another_process_cannot_overwrite_a_transaction_in_progress(tmp_path):
    path = tmp_path / "store.yml"
    script = """
import sys
from pathlib import Path
from stitch_lineage.io.staged_store import StagedRelationship, add_staged
print('started', flush=True)
record = StagedRelationship(
    from_model='orders', from_column='second', to_model='customers', to_column='id'
)
add_staged(record, Path(sys.argv[1]))
"""
    process = None
    try:
        with file_lock(path):
            staged_store.add_staged(entry("first"), path)
            process = subprocess.Popen(
                [sys.executable, "-c", script, str(path)],
                cwd=Path(__file__).resolve().parents[1],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            assert process.stdout.readline().strip() == "started"
            with pytest.raises(subprocess.TimeoutExpired):
                process.wait(timeout=0.1)
        stdout, stderr = process.communicate(timeout=5)
        assert process.returncode == 0, (stdout, stderr)
        assert {item.from_column for item in staged_store.read_staged(path)} == {"first", "second"}
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.communicate()


def test_windows_byte_lock_is_released_even_when_transaction_raises(tmp_path, monkeypatch):
    locks = importlib.import_module("stitch_lineage.io.file_lock")
    calls = []
    monkeypatch.setattr(locks, "_WINDOWS", True)
    monkeypatch.setitem(
        sys.modules,
        "msvcrt",
        SimpleNamespace(
            LK_LOCK=1,
            LK_UNLCK=2,
            locking=lambda fd, operation, length: calls.append((operation, length)),
        ),
    )
    with (
        (tmp_path / "lock").open("a+b") as handle,
        pytest.raises(RuntimeError),
        locks._process_lock(handle),
    ):
        raise RuntimeError("synthetic failure")
    assert calls == [(1, 1), (2, 1)]
    assert (tmp_path / "lock").read_bytes() == b"\0"
