"""Native SQL card resolution (SPEC.md section 7.4): template tags, then sqlglot.

The fixture instance is the same one test_resolve_metabase.py uses -- database 2
"Analytics", marts.fct_orders (fields 100 order_id, 101 customer_id, 102 order_total,
103 created_at) and marts.dim_customers (104 customer_id, 105 customer_name, 106
country_code) -- so a native card and an MBQL card resolve against identical metadata.
cards_native.json is the mixed instance: every tag type, both dataset_query shapes.
"""

import json
from pathlib import Path

import pytest

from stitch_lineage.graph.schema import (
    Confidence,
    EdgeType,
    mb_card_node_id,
    mb_field_node_id,
)
from stitch_lineage.payloads import MetabasePayload
from stitch_lineage.resolve.metabase import resolve_metabase

FIXTURES = Path(__file__).parent / "fixtures" / "metabase"


def fixture(name: str) -> dict | list:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def instance() -> MetabasePayload:
    databases = fixture("databases")["data"]
    return MetabasePayload(
        databases=[db for db in databases if db["name"] == "Analytics"],
        database_metadata={2: fixture("database_metadata_2")},
        cards=fixture("cards_native"),
        snippets=fixture("snippets"),
    )


@pytest.fixture(scope="module")
def native(instance) -> object:
    return resolve_metabase(instance, [])


def _resolve(instance: MetabasePayload, cards: list[dict], **overrides):
    return resolve_metabase(
        instance.model_copy(update={"cards": cards, **overrides}),
        [],
    )


def _native_card(card_id: int, query: str, template_tags: dict | None = None) -> dict:
    """A legacy native card: dataset_query.type "native", SQL under native.query."""
    return {
        "id": card_id,
        "name": f"card {card_id}",
        "collection_id": None,
        "archived": False,
        "dataset_query": {
            "type": "native",
            "database": 2,
            "native": {"query": query, "template-tags": template_tags or {}},
        },
    }


def _native_stage_card(card_id: int, query: str, template_tags: dict | None = None) -> dict:
    """An MBQL 5 native card: one mbql.stage/native stage whose `native` is the SQL."""
    stage: dict = {"lib/type": "mbql.stage/native", "native": query}
    if template_tags:
        stage["template-tags"] = template_tags
    return {
        "id": card_id,
        "name": f"card {card_id}",
        "collection_id": None,
        "archived": False,
        "dataset_query": {"database": 2, "lib/type": "mbql/query", "stages": [stage]},
    }


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


def problems(resolution, card_id: int) -> list[dict]:
    return [p for p in resolution.unresolved_field_refs if p["card_id"] == card_id]


def card_node(resolution, card_id: int):
    return next(n for n in resolution.nodes if n.node_id == mb_card_node_id(card_id))


# --- the mixed instance: every tag type, both shapes -------------------------


def test_value_tags_and_bare_table_name(native):
    """Number/text/date tags substitute to literals, and an unqualified table resolves."""
    edges = consumed_by(native, 601)
    assert set(edges) == {mb_field_node_id(i) for i in (100, 101, 102, 103)}
    assert edges[mb_field_node_id(102)] == {"source": "native_sql", "table": "marts.fct_orders"}
    assert consumed_edge(native, 601, 102).confidence == Confidence.PARSED
    assert 601 not in native.unresolved_cards


def test_optional_clause_contents_are_kept(native):
    """created_at is only referenced inside [[...]]. Dropping the clause would drop the
    column from the blast radius of a rename that really does break this card."""
    assert mb_field_node_id(103) in consumed_by(native, 601)


def test_field_filter_tag_is_exact_not_parsed(native):
    """A dimension tag names a field id outright: no SQL was parsed to find it."""
    edges = consumed_by(native, 602)
    assert set(edges) == {mb_field_node_id(103)}
    assert edges[mb_field_node_id(103)] == {
        "source": "template_tag",
        "clauses": ["template-tag.dimension"],
    }
    assert consumed_edge(native, 602, 103).confidence == Confidence.EXACT


def test_nested_snippets_are_inlined(native):
    """`recent orders` filters on created_at and itself uses the `paid only` snippet."""
    edges = consumed_by(native, 603)
    assert set(edges) == {mb_field_node_id(i) for i in (100, 102, 103)}


