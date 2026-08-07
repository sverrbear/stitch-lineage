import json
from pathlib import Path

import pytest

from stitch_lineage.graph.schema import (
    Confidence,
    EdgeType,
    NodeType,
    mb_card_node_id,
    mb_dashboard_node_id,
    mb_field_node_id,
)
from stitch_lineage.payloads import MetabasePayload
from stitch_lineage.resolve.metabase import resolve_metabase

FIXTURES = Path(__file__).parent / "fixtures" / "metabase"
EXCLUDE = ["Personal*", "Archive*"]


def fixture(name: str) -> dict | list:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def payload() -> MetabasePayload:
    databases = fixture("databases")["data"]
    return MetabasePayload(
        metabase_version="v0.53.2",
        databases=[db for db in databases if db["name"] == "Analytics"],
        database_metadata={2: fixture("database_metadata_2")},
        cards=fixture("cards"),
        dashboards=[fixture("dashboard_301"), fixture("dashboard_302")],
        collections=fixture("collections"),
    )


@pytest.fixture(scope="module")
def resolution(payload):
    return resolve_metabase(payload, EXCLUDE)


def consumed_by(resolution, card_id: int) -> dict[str, dict]:
    return {
        edge.from_: edge.evidence
        for edge in resolution.edges
        if edge.edge_type == EdgeType.CONSUMED_BY and edge.to == mb_card_node_id(card_id)
    }


def consumed_edge(resolution, card_id: int, field_id: int):
    return next(
        edge
        for edge in resolution.edges
        if edge.edge_type == EdgeType.CONSUMED_BY
        and edge.to == mb_card_node_id(card_id)
        and edge.from_ == mb_field_node_id(field_id)
    )


def test_node_inventory(resolution):
    by_type = {}
    for node in resolution.nodes:
        by_type.setdefault(node.node_type, set()).add(node.node_id)
    assert by_type[NodeType.MB_FIELD] == {mb_field_node_id(i) for i in range(100, 107)}
    assert by_type[NodeType.MB_CARD] == {
        mb_card_node_id(i) for i in (201, 202, 203, 204, 205, 206, 208)
    }
    assert by_type[NodeType.MB_DASHBOARD] == {mb_dashboard_node_id(301), mb_dashboard_node_id(302)}


def test_mb_field_node_payload(resolution):
    node = next(n for n in resolution.nodes if n.node_id == mb_field_node_id(102))
    assert node.name == "Order Total"
    assert node.database == "Analytics"
    assert node.schema_ == "marts"
    assert node.table == "fct_orders"
    assert node.column == "order_total"
    assert node.data_type == "type/Float"
    assert node.description == "Order value in USD"
    assert node.properties == {"visibility": "normal"}

    fk = next(n for n in resolution.nodes if n.node_id == mb_field_node_id(101))
    assert fk.properties["semantic_type"] == "type/FK"
    assert fk.properties["fk_target_field_id"] == 104


def test_mb_card_node_payload(resolution):
    node = next(n for n in resolution.nodes if n.node_id == mb_card_node_id(201))
    assert node.name == "Orders overview"
    assert node.properties == {
        "archived": False,
        "collection_id": 7,
        "creator": "Sverrir",
        "display": "line",
        "query_type": "query",
    }


def test_every_clause_type_yields_edges(resolution):
    edges = consumed_by(resolution, 201)
    assert set(edges) == {mb_field_node_id(100), mb_field_node_id(102), mb_field_node_id(103)}
    assert edges[mb_field_node_id(100)]["clauses"] == ["fields"]
    assert edges[mb_field_node_id(102)]["clauses"] == [
        "aggregation",
        "expressions",
        "fields",
        "filter",
    ]
    assert edges[mb_field_node_id(103)]["clauses"] == ["breakout", "order-by"]


def test_edge_direction_and_confidence(resolution):
    for edge in resolution.edges:
        if edge.edge_type == EdgeType.CONSUMED_BY:
            assert edge.from_.startswith("mb_field::")
            assert edge.to.startswith("mb_card::")
            assert edge.confidence == Confidence.EXACT
        elif edge.edge_type == EdgeType.APPEARS_ON:
            assert edge.from_.startswith("mb_card::")
            assert edge.to.startswith("mb_dash::")


