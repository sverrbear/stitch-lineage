"""Estate hygiene report (`stitch doctor --dead`) over synthetic graphs."""

import json

import pytest
from typer.testing import CliRunner

from stitch_lineage.cli import app
from stitch_lineage.graph.dead import dead_report, format_dead_report
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
from stitch_lineage.io.graph_store import write_graph

runner = CliRunner()

STG = "model.demo.stg_orders"
FCT = "model.demo.fct_orders"
DEAD = "model.demo.mart_unused"
SRC = "source.demo.raw.orders"

CONFIG = """
metabase:
  url: https://mb.example.com
  api_key: ${STITCH_METABASE_API_KEY}
  databases:
    - metabase_name: Analytics
      dbt_database: ANALYTICS
"""


def col(owner, name):
    return Node(node_id=column_node_id(owner, name), node_type=NodeType.COLUMN, name=name)


def entity(uid, name, node_type=NodeType.MODEL):
    return Node(node_id=uid, node_type=node_type, name=name)


def card(card_id, name, archived=False):
    return Node(
        node_id=mb_card_node_id(card_id),
        node_type=NodeType.MB_CARD,
        name=name,
        properties={"archived": archived},
    )


def dashboard(dash_id, name, archived=False):
    return Node(
        node_id=mb_dashboard_node_id(dash_id),
        node_type=NodeType.MB_DASHBOARD,
        name=name,
        properties={"archived": archived},
    )


def edge(from_, to, edge_type):
    return Edge(from_=from_, to=to, edge_type=edge_type, confidence=Confidence.EXACT)


def estate():
    """source -> stg -> fct -> mb_field -> card -> dashboard, plus dead weight beside it.

    Live: total (traced end to end). Dead: stg_orders.note (feeds nothing), fct_orders.memo
    (bound to no field), the whole of mart_unused, source column raw.orders.legacy.
    """
    field = mb_field_node_id(101)
    nodes = [
        entity(SRC, "orders", NodeType.SOURCE),
        entity(STG, "stg_orders"),
        entity(FCT, "fct_orders"),
        entity(DEAD, "mart_unused"),
        col(SRC, "total"),
        col(SRC, "legacy"),
        col(STG, "total"),
        col(STG, "note"),
        col(FCT, "total"),
        col(FCT, "memo"),
        col(DEAD, "a"),
        col(DEAD, "b"),
        Node(node_id=field, node_type=NodeType.MB_FIELD, name="Total"),
        card(412, "Revenue by week"),
        dashboard(9, "Revenue"),
    ]
    edges = [
        edge(SRC, STG, EdgeType.REFERENCES),
        edge(STG, FCT, EdgeType.REFERENCES),
        edge(column_node_id(SRC, "total"), column_node_id(STG, "total"), EdgeType.FEEDS),
        edge(column_node_id(STG, "total"), column_node_id(FCT, "total"), EdgeType.FEEDS),
        edge(column_node_id(FCT, "total"), field, EdgeType.BINDS_TO),
        edge(field, mb_card_node_id(412), EdgeType.CONSUMED_BY),
        edge(mb_card_node_id(412), mb_dashboard_node_id(9), EdgeType.APPEARS_ON),
    ]
    return Graph(nodes=nodes, edges=edges)


# --- unconsumed columns -------------------------------------------------------


def test_unconsumed_columns_grouped_by_owner():
    report = dead_report(estate())
    assert report.columns_total == 8
    assert report.unconsumed_column_count == 5
    assert {group.owner_id: group.column_node_ids for group in report.unconsumed_columns} == {
        DEAD: [column_node_id(DEAD, "a"), column_node_id(DEAD, "b")],
        FCT: [column_node_id(FCT, "memo")],
        STG: [column_node_id(STG, "note")],
        SRC: [column_node_id(SRC, "legacy")],
    }


def test_consumed_column_absent_from_the_report():
    report = dead_report(estate())
    unconsumed = {nid for group in report.unconsumed_columns for nid in group.column_node_ids}
    for owner in (SRC, STG, FCT):
        assert column_node_id(owner, "total") not in unconsumed


def test_group_records_the_owners_kind_and_column_total():
    groups = {group.owner_id: group for group in dead_report(estate()).unconsumed_columns}
    assert groups[SRC].owner_type is NodeType.SOURCE
    assert groups[SRC].owner_name == "orders"
    assert groups[FCT].owner_type is NodeType.MODEL
    assert (groups[FCT].owner_columns_total, groups[FCT].whole_owner) == (2, False)
    assert (groups[DEAD].owner_columns_total, groups[DEAD].whole_owner) == (2, True)


