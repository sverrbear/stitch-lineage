"""The mend plan: taxonomy, autonomy, determinism and ordering (issue #143)."""

import json

import mend_scenario as scenario
import pytest

from stitch_lineage.graph.impact import diff_columns
from stitch_lineage.mend.models import MendAction
from stitch_lineage.mend.plan import affected_card_ids, build_plan, card_dependencies

ALL_AUTO = [MendAction.REPOINT, MendAction.STRIP, MendAction.ARCHIVE]
NOTIFY_ONLY = ["*Personal*"]


def make_plan(
    *,
    auto=ALL_AUTO,
    notify_only=NOTIFY_ONLY,
    renames=None,
    bind_new_column=True,
    payload=None,
    revisions=None,
):
    baseline = scenario.baseline_graph()
    candidate = scenario.candidate_graph(bind_new_column=bind_new_column)
    return build_plan(
        baseline,
        candidate,
        diff_columns(baseline, candidate),
        payload if payload is not None else scenario.payload(),
        renames=scenario.DECLARED_RENAMES if renames is None else renames,
        auto=auto,
        notify_only_collections=notify_only,
        revisions=revisions,
    )


def actions(plan) -> dict[int, MendAction]:
    return {card.card_id: card.action for card in plan.cards}


def card(plan, card_id):
    return next(entry for entry in plan.cards if entry.card_id == card_id)


# --------------------------------------------------------------------------------------
# taxonomy
# --------------------------------------------------------------------------------------


def test_every_card_gets_exactly_the_action_the_story_calls_for():
    assert actions(make_plan()) == {
        401: MendAction.REPOINT,
        402: MendAction.STRIP,
        403: MendAction.ARCHIVE,
        404: MendAction.STRIP,
        405: MendAction.NOTIFY,
        406: MendAction.NOTIFY,
    }


def test_repoint_rewrites_the_query_and_names_the_columns():
    entry = card(make_plan(), 401)
    assert entry.after["query"]["aggregation"] == [["sum", ["field", scenario.F_AMOUNT_USD, None]]]
    assert entry.repointed == ["aggregation: fct_orders.amount -> fct_orders.amount_usd"]
    assert entry.removed_clauses == []
    assert entry.archive is False


def test_strip_deletes_only_the_dead_filter():
    entry = card(make_plan(), 402)
    survivor = [">", ["field", scenario.F_CREATED, None], "2026-01-01"]
    assert entry.after["query"]["filter"] == survivor
    assert entry.removed_clauses == ["filter -> fct_orders.promo_code"]


def test_strip_works_the_same_on_an_mbql5_card():
    entry = card(make_plan(), 404)
    filters = entry.after["stages"][0]["filters"]
    assert [flt[0] for flt in filters] == [">"]
    assert entry.removed_clauses == ["filter -> fct_orders.promo_code"]


def test_archive_carries_the_flag_and_no_rewrite():
    entry = card(make_plan(), 403)
    assert entry.archive is True
    assert entry.after is None
    assert entry.before is not None
    assert "essential" in entry.reason


def test_a_card_reached_only_through_another_card_is_left_to_that_repair():
    entry = card(make_plan(), 406)
    assert entry.action is MendAction.NOTIFY
    assert entry.writes is False
    assert "#401" in entry.reason


def test_dead_refs_record_clauses_and_the_rename_target():
    refs = card(make_plan(), 401).dead_refs
    assert [ref.column for ref in refs] == ["fct_orders.amount"]
    assert refs[0].clauses == ["aggregation"]
    assert refs[0].rename_to == "fct_orders.amount_usd"
    assert refs[0].new_field_id == scenario.F_AMOUNT_USD
    assert refs[0].essential is False


def test_archived_cards_are_not_in_the_plan_at_all():
    payload = scenario.payload()
    for entry in payload.cards:
        if entry["id"] == 402:
            entry["archived"] = True
    assert 402 not in actions(make_plan(payload=payload))


def test_a_native_card_is_reported_rather_than_rewritten():
    payload = scenario.payload()
    for entry in payload.cards:
        if entry["id"] == 402:
            entry["dataset_query"] = {
                "type": "native",
                "database": 1,
                "native": {"query": "select promo_code from fct_orders"},
            }
    entry = card(make_plan(payload=payload), 402)
    assert entry.action is MendAction.NOTIFY
    assert "native SQL" in entry.reason


# --------------------------------------------------------------------------------------
# renames: declared only, and never guessed
# --------------------------------------------------------------------------------------


def test_without_a_declared_rename_the_renamed_column_is_stripped_not_repointed():
    # `amount` is simply gone as far as mend can tell -- and its card's sole aggregation
    # is that column, so the honest action is archive, not a guessed repoint
    plan = make_plan(renames={})
    assert plan.renames == {}
    assert card(plan, 401).action is MendAction.ARCHIVE


def test_a_rename_whose_target_is_not_bound_yet_notifies_instead_of_stripping():
    # Metabase has not synced the new column, so there is no field id to point at. Falling
    # back to a strip would delete a clause whose column still exists under a new name.
    plan = make_plan(bind_new_column=False)
    entry = card(plan, 401)
    assert entry.action is MendAction.NOTIFY
    assert entry.writes is False
    assert "did not resolve" in entry.reason
    assert any("not bound to a Metabase field yet" in note for note in plan.unresolved_renames)


def test_a_rename_naming_an_unknown_column_is_reported_and_ignored():
    plan = make_plan(renames={"fct_orders.nope": "fct_orders.also_nope"})
    assert plan.unresolved_renames
    assert "does not name one column" in plan.unresolved_renames[0]


