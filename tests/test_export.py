import json

from stitch_lineage.export.jsonl import export_jsonl


def test_deterministic_across_input_ordering(tmp_path, sample_graph):
    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    export_jsonl(sample_graph, dir_a)
    shuffled = sample_graph.model_copy(
        update={
            "nodes": list(reversed(sample_graph.nodes)),
            "edges": list(reversed(sample_graph.edges)),
        }
    )
    export_jsonl(shuffled, dir_b)
    assert (dir_a / "nodes.jsonl").read_bytes() == (dir_b / "nodes.jsonl").read_bytes()
    assert (dir_a / "edges.jsonl").read_bytes() == (dir_b / "edges.jsonl").read_bytes()


def test_lines_parse_with_aliases_and_no_none(tmp_path, sample_graph):
    nodes_path, edges_path = export_jsonl(sample_graph, tmp_path / "out")
    node_records = [json.loads(line) for line in nodes_path.read_text().splitlines()]
    edge_records = [json.loads(line) for line in edges_path.read_text().splitlines()]
    assert len(node_records) == len(sample_graph.nodes)
    assert len(edge_records) == len(sample_graph.edges)
    assert all("from" in record and "to" in record for record in edge_records)
    model_record = next(r for r in node_records if r["node_id"] == "model.smitten.fct_matches")
    assert model_record["schema"] == "MARTS"
    assert "description" not in model_record


def test_ordering(tmp_path, sample_graph):
    nodes_path, edges_path = export_jsonl(sample_graph, tmp_path / "out")
    node_ids = [json.loads(line)["node_id"] for line in nodes_path.read_text().splitlines()]
    assert node_ids == sorted(node_ids)
    edge_keys = [
        (record["from"], record["to"], record["edge_type"])
        for record in map(json.loads, edges_path.read_text().splitlines())
    ]
    assert edge_keys == sorted(edge_keys)


def test_creates_out_dir_and_returns_paths(tmp_path, sample_graph):
    out = tmp_path / "nested" / "export"
    nodes_path, edges_path = export_jsonl(sample_graph, out)
    assert nodes_path == out / "nodes.jsonl"
    assert edges_path == out / "edges.jsonl"
    assert nodes_path.is_file() and edges_path.is_file()
