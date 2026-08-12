"""The suggestion engine (SPEC.md section 9, issue #30): what it proposes and what it refuses.

Fixtures are synthetic but real-shaped: `evidence.implicit_join` on a consumed_by edge
plus `fk_target_field_id` on the mb_field node is exactly what resolve/metabase.py emits
for an MBQL `opts["source-field"]`.
"""

import pytest

from stitch_lineage.graph.schema import (
    Confidence,
    Edge,
    EdgeType,
    Graph,
    Node,
    NodeType,
    column_node_id,
    mb_card_node_id,
    mb_field_node_id,
    relationship_id,
)
from stitch_lineage.graph.suggest import NAMING_SCORE, suggest

ORDERS = "model.demo.fct_orders"
CUSTOMERS = "model.demo.dim_customers"
PRODUCTS = "model.demo.dim_products"

# mb_field ids: the FK column users join through, and what it points at
ORDERS_CUSTOMER_FIELD = 501
CUSTOMERS_KEY_FIELD = 502


def _model(uid, name, **kwargs):
    return Node(node_id=uid, node_type=NodeType.MODEL, name=name, schema_="MARTS", **kwargs)


def _column(uid, name):
    return Node(node_id=column_node_id(uid, name), node_type=NodeType.COLUMN, name=name)


def _field(field_id, name, fk_target=None):
    properties = {"fk_target_field_id": fk_target} if fk_target is not None else {}
    return Node(
        node_id=mb_field_node_id(field_id),
        node_type=NodeType.MB_FIELD,
        name=name,
        properties=properties,
    )


def _card(card_id, name):
    return Node(node_id=mb_card_node_id(card_id), node_type=NodeType.MB_CARD, name=name)


def _binds(uid, column, field_id):
    return Edge(
        from_=column_node_id(uid, column),
        to=mb_field_node_id(field_id),
        edge_type=EdgeType.BINDS_TO,
        confidence=Confidence.EXACT,
    )


def _consumed(field_id, card_id, implicit_join=False):
    evidence = {"clauses": ["fields"]}
    if implicit_join:
        evidence["implicit_join"] = True
    return Edge(
        from_=mb_field_node_id(field_id),
        to=mb_card_node_id(card_id),
        edge_type=EdgeType.CONSUMED_BY,
        confidence=Confidence.EXACT,
        evidence=evidence,
    )


def _base_nodes():
    return [
        _model(ORDERS, "fct_orders"),
        _column(ORDERS, "order_id"),
        _column(ORDERS, "customer_id"),
        _column(ORDERS, "order_total"),
        _model(CUSTOMERS, "dim_customers"),
        _column(CUSTOMERS, "customer_id"),
        _column(CUSTOMERS, "country"),
        _field(ORDERS_CUSTOMER_FIELD, "Customer ID", fk_target=CUSTOMERS_KEY_FIELD),
        _field(CUSTOMERS_KEY_FIELD, "ID"),
        _card(901, "Orders by country"),
        _card(902, "Revenue by country"),
    ]


def _base_edges():
    return [
        _binds(ORDERS, "customer_id", ORDERS_CUSTOMER_FIELD),
        _binds(CUSTOMERS, "customer_id", CUSTOMERS_KEY_FIELD),
    ]


@pytest.fixture
def implicit_join_graph():
    """Two cards reach dim_customers.country by joining through fct_orders.customer_id."""
    return Graph(
        nodes=_base_nodes(),
        edges=[
            *_base_edges(),
            _consumed(ORDERS_CUSTOMER_FIELD, 901, implicit_join=True),
            _consumed(ORDERS_CUSTOMER_FIELD, 902, implicit_join=True),
            _consumed(CUSTOMERS_KEY_FIELD, 901),
        ],
    )


def _only(suggestions, source):
    return [entry for entry in suggestions if entry.source == source]


def _pairs(suggestions):
    return {
        (entry.from_model, entry.from_column, entry.to_model, entry.to_column)
        for entry in suggestions
    }


# --- implicit joins -------------------------------------------------------------------


