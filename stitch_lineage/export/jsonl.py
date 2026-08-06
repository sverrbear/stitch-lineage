"""Flat JSONL export for agents and warehouse loading (SPEC.md section 11)."""

import json
from pathlib import Path

from stitch_lineage.graph.schema import Graph


def _dump_lines(records: list[dict]) -> str:
    return "".join(json.dumps(record, sort_keys=True) + "\n" for record in records)


def export_jsonl(graph: Graph, out_dir: Path) -> tuple[Path, Path]:
    """Write {out_dir}/nodes.jsonl and {out_dir}/edges.jsonl; return their paths.

    One record per line, serialized by alias ("from", "schema"), None fields excluded,
    same ordering as graph_store (nodes by node_id, edges by (from, to, edge_type)) so
    the export is deterministic too. Creates out_dir if needed.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    node_records = sorted(
        (node.model_dump(mode="json", by_alias=True, exclude_none=True) for node in graph.nodes),
        key=lambda record: record["node_id"],
    )
    edge_records = sorted(
        (edge.model_dump(mode="json", by_alias=True, exclude_none=True) for edge in graph.edges),
        key=lambda record: (record["from"], record["to"], record["edge_type"]),
    )
    nodes_path = out_dir / "nodes.jsonl"
    edges_path = out_dir / "edges.jsonl"
    nodes_path.write_text(_dump_lines(node_records), encoding="utf-8")
    edges_path.write_text(_dump_lines(edge_records), encoding="utf-8")
    return nodes_path, edges_path
