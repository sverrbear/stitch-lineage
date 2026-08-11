"""A synthetic six-card estate for the `stitch mend` tests (issue #143).

The shape of the real story the feature was built for -- one feature deprecation removes two
columns, and the cards downstream need four different repairs -- shrunk to something a test
can assert on and a PR body can print. Shape only: every name here is invented, and no card
title, column name or dashboard name from a real instance appears in this repo.

    fct_orders.amount      -> renamed to fct_orders.amount_usd (declared)
    fct_orders.promo_code  -> gone

    #401 legacy MBQL, sums the renamed column                  -> repoint
    #402 legacy MBQL, one filter of two names the dead column  -> strip
    #403 legacy MBQL, its ONLY aggregation is the dead column  -> archive
    #404 MBQL 5 stages, one filter of two                      -> strip
    #405 lives in a personal collection                        -> notify
    #406 sources #401 and never names a dead column itself     -> notify
"""

from typing import Any

from stitch_lineage.graph.schema import (
    Confidence,
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
from stitch_lineage.payloads import MetabasePayload

MODEL = "model.demo.fct_orders"
AMOUNT = column_node_id(MODEL, "amount")
AMOUNT_USD = column_node_id(MODEL, "amount_usd")
PROMO = column_node_id(MODEL, "promo_code")
CREATED = column_node_id(MODEL, "created_at")
REGION = column_node_id(MODEL, "region")

F_AMOUNT = 101
F_PROMO = 102
F_CREATED = 103
F_REGION = 104
F_AMOUNT_USD = 201

OPS_COLLECTION = 7
PERSONAL_COLLECTION = 9
DASHBOARD = 10

UPDATED_AT = "2026-08-10T09:00:00Z"


def _model_node() -> Node:
    return Node(
        node_id=MODEL,
        node_type=NodeType.MODEL,
        name="fct_orders",
        database="ANALYTICS",
        schema_="MARTS",
        table="FCT_ORDERS",
    )


def _column(node_id: str, column: str, data_type: str = "NUMBER") -> Node:
    return Node(
        node_id=node_id,
        node_type=NodeType.COLUMN,
        name=column.lower(),
        column=column,
        data_type=data_type,
    )


def _field(field_id: int, column: str) -> Node:
    return Node(
        node_id=mb_field_node_id(field_id),
        node_type=NodeType.MB_FIELD,
        name=column.title(),
        database="ANALYTICS",
        schema_="MARTS",
        table="FCT_ORDERS",
        column=column,
    )


def _card_node(card_id: int, name: str, collection_id: int, creator: str = "dev") -> Node:
    return Node(
        node_id=mb_card_node_id(card_id),
        node_type=NodeType.MB_CARD,
        name=name,
        properties={"collection_id": collection_id, "creator": creator, "archived": False},
    )


def _binds(column_node_id_: str, field_id: int) -> Edge:
    return Edge(
        from_=column_node_id_,
        to=mb_field_node_id(field_id),
        edge_type=EdgeType.BINDS_TO,
        confidence=Confidence.EXACT,
    )


def _consumed(field_id: int, card_id: int, clauses: list[str], via: str | None = None) -> Edge:
    evidence: dict[str, Any] = {"clauses": clauses} if clauses else {}
    if via:
        evidence = {"via": via}
    return Edge(
        from_=mb_field_node_id(field_id),
        to=mb_card_node_id(card_id),
        edge_type=EdgeType.CONSUMED_BY,
        confidence=Confidence.EXACT,
        evidence=evidence,
    )


CARD_NAMES = {
    401: "Revenue by month",
    402: "Orders, promo cohort",
    403: "Promo uptake",
    404: "Regional order counts",
    405: "Scratch revenue check",
    406: "Revenue rollup",
}


def baseline_graph() -> Graph:
    """The graph the change broke: old columns, old field ids, the whole blast radius."""
    nodes = [
        _model_node(),
        _column(AMOUNT, "AMOUNT"),
        _column(PROMO, "PROMO_CODE", "TEXT"),
        _column(CREATED, "CREATED_AT", "TIMESTAMP_NTZ"),
        _column(REGION, "REGION", "TEXT"),
        _field(F_AMOUNT, "AMOUNT"),
        _field(F_PROMO, "PROMO_CODE"),
        _field(F_CREATED, "CREATED_AT"),
        _field(F_REGION, "REGION"),
        _card_node(401, CARD_NAMES[401], OPS_COLLECTION),
        _card_node(402, CARD_NAMES[402], OPS_COLLECTION),
        _card_node(403, CARD_NAMES[403], OPS_COLLECTION, creator="analyst"),
        _card_node(404, CARD_NAMES[404], OPS_COLLECTION),
        _card_node(405, CARD_NAMES[405], PERSONAL_COLLECTION),
        _card_node(406, CARD_NAMES[406], OPS_COLLECTION),
        Node(
            node_id=mb_dashboard_node_id(DASHBOARD),
            node_type=NodeType.MB_DASHBOARD,
            name="Order operations",
            properties={"collection_id": OPS_COLLECTION, "archived": False},
        ),
    ]
    edges = [
        _binds(AMOUNT, F_AMOUNT),
        _binds(PROMO, F_PROMO),
        _binds(CREATED, F_CREATED),
        _binds(REGION, F_REGION),
        _consumed(F_AMOUNT, 401, ["aggregation"]),
        _consumed(F_CREATED, 401, ["breakout"]),
        _consumed(F_PROMO, 402, ["filter"]),
        _consumed(F_CREATED, 402, ["filter"]),
        _consumed(F_PROMO, 403, ["aggregation"]),
        _consumed(F_CREATED, 403, ["breakout"]),
        _consumed(F_PROMO, 404, ["filter"]),
        _consumed(F_CREATED, 404, ["filter"]),
        _consumed(F_REGION, 404, ["breakout"]),
        _consumed(F_AMOUNT, 405, ["aggregation"]),
        # #406 reads #401: the dead reference is in the query it sources, not its own
        _consumed(F_AMOUNT, 406, [], via="card__401"),
        Edge(
            from_=mb_card_node_id(401),
            to=mb_dashboard_node_id(DASHBOARD),
            edge_type=EdgeType.APPEARS_ON,
            confidence=Confidence.EXACT,
            evidence={"dashcard_id": 51},
        ),
        Edge(
            from_=mb_card_node_id(402),
            to=mb_dashboard_node_id(DASHBOARD),
            edge_type=EdgeType.APPEARS_ON,
            confidence=Confidence.EXACT,
            evidence={"dashcard_id": 52},
        ),
    ]
    return Graph(generated_at="2026-08-10T00:00:00+00:00", nodes=nodes, edges=edges)


def candidate_graph(*, bind_new_column: bool = True) -> Graph:
    """The graph after the change: `amount` is now `amount_usd`, `promo_code` is gone.

    `bind_new_column=False` is the Metabase-not-synced-yet case -- the new column exists in
    dbt but no field id backs it, which must NOT be allowed to decay into a strip.
    """
    nodes = [
        _model_node(),
        _column(AMOUNT_USD, "AMOUNT_USD"),
        _column(CREATED, "CREATED_AT", "TIMESTAMP_NTZ"),
        _column(REGION, "REGION", "TEXT"),
        _field(F_CREATED, "CREATED_AT"),
        _field(F_REGION, "REGION"),
    ]
    edges = [_binds(CREATED, F_CREATED), _binds(REGION, F_REGION)]
    if bind_new_column:
        nodes.append(_field(F_AMOUNT_USD, "AMOUNT_USD"))
        edges.append(_binds(AMOUNT_USD, F_AMOUNT_USD))
    return Graph(generated_at="2026-08-11T00:00:00+00:00", nodes=nodes, edges=edges)


# --------------------------------------------------------------------------------------
# the payload: what no graph carries -- live dataset_query, updated_at, dashcards
# --------------------------------------------------------------------------------------


def legacy_repoint_query() -> dict[str, Any]:
    return {
        "type": "query",
        "database": 1,
        "query": {
            "source-table": 5,
            "aggregation": [["sum", ["field", F_AMOUNT, None]]],
            "breakout": [["field", F_CREATED, {"temporal-unit": "month"}]],
        },
    }


def legacy_strip_query() -> dict[str, Any]:
    return {
        "type": "query",
        "database": 1,
        "query": {
            "source-table": 5,
            "aggregation": [["count"]],
            "filter": [
                "and",
                ["=", ["field", F_PROMO, None], "SUMMER"],
                [">", ["field", F_CREATED, None], "2026-01-01"],
            ],
        },
    }


def legacy_archive_query() -> dict[str, Any]:
    return {
        "type": "query",
        "database": 1,
        "query": {
            "source-table": 5,
            "aggregation": [["distinct", ["field", F_PROMO, None]]],
            "breakout": [["field", F_CREATED, {"temporal-unit": "week"}]],
        },
    }


def stages_strip_query() -> dict[str, Any]:
    return {
        "lib/type": "mbql/query",
        "database": 1,
        "stages": [
            {
                "lib/type": "mbql.stage/mbql",
                "source-table": 5,
                "aggregation": [["count", {"lib/uuid": "agg-1"}]],
                "breakout": [["field", {"base-type": "type/Text"}, F_REGION]],
                "filters": [
                    ["=", {"lib/uuid": "flt-1"}, ["field", {}, F_PROMO], "SUMMER"],
                    [">", {"lib/uuid": "flt-2"}, ["field", {}, F_CREATED], "2026-01-01"],
                ],
            }
        ],
    }


def _card(
    card_id: int,
    query: dict[str, Any],
    collection_id: int = OPS_COLLECTION,
    creator: str = "dev",
    archived: bool = False,
    updated_at: str = UPDATED_AT,
) -> dict[str, Any]:
    return {
        "id": card_id,
        "name": CARD_NAMES[card_id],
        "dataset_query": query,
        "collection_id": collection_id,
        "archived": archived,
        "updated_at": updated_at,
        "creator": {"common_name": creator},
        "display": "bar",
    }


def payload(*, extra_cards: list[dict[str, Any]] | None = None) -> MetabasePayload:
    """The raw Metabase side of the scenario, as /api/card and /api/dashboard return it."""
    cards = [
        _card(401, legacy_repoint_query()),
        _card(402, legacy_strip_query()),
        _card(403, legacy_archive_query(), creator="analyst"),
        _card(404, stages_strip_query()),
        _card(405, legacy_repoint_query(), collection_id=PERSONAL_COLLECTION),
        _card(
            406,
            {
                "type": "query",
                "database": 1,
                "query": {"source-table": "card__401", "aggregation": [["count"]]},
            },
        ),
        *(extra_cards or []),
    ]
    dashboards = [
        {
            "id": DASHBOARD,
            "name": "Order operations",
            "collection_id": OPS_COLLECTION,
            "dashcards": [
                {
                    "id": 51,
                    "card_id": 401,
                    "parameter_mappings": [
                        {
                            "parameter_id": "p-amount",
                            "card_id": 401,
                            "target": ["dimension", ["field", F_AMOUNT, None]],
                        }
                    ],
                },
                {
                    "id": 52,
                    "card_id": 402,
                    "parameter_mappings": [
                        {
                            "parameter_id": "p-promo",
                            "card_id": 402,
                            "target": ["dimension", ["field", F_PROMO, None]],
                        },
                        {
                            "parameter_id": "p-created",
                            "card_id": 402,
                            "target": ["dimension", ["field", F_CREATED, None]],
                        },
                    ],
                },
            ],
        }
    ]
    collections = [
        {"id": OPS_COLLECTION, "name": "Order ops", "location": "/"},
        {
            "id": PERSONAL_COLLECTION,
            "name": "Dev's Personal Collection",
            "location": "/",
            "personal_owner_id": 3,
        },
    ]
    return MetabasePayload(
        metabase_version="v0.53.2",
        databases=[{"id": 1, "name": "Analytics"}],
        database_metadata={},
        cards=cards,
        dashboards=dashboards,
        collections=collections,
        snippets=[],
    )


DECLARED_RENAMES = {"fct_orders.amount": "fct_orders.amount_usd"}