def test_implicit_join_maps_the_field_pair_back_to_dbt_columns(implicit_join_graph):
    [suggestion] = _only(suggest(implicit_join_graph), "implicit_join")
    assert (suggestion.from_model, suggestion.from_column) == ("fct_orders", "customer_id")
    assert (suggestion.to_model, suggestion.to_column) == ("dim_customers", "customer_id")
    assert suggestion.cardinality_guess == "many-to-one"


def test_score_counts_witnessing_cards(implicit_join_graph):
    [suggestion] = _only(suggest(implicit_join_graph), "implicit_join")
    assert suggestion.score == 2.0
    assert suggestion.evidence["card_ids"] == ["mb_card::901", "mb_card::902"]
    assert suggestion.evidence["mb_source_field"] == mb_field_node_id(ORDERS_CUSTOMER_FIELD)
    assert suggestion.evidence["mb_target_field"] == mb_field_node_id(CUSTOMERS_KEY_FIELD)


def test_the_same_card_twice_does_not_inflate_the_score(implicit_join_graph):
    implicit_join_graph.edges.append(_consumed(ORDERS_CUSTOMER_FIELD, 902, implicit_join=True))
    [suggestion] = _only(suggest(implicit_join_graph), "implicit_join")
    assert suggestion.score == 2.0


def test_a_plain_consumed_by_edge_is_not_evidence(implicit_join_graph):
    plain = Graph(
        nodes=implicit_join_graph.nodes,
        edges=[*_base_edges(), _consumed(ORDERS_CUSTOMER_FIELD, 901)],
    )
    assert _only(suggest(plain), "implicit_join") == []


def test_an_fk_field_with_no_target_is_dropped_not_guessed():
    nodes = [node for node in _base_nodes() if node.node_id != mb_field_node_id(501)]
    nodes.append(_field(ORDERS_CUSTOMER_FIELD, "Customer ID"))
    graph = Graph(
        nodes=nodes,
        edges=[*_base_edges(), _consumed(ORDERS_CUSTOMER_FIELD, 901, implicit_join=True)],
    )
    assert _only(suggest(graph), "implicit_join") == []


def test_an_unbound_field_pair_is_dropped():
    """Metabase joins two tables stitch never bound to dbt columns -- nothing to suggest."""
    graph = Graph(
        nodes=_base_nodes(),
        edges=[
            _binds(ORDERS, "customer_id", ORDERS_CUSTOMER_FIELD),
            _consumed(ORDERS_CUSTOMER_FIELD, 901, implicit_join=True),
        ],
    )
    assert _only(suggest(graph), "implicit_join") == []


def test_implicit_join_beats_the_naming_guess_for_the_same_pair(implicit_join_graph):
    matches = [
        entry
        for entry in suggest(implicit_join_graph)
        if (entry.from_model, entry.to_model) == ("fct_orders", "dim_customers")
    ]
    assert [entry.source for entry in matches] == ["implicit_join"]
    assert matches[0].score == 2.0


# --- naming conventions ---------------------------------------------------------------


def _naming_graph(extra_nodes=()):
    return Graph(
        nodes=[
            _model(ORDERS, "fct_orders"),
            _column(ORDERS, "order_id"),
            _column(ORDERS, "customer_id"),
            _model(CUSTOMERS, "dim_customers"),
            _column(CUSTOMERS, "customer_id"),
            *extra_nodes,
        ]
    )


def test_entity_id_matching_a_models_grain_is_suggested():
    [suggestion] = _only(suggest(_naming_graph()), "naming")
    assert (suggestion.from_model, suggestion.from_column) == ("fct_orders", "customer_id")
    assert (suggestion.to_model, suggestion.to_column) == ("dim_customers", "customer_id")
    assert suggestion.score == NAMING_SCORE
    assert suggestion.evidence == {"entity": "customer", "matched_column": "customer_id"}


def test_a_bare_id_column_is_an_acceptable_target():
    graph = Graph(
        nodes=[
            _model(ORDERS, "fct_orders"),
            _column(ORDERS, "product_id"),
            _model(PRODUCTS, "dim_products"),
            _column(PRODUCTS, "id"),
        ]
    )
    [suggestion] = _only(suggest(graph), "naming")
    assert (suggestion.to_model, suggestion.to_column) == ("dim_products", "id")