def test_joins_and_source_field_implicit_join(resolution):
    edges = consumed_by(resolution, 202)
    assert set(edges) == {mb_field_node_id(i) for i in (101, 104, 105, 106)}
    assert edges[mb_field_node_id(104)]["clauses"] == ["joins.condition"]
    assert edges[mb_field_node_id(105)]["clauses"] == ["joins.fields"]
    assert edges[mb_field_node_id(106)]["clauses"] == ["breakout"]
    assert edges[mb_field_node_id(101)]["implicit_join"] is True
    assert set(edges[mb_field_node_id(101)]["clauses"]) == {"joins.condition", "breakout"}


def test_card_on_card_propagation(resolution):
    edges = consumed_by(resolution, 203)
    assert set(edges) == {mb_field_node_id(i) for i in (101, 104, 105, 106)}
    assert edges[mb_field_node_id(105)]["by_name"] is True
    assert edges[mb_field_node_id(105)]["clauses"] == ["breakout"]
    for field_id in (101, 104, 106):
        assert edges[mb_field_node_id(field_id)] == {"via": "card__202"}


def test_nested_source_query(resolution):
    edges = consumed_by(resolution, 204)
    assert set(edges) == {mb_field_node_id(101), mb_field_node_id(102)}
    assert edges[mb_field_node_id(102)]["clauses"] == ["source-query.aggregation"]
    assert set(edges[mb_field_node_id(101)]["clauses"]) == {"filter", "source-query.breakout"}
    assert edges[mb_field_node_id(101)]["by_name"] is True


def test_native_card_counted_not_resolved(resolution):
    assert mb_card_node_id(205) in {n.node_id for n in resolution.nodes}
    assert consumed_by(resolution, 205) == {}
    assert resolution.native_cards_total == 1
    assert resolution.native_cards_resolved == 0
    assert 205 in resolution.unresolved_cards


def test_unresolvable_name_ref_recorded_not_crashed(resolution):
    edges = consumed_by(resolution, 208)
    assert set(edges) == {mb_field_node_id(100)}
    assert 208 in resolution.unresolved_cards
    ghost = [
        problem
        for problem in resolution.unresolved_field_refs
        if problem["card_id"] == 208 and problem["reason"] == "unresolvable field name"
    ]
    assert len(ghost) == 1
    assert ghost[0]["ref"][1] == "ghost_column"


def test_collection_exclusion(resolution):
    node_ids = {n.node_id for n in resolution.nodes}
    assert mb_card_node_id(207) not in node_ids  # personal collection via personal_owner_id
    assert mb_card_node_id(209) not in node_ids  # nested under Archive 2020, matched by path
    assert 207 not in resolution.unresolved_cards
    assert 209 not in resolution.unresolved_cards


def test_archived_card_kept_with_flag(resolution):
    node = next(n for n in resolution.nodes if n.node_id == mb_card_node_id(206))
    assert node.properties["archived"] is True
    assert set(consumed_by(resolution, 206)) == {mb_field_node_id(100)}


def test_appears_on_edges(resolution):
    appears = {
        (edge.from_, edge.to)
        for edge in resolution.edges
        if edge.edge_type == EdgeType.APPEARS_ON
    }
    assert appears == {
        (mb_card_node_id(201), mb_dashboard_node_id(301)),
        (mb_card_node_id(202), mb_dashboard_node_id(301)),
        (mb_card_node_id(203), mb_dashboard_node_id(302)),
        (mb_card_node_id(206), mb_dashboard_node_id(302)),
    }


def test_dashboard_nodes(resolution):
    node = next(n for n in resolution.nodes if n.node_id == mb_dashboard_node_id(301))
    assert node.name == "Orders Board"
    assert node.properties == {"collection_id": 7, "archived": False}


def test_coverage_numbers(resolution):
    assert resolution.mbql_cards_total == 6
    assert resolution.mbql_cards_resolved == 5
    assert resolution.native_cards_total == 1
    assert resolution.native_cards_resolved == 0
    assert resolution.dashboards == 2
    assert resolution.dashboards_total == 2
    assert sorted(resolution.unresolved_cards) == [205, 208]


