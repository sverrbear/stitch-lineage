"""Archived Metabase ingestion: HTTP filters through graph estate hygiene."""

import json
from pathlib import Path

import pytest
import requests
import responses

from stitch_lineage.graph.dead import dead_report
from stitch_lineage.graph.schema import Confidence, Edge, EdgeType, Graph, Node, NodeType
from stitch_lineage.io.metabase_client import MetabaseAPIError, MetabaseClient, load_cached
from stitch_lineage.resolve.metabase import resolve_metabase

BASE = "http://mb.local"
FIXTURES = Path(__file__).parent / "fixtures" / "metabase"


def fixture(name):
    return json.loads((FIXTURES / f"{name}.json").read_text())


def archived_dashboard():
    return {
        "id": 303,
        "name": "Retired board",
        "collection_id": 13,
        "archived": True,
        "dashcards": [{"id": 501, "card_id": 205}],
    }


def register_listing(path, items, archived_params):
    for archived, params in ((False, {}), (True, archived_params)):
        rows = [item for item in items if bool(item.get("archived")) == archived]
        body = {"data": rows, "total": len(rows)} if archived else rows
        responses.get(
            f"{BASE}{path}",
            json=body,
            match=[responses.matchers.query_param_matcher(params)],
        )


def register_fetch(*, extra_cards=(), extra_dashboards=(), extra_collections=()):
    responses.get(f"{BASE}/api/session/properties", json=fixture("session_properties"))
    responses.get(f"{BASE}/api/database", json=fixture("databases"))
    responses.get(f"{BASE}/api/database/2/metadata", json=fixture("database_metadata_2"))
    register_listing("/api/card", [*fixture("cards"), *extra_cards], {"f": "archived"})
    summaries = [
        {key: value for key, value in dashboard.items() if key != "dashcards"}
        for dashboard in extra_dashboards
    ]
    register_listing(
        "/api/dashboard",
        [*fixture("dashboards")["data"], *summaries],
        {"f": "archived"},
    )
    responses.get(f"{BASE}/api/dashboard/301", json=fixture("dashboard_301"))
    responses.get(f"{BASE}/api/dashboard/302", json=fixture("dashboard_302"))
    for dashboard in extra_dashboards:
        responses.get(f"{BASE}/api/dashboard/{dashboard['id']}", json=dashboard)
    register_listing(
        "/api/collection",
        [*fixture("collections"), *extra_collections],
        {"archived": "true"},
    )
    responses.get(f"{BASE}/api/native-query-snippet", json=fixture("snippets"))


def client(**kwargs):
    return MetabaseClient(BASE, "synthetic-key", backoff=0, **kwargs)


@responses.activate
def test_archived_lists_are_merged_deterministically_and_cached_separately(tmp_path):
    register_fetch(extra_dashboards=[archived_dashboard()])
    # A card can move to the archive between the two HTTP requests.
    earlier_cards = fixture("cards")
    earlier_cards[5]["archived"] = False
    earlier_cards[5]["name"] = "Earlier card state"
    responses.replace(
        responses.GET,
        f"{BASE}/api/card",
        json=list(reversed(earlier_cards)),
        match=[responses.matchers.query_param_matcher({})],
    )
    payload = client(cache_dir=tmp_path).fetch_all(["Analytics"])

    assert [card["id"] for card in payload.cards] == list(range(201, 210))
    archived = next(card for card in payload.cards if card["id"] == 206)
    assert archived["archived"] is True
    assert archived["name"] == "Old orders count"
    assert [dashboard["id"] for dashboard in payload.dashboards] == [301, 302, 303]
    assert payload.dashboards[-1]["dashcards"] == [{"id": 501, "card_id": 205}]

    run = next(tmp_path.iterdir())
    assert len(json.loads((run / "cards.json").read_text())) == 9
    assert json.loads((run / "cards_archived.json").read_text())["data"] == [fixture("cards")[5]]
    assert (run / "dashboards_archived.json").is_file()
    assert (run / "collections_archived.json").is_file()
    assert load_cached(tmp_path) == payload


@responses.activate
def test_fetched_archived_objects_reach_dead_report_and_respect_collection_scope():
    archived_card = {**fixture("cards")[5], "id": 210, "collection_id": 15}
    archived_collection = {"id": 13, "name": "Retired", "location": "/", "archived": True}
    excluded_parent = {"id": 14, "name": "Hidden", "location": "/", "archived": True}
    excluded_child = {"id": 15, "name": "Nested", "location": "/14/", "archived": True}
    excluded_dashboard = {**archived_dashboard(), "id": 304, "collection_id": 15}
    register_fetch(
        extra_cards=[archived_card],
        extra_dashboards=[archived_dashboard(), excluded_dashboard],
        extra_collections=[archived_collection, excluded_parent, excluded_child],
    )
    payload = client().fetch_all(["Analytics"])
    resolution = resolve_metabase(payload, ["Hidden*"])
    nodes = {node.node_id: node for node in resolution.nodes}
    assert nodes["mb_card::206"].properties["archived"] is True
    assert nodes["mb_dash::303"].properties["archived"] is True
    assert "mb_card::210" not in nodes
    assert "mb_dash::304" not in nodes

    column = Node(
        node_id="model.demo.fct_orders::order_id",
        node_type=NodeType.COLUMN,
        name="order_id",
    )
    binding = Edge(
        from_=column.node_id,
        to="mb_field::100",
        edge_type=EdgeType.BINDS_TO,
        confidence=Confidence.EXACT,
    )
    graph = Graph(nodes=[column, *resolution.nodes], edges=[binding, *resolution.edges])
    report = dead_report(graph)
    assert [card.card_ref for card in report.archived_cards_bound] == ["206"]
    assert [
        (card.card_ref, card.dashboards) for card in report.cards_only_on_archived_dashboards
    ] == [("205", ["Retired board"])]


@responses.activate
def test_legacy_archived_dashboard_summary_does_not_claim_resolved_membership():
    register_fetch(extra_dashboards=[archived_dashboard()])
    responses.replace(
        responses.GET,
        f"{BASE}/api/dashboard/303",
        status=404,
        json={"message": "The object has been archived.", "error_code": "archived"},
    )
    payload = client().fetch_all(["Analytics"])
    summary = next(dashboard for dashboard in payload.dashboards if dashboard["id"] == 303)
    assert summary["archived"] is True
    assert "dashcards" not in summary
    resolution = resolve_metabase(payload, [])
    assert resolution.dashboards_total == 3
    assert resolution.dashboards == 2
    assert not any(edge.to == "mb_dash::303" for edge in resolution.edges)


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (404, {"message": "Not found"}),
        (403, {"message": "Forbidden"}),
        (500, {"message": "Unavailable"}),
    ],
)
@responses.activate
def test_archived_dashboard_does_not_swallow_other_api_failures(status, body):
    register_fetch(extra_dashboards=[archived_dashboard()])
    responses.replace(responses.GET, f"{BASE}/api/dashboard/303", status=status, json=body)
    with pytest.raises(MetabaseAPIError, match=f"HTTP {status}"):
        client().fetch_all(["Analytics"])


@responses.activate
def test_archived_dashboard_does_not_swallow_connection_failure():
    register_fetch(extra_dashboards=[archived_dashboard()])
    responses.replace(
        responses.GET,
        f"{BASE}/api/dashboard/303",
        body=requests.ConnectionError("synthetic outage"),
    )
    with pytest.raises(MetabaseAPIError, match="ConnectionError"):
        client().fetch_all(["Analytics"])
