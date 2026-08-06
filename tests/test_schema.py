from stitch_lineage.graph.schema import (
    Confidence,
    Coverage,
    Edge,
    EdgeType,
    Graph,
    Node,
    NodeType,
    column_node_id,
    mb_card_node_id,
    mb_dashboard_node_id,
    mb_field_node_id,
)


def test_node_id_helpers():
    assert column_node_id("model.smitten.fct_matches", "USER_ID") == (
        "model.smitten.fct_matches::user_id"
    )
    assert mb_field_node_id(101) == "mb_field::101"
    assert mb_card_node_id(412) == "mb_card::412"
    assert mb_dashboard_node_id(7) == "mb_dash::7"


def test_node_schema_alias_round_trip():
    node = Node(node_id="x", node_type=NodeType.MODEL, name="x", schema_="MARTS")
    dumped = node.model_dump(by_alias=True)
    assert dumped["schema"] == "MARTS"
    assert "schema_" not in dumped

    revalidated = Node.model_validate(
        {"node_id": "x", "node_type": "model", "name": "x", "schema": "MARTS"}
    )
    assert revalidated.schema_ == "MARTS"


def test_edge_from_alias_round_trip():
    edge = Edge(from_="a", to="b", edge_type=EdgeType.FEEDS, confidence=Confidence.PARSED)
    dumped = edge.model_dump(by_alias=True)
    assert dumped["from"] == "a"
    assert "from_" not in dumped

    revalidated = Edge.model_validate(
        {"from": "a", "to": "b", "edge_type": "feeds", "confidence": "parsed"}
    )
    assert revalidated.from_ == "a"


def test_enums_serialize_as_plain_strings():
    assert NodeType.MB_DASHBOARD == "mb_dashboard"
    assert EdgeType.RELATES_TO == "relates_to"
    assert Confidence.VALIDATED == "validated"


def test_coverage_partial_construction():
    coverage = Coverage(models_bound=3)
    assert coverage.models_bound == 3
    assert coverage.models_total == 0
    assert coverage.unbound_models == []
    assert coverage.unresolved_cards == []
    assert coverage.untraced_columns == []
    assert coverage.dangling_relationships == []


def test_graph_defaults():
    graph = Graph()
    assert graph.schema_version == 1
    assert graph.generated_at is None
    assert graph.nodes == []
    assert graph.edges == []
    assert graph.coverage == Coverage()