def test_star_join_and_cte_through_a_native_stage(native):
    """MBQL 5 native stage: `select *` expands, a join resolves, and the warehouse
    database in a three-part name is ignored (Metabase's display name is not it)."""
    edges = consumed_by(native, 604)
    assert set(edges) == {mb_field_node_id(i) for i in (100, 101, 102, 103, 104, 105)}
    assert edges[mb_field_node_id(105)]["table"] == "marts.dim_customers"


def test_card_reference_routes_through_the_transitive_machinery(native):
    """`{{#601-card}}` is a card-on-card source, resolved exactly as MBQL's is -- and the
    `via` edge is parsed, because the fields it inherits were parsed out of SQL."""
    edges = consumed_by(native, 605)
    assert set(edges) == set(consumed_by(native, 601))
    assert all(evidence == {"via": "card__601"} for evidence in edges.values())
    assert consumed_edge(native, 605, 100).confidence == Confidence.PARSED


def test_unavailable_snippet_degrades_to_table_level(native):
    """The snippet was a whole WHERE clause, so the SQL no longer parses. The card keeps
    its node, names the table it reads, and says why -- it is never dropped."""
    assert consumed_by(native, 606) == {}
    assert 606 in native.unresolved_cards
    assert card_node(native, 606).properties["native_tables"] == ["marts.fct_orders"]
    reasons = [p["reason"] for p in problems(native, 606)]
    assert "snippet not available" in reasons
    assert any(reason.startswith("native SQL parse failure") for reason in reasons)


def test_coverage_counts_native_cards(native):
    assert (native.native_cards_resolved, native.native_cards_total) == (5, 6)
    assert (native.mbql_cards_resolved, native.mbql_cards_total) == (0, 0)
    assert native.unresolved_cards == [606]


def test_resolved_native_card_carries_no_table_property(native):
    """native_tables only exists where the column edges do not cover the story."""
    assert "native_tables" not in card_node(native, 601).properties


# --- degradation paths -------------------------------------------------------


def test_unparseable_sql_keeps_the_card_and_names_its_tables(instance):
    resolution = _resolve(
        instance, [_native_card(701, "select order_id from marts.fct_orders where )(")]
    )
    assert consumed_by(resolution, 701) == {}
    assert 701 in resolution.unresolved_cards
    assert card_node(resolution, 701).properties["native_tables"] == ["marts.fct_orders"]
    assert problems(resolution, 701)[0]["reason"].startswith("native SQL parse failure")
    # the reason is stored and printed: no ANSI escapes, no echoed SQL fragment
    assert "\x1b" not in problems(resolution, 701)[0]["reason"]


def test_unknown_table_is_reported_and_the_rest_still_resolves(instance):
    resolution = _resolve(
        instance,
        [
            _native_card(
                702,
                "select o.order_id, x.note from marts.fct_orders o "
                "join marts.legacy_export x on x.order_id = o.order_id",
            )
        ],
    )
    assert set(consumed_by(resolution, 702)) == {mb_field_node_id(100)}
    assert 702 in resolution.unresolved_cards
    assert problems(resolution, 702) == [
        {
            "card_id": 702,
            "ref": "marts.legacy_export",
            "reason": "table not found in Metabase metadata",
        }
    ]


def test_column_absent_from_metabase_metadata_is_reported(instance):
    """Metadata syncs lag hand-written SQL. The card resolves what it can and says which
    column it could not place, instead of failing the whole parse."""
    resolution = _resolve(
        instance, [_native_card(703, "select order_id, discount_code from marts.fct_orders")]
    )
    assert set(consumed_by(resolution, 703)) == {mb_field_node_id(100)}
    # sqlglot normalizes identifiers for the dialect, so the diagnostic carries
    # Snowflake's casing of the column it could not place
    assert problems(resolution, 703) == [
        {
            "card_id": 703,
            "ref": "marts.fct_orders.DISCOUNT_CODE",
            "reason": "column not listed in Metabase metadata",
        }
    ]


def test_ambiguous_bare_table_name_is_not_guessed(instance):
    """Two schemas, one table name, no schema context in the SQL: refuse to pick."""
    metadata = json.loads(json.dumps(fixture("database_metadata_2")))
    twin = json.loads(json.dumps(metadata["tables"][0]))
    twin["id"] = 12
    twin["schema"] = "staging"
    twin["fields"] = [{**field, "id": field["id"] + 100} for field in twin["fields"]]
    metadata["tables"].append(twin)
    resolution = _resolve(
        instance,
        [_native_card(704, "select order_id from fct_orders")],
        database_metadata={2: metadata},
    )
    assert consumed_by(resolution, 704) == {}
    assert problems(resolution, 704)[0]["ref"] == "fct_orders"


