"""Read/write .stitch/graph.json with deterministic ordering (SPEC.md section 5).

Determinism contract: nodes sorted by node_id, edges by (from, to, edge_type), all
keys sorted, indent=2, trailing newline. Regeneration without semantic change must
produce a byte-identical file so the committed graph diffs cleanly.
"""

import json
from pathlib import Path
from typing import Any

from stitch_lineage.graph.schema import Graph

_VOLATILE_FIELDS = ("generated_at", "dbt_invocation_id", "metabase_version")


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


def write_graph(graph: Graph, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _canonical_payload(graph)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_graph(path: Path) -> Graph:
    return Graph.model_validate_json(path.read_text(encoding="utf-8"))


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
