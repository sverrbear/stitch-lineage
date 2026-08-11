"""MBQL rewriting and the essentialness rule, on both dataset_query shapes (issue #143)."""

from stitch_lineage.mend.rewrite import (
    ClauseUse,
    DeadSet,
    clause_base,
    essential_targets,
    rewrite_parameter_mappings,
    rewrite_query,
    scan_query,
)

DEAD = 102
LIVE = 101
NEW = 201


def dead(**kwargs) -> DeadSet:
    kwargs.setdefault("field_ids", frozenset({DEAD}))
    return DeadSet(**kwargs)


def legacy(**clauses) -> dict:
    return {"type": "query", "database": 1, "query": {"source-table": 5, **clauses}}


def stages(*stage_clauses) -> dict:
    return {
        "lib/type": "mbql/query",
        "database": 1,
        "stages": [
            {"lib/type": "mbql.stage/mbql", "source-table": 5, **clauses}
            for clauses in stage_clauses
        ],
    }


# --------------------------------------------------------------------------------------
# clause labels
# --------------------------------------------------------------------------------------


def test_clause_base_strips_stage_and_nesting_prefixes():
    assert clause_base("breakout") == "breakout"
    assert clause_base("stage1.breakout") == "breakout"
    assert clause_base("source-query.filter") == "filter"
    assert clause_base("joins.condition") == "joins.condition"
    assert clause_base("stage2.joins.condition") == "joins.condition"
    assert clause_base("joins.source-query.stage1.joins.fields") == "joins.fields"


# --------------------------------------------------------------------------------------
# essentialness
# --------------------------------------------------------------------------------------


def test_one_filter_of_two_is_not_essential():
    query = legacy(
        aggregation=[["count"]],
        filter=["and", ["=", ["field", DEAD, None], "x"], [">", ["field", LIVE, None], 0]],
    )
    uses = scan_query(query, dead())
    assert uses[DEAD] == [ClauseUse(label="filter", essential=False)]
    assert essential_targets(uses) == []


def test_sole_aggregation_is_essential():
    uses = scan_query(legacy(aggregation=[["sum", ["field", DEAD, None]]]), dead())
    assert essential_targets(uses) == [DEAD]


def test_one_aggregation_of_two_is_not_essential():
    query = legacy(aggregation=[["count"], ["sum", ["field", DEAD, None]]])
    assert essential_targets(scan_query(query, dead())) == []


def test_sole_breakout_is_essential_and_one_of_several_is_not():
    sole = legacy(aggregation=[["count"]], breakout=[["field", DEAD, None]])
    several = legacy(
        aggregation=[["count"]], breakout=[["field", LIVE, None], ["field", DEAD, None]]
    )
    assert essential_targets(scan_query(sole, dead())) == [DEAD]
    assert essential_targets(scan_query(several, dead())) == []


def test_order_by_is_never_essential():
    query = legacy(aggregation=[["count"]], **{"order-by": [["desc", ["field", DEAD, None]]]})
    assert essential_targets(scan_query(query, dead())) == []


def test_expression_and_join_condition_are_always_essential():
    expression = legacy(expressions={"net": ["-", ["field", DEAD, None], 1]})
    join = legacy(
        joins=[
            {
                "alias": "o",
                "source-table": 6,
                "condition": ["=", ["field", DEAD, None], ["field", 900, {"join-alias": "o"}]],
            }
        ]
    )
    assert essential_targets(scan_query(expression, dead())) == [DEAD]
    assert essential_targets(scan_query(join, dead())) == [DEAD]


def test_sole_field_of_a_table_card_is_essential():
    # dropping `fields` entirely means "every column", which is a different card
    assert essential_targets(scan_query(legacy(fields=[["field", DEAD, None]]), dead())) == [DEAD]


def test_implicit_join_through_a_dead_fk_counts_as_a_reference():
    query = legacy(breakout=[["field", 900, {"source-field": DEAD}]], aggregation=[["count"]])
    uses = scan_query(query, dead())
    assert DEAD in uses


def test_a_card_that_never_names_a_dead_column_scans_empty():
    assert scan_query(legacy(aggregation=[["sum", ["field", LIVE, None]]]), dead()) == {}


# --------------------------------------------------------------------------------------
# stripping: legacy
# --------------------------------------------------------------------------------------


def test_strip_removes_one_and_argument_and_collapses_the_and():
    query = legacy(
        aggregation=[["count"]],
        filter=["and", ["=", ["field", DEAD, None], "x"], [">", ["field", LIVE, None], 0]],
    )
    result = rewrite_query(query, dead())
    assert result.query["query"]["filter"] == [">", ["field", LIVE, None], 0]
    assert result.removed == ["filter -> field 102"]


