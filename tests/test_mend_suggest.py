"""Rename hints do not establish identity, modify graphs, or authorize repairs."""

import mend_scenario as scenario
import pytest

from stitch_lineage.graph.impact import diff_columns
from stitch_lineage.graph.schema import Confidence, DataTypeSource, Edge, EdgeType, Node, NodeType
from stitch_lineage.mend.suggest import suggest_renames


def candidates(base=None, current=None):
    groups = suggest_renames(
        base or scenario.baseline_graph(), current or scenario.candidate_graph()
    )
    return next(group for group in groups if group.column == scenario.AMOUNT)


def add_candidate(graph, name="amount_eur", field_id=202, model=scenario.MODEL):
    node_id = f"{model}::{name}"
    graph.nodes.extend(
        [scenario._column(node_id, name.upper()), scenario._field(field_id, name.upper())]
    )
    graph.edges.append(scenario._binds(node_id, field_id))
    return node_id


def test_supported_snapshot_evidence_is_deterministic_and_never_reclassifies_impact():
    base, current = scenario.baseline_graph(), scenario.candidate_graph()
    before = [graph.model_dump_json() for graph in (base, current)]
    original_diff = diff_columns(base, current)
    group = candidates(base, current)
    assert group.total_candidates == 1
    assert not group.ambiguous
    assert group.candidates[0].id == scenario.AMOUNT_USD
    assert group.candidates[0].evidence == [
        "Added in the same dbt relation",
        "Compatible snapshot types: NUMBER → NUMBER",
        "Shared name words: amount",
        "Exact Metabase field binding in the current snapshot",
    ]
    assert [graph.model_dump_json() for graph in (base, current)] == before
    assert diff_columns(base, current) == original_diff
    assert scenario.AMOUNT in original_diff.removed and scenario.AMOUNT_USD in original_diff.added
    base.nodes.reverse()
    current.edges.reverse()
    assert candidates(base, current) == group
    assert (
        next(g for g in suggest_renames(base, current) if g.column == scenario.PROMO).candidates
        == []
    )


@pytest.mark.parametrize(
    "data_type,source",
    [
        ("VARCHAR", None),
        (None, None),
        ("NUMBER", DataTypeSource.INFERRED),
        ("UNKNOWN", None),
        ("TYPE/*", None),
    ],
)
def test_incompatible_missing_or_inferred_types_abstain(data_type, source):
    current = scenario.candidate_graph()
    target = next(n for n in current.nodes if n.node_id == scenario.AMOUNT_USD)
    target.data_type, target.data_type_source = data_type, source
    assert candidates(current=current).candidates == []


def test_compatible_aliases_and_precision_keep_the_actual_types_in_evidence():
    current = scenario.candidate_graph()
    target = next(n for n in current.nodes if n.node_id == scenario.AMOUNT_USD)
    target.data_type = "DECIMAL(18,2)"
    assert (
        "Compatible snapshot types: NUMBER → DECIMAL(18,2)"
        in candidates(current=current).candidates[0].evidence
    )


@pytest.mark.parametrize("mode", ["missing", "inactive", "retired", "fuzzy", "renamed"])
def test_unusable_or_uncertain_snapshot_bindings_abstain(mode):
    current = scenario.candidate_graph()
    target_field = next(
        n for n in current.nodes if n.node_id == f"mb_field::{scenario.F_AMOUNT_USD}"
    )
    if mode == "missing":
        current.nodes.remove(target_field)
    elif mode == "inactive":
        target_field.properties["active"] = False
    elif mode == "retired":
        target_field.properties["visibility"] = "retired"
    elif mode == "renamed":
        target_field.column = "SOMETHING_ELSE"
    else:
        next(
            e for e in current.edges if e.from_ == scenario.AMOUNT_USD
        ).confidence = Confidence.FUZZY
    assert candidates(current=current).candidates == []


def test_retired_field_is_rejected_using_the_resolvers_actual_persisted_metadata():
    from mend_browser_scenario import BrowserMetabase

    from stitch_lineage.resolve.metabase import resolve_metabase

    current = scenario.candidate_graph()
    payload = BrowserMetabase().payload
    field = payload.database_metadata[1]["tables"][0]["fields"][0]
    field["visibility_type"] = "retired"
    resolved = resolve_metabase(payload, [])
    target_field = next(
        n for n in resolved.nodes if n.node_id == f"mb_field::{scenario.F_AMOUNT_USD}"
    )
    assert target_field.properties["visibility"] == "retired"
    current.nodes = [
        target_field if n.node_id == target_field.node_id else n for n in current.nodes
    ]
    assert candidates(current=current).candidates == []


def test_existing_cross_model_and_weakly_named_columns_are_not_suggested():
    base, current = scenario.baseline_graph(), scenario.candidate_graph()
    base.nodes.append(
        next(n.model_copy(deep=True) for n in current.nodes if n.node_id == scenario.AMOUNT_USD)
    )
    add_candidate(current, "amount", 202, "model.demo.other_orders")
    add_candidate(current, "unrelated_total", 203)
    assert candidates(base, current).candidates == []


def test_same_exact_upstream_lineage_can_support_a_different_name_but_parsed_lineage_cannot():
    base, current = scenario.baseline_graph(), scenario.candidate_graph()
    # Remove the familiar name candidate; the new name has no name-match evidence.
    current.nodes = [n for n in current.nodes if n.node_id != scenario.AMOUNT_USD]
    new = add_candidate(current, "net", 202)
    source = Node(
        node_id="source.demo.orders::subtotal",
        node_type=NodeType.COLUMN,
        name="subtotal",
        column="SUBTOTAL",
    )
    for graph, target in [(base, scenario.AMOUNT), (current, new)]:
        graph.nodes.append(source.model_copy(deep=True))
        graph.edges.append(
            Edge(
                from_=source.node_id,
                to=target,
                edge_type=EdgeType.FEEDS,
                confidence=Confidence.EXACT,
            )
        )
    group = candidates(base, current)
    assert [c.id for c in group.candidates] == [new]
    assert "Same exact upstream columns: orders.subtotal" in group.candidates[0].evidence
    current.edges[-1].confidence = Confidence.PARSED
    assert candidates(base, current).candidates == []


def test_multiple_candidates_stay_ambiguous_even_when_the_visible_list_is_limited():
    current = scenario.candidate_graph()
    for index, currency in enumerate(["eur", "gbp", "isk", "cad"]):
        add_candidate(current, f"amount_{currency}", 202 + index)
    group = candidates(current=current)
    assert group.ambiguous
    assert group.total_candidates == 5
    assert len(group.candidates) == 3
    assert [c.id for c in group.candidates] == sorted(c.id for c in group.candidates)


def test_a_target_shared_by_two_removed_columns_is_ambiguous_for_both():
    base = scenario.baseline_graph()
    old = f"{scenario.MODEL}::amount_usd_old"
    base.nodes.append(scenario._column(old, "AMOUNT_USD_OLD"))
    groups = {group.column: group for group in suggest_renames(base, scenario.candidate_graph())}
    for column in [scenario.AMOUNT, old]:
        assert groups[column].ambiguous
        assert groups[column].total_candidates == 1
        assert groups[column].candidates[0].also_suggested_for