def test_layer_prefixes_and_plurals_do_not_hide_the_grain():
    graph = Graph(
        nodes=[
            _model("model.demo.viz_fct_orders", "viz_fct_orders"),
            _column("model.demo.viz_fct_orders", "company_id"),
            _model("model.demo.viz_dim_companies", "viz_dim_companies"),
            _column("model.demo.viz_dim_companies", "company_id"),
        ]
    )
    [suggestion] = _only(suggest(graph), "naming")
    assert suggestion.to_model == "viz_dim_companies"


def test_a_column_naming_its_own_models_grain_is_a_key_not_a_foreign_key():
    """dim_customers.customer_id must not be offered as an FK into anything."""
    suggestions = suggest(_naming_graph())
    assert all(entry.from_model != "dim_customers" for entry in suggestions)


@pytest.mark.parametrize(
    ("column", "reason"),
    [
        ("order_total", "not an id column"),
        ("id", "bare id names no entity"),
        ("_id", "no entity before the suffix"),
        ("supplier_id", "no model has that grain"),
        ("customer_key", "wrong suffix convention"),
    ],
)
def test_naming_negatives(column, reason):
    graph = Graph(
        nodes=[
            _model(ORDERS, "fct_orders"),
            _column(ORDERS, column),
            _model(CUSTOMERS, "dim_customers"),
            _column(CUSTOMERS, "customer_id"),
        ]
    )
    assert _only(suggest(graph), "naming") == [], reason


def test_a_grain_match_with_no_matching_target_column_is_not_suggested():
    graph = Graph(
        nodes=[
            _model(ORDERS, "fct_orders"),
            _column(ORDERS, "customer_id"),
            _model(CUSTOMERS, "dim_customers"),
            _column(CUSTOMERS, "email"),
        ]
    )
    assert suggest(graph) == []


def test_source_columns_are_never_endpoints():
    """Neither the staging API nor `stitch apply` can write a source, so never offer one."""
    source = "source.demo.raw.customers"
    graph = Graph(
        nodes=[
            _model(ORDERS, "fct_orders"),
            _column(ORDERS, "customer_id"),
            Node(node_id=source, node_type=NodeType.SOURCE, name="customers"),
            _column(source, "customer_id"),
        ]
    )
    assert suggest(graph) == []


# --- exclusions -----------------------------------------------------------------------


def test_declared_relationships_are_excluded():
    graph = _naming_graph()
    graph.edges.append(
        Edge(
            from_=column_node_id(ORDERS, "customer_id"),
            to=column_node_id(CUSTOMERS, "customer_id"),
            edge_type=EdgeType.RELATES_TO,
            confidence=Confidence.DECLARED,
        )
    )
    assert suggest(graph) == []


def test_a_declaration_pointing_the_other_way_is_the_same_relationship():
    graph = _naming_graph()
    graph.edges.append(
        Edge(
            from_=column_node_id(CUSTOMERS, "customer_id"),
            to=column_node_id(ORDERS, "customer_id"),
            edge_type=EdgeType.RELATES_TO,
            confidence=Confidence.DECLARED,
        )
    )
    assert suggest(graph) == []


def test_staged_pairs_are_excluded_in_either_direction():
    forward = ("fct_orders", "customer_id", "dim_customers", "customer_id")
    reverse = ("dim_customers", "customer_id", "fct_orders", "customer_id")
    assert suggest(_naming_graph(), staged=[forward]) == []
    assert suggest(_naming_graph(), staged=[reverse]) == []


def test_dismissed_ids_are_excluded():
    dismissed = relationship_id("fct_orders", "customer_id", "dim_customers", "customer_id")
    assert suggest(_naming_graph(), dismissed=[dismissed]) == []
    assert suggest(_naming_graph(), dismissed=["someotherid"]) != []