def _minimal_card(card_id: int, query: dict) -> dict:
    return {
        "id": card_id,
        "name": f"card {card_id}",
        "collection_id": None,
        "archived": False,
        "dataset_query": {"type": "query", "database": 2, "query": query},
    }


def test_card_on_card_cycle_guard(payload):
    cyclic = MetabasePayload(
        databases=payload.databases,
        database_metadata=payload.database_metadata,
        cards=[
            _minimal_card(
                210, {"source-table": "card__211", "fields": [["field", 100, None]]}
            ),
            _minimal_card(
                211, {"source-table": "card__210", "fields": [["field", 102, None]]}
            ),
        ],
    )
    resolution = resolve_metabase(cyclic, [])
    assert set(consumed_by(resolution, 210)) == {mb_field_node_id(100), mb_field_node_id(102)}
    assert consumed_by(resolution, 211)[mb_field_node_id(100)] == {"via": "card__210"}


def test_card_in_cycle_exposes_full_field_set_to_a_third_card(payload):
    # 260 <-> 261 is a genuine cycle; 262 sources 260 and must still see 261's fields.
    # Card order matters: resolving 260 first used to memoize a truncated set for 260
    # (its walk hit the back-edge to itself), which 262 then inherited.
    cyclic = MetabasePayload(
        databases=payload.databases,
        database_metadata=payload.database_metadata,
        cards=[
            _minimal_card(260, {"source-table": "card__261", "fields": [["field", 100, None]]}),
            _minimal_card(261, {"source-table": "card__260", "fields": [["field", 102, None]]}),
            _minimal_card(262, {"source-table": "card__260", "fields": [["field", 103, None]]}),
        ],
    )
    resolution = resolve_metabase(cyclic, [])
    assert set(consumed_by(resolution, 262)) == {
        mb_field_node_id(100),
        mb_field_node_id(102),
        mb_field_node_id(103),
    }


def test_by_name_falls_back_to_upstream_consumed_fields_as_parsed(payload):
    cards = [
        _minimal_card(220, {"source-table": 11, "fields": [["field", 105, None]]}),
        _minimal_card(
            221,
            {
                "source-table": "card__220",
                "breakout": [["field", "customer_name", {"base-type": "type/Text"}]],
            },
        ),
    ]
    resolution = resolve_metabase(
        MetabasePayload(
            databases=payload.databases,
            database_metadata=payload.database_metadata,
            cards=cards,
        ),
        [],
    )
    edges = consumed_by(resolution, 221)
    assert edges[mb_field_node_id(105)]["by_name"] is True
    # heuristic name match against the upstream card's consumed fields, not a fact
    assert consumed_edge(resolution, 221, 105).confidence == Confidence.PARSED
    assert resolution.mbql_cards_resolved == 2


def test_by_name_via_result_metadata_stays_exact(resolution):
    assert consumed_edge(resolution, 203, 105).confidence == Confidence.EXACT
    assert consumed_by(resolution, 203)[mb_field_node_id(105)]["by_name"] is True


def test_join_alias_maps_string_ref_to_joined_table(payload):
    condition = ["=", ["field", 101, None], ["field", 104, {"join-alias": "Cust"}]]
    card = _minimal_card(
        240,
        {
            "source-table": 10,
            "joins": [{"alias": "Cust", "source-table": 11, "condition": condition}],
            "breakout": [
                ["field", "customer_name", {"base-type": "type/Text", "join-alias": "Cust"}]
            ],
        },
    )
    resolution = resolve_metabase(
        MetabasePayload(
            databases=payload.databases,
            database_metadata=payload.database_metadata,
            cards=[card],
        ),
        [],
    )
    edge = consumed_edge(resolution, 240, 105)
    assert edge.confidence == Confidence.EXACT
    assert edge.evidence["by_name"] is True
    assert resolution.mbql_cards_resolved == 1


