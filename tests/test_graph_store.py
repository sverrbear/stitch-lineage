import json

from stitch_lineage.graph.schema import Confidence, Edge, EdgeType, Graph
from stitch_lineage.io.graph_store import (
    graphs_semantically_equal,
    merge_edges,
    read_graph,
    write_graph,
)


def test_round_trip(tmp_path, sample_graph):
    path = tmp_path / "graph.json"
    write_graph(sample_graph, path)
    loaded = read_graph(path)
    assert graphs_semantically_equal(sample_graph, loaded)
    assert loaded.generated_at == sample_graph.generated_at
    assert len(loaded.nodes) == len(sample_graph.nodes)
    assert len(loaded.edges) == len(sample_graph.edges)


def test_writes_are_byte_identical(tmp_path, sample_graph):
    path_a = tmp_path / "a.json"
    path_b = tmp_path / "b.json"
    write_graph(sample_graph, path_a)

    shuffled = sample_graph.model_copy(
        update={
            "nodes": list(reversed(sample_graph.nodes)),
            "edges": list(reversed(sample_graph.edges)),
        }
    )
    write_graph(shuffled, path_b)
    assert path_a.read_bytes() == path_b.read_bytes()


def test_writes_are_byte_identical_regardless_of_evidence_key_order(tmp_path, sample_graph):
    # nested dicts are key-sorted by the writer, so runtime insertion order cannot
    # leak into the file (json sort_keys can no longer do this -- the header is ordered)
    path_a = tmp_path / "a.json"
    path_b = tmp_path / "b.json"
    edge = sample_graph.edges[0]
    forward = edge.model_copy(update={"evidence": {"alpha": 1, "beta": 2}})
    reversed_keys = edge.model_copy(update={"evidence": {"beta": 2, "alpha": 1}})
    write_graph(sample_graph.model_copy(update={"edges": [forward]}), path_a)
    write_graph(sample_graph.model_copy(update={"edges": [reversed_keys]}), path_b)
    assert path_a.read_bytes() == path_b.read_bytes()


def test_header_fields_are_written_first(tmp_path, sample_graph):
    path = tmp_path / "graph.json"
    write_graph(sample_graph, path)
    keys = list(json.loads(path.read_text()))  # json.loads preserves file key order
    assert keys[:4] == [
        "schema_version",
        "generated_at",
        "dbt_invocation_id",
        "metabase_version",
    ]
    assert keys[4:] == sorted(keys[4:])


def test_file_format(tmp_path, sample_graph):
    path = tmp_path / "graph.json"
    write_graph(sample_graph, path)
    text = path.read_text()
    assert text.endswith("\n")

    payload = json.loads(text)
    assert payload["schema_version"] == 1
    assert [node["node_id"] for node in payload["nodes"]] == sorted(
        node["node_id"] for node in payload["nodes"]
    )
    model_node = next(
        node for node in payload["nodes"] if node["node_id"] == "model.smitten.fct_matches"
    )
    assert model_node["schema"] == "MARTS"
    assert "description" not in model_node
    assert all("from" in edge for edge in payload["edges"])


def test_semantic_equality_ignores_volatile_fields(sample_graph):
    other = sample_graph.model_copy(
        update={
            "generated_at": "2030-01-01T00:00:00+00:00",
            "dbt_invocation_id": "different",
            "metabase_version": "0.99.0",
        }
    )
    assert graphs_semantically_equal(sample_graph, other)


def test_semantic_equality_ignores_ordering(sample_graph):
    shuffled = sample_graph.model_copy(
        update={
            "nodes": list(reversed(sample_graph.nodes)),
            "edges": list(reversed(sample_graph.edges)),
        }
    )
    assert graphs_semantically_equal(sample_graph, shuffled)


def test_semantic_equality_detects_real_change(sample_graph):
    changed_node = sample_graph.nodes[0].model_copy(update={"name": "fct_matches_v2"})
    changed = sample_graph.model_copy(update={"nodes": [changed_node, *sample_graph.nodes[1:]]})
    assert not graphs_semantically_equal(sample_graph, changed)

    fewer_edges = sample_graph.model_copy(update={"edges": sample_graph.edges[:1]})
    assert not graphs_semantically_equal(sample_graph, fewer_edges)


def test_write_creates_parent_dirs(tmp_path):
    path = tmp_path / ".stitch" / "graph.json"
    write_graph(Graph(), path)
    assert path.is_file()


# --- merge_edges: the `stitch apply` graph patch ------------------------------------------


def _relates(from_id="a::x", to_id="b::y", confidence=Confidence.VALIDATED, **evidence):
    return Edge(
        from_=from_id,
        to=to_id,
        edge_type=EdgeType.RELATES_TO,
        confidence=confidence,
        evidence=evidence,
    )


def test_merge_edges_adds_new_edges(sample_graph):
    before = len(sample_graph.edges)
    added = merge_edges(sample_graph, [_relates(), _relates(to_id="c::z")])
    assert added == 2
    assert len(sample_graph.edges) == before + 2


def test_merge_edges_skips_an_edge_already_in_the_graph(sample_graph):
    merge_edges(sample_graph, [_relates()])
    before = list(sample_graph.edges)

    added = merge_edges(sample_graph, [_relates(confidence=Confidence.DECLARED, source="other")])
    assert added == 0
    # identity is (from, to, edge_type): the existing edge is kept, not rewritten
    assert sample_graph.edges == before


def test_merge_edges_dedupes_within_its_own_input(sample_graph):
    assert merge_edges(sample_graph, [_relates(), _relates()]) == 1


def test_merge_edges_keeps_a_different_edge_type_between_the_same_nodes(sample_graph):
    merge_edges(sample_graph, [_relates()])
    same_nodes = Edge(
        from_="a::x", to="b::y", edge_type=EdgeType.FEEDS, confidence=Confidence.PARSED
    )
    assert merge_edges(sample_graph, [same_nodes]) == 1


def test_a_patched_graph_still_writes_deterministically(tmp_path, sample_graph):
    patched = tmp_path / "patched.json"
    merge_edges(sample_graph, [_relates(to_id="z::z"), _relates()])
    write_graph(sample_graph, patched)

    rebuilt = tmp_path / "rebuilt.json"
    write_graph(read_graph(patched), rebuilt)
    assert patched.read_bytes() == rebuilt.read_bytes()