def test_self_joins_are_excluded():
    """Both ends of the implicit join bind into fct_orders -- v1 does not offer self-joins."""
    graph = Graph(
        nodes=[
            _model(ORDERS, "fct_orders"),
            _column(ORDERS, "order_id"),
            _column(ORDERS, "parent_order_id"),
            _field(ORDERS_CUSTOMER_FIELD, "Parent Order ID", fk_target=CUSTOMERS_KEY_FIELD),
            _field(CUSTOMERS_KEY_FIELD, "Order ID"),
            _card(901, "Split orders"),
        ],
        edges=[
            _binds(ORDERS, "parent_order_id", ORDERS_CUSTOMER_FIELD),
            _binds(ORDERS, "order_id", CUSTOMERS_KEY_FIELD),
            _consumed(ORDERS_CUSTOMER_FIELD, 901, implicit_join=True),
        ],
    )
    assert suggest(graph) == []


def test_implicit_join_exclusions_apply_too(implicit_join_graph):
    staged = ("fct_orders", "customer_id", "dim_customers", "customer_id")
    assert _only(suggest(implicit_join_graph, staged=[staged]), "implicit_join") == []


# --- ids, ordering, determinism -------------------------------------------------------


def test_id_is_the_staged_relationship_hash(implicit_join_graph):
    [suggestion] = _only(suggest(implicit_join_graph), "implicit_join")
    assert suggestion.id == relationship_id(
        "fct_orders", "customer_id", "dim_customers", "customer_id"
    )


def test_ids_survive_a_rebuild_that_moves_everything_else(implicit_join_graph):
    """Node ordering, generated_at and card ids all churn between builds; the id must not."""
    before = {entry.id for entry in suggest(implicit_join_graph)}
    rebuilt = Graph(
        generated_at="2027-01-01T00:00:00+00:00",
        dbt_invocation_id="a-different-run",
        nodes=[
            *reversed(implicit_join_graph.nodes),
            _card(903, "A new card"),
        ],
        edges=[
            *reversed(implicit_join_graph.edges),
            _consumed(ORDERS_CUSTOMER_FIELD, 903, implicit_join=True),
        ],
    )
    after = suggest(rebuilt)
    assert {entry.id for entry in after} == before
    # the score moved (a third card now joins through it) but the identity did not
    assert _only(after, "implicit_join")[0].score == 3.0


def test_results_sort_by_score_then_deterministically(implicit_join_graph):
    implicit_join_graph.nodes.extend(
        [
            _model(PRODUCTS, "dim_products"),
            _column(PRODUCTS, "product_id"),
            _column(ORDERS, "product_id"),
        ]
    )
    suggestions = suggest(implicit_join_graph)
    assert [entry.score for entry in suggestions] == sorted(
        (entry.score for entry in suggestions), reverse=True
    )
    assert suggestions[0].source == "implicit_join"
    assert [entry.sort_key() for entry in suggestions] == sorted(
        entry.sort_key() for entry in suggestions
    )
    assert _pairs(suggestions) == _pairs(suggest(implicit_join_graph))


def test_an_empty_graph_suggests_nothing():
    assert suggest(Graph()) == []


# --- semantic views (#191) --------------------------------------------------------------


def test_a_semantic_view_is_never_a_suggestion_endpoint():
    """A suggestion is a candidate ERD edge, and the ERD draws no semantic view -- so
    proposing one is proposing a relationship with no table to land on."""
    graph = Graph(
        nodes=[
            _model(ORDERS, "fct_orders"),
            _column(ORDERS, "order_id"),
            _column(ORDERS, "customer_id"),
            _model(
                "model.demo.sv_customers",
                "sv_customers",
                properties={"materialization": "semantic_view"},
            ),
            _column("model.demo.sv_customers", "customer_id"),
        ]
    )
    assert suggest(graph) == []


@pytest.mark.parametrize("materialization", ["table", "view", "incremental", "ephemeral", None])
def test_every_other_materialization_still_suggests(materialization):
    """The rule is the materialization, never the `sv_` name: a table-materialized
    model is a candidate whatever it is called."""
    graph = _naming_graph()
    for node in graph.nodes:
        if node.node_type is NodeType.MODEL:
            node.properties["materialization"] = materialization
    assert len(_only(suggest(graph), "naming")) == 1