def test_join_alias_is_scoped_to_its_query_stage(payload):
    # the same alias names fct_orders in the outer stage and dim_customers in the
    # nested source-query; the nested by-name ref must resolve against the nested join
    inner_condition = ["=", ["field", 101, None], ["field", 104, {"join-alias": "J"}]]
    outer_condition = ["=", ["field", 100, None], ["field", 100, {"join-alias": "J"}]]
    card = _minimal_card(
        270,
        {
            "source-query": {
                "source-table": 10,
                "joins": [{"alias": "J", "source-table": 11, "condition": inner_condition}],
                "breakout": [
                    ["field", "customer_name", {"base-type": "type/Text", "join-alias": "J"}]
                ],
            },
            "joins": [{"alias": "J", "source-table": 10, "condition": outer_condition}],
        },
    )
    resolution = resolve_metabase(
        MetabasePayload(
            databases=payload.databases,
            database_metadata=payload.database_metadata,
            cards=[card],
        ),
        [],
    )
    edge = consumed_edge(resolution, 270, 105)
    assert edge.confidence == Confidence.EXACT
    assert edge.evidence["clauses"] == ["source-query.breakout"]
    assert resolution.mbql_cards_resolved == 1


def test_unmappable_join_alias_downgrades_to_parsed(payload):
    condition = ["=", ["field", 101, None], ["field", 104, {"join-alias": "Up"}]]
    cards = [
        _minimal_card(250, {"source-table": 11, "fields": [["field", 104, None]]}),
        _minimal_card(
            251,
            {
                "source-table": 10,
                "joins": [{"alias": "Up", "source-table": "card__250", "condition": condition}],
                "breakout": [
                    ["field", "order_id", {"base-type": "type/BigInteger", "join-alias": "Up"}]
                ],
            },
        ),
    ]
    resolution = resolve_metabase(
        MetabasePayload(
            databases=payload.databases,
            database_metadata=payload.database_metadata,
            cards=cards,
        ),
        [],
    )
    assert consumed_edge(resolution, 251, 100).confidence == Confidence.PARSED


def test_include_schemas_scopes_fields(payload):
    scoped = resolve_metabase(payload, EXCLUDE, include_schemas=["MARTS"])
    fields = {n.node_id for n in scoped.nodes if n.node_type == NodeType.MB_FIELD}
    assert fields == {mb_field_node_id(i) for i in range(100, 107)}  # case-insensitive match

    none_match = resolve_metabase(payload, EXCLUDE, include_schemas=["elsewhere"])
    assert not any(n.node_type == NodeType.MB_FIELD for n in none_match.nodes)
    assert none_match.mbql_cards_resolved == 0  # every field ref now out of scope


def test_dashboard_with_unknown_card_not_counted_resolved(payload):
    dashboard = {
        "id": 310,
        "name": "Half broken",
        "collection_id": None,
        "archived": False,
        "dashcards": [
            {"id": 1, "card_id": 999},
            {"id": 2, "card_id": None, "visualization_settings": {"text": "virtual"}},
        ],
    }
    resolution = resolve_metabase(
        MetabasePayload(
            databases=payload.databases,
            database_metadata=payload.database_metadata,
            dashboards=[dashboard],
        ),
        [],
    )
    assert resolution.dashboards_total == 1
    assert resolution.dashboards == 0


def test_reference_to_missing_card_is_a_problem_not_a_crash(payload):
    resolution = resolve_metabase(
        MetabasePayload(
            databases=payload.databases,
            database_metadata=payload.database_metadata,
            cards=[_minimal_card(230, {"source-table": "card__999"})],
        ),
        [],
    )
    assert 230 in resolution.unresolved_cards
    assert any(
        problem["ref"] == "card__999" and "missing or excluded" in problem["reason"]
        for problem in resolution.unresolved_field_refs
    )


def test_on_progress_fires_per_card_with_fixed_total(payload):
    # 7 in-scope cards: 201-206 and 208 (207 and 209 sit in excluded collections)
    calls: list[tuple[int, int]] = []

    with_progress = resolve_metabase(
        payload, EXCLUDE, on_progress=lambda done, total: calls.append((done, total))
    )

    assert calls, "on_progress never fired"
    assert {total for _, total in calls} == {7}
    dones = [done for done, _ in calls]
    assert dones == sorted(dones), "done must be monotonically nondecreasing"
    assert dones[-1] == 7, "progress must reach total"
    # the callback is observational only: the resolution is unchanged
    assert with_progress == resolve_metabase(payload, EXCLUDE)
