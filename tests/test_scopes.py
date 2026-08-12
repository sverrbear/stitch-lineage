import pytest

from stitch_lineage.graph.schema import Graph, Node, NodeType
from stitch_lineage.graph.scopes import erd_scopes, is_erd_table


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


# --- semantic views (#191) --------------------------------------------------------------


def _semantic_view(uid="model.demo.sv_revenue", schema="marts", **properties):
    return Node(
        node_id=uid,
        node_type=NodeType.MODEL,
        name=uid.rpartition(".")[2],
        schema_=schema,
        properties={"materialization": "semantic_view", **properties},
    )


def test_a_semantic_view_is_not_an_erd_table():
    assert is_erd_table(_semantic_view()) is False


@pytest.mark.parametrize("materialization", ["table", "view", "incremental", "ephemeral", None])
def test_every_other_materialization_is(materialization):
    """The rule is the materialization, never the `sv_` name prefix."""
    node = Node(
        node_id="model.demo.sv_looks_like_one",
        node_type=NodeType.MODEL,
        name="sv_looks_like_one",
        schema_="marts",
        properties={"materialization": materialization},
    )
    assert is_erd_table(node) is True


def test_a_semantic_view_contributes_no_scope():
    """A schema and a tag only semantic views carry are not scopes the ERD can open."""
    graph = _graph(
        Node(
            node_id="model.demo.fct",
            node_type=NodeType.MODEL,
            name="fct",
            schema_="marts",
            properties={"materialization": "table", "tags": ["core"]},
        ),
        _semantic_view(tags=["core", "semantic"]),
        _semantic_view("model.demo.sv_users", schema="semantic_layer", tags=["semantic"]),
    )
    assert erd_scopes(graph) == {"schema:marts", "tag:core"}