def test_card_on_a_database_outside_scope_degrades(instance):
    card = _native_card(705, "select order_id from marts.fct_orders")
    card["dataset_query"]["database"] = 99
    resolution = _resolve(instance, [card])
    assert consumed_by(resolution, 705) == {}
    assert problems(resolution, 705) == [
        {"card_id": 705, "ref": None, "reason": "native card's database is not in scope"}
    ]


def test_empty_native_sql_is_recorded_not_crashed(instance):
    resolution = _resolve(instance, [_native_stage_card(706, "   ")])
    assert problems(resolution, 706) == [
        {"card_id": 706, "ref": None, "reason": "native SQL is empty"}
    ]
    assert (resolution.native_cards_resolved, resolution.native_cards_total) == (0, 1)


def test_undeclared_tag_still_parses(instance):
    """A tag with no entry in template-tags substitutes a string literal: the card's
    columns resolve even though nothing describes the parameter."""
    resolution = _resolve(
        instance,
        [_native_card(707, "select order_id from marts.fct_orders where customer_id = {{who}}")],
    )
    assert set(consumed_by(resolution, 707)) == {mb_field_node_id(100), mb_field_node_id(101)}
    assert 707 not in resolution.unresolved_cards


def test_field_filter_tag_naming_an_unknown_field_is_reported(instance):
    resolution = _resolve(
        instance,
        [
            _native_card(
                708,
                "select order_id from marts.fct_orders where {{ghost}}",
                {
                    "ghost": {
                        "name": "ghost",
                        "type": "dimension",
                        "dimension": ["field", 999, None],
                    }
                },
            )
        ],
    )
    assert set(consumed_by(resolution, 708)) == {mb_field_node_id(100)}
    assert problems(resolution, 708) == [
        {"card_id": 708, "ref": "ghost", "reason": "field filter tag names an unknown field"}
    ]


def test_snippet_recursion_is_capped(instance):
    """A snippet that includes itself must not recurse forever."""
    resolution = _resolve(
        instance,
        [
            _native_card(
                709,
                "select order_id from marts.fct_orders where {{snippet: loop}}",
                {
                    "snippet: loop": {
                        "name": "snippet: loop",
                        "type": "snippet",
                        "snippet-name": "loop",
                        "snippet-id": 7,
                    }
                },
            )
        ],
        snippets=[{"id": 7, "name": "loop", "content": "1 = 1 and {{snippet: loop}}"}],
    )
    reasons = [p["reason"] for p in problems(resolution, 709)]
    assert any("too deep" in reason for reason in reasons)
    # the cap leaves a dangling WHERE, so this one lands on the table-level degrade
    assert card_node(resolution, 709).properties["native_tables"] == ["marts.fct_orders"]


def test_order_by_on_a_select_alias_is_not_a_problem(instance):
    """Qualification leaves an alias reference unqualified; it is not a table column."""
    resolution = _resolve(
        instance,
        [
            _native_card(
                710,
                "select count(*) as order_count, created_at as day from marts.fct_orders "
                "group by day order by order_count desc",
            )
        ],
    )
    assert set(consumed_by(resolution, 710)) == {mb_field_node_id(103)}
    assert 710 not in resolution.unresolved_cards


def test_dimension_tag_and_sql_reference_to_one_field_collapse_to_the_exact_edge(instance):
    resolution = _resolve(
        instance,
        [
            _native_card(
                711,
                "select created_at from marts.fct_orders where {{created}}",
                {"created": {"name": "created", "type": "dimension", "dimension": ["field", 103]}},
            )
        ],
    )
    assert consumed_edge(resolution, 711, 103).confidence == Confidence.EXACT
    assert len(consumed_by(resolution, 711)) == 1


def test_mbql_card_sourcing_a_native_card_inherits_parsed_confidence(instance):
    """A "model" built on a native card is only as certain as the parse underneath it."""
    consumer = {
        "id": 712,
        "name": "card 712",
        "collection_id": None,
        "archived": False,
        "dataset_query": {
            "type": "query",
            "database": 2,
            "query": {"source-table": "card__601", "fields": [["field", 100, None]]},
        },
    }
    resolution = _resolve(instance, [*fixture("cards_native"), consumer])
    inherited = consumed_edge(resolution, 712, 102)
    assert inherited.evidence == {"via": "card__601"}
    assert inherited.confidence == Confidence.PARSED
