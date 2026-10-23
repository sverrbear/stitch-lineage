"""Readable review must preserve the exact repair and never relabel numeric literals."""

from copy import deepcopy

import mend_scenario as s
import pytest

from stitch_lineage.mend.explain import explain_plan, named_query
from stitch_lineage.mend.models import CardPlan, DeadRef, MendAction, MendPlan


def explain(before, after):
    card = CardPlan(
        card_id=1,
        name="Revenue",
        action=MendAction.REPOINT,
        reason="Declared rename",
        before=before,
        after=after,
        dead_refs=[
            DeadRef(
                column="fct_orders.amount",
                node_id=s.AMOUNT,
                field_id=101,
                rename_to="fct_orders.amount_usd",
                new_field_id=201,
            )
        ],
    )
    plan = MendPlan(cards=[card])
    original = plan.model_dump()
    review = explain_plan(plan, s.baseline_graph(), s.candidate_graph())["1"]
    assert plan.model_dump() == original
    return review


def legacy(**clauses):
    return {"type": "query", "database": 1, "query": {"source-table": 5, **clauses}}


def test_explains_sum_and_labels_diff_without_changing_literals_or_payload():
    before = legacy(aggregation=[["sum", ["field", 101, None]]], limit=101)
    after = legacy(aggregation=[["sum", ["field", 201, None]]], limit=101)
    review = explain(before, after)
    assert review["changes"] == [
        {
            "label": "Calculation",
            "before": "Sum of fct_orders.amount",
            "after": "Sum of fct_orders.amount_usd",
            "plain": True,
        }
    ]
    assert "fct_orders.amount [field 101]" in review["query_diff"]
    assert "fct_orders.amount_usd [field 201]" in review["query_diff"]
    assert named_query(before, {101: "amount"})["query"]["limit"] == 101


def test_staged_calculation_and_filter_explain_the_correct_query_step():
    before = {
        "lib/type": "mbql/query",
        "stages": [
            {"source-table": 5},
            {
                "aggregation": [["avg", {"lib/uuid": "agg"}, ["field", {}, 101]]],
                "filters": [[">", {"lib/uuid": "filter"}, ["field", {}, 101], 101]],
            },
        ],
    }
    after = deepcopy(before)
    after["stages"][1]["aggregation"][0][2][2] = 201
    after["stages"][1]["filters"][0][2][2] = 201
    changes = explain(before, after)["changes"]
    assert changes[0]["label"] == "Step 2 · Calculation"
    assert changes[0]["after"] == "Average of fct_orders.amount_usd"
    assert changes[1]["before"] == "(fct_orders.amount > 101)"
    assert changes[1]["after"] == "(fct_orders.amount_usd > 101)"


def test_removed_filter_is_explicit_and_other_conditions_are_retained():
    before = legacy(
        filter=["and", ["=", ["field", 102, None], "PROMO"], [">", ["field", 101, None], 0]]
    )
    after = legacy(filter=[">", ["field", 101, None], 0])
    change = explain(before, after)["changes"][0]
    assert "promo_code" in change["before"]
    assert change["after"] == "(fct_orders.amount > 0)"
    removed = explain(before, legacy())["changes"][0]
    assert removed["after"] == "None"


@pytest.mark.parametrize(
    "expression",
    [
        ["custom-operation", ["field", 101, None]],
        ["sum", ["field", 101, {"temporal-unit": "month"}]],
        ["sum", {"unrecognized-option": True}, ["field", {}, 101]],
        ["=", ["field", 101, None], "A", "B"],
    ],
)
def test_unknown_operators_and_semantic_options_are_not_silently_simplified(expression):
    before = legacy(aggregation=[expression])
    after = legacy(aggregation=[["sum", ["field", 201, None]]])
    change = explain(before, after)["changes"][0]
    assert not change["plain"]
    assert "fct_orders.amount [field 101]" in change["before"]


def test_nested_query_and_repeated_join_conditions_are_not_mispaired():
    before = legacy(
        **{
            "source-query": {"aggregation": [["sum", ["field", 101, None]]]},
            "joins": [
                {"condition": ["=", ["field", 101, None], 1]},
                {"condition": ["=", ["field", 104, None], "West"]},
            ],
        }
    )
    after = deepcopy(before)
    del after["query"]["joins"][0]["condition"]
    after["query"]["source-query"]["aggregation"][0][1][1] = 201
    changes = explain(before, after)["changes"]
    assert changes[0]["label"] == "Join condition"
    assert changes[0]["after"] == '(fct_orders.region = "West")'
    assert changes[1]["label"] == "Source query · Calculation"


def test_implicit_join_field_is_named_but_other_numbers_remain_verbatim():
    query = ["=", ["field", 999, {"source-field": 101}], 101]
    annotated = named_query(query, {101: "fct_orders.amount"})
    assert annotated[1][1] == "Unknown field #999 [field 999]"
    assert annotated[1][2]["source-field"] == "fct_orders.amount [field 101]"
    assert annotated[2] == 101
    assert query[1][2]["source-field"] == 101