def test_column_bound_to_a_field_no_card_consumes_is_unconsumed():
    graph = estate()
    orphan = mb_field_node_id(202)
    graph.nodes.append(Node(node_id=orphan, node_type=NodeType.MB_FIELD, name="Memo"))
    graph.edges.append(edge(column_node_id(FCT, "memo"), orphan, EdgeType.BINDS_TO))
    unconsumed = {
        nid for group in dead_report(graph).unconsumed_columns for nid in group.column_node_ids
    }
    assert column_node_id(FCT, "memo") in unconsumed


def test_relates_to_is_a_declaration_not_consumption():
    graph = estate()
    graph.edges.append(
        edge(column_node_id(DEAD, "a"), column_node_id(FCT, "total"), EdgeType.RELATES_TO)
    )
    unconsumed = {
        nid for group in dead_report(graph).unconsumed_columns for nid in group.column_node_ids
    }
    assert column_node_id(DEAD, "a") in unconsumed


# --- models feeding nothing ---------------------------------------------------


def test_dead_models_are_the_ones_with_no_path_to_a_consumer():
    report = dead_report(estate())
    assert report.models_total == 3
    assert [(model.node_id, model.columns_total) for model in report.dead_models] == [(DEAD, 2)]


def test_model_feeding_a_live_model_is_alive_even_with_untraced_columns():
    """Whole point of the owner hop: a missing `feeds` edge is stitch's blind spot, and
    it must not turn into the report's loudest finding."""
    graph = estate()
    graph.edges = [
        e
        for e in graph.edges
        if not (e.edge_type is EdgeType.FEEDS and e.to == column_node_id(FCT, "total"))
    ]
    report = dead_report(graph)
    assert [model.node_id for model in report.dead_models] == [DEAD]
    # the untraced column itself is still reported -- nothing consumes it
    unconsumed = {nid for group in report.unconsumed_columns for nid in group.column_node_ids}
    assert column_node_id(STG, "total") in unconsumed


def test_model_feeding_only_a_dead_model_is_dead_too():
    graph = estate()
    upstream = "model.demo.stg_unused"
    graph.nodes.append(entity(upstream, "stg_unused"))
    graph.edges.append(edge(upstream, DEAD, EdgeType.REFERENCES))
    assert [model.node_id for model in dead_report(graph).dead_models] == [DEAD, upstream]


def test_sources_are_not_reported_as_dead_models():
    report = dead_report(estate())
    assert SRC not in {model.node_id for model in report.dead_models}


# --- archived but bound -------------------------------------------------------


def archived_estate():
    """An archived card still holding a column, and a live card only on a dead dashboard."""
    graph = estate()
    field = mb_field_node_id(303)
    graph.nodes.extend(
        [
            Node(node_id=field, node_type=NodeType.MB_FIELD, name="Memo"),
            card(500, "Old cohort chart", archived=True),
            card(501, "Weekly signups"),
            dashboard(20, "Q1 retro", archived=True),
        ]
    )
    graph.edges.extend(
        [
            edge(column_node_id(FCT, "memo"), field, EdgeType.BINDS_TO),
            edge(field, mb_card_node_id(500), EdgeType.CONSUMED_BY),
            edge(mb_field_node_id(101), mb_card_node_id(501), EdgeType.CONSUMED_BY),
            edge(mb_card_node_id(501), mb_dashboard_node_id(20), EdgeType.APPEARS_ON),
        ]
    )
    return graph


def test_archived_card_still_bound_lists_its_columns():
    report = dead_report(archived_estate())
    assert [(c.card_ref, c.name, c.columns) for c in report.archived_cards_bound] == [
        ("500", "Old cohort chart", ["fct_orders.memo"])
    ]


def test_archived_card_bound_to_nothing_is_not_reported():
    graph = archived_estate()
    graph.nodes.append(card(600, "Empty archived card", archived=True))
    assert [c.card_ref for c in dead_report(graph).archived_cards_bound] == ["500"]


def test_cards_whose_only_dashboards_are_archived():
    report = dead_report(archived_estate())
    assert [(c.card_ref, c.dashboards) for c in report.cards_only_on_archived_dashboards] == [
        ("501", ["Q1 retro"])
    ]


def test_card_with_one_live_dashboard_is_not_orphaned():
    graph = archived_estate()
    graph.edges.append(edge(mb_card_node_id(501), mb_dashboard_node_id(9), EdgeType.APPEARS_ON))
    assert dead_report(graph).cards_only_on_archived_dashboards == []