def test_strip_drops_the_whole_filter_when_it_is_the_only_condition():
    query = legacy(aggregation=[["count"]], filter=["=", ["field", DEAD, None], "x"])
    result = rewrite_query(query, dead())
    assert "filter" not in result.query["query"]


def test_strip_removes_one_breakout_of_several():
    query = legacy(aggregation=[["count"]], breakout=[["field", LIVE, None], ["field", DEAD, None]])
    result = rewrite_query(query, dead())
    assert result.query["query"]["breakout"] == [["field", LIVE, None]]


def test_strip_renumbers_aggregation_references_left_behind():
    # dropping aggregation 0 of 2 makes the surviving one index 0; an order-by still
    # pointing at 1 would silently sort by a measure that no longer exists there
    query = legacy(
        aggregation=[["sum", ["field", DEAD, None]], ["count"]],
        **{"order-by": [["desc", ["aggregation", 1]]]},
    )
    result = rewrite_query(query, dead())
    assert result.query["query"]["aggregation"] == [["count"]]
    assert result.query["query"]["order-by"] == [["desc", ["aggregation", 0]]]


def test_strip_drops_a_clause_that_referenced_the_removed_aggregation():
    query = legacy(
        aggregation=[["sum", ["field", DEAD, None]], ["count"]],
        **{"order-by": [["desc", ["aggregation", 0]]]},
    )
    result = rewrite_query(query, dead())
    assert "order-by" not in result.query["query"]
    assert any("removed aggregation" in note for note in result.removed)


def test_strip_reaches_into_a_nested_source_query():
    query = legacy(
        aggregation=[["count"]],
        **{
            "source-query": {
                "source-table": 5,
                "filter": [
                    "and",
                    ["=", ["field", DEAD, None], "x"],
                    [">", ["field", LIVE, None], 0],
                ],
            }
        },
    )
    result = rewrite_query(query, dead())
    assert result.query["query"]["source-query"]["filter"] == [">", ["field", LIVE, None], 0]
    assert result.removed == ["source-query.filter -> field 102"]


def test_strip_leaves_the_input_untouched():
    query = legacy(aggregation=[["count"]], filter=["=", ["field", DEAD, None], "x"])
    before = str(query)
    rewrite_query(query, dead())
    assert str(query) == before


# --------------------------------------------------------------------------------------
# stripping: MBQL 5
# --------------------------------------------------------------------------------------


def test_mbql5_strips_one_filter_of_two():
    query = stages(
        {
            "aggregation": [["count", {"lib/uuid": "a"}]],
            "filters": [
                ["=", {"lib/uuid": "f1"}, ["field", {}, DEAD], "x"],
                [">", {"lib/uuid": "f2"}, ["field", {}, LIVE], 0],
            ],
        }
    )
    result = rewrite_query(query, dead())
    assert result.query["stages"][0]["filters"] == [
        [">", {"lib/uuid": "f2"}, ["field", {}, LIVE], 0]
    ]


def test_mbql5_sole_aggregation_is_essential():
    query = stages({"aggregation": [["sum", {"lib/uuid": "a"}, ["field", {}, DEAD]]]})
    assert essential_targets(scan_query(query, dead())) == [DEAD]


def test_mbql5_later_stages_are_labelled_by_stage():
    query = stages(
        {"aggregation": [["count", {"lib/uuid": "a"}]]},
        {"filters": [["=", {"lib/uuid": "f"}, ["field", {}, DEAD], "x"]]},
    )
    uses = scan_query(query, dead())
    assert [use.label for use in uses[DEAD]] == ["stage1.filter"]


def test_mbql5_join_conditions_are_found_under_the_singular_label():
    query = stages(
        {
            "aggregation": [["count", {"lib/uuid": "a"}]],
            "joins": [
                {
                    "alias": "o",
                    "stages": [{"source-table": 6}],
                    "conditions": [["=", {}, ["field", {}, DEAD], ["field", {}, 900]]],
                }
            ],
        }
    )
    uses = scan_query(query, dead())
    assert [use.label for use in uses[DEAD]] == ["joins.condition"]
    assert essential_targets(uses) == [DEAD]


# --------------------------------------------------------------------------------------
# repointing
# --------------------------------------------------------------------------------------


def test_repoint_rewrites_field_ids_in_every_clause_of_both_shapes():
    spec = DeadSet(field_map={LIVE: NEW})
    legacy_result = rewrite_query(
        legacy(
            aggregation=[["sum", ["field", LIVE, None]]],
            filter=[">", ["field", LIVE, None], 0],
        ),
        spec,
    )
    assert legacy_result.query["query"]["aggregation"] == [["sum", ["field", NEW, None]]]
    assert legacy_result.query["query"]["filter"] == [">", ["field", NEW, None], 0]

    stage_result = rewrite_query(
        stages({"aggregation": [["sum", {"lib/uuid": "a"}, ["field", {"base-type": "x"}, LIVE]]]}),
        spec,
    )
    assert stage_result.query["stages"][0]["aggregation"] == [
        ["sum", {"lib/uuid": "a"}, ["field", {"base-type": "x"}, NEW]]
    ]


