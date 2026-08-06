"""Flat JSONL export for agents and warehouse loading (SPEC.md section 11)."""

from pathlib import Path

from stitch_lineage.graph.schema import Graph


def export_jsonl(graph: Graph, out_dir: Path) -> tuple[Path, Path]:
    """Write {out_dir}/nodes.jsonl and {out_dir}/edges.jsonl; return their paths.

    One record per line, serialized by alias ("from", "schema"), None fields excluded,
    same ordering as graph_store (nodes by node_id, edges by (from, to, edge_type)) so
    the export is deterministic too. Creates out_dir if needed.
    """
    raise NotImplementedError
