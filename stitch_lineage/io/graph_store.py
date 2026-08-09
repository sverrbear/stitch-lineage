"""Read/write .stitch/graph.json with deterministic ordering (SPEC.md section 5).

Determinism contract: nodes sorted by node_id, edges by (from, to, edge_type), the
header keys (schema_version plus the volatile fields, kept at the top of the file per
SPEC.md section 5) first and every other key sorted, indent=2, trailing newline.
Regeneration without semantic change must produce a byte-identical file so the
committed graph diffs cleanly.
"""

import json
import shutil
from pathlib import Path
from typing import Any

from stitch_lineage.graph.schema import Graph

_VOLATILE_FIELDS = ("generated_at", "dbt_invocation_id", "metabase_version")
_HEADER_FIELDS = ("schema_version", *_VOLATILE_FIELDS)

PREV_FILENAME = "graph.prev.json"


def _canonical_payload(graph: Graph) -> dict[str, Any]:
    payload = graph.model_dump(mode="json", by_alias=True, exclude={"nodes", "edges"})
    payload["nodes"] = sorted(
        (node.model_dump(mode="json", by_alias=True, exclude_none=True) for node in graph.nodes),
        key=lambda node: node["node_id"],
    )
    payload["edges"] = sorted(
        (edge.model_dump(mode="json", by_alias=True) for edge in graph.edges),
        key=lambda edge: (edge["from"], edge["to"], edge["edge_type"]),
    )
    return payload


def _sort_keys(value: Any) -> Any:
    """Deep key sort, list order untouched -- byte-determinism without json sort_keys,
    which cannot express 'header first, everything else sorted'."""
    if isinstance(value, dict):
        return {key: _sort_keys(item) for key, item in sorted(value.items())}
    if isinstance(value, list):
        return [_sort_keys(item) for item in value]
    return value


def write_graph(graph: Graph, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _sort_keys(_canonical_payload(graph))
    ordered = {key: payload.pop(key) for key in _HEADER_FIELDS if key in payload}
    ordered.update(payload)
    path.write_text(json.dumps(ordered, indent=2) + "\n", encoding="utf-8")


def read_graph(path: Path) -> Graph:
    return Graph.model_validate_json(path.read_text(encoding="utf-8"))


def previous_graph_path(graph_path: Path) -> Path:
    """Where the last build's graph is kept -- `stitch impact`'s default baseline."""
    return graph_path.with_name(PREV_FILENAME)


def snapshot_previous(graph_path: Path) -> Graph | None:
    """Copy the graph about to be overwritten to graph.prev.json and return it.

    None on the first build, and also when the old file does not parse: a rebuild is
    exactly how you recover from a stale artifact, so it must never fail on one.
    """
    if not graph_path.is_file():
        return None
    destination = previous_graph_path(graph_path)
    shutil.copyfile(graph_path, destination)
    try:
        return read_graph(destination)
    except ValueError:
        return None


def graphs_semantically_equal(a: Graph, b: Graph) -> bool:
    """Equality ignoring the volatile header fields -- powers `stitch build --check`.

    Node/edge order is irrelevant (both sides are canonicalized before comparing).
    """
    payload_a = _canonical_payload(a)
    payload_b = _canonical_payload(b)
    for field in _VOLATILE_FIELDS:
        payload_a.pop(field, None)
        payload_b.pop(field, None)
    return payload_a == payload_b