def test_repoint_rewrites_by_name_refs_and_source_field_options():
    spec = DeadSet(name_map={"amount": "AMOUNT_USD"}, field_map={LIVE: NEW})
    result = rewrite_query(
        legacy(
            fields=[["field", "AMOUNT", None]],
            breakout=[["field", 900, {"source-field": LIVE}]],
            aggregation=[["count"]],
        ),
        spec,
    )
    assert result.query["query"]["fields"] == [["field", "AMOUNT_USD", None]]
    assert result.query["query"]["breakout"] == [["field", 900, {"source-field": NEW}]]


def test_repoint_uses_the_supplied_labels_in_its_account_of_what_changed():
    spec = DeadSet(
        field_map={LIVE: NEW},
        labels={LIVE: "fct_orders.amount"},
        rename_labels={LIVE: "fct_orders.amount_usd"},
    )
    result = rewrite_query(legacy(aggregation=[["sum", ["field", LIVE, None]]]), spec)
    assert result.repointed == ["aggregation: fct_orders.amount -> fct_orders.amount_usd"]


def test_repoint_and_strip_compose_on_one_card():
    spec = DeadSet(field_ids=frozenset({DEAD}), field_map={LIVE: NEW})
    result = rewrite_query(
        legacy(
            aggregation=[["sum", ["field", LIVE, None]]],
            filter=["=", ["field", DEAD, None], "x"],
        ),
        spec,
    )
    assert result.query["query"]["aggregation"] == [["sum", ["field", NEW, None]]]
    assert "filter" not in result.query["query"]
    assert result.repointed and result.removed


# --------------------------------------------------------------------------------------
# refusals
# --------------------------------------------------------------------------------------


def test_native_cards_are_refused_not_rewritten():
    native = {"type": "native", "native": {"query": "select promo_code from orders"}}
    result = rewrite_query(native, dead())
    assert result.query is None
    assert "native SQL" in result.unsupported


def test_mbql5_native_first_stage_is_refused():
    query = {
        "lib/type": "mbql/query",
        "stages": [{"lib/type": "mbql.stage/native", "native": "select 1"}],
    }
    assert rewrite_query(query, dead()).unsupported is not None


def test_a_dead_reference_in_an_expression_refuses_the_whole_card():
    # deleting a custom column is deleting a definition other clauses may build on, so the
    # rewrite refuses rather than shipping a query that runs and means something else
    query = legacy(expressions={"net": ["-", ["field", DEAD, None], 1]}, aggregation=[["count"]])
    result = rewrite_query(query, dead())
    assert result.query is None
    assert "expressions" in result.unsupported


def test_unrecognised_shapes_are_refused():
    assert rewrite_query({"type": "query"}, dead()).unsupported is not None
    assert rewrite_query(None, dead()).unsupported is not None


def test_an_empty_dead_set_returns_the_query_unchanged():
    query = legacy(aggregation=[["count"]])
    result = rewrite_query(query, DeadSet())
    assert result.query == query
    assert not result.changed


# --------------------------------------------------------------------------------------
# dashcard parameter mappings
# --------------------------------------------------------------------------------------


def _mapping(field_id, parameter_id="p1"):
    return {
        "parameter_id": parameter_id,
        "card_id": 1,
        "target": ["dimension", ["field", field_id, None]],
    }


def test_parameter_mappings_repoint_a_renamed_field():
    after, notes = rewrite_parameter_mappings([_mapping(LIVE)], DeadSet(field_map={LIVE: NEW}))
    assert after == [_mapping(NEW)]
    assert notes


def test_parameter_mappings_drop_a_widget_wired_to_a_dead_column():
    mappings = [_mapping(DEAD, "p-dead"), _mapping(LIVE, "p-live")]
    after, notes = rewrite_parameter_mappings(mappings, dead())
    assert after == [_mapping(LIVE, "p-live")]
    assert any("dropped" in note for note in notes)


def test_parameter_mappings_leave_the_input_untouched():
    mappings = [_mapping(LIVE)]
    rewrite_parameter_mappings(mappings, DeadSet(field_map={LIVE: NEW}))
    assert mappings == [_mapping(LIVE)]


def test_parameter_mappings_tolerate_junk():
    assert rewrite_parameter_mappings(None, dead()) == ([], [])
    after, _ = rewrite_parameter_mappings(["nonsense"], dead())
    assert after == ["nonsense"]
