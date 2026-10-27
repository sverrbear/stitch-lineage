"""Serve the entirely synthetic public lineage screenshot at localhost:8790.

Run `python scripts/demo_lineage.py`, open the printed URL, expand the downstream
branches, then select Show overview. No warehouse, dbt project or credentials are read.
"""

import argparse
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import uvicorn

from stitch_lineage.app.server import create_app
from stitch_lineage.graph.schema import Graph
from stitch_lineage.io.graph_store import write_graph


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8790)
    args = parser.parse_args()
    nodes, edges = [], []
    for node_id, kind, name, schema in [
        ("source.demo.payments.transactions", "source", "transactions", "raw"),
        ("model.demo.stg_payments", "model", "stg_payments", "staging"),
        ("model.demo.fct_orders", "model", "fct_orders", "marts"),
    ]:
        nodes.append(
            {"node_id": node_id, "node_type": kind, "name": name, "schema": schema, "table": name}
        )
        nodes.append(
            {
                "node_id": f"{node_id}::net_revenue",
                "node_type": "column",
                "name": "net_revenue",
                "column": "net_revenue",
                "schema": schema,
                "table": name,
                "data_type": "NUMBER",
            }
        )
    for node_id, kind, name in [
        ("mb_field::101", "mb_field", "Net Revenue"),
        ("mb_card::401", "mb_card", "Revenue by month"),
        ("mb_card::402", "mb_card", "Revenue by region"),
        ("mb_dashboard::10", "mb_dashboard", "Revenue overview"),
    ]:
        nodes.append({"node_id": node_id, "node_type": kind, "name": name})
    for source, target, kind, confidence in [
        (
            "source.demo.payments.transactions::net_revenue",
            "model.demo.stg_payments::net_revenue",
            "feeds",
            "parsed",
        ),
        (
            "model.demo.stg_payments::net_revenue",
            "model.demo.fct_orders::net_revenue",
            "feeds",
            "parsed",
        ),
        ("model.demo.fct_orders::net_revenue", "mb_field::101", "binds_to", "exact"),
        ("mb_field::101", "mb_card::401", "consumed_by", "exact"),
        ("mb_field::101", "mb_card::402", "consumed_by", "exact"),
        ("mb_card::401", "mb_dashboard::10", "appears_on", "exact"),
        ("mb_card::402", "mb_dashboard::10", "appears_on", "exact"),
    ]:
        edges.append({"from": source, "to": target, "edge_type": kind, "confidence": confidence})
    graph = Graph.model_validate(
        {"generated_at": "2026-10-27T09:00:00Z", "nodes": nodes, "edges": edges}
    )
    with tempfile.TemporaryDirectory(prefix="stitch-lineage-demo-") as directory:
        path = Path(directory) / "graph.json"
        write_graph(graph, path)
        print(
            f"Synthetic lineage: http://127.0.0.1:{args.port}/#/lineage/"
            "model.demo.fct_orders%3A%3Anet_revenue",
            flush=True,
        )
        uvicorn.run(
            create_app(path, "https://metabase.example.test"), host="127.0.0.1", port=args.port
        )


if __name__ == "__main__":
    main()
