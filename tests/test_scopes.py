from stitch_lineage.graph.schema import Graph, Node, NodeType
from stitch_lineage.graph.scopes import erd_scopes


def _graph(*nodes: Node) -> Graph:
    return Graph(generated_at="2026-08-07T00:00:00+00:00", nodes=list(nodes))


def test_scopes_come_from_model_and_source_schemas_and_tags(sample_graph):
    assert erd_scopes(sample_graph) == {"schema:MARTS"}


def test_tags_become_scopes():
    graph = _graph(
        Node(
            node_id="model.demo.fct",
            node_type=NodeType.MODEL,
            name="fct",
            schema_="marts",
            properties={"tags": ["core", "finance"]},
        ),
        Node(
            node_id="source.demo.app.events",
            node_type=NodeType.SOURCE,
            name="events",
            schema_="raw",
        ),
    )
    assert erd_scopes(graph) == {"schema:marts", "schema:raw", "tag:core", "tag:finance"}


def test_columns_and_bi_nodes_are_not_scopes():
    graph = _graph(
        Node(
            node_id="model.demo.fct::user_id",
            node_type=NodeType.COLUMN,
            name="user_id",
            schema_="marts",
        ),
        Node(node_id="mb_card::1", node_type=NodeType.MB_CARD, name="card"),
    )
    assert erd_scopes(graph) == set()


def test_schemaless_and_malformed_tags_are_skipped():
    graph = _graph(
        Node(node_id="model.demo.a", node_type=NodeType.MODEL, name="a"),
        Node(
            node_id="model.demo.b",
            node_type=NodeType.MODEL,
            name="b",
            schema_="marts",
            properties={"tags": "core"},
        ),
    )
    assert erd_scopes(graph) == {"schema:marts"}