def test_a_rename_of_a_column_that_was_not_removed_is_reported():
    plan = make_plan(renames={"fct_orders.region": "fct_orders.region_code"})
    assert any("was not removed" in note for note in plan.unresolved_renames)


# --------------------------------------------------------------------------------------
# autonomy
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("dialed_out", "card_id", "expected_downgrade"),
    [
        (MendAction.STRIP, 402, MendAction.STRIP),
        (MendAction.ARCHIVE, 403, MendAction.ARCHIVE),
        (MendAction.REPOINT, 401, MendAction.REPOINT),
    ],
)
def test_an_action_outside_mend_auto_downgrades_to_notify(dialed_out, card_id, expected_downgrade):
    auto = [action for action in ALL_AUTO if action is not dialed_out]
    entry = card(make_plan(auto=auto), card_id)
    assert entry.action is MendAction.NOTIFY
    assert entry.downgraded_from is expected_downgrade
    assert entry.writes is False
    assert f"'{dialed_out.value}' is not in mend.auto" in entry.reason


def test_an_empty_auto_list_writes_nothing_at_all():
    plan = make_plan(auto=[])
    assert plan.writing == []
    assert {entry.action for entry in plan.cards} == {MendAction.NOTIFY}


def test_a_notify_only_collection_is_never_written_to():
    entry = card(make_plan(), 405)
    assert entry.action is MendAction.NOTIFY
    assert entry.downgraded_from is MendAction.REPOINT
    assert entry.writes is False
    assert "notify-only" in entry.reason


def test_dropping_the_notify_only_pattern_lets_the_personal_card_be_repaired():
    assert card(make_plan(notify_only=[]), 405).action is MendAction.REPOINT


# --------------------------------------------------------------------------------------
# dashcards
# --------------------------------------------------------------------------------------


def test_a_dashboard_filter_on_a_renamed_column_is_repointed():
    edits = card(make_plan(), 401).dashcards
    assert len(edits) == 1
    assert edits[0].dashcard_id == 51
    assert edits[0].after[0]["target"] == ["dimension", ["field", scenario.F_AMOUNT_USD, None]]


def test_a_dashboard_filter_on_a_dead_column_is_dropped():
    edits = card(make_plan(), 402).dashcards
    assert len(edits) == 1
    assert len(edits[0].before) == 2
    assert [mapping["parameter_id"] for mapping in edits[0].after] == ["p-created"]


def test_a_card_with_no_affected_dashcard_carries_no_dashcard_edits():
    assert card(make_plan(), 404).dashcards == []


# --------------------------------------------------------------------------------------
# determinism and ordering
# --------------------------------------------------------------------------------------


def test_the_same_inputs_produce_a_byte_identical_plan():
    first = make_plan().model_dump(mode="json")
    second = make_plan().model_dump(mode="json")
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_the_plan_body_carries_no_wall_clock_beyond_the_observed_updated_at():
    body = json.dumps(make_plan().model_dump(mode="json"))
    assert scenario.UPDATED_AT in body
    for volatile in ("generated_at", "planned_at", "timestamp"):
        assert volatile not in body


def test_cards_are_ordered_upstream_first_then_by_id():
    # #406 sources #401, so it sorts after every depth-0 card whatever its id
    order = [entry.card_id for entry in make_plan().cards]
    assert order == [401, 402, 403, 404, 405, 406]
    assert order.index(401) < order.index(406)


def test_a_card_cycle_does_not_hang_the_ordering():
    baseline = scenario.baseline_graph()
    edges = list(baseline.edges)
    for edge in edges:
        if edge.to.endswith("::406"):
            edge.evidence = {"via": "card__401"}
    # make #401 read #406 as well: A -> B -> A
    for edge in edges:
        if edge.to.endswith("::401") and edge.from_.endswith("::101"):
            edge.evidence = {"via": "card__406"}
    baseline.edges = edges
    candidate = scenario.candidate_graph()
    plan = build_plan(
        baseline,
        candidate,
        diff_columns(baseline, candidate),
        scenario.payload(),
        renames=scenario.DECLARED_RENAMES,
        auto=ALL_AUTO,
        notify_only_collections=NOTIFY_ONLY,
    )
    assert [entry.card_id for entry in plan.cards]


def test_the_plan_echoes_the_configuration_it_was_built_under():
    plan = make_plan(auto=[MendAction.REPOINT])
    assert plan.auto == [MendAction.REPOINT]
    assert plan.notify_only_collections == NOTIFY_ONLY
    assert plan.renames == scenario.DECLARED_RENAMES
    assert plan.removed_columns == ["fct_orders.amount", "fct_orders.promo_code"]


def test_revision_ids_observed_at_plan_time_are_carried():
    plan = make_plan(revisions={401: 9001, 402: 9002})
    assert card(plan, 401).revision_id == 9001
    assert card(plan, 403).revision_id is None


def test_an_unchanged_graph_plans_nothing():
    baseline = scenario.baseline_graph()
    plan = build_plan(
        baseline,
        baseline,
        diff_columns(baseline, baseline),
        scenario.payload(),
        auto=ALL_AUTO,
    )
    assert plan.cards == []
    assert plan.removed_columns == []


# --------------------------------------------------------------------------------------
# graph reading helpers
# --------------------------------------------------------------------------------------


def test_affected_card_ids_agrees_with_the_plan():
    baseline = scenario.baseline_graph()
    candidate = scenario.candidate_graph()
    ids = affected_card_ids(baseline, diff_columns(baseline, candidate))
    assert ids == [401, 402, 403, 404, 405, 406]


def test_card_dependencies_reads_via_evidence():
    assert card_dependencies(scenario.baseline_graph()) == {406: [401]}
