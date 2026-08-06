from stitch_lineage.graph.schema import Graph, Node, NodeType, column_node_id
from stitch_lineage.graph.search import search


def node(node_id, name, node_type=NodeType.MODEL, **kwargs):
    return Node(node_id=node_id, node_type=node_type, name=name, **kwargs)


def tier_graph():
    return Graph(
        nodes=[
            node("model.s.revenue", "revenue"),
            node("model.s.revenue_net", "revenue_net"),
            node("model.s.net_revenue", "net_revenue"),
            node("model.s.prevenue", "prevenue"),
            node("model.s.revene", "revene"),
            node(
                "model.s.obscure_thing",
                "obscure_thing",
                description="tracks revenue recognition",
            ),
            node("model.s.swipes", "swipes"),
        ]
    )


def test_tier_ordering():
    results = search(tier_graph(), "revenue")
    names = [r.name for r in results]
    assert names == [
        "revenue",  # exact
        "revenue_net",  # prefix
        "net_revenue",  # word boundary
        "obscure_thing",  # substring (description), before prevenue by name
        "prevenue",  # substring (name)
        "revene",  # fuzzy
    ]
    scores = [r.score for r in results]
    assert scores == sorted(scores, reverse=True)
    assert results[0].score == 5.0
    assert "swipes" not in names  # below the fuzzy floor


def test_fuzzy_tier_ordered_by_ratio_not_name():
    graph = Graph(
        nodes=[
            node("model.s.pay_events", "pay_events"),  # ratio ~0.78, alphabetically first
            node("model.s.payment_z", "payment_z"),  # ratio ~0.82
        ]
    )
    results = search(graph, "payments")
    assert [r.name for r in results] == ["payment_z", "pay_events"]
    assert results[0].score > results[1].score


def test_matched_field_reported():
    results = search(tier_graph(), "revenue")
    by_name = {r.name: r for r in results}
    assert by_name["revenue"].matched_field == "name"
    assert by_name["obscure_thing"].matched_field == "description"


def test_case_insensitive():
    lower = search(tier_graph(), "revenue")
    upper = search(tier_graph(), "REVENUE")
    assert [r.node_id for r in lower] == [r.node_id for r in upper]


def test_limit():
    results = search(tier_graph(), "revenue", limit=3)
    assert [r.name for r in results] == ["revenue", "revenue_net", "net_revenue"]


def test_empty_query_returns_nothing():
    assert search(tier_graph(), "   ") == []


def test_column_context_is_model_name():
    graph = Graph(
        nodes=[
            node("model.s.fct_matches", "fct_matches"),
            node(
                column_node_id("model.s.fct_matches", "match_intensity"),
                "match_intensity",
                node_type=NodeType.COLUMN,
            ),
        ]
    )
    results = search(graph, "match_intensity")
    assert results[0].node_type == NodeType.COLUMN
    assert results[0].context == "fct_matches"


def test_tags_and_collection():
    graph = Graph(
        nodes=[
            node(
                "mb_card::7",
                "Weekly KPIs",
                node_type=NodeType.MB_CARD,
                properties={"tags": ["finance"], "collection_name": "Board"},
            )
        ]
    )
    results = search(graph, "finance")
    assert len(results) == 1
    assert results[0].matched_field == "properties.tags"
    assert results[0].context == "Board"
