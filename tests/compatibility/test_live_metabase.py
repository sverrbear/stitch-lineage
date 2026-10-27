"""Contract assertions against actual Metabase releases, never mocked HTTP."""

from copy import deepcopy

import pytest

from stitch_lineage.graph.schema import EdgeType, mb_card_node_id, mb_field_node_id
from stitch_lineage.io.metabase_client import MetabaseAPIError
from stitch_lineage.mend.apply import apply_plan, query_error
from stitch_lineage.mend.models import CardPlan, DashcardEdit, MendAction, MendPlan
from stitch_lineage.mend.rewrite import DeadSet, rewrite_parameter_mappings, rewrite_query
from stitch_lineage.resolve.metabase import resolve_metabase


def plan_for(instance, card, after, **kwargs):
    current = instance.client.get_card(card["id"])
    return MendPlan(
        auto=[MendAction.REPOINT],
        cards=[
            CardPlan(
                card_id=current["id"],
                name=current["name"],
                action=MendAction.REPOINT,
                reason="Synthetic declared rename",
                before=current["dataset_query"],
                after=after,
                updated_at=current["updated_at"],
                **kwargs,
            )
        ],
    )


def apply(plan, client):
    return apply_plan(plan, client, auto=[MendAction.REPOINT], notify_only_collections=[]).cards[0]


def test_fetch_resolve_nested_native_and_archived(live_metabase):
    mb = live_metabase
    base = mb.card("Compatibility base", mb.query())
    nested = mb.card(
        "Compatibility saved question",
        {
            "database": mb.database_id,
            "type": "query",
            "query": {"source-table": f"card__{base['id']}"},
        },
    )
    native = mb.card("Compatibility SQL", mb.native("SELECT amount FROM public.compat_orders"))
    archived = mb.card("Compatibility archived", mb.query())
    mb.client.update_card(archived["id"], {"archived": True})
    mb.client.cache_dir = mb.artifacts / "payloads"
    payload = mb.client.fetch_all(["Stitch synthetic warehouse"])
    resolution = resolve_metabase(payload, [])
    for card in (base, nested, native):
        assert query_error(mb.client.run_card_query(card["id"])) is None
        assert any(
            e.from_ == mb_field_node_id(mb.fields["amount"])
            and e.to == mb_card_node_id(card["id"])
            and e.edge_type == EdgeType.CONSUMED_BY
            for e in resolution.edges
        )
    assert next(c for c in payload.cards if c["id"] == archived["id"])["archived"]
    live_query = mb.client.get_card(base["id"])["dataset_query"]
    shape = "stages" if "stages" in live_query else "legacy"
    assert shape == ("stages" if mb.version == "v0.64.1" else "legacy")
    mb.evidence.update({"query_shape": shape, "resolved_cards": 3, "archived_listing": True})
    mb.save_evidence()


def test_mend_repoint_and_dashboard_filter(live_metabase):
    mb = live_metabase
    card = mb.card("Compatibility repair", mb.query())
    dashboard = mb.request(
        "POST",
        "/api/dashboard",
        {
            "name": "Compatibility dashboard",
            "collection_id": mb.collection,
            "parameters": [
                {"id": "amount-filter", "name": "Amount", "slug": "amount", "type": "number/="}
            ],
        },
    )
    mappings = [
        {
            "parameter_id": "amount-filter",
            "card_id": card["id"],
            "target": ["dimension", ["field", mb.fields["amount"], None]],
        }
    ]
    mb.client.update_dashcards(
        dashboard["id"],
        [
            {
                "id": -1,
                "card_id": card["id"],
                "row": 0,
                "col": 0,
                "size_x": 12,
                "size_y": 6,
                "parameter_mappings": mappings,
                "visualization_settings": {},
            }
        ],
    )
    dashcard = mb.client.get_dashboard(dashboard["id"])["dashcards"][0]
    dead = DeadSet(field_map={mb.fields["amount"]: mb.fields["amount_usd"]})
    before = mb.client.get_card(card["id"])["dataset_query"]
    rewritten = rewrite_query(before, dead)
    assert rewritten.changed and rewritten.query is not None
    after_mappings, notes = rewrite_parameter_mappings(dashcard["parameter_mappings"], dead)
    assert notes
    edit = DashcardEdit(
        dashboard_id=dashboard["id"],
        dashboard_name=dashboard["name"],
        dashcard_id=dashcard["id"],
        before=dashcard["parameter_mappings"],
        after=after_mappings,
    )
    outcome = apply(plan_for(mb, card, rewritten.query, dashcards=[edit]), mb.client)
    assert outcome.status == "applied", outcome.detail
    assert outcome.dashcards_written == 1
    result = mb.client.run_card_query(card["id"])
    assert sorted(row[0] for row in result["data"]["rows"]) == [100, 200]
    actual = mb.client.get_dashboard(dashboard["id"])["dashcards"][0]["parameter_mappings"]
    assert actual == after_mappings
    payload = mb.client.fetch_all(["Stitch synthetic warehouse"])
    assert any(
        e.edge_type == EdgeType.APPEARS_ON and e.from_ == mb_card_node_id(card["id"])
        for e in resolve_metabase(payload, []).edges
    )


def test_read_only_api_key_cannot_apply(live_metabase):
    mb = live_metabase
    card = mb.card("Compatibility read only", mb.query())
    before = mb.client.get_card(card["id"])
    assert mb.client.get_card(card["id"])["can_write"] is True
    assert mb.reader.get_card(card["id"])["can_write"] is False
    outcome = apply(plan_for(mb, card, mb.query("amount_usd")), mb.reader)
    assert outcome.status == "skipped", outcome.detail
    with pytest.raises(MetabaseAPIError) as error:
        mb.reader.update_card(card["id"], {"dataset_query": mb.query("amount_usd")})
    assert error.value.status_code == 403
    assert mb.client.get_card(card["id"])["updated_at"] == before["updated_at"]


def test_rejected_query_is_restored_via_real_revision(live_metabase):
    mb = live_metabase
    card = mb.card("Compatibility recovery", mb.native("SELECT amount FROM public.compat_orders"))
    revision = mb.client.latest_card_revision(card["id"])
    assert revision is not None
    before = deepcopy(mb.client.get_card(card["id"])["dataset_query"])
    broken = mb.native("SELECT missing_compat_column FROM public.compat_orders")
    outcome = apply(plan_for(mb, card, broken, revision_id=revision), mb.client)
    assert outcome.status == "failed"
    assert outcome.reverted is True, outcome.detail
    assert f"reverted to revision {revision}" in outcome.detail
    assert mb.client.get_card(card["id"])["dataset_query"] == before
    assert sorted(row[0] for row in mb.client.run_card_query(card["id"])["data"]["rows"]) == [
        10,
        20,
    ]


def test_concurrent_edit_blocks_apply(live_metabase):
    mb = live_metabase
    card = mb.card("Compatibility stale", mb.query())
    plan = plan_for(mb, card, mb.query("amount_usd"))
    mb.client.update_card(card["id"], {"name": "A later synthetic edit"})
    outcome = apply(plan, mb.client)
    assert outcome.status == "stale", outcome.detail
    assert sorted(row[0] for row in mb.client.run_card_query(card["id"])["data"]["rows"]) == [
        10,
        20,
    ]