def test_card_on_no_dashboard_at_all_is_not_orphaned_by_archiving():
    graph = archived_estate()
    graph.nodes.append(card(700, "Standalone"))
    assert [c.card_ref for c in dead_report(graph).cards_only_on_archived_dashboards] == ["501"]


def test_archived_consumption_still_counts_as_consumption_for_columns():
    """Per the issue, "any card" consumes: the archived card is surfaced in its own
    group instead, so the column is not double-reported as dead."""
    unconsumed = {
        nid
        for group in dead_report(archived_estate()).unconsumed_columns
        for nid in group.column_node_ids
    }
    assert column_node_id(FCT, "memo") not in unconsumed


def test_missing_archived_property_reads_as_live():
    graph = estate()
    graph.nodes.append(Node(node_id=mb_card_node_id(800), node_type=NodeType.MB_CARD, name="Bare"))
    report = dead_report(graph)
    assert report.archived_cards_bound == []
    assert report.cards_only_on_archived_dashboards == []


# --- rendering ----------------------------------------------------------------


def test_report_headlines_counts_then_lists():
    text = format_dead_report(dead_report(archived_estate()))
    lines = text.splitlines()
    assert lines[0].startswith("unconsumed columns")
    assert lines[0].endswith("4/8   (across 2 models, 1 source)")
    assert lines[1].endswith("1/3")
    assert lines[2].endswith("1")
    assert lines[3].endswith("1")
    assert "stitch only sees Metabase" in text
    assert "not a delete queue" in text
    assert text.index("unconsumed columns (4):") < text.index("models feeding nothing (1):")
    assert "  model.demo.mart_unused -- all 2 columns" in text
    assert "  model.demo.stg_orders -- 1 of 2 columns" in text
    assert "    note" in lines
    assert "  #500 Old cohort chart -- 1 column" in text
    assert "  #501 Weekly signups -- Q1 retro" in text


def test_clean_graph_says_so():
    field = mb_field_node_id(101)
    graph = Graph(
        nodes=[
            entity(FCT, "fct_orders"),
            col(FCT, "total"),
            Node(node_id=field, node_type=NodeType.MB_FIELD, name="Total"),
            card(412, "Revenue"),
        ],
        edges=[
            edge(column_node_id(FCT, "total"), field, EdgeType.BINDS_TO),
            edge(field, mb_card_node_id(412), EdgeType.CONSUMED_BY),
        ],
    )
    report = dead_report(graph)
    assert report.empty
    text = format_dead_report(report)
    assert "nothing flagged" in text
    assert "unconsumed columns (0)" not in text


def test_graph_with_no_metabase_side_says_the_report_is_vacuous():
    graph = Graph(nodes=[entity(FCT, "fct_orders"), col(FCT, "total")])
    report = dead_report(graph)
    assert report.cards_total == 0
    text = format_dead_report(report)
    assert "no Metabase cards" in text
    assert [model.node_id for model in report.dead_models] == [FCT]


def test_report_is_deterministic():
    first = format_dead_report(dead_report(archived_estate()))
    assert first == format_dead_report(dead_report(archived_estate()))


# --- CLI ----------------------------------------------------------------------


@pytest.fixture
def offline_project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("STITCH_METABASE_URL", raising=False)
    monkeypatch.delenv("STITCH_METABASE_API_KEY", raising=False)
    (tmp_path / "stitch.yml").write_text(CONFIG)
    write_graph(archived_estate(), tmp_path / ".stitch" / "graph.json")
    return tmp_path


def test_doctor_dead_works_without_metabase_env(offline_project):
    result = runner.invoke(app, ["doctor", "--dead"])
    assert result.exit_code == 0, result.output
    assert "unconsumed columns" in result.output
    assert "model.demo.mart_unused" in result.output
    assert "#500 Old cohort chart" in result.output


def test_doctor_dead_json(offline_project):
    result = runner.invoke(app, ["doctor", "--dead", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["columns_total"] == 8
    assert payload["dead_models"] == [{"node_id": DEAD, "name": "mart_unused", "columns_total": 2}]
    assert payload["archived_cards_bound"][0]["card_ref"] == "500"
    assert payload["cards_only_on_archived_dashboards"][0]["dashboards"] == ["Q1 retro"]
    assert "reverse ETL" in payload["caveat"]


def test_doctor_json_without_dead_is_rejected(offline_project):
    result = runner.invoke(app, ["doctor", "--json"])
    assert result.exit_code == 1
    assert "--json is only supported with --dead" in result.output


def test_doctor_dead_without_a_graph_fails(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "stitch.yml").write_text(CONFIG)
    result = runner.invoke(app, ["doctor", "--dead"])
    assert result.exit_code == 1
    assert "run 'stitch build' first" in result.output
