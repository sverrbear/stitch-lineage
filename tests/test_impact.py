from stitch_lineage.graph.impact import (
    ColumnDiff,
    ImpactReport,
    column_blast_radius,
    diff_columns,
    downstream,
    format_blast_radius,
    format_build_summary,
    format_github_comment,
    format_slack_comment,
    impact_from_graphs,
    resolve_column_ref,
)
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

FCT = "model.smitten.fct_matches"
MART = "model.smitten.mart_engagement"


def col(model_uid, name, data_type="NUMBER"):
    return Node(
        node_id=column_node_id(model_uid, name),
        node_type=NodeType.COLUMN,
        name=name,
        data_type=data_type,
    )


def model(uid, name):
    return Node(node_id=uid, node_type=NodeType.MODEL, name=name)


def edge(from_, to, edge_type):
    return Edge(from_=from_, to=to, edge_type=edge_type, confidence=Confidence.EXACT)


def chain_graphs():
    fct_col = column_node_id(FCT, "match_intensity")
    mart_col = column_node_id(MART, "match_intensity")
    field = mb_field_node_id(101)
    card = mb_card_node_id(412)
    dash = mb_dashboard_node_id(9)
    nodes = [
        model(FCT, "fct_matches"),
        model(MART, "mart_engagement"),
        col(FCT, "match_intensity"),
        col(MART, "match_intensity"),
        Node(node_id=field, node_type=NodeType.MB_FIELD, name="Match Intensity"),
        Node(
            node_id=card,
            node_type=NodeType.MB_CARD,
            name="Match intensity by country",
            properties={"creator": "sverrir"},
        ),
        Node(node_id=dash, node_type=NodeType.MB_DASHBOARD, name="Board dashboard"),
    ]
    edges = [
        edge(fct_col, mart_col, EdgeType.FEEDS),
        edge(mart_col, field, EdgeType.BINDS_TO),
        edge(field, card, EdgeType.CONSUMED_BY),
        edge(card, dash, EdgeType.APPEARS_ON),
    ]
    base = Graph(nodes=nodes, edges=edges)
    candidate = Graph(
        nodes=[n for n in nodes if n.node_id != fct_col],
        edges=[e for e in edges if fct_col not in (e.from_, e.to)],
    )
    return base, candidate


def test_diff_columns():
    base = Graph(nodes=[col(FCT, "a"), col(FCT, "b", "NUMBER")])
    candidate = Graph(nodes=[col(FCT, "b", "FLOAT"), col(FCT, "c")])
    diff = diff_columns(base, candidate)
    assert diff.removed == [column_node_id(FCT, "a")]
    assert diff.added == [column_node_id(FCT, "c")]
    assert len(diff.type_changed) == 1
    tc = diff.type_changed[0]
    assert (tc.node_id, tc.old_type, tc.new_type) == (column_node_id(FCT, "b"), "NUMBER", "FLOAT")


def test_downstream_follows_flow_not_relates_to():
    a, b, c = (column_node_id(FCT, x) for x in "abc")
    fk = column_node_id(MART, "fk")
    field = mb_field_node_id(1)
    graph = Graph(
        nodes=[
            col(FCT, "a"),
            col(FCT, "b"),
            col(FCT, "c"),
            col(MART, "fk"),
            Node(node_id=field, node_type=NodeType.MB_FIELD, name="B"),
        ],
        edges=[
            edge(a, b, EdgeType.FEEDS),
            edge(b, c, EdgeType.FEEDS),
            edge(c, field, EdgeType.BINDS_TO),
            edge(b, fk, EdgeType.RELATES_TO),
        ],
    )
    report = downstream(graph, [b])
    reached = {n.node_id for n in report.impacted[b]}
    assert reached == {c, field}
    assert a not in reached  # upstream never reached
    assert fk not in reached  # relates_to never followed
    depths = {n.node_id: n.depth for n in report.impacted[b]}
    assert depths == {c: 1, field: 2}
    field_hit = next(n for n in report.impacted[b] if n.node_id == field)
    assert field_hit.path == [b, c, field]
    assert field_hit.edge_types == [EdgeType.FEEDS, EdgeType.BINDS_TO]


def test_diamond_dedupes_to_shortest_path():
    ids = {x: column_node_id(FCT, x) for x in "abcde"}
    graph = Graph(
        nodes=[col(FCT, x) for x in "abcde"],
        edges=[
            edge(ids["a"], ids["b"], EdgeType.FEEDS),
            edge(ids["a"], ids["c"], EdgeType.FEEDS),
            edge(ids["b"], ids["d"], EdgeType.FEEDS),
            edge(ids["c"], ids["d"], EdgeType.FEEDS),
            edge(ids["d"], ids["e"], EdgeType.FEEDS),
        ],
    )
    report = downstream(graph, [ids["a"]])
    d_hits = [n for n in report.impacted[ids["a"]] if n.node_id == ids["d"]]
    assert len(d_hits) == 1
    assert d_hits[0].depth == 2
    assert d_hits[0].path == [ids["a"], ids["b"], ids["d"]]
    e_hit = next(n for n in report.impacted[ids["a"]] if n.node_id == ids["e"])
    assert e_hit.depth == 3


def test_depth_cap_and_truncation_flag():
    ids = {x: column_node_id(FCT, x) for x in "abcd"}
    graph = Graph(
        nodes=[col(FCT, x) for x in "abcd"],
        edges=[
            edge(ids["a"], ids["b"], EdgeType.FEEDS),
            edge(ids["b"], ids["c"], EdgeType.FEEDS),
            edge(ids["c"], ids["d"], EdgeType.FEEDS),
        ],
    )
    capped = downstream(graph, [ids["a"]], max_depth=2)
    assert {n.node_id for n in capped.impacted[ids["a"]]} == {ids["b"], ids["c"]}
    assert capped.truncated
    full = downstream(graph, [ids["a"]])
    assert {n.node_id for n in full.impacted[ids["a"]]} == {ids["b"], ids["c"], ids["d"]}
    assert not full.truncated


def test_missing_start_yields_empty_entry():
    report = downstream(Graph(), ["model.x::ghost"])
    assert report.impacted == {"model.x::ghost": []}


def test_github_comment_golden():
    base, candidate = chain_graphs()
    diff, report = impact_from_graphs(base, candidate)
    comment = format_github_comment(diff, report, base)
    expected = (
        "⚠ 1 column removed or renamed\n"
        "\n"
        "fct_matches.match_intensity → removed\n"
        "  ├ 1 downstream model: mart_engagement\n"
        "  └ 1 Metabase card:\n"
        "      #412 Match intensity by country  (Board dashboard, sverrir)\n"
        "\n"
        "_Renames appear as remove+add: a renamed column shows up here as removed._"
    )
    assert comment == expected


def test_github_comment_empty_diff():
    base, _ = chain_graphs()
    diff, report = impact_from_graphs(base, base)
    assert format_github_comment(diff, report, base) == (
        "✅ no downstream-impacting column changes"
    )
    assert diff.removed == [] and diff.type_changed == [] and diff.added == []


def test_github_comment_type_change():
    base, _ = chain_graphs()
    changed_id = column_node_id(FCT, "match_intensity")
    mutated = [
        n.model_copy(update={"data_type": "FLOAT"}) if n.node_id == changed_id else n
        for n in base.nodes
    ]
    candidate = Graph(nodes=mutated, edges=base.edges)
    diff, report = impact_from_graphs(base, candidate)
    comment = format_github_comment(diff, report, base)
    assert "⚠ 1 column type changed" in comment
    assert "fct_matches.match_intensity → type changed: NUMBER → FLOAT" in comment
    assert "#412 Match intensity by country  (Board dashboard, sverrir)" in comment


def test_github_comment_no_downstream_impact_block():
    base, candidate = chain_graphs()
    diff = ColumnDiff(removed=[column_node_id(FCT, "orphan")])
    report = ImpactReport(impacted={column_node_id(FCT, "orphan"): []})
    comment = format_github_comment(diff, report, base)
    assert "fct_matches.orphan → removed" in comment
    assert "└ no downstream impact found" in comment
    _ = candidate


def test_slack_comment_golden():
    base, candidate = chain_graphs()
    diff, report = impact_from_graphs(base, candidate)
    comment = format_slack_comment(diff, report, base)
    expected = (
        "*⚠ 1 column removed or renamed*\n"
        "\n"
        "*fct_matches.match_intensity* → removed\n"
        "• 1 downstream model: mart_engagement\n"
        "• 1 Metabase card:\n"
        "    • #412 Match intensity by country (Board dashboard, sverrir)\n"
        "\n"
        "_Renames appear as remove+add: a renamed column shows up here as removed._"
    )
    assert comment == expected


def test_slack_comment_empty_diff():
    base, _ = chain_graphs()
    diff, report = impact_from_graphs(base, base)
    assert format_slack_comment(diff, report, base) == ("✅ no downstream-impacting column changes")


def test_build_summary_counts_the_blast_radius():
    base, candidate = chain_graphs()
    diff, report = impact_from_graphs(base, candidate)
    assert format_build_summary(diff, report) == (
        "since last build: 1 column removed -> 1 card on 1 dashboard affected "
        "(run 'stitch impact' for the tree)"
    )


def test_build_summary_is_silent_when_nothing_changed():
    base, _ = chain_graphs()
    diff, report = impact_from_graphs(base, base)
    assert format_build_summary(diff, report) is None


def test_build_summary_reports_type_changes_and_additions():
    base, _ = chain_graphs()
    changed_id = column_node_id(FCT, "match_intensity")
    mutated = [
        n.model_copy(update={"data_type": "FLOAT"}) if n.node_id == changed_id else n
        for n in base.nodes
    ]
    candidate = Graph(nodes=[*mutated, col(FCT, "new_column")], edges=base.edges)
    diff, report = impact_from_graphs(base, candidate)
    assert format_build_summary(diff, report) == (
        "since last build: 1 type-changed, 1 added -> 1 card on 1 dashboard affected "
        "(run 'stitch impact' for the tree)"
    )


def test_build_summary_for_additions_only_has_no_blast_radius():
    base, _ = chain_graphs()
    candidate = Graph(nodes=[*base.nodes, col(FCT, "new_column")], edges=base.edges)
    diff, report = impact_from_graphs(base, candidate)
    assert format_build_summary(diff, report) == "since last build: 1 added"


def test_build_summary_says_when_nothing_downstream_is_hit():
    orphan = column_node_id(FCT, "orphan")
    diff = ColumnDiff(removed=[orphan])
    report = ImpactReport(impacted={orphan: []})
    assert format_build_summary(diff, report) == (
        "since last build: 1 column removed -> no Metabase cards affected "
        "(run 'stitch impact' for the tree)"
    )


def test_slack_comment_no_downstream_impact_block():
    base, _ = chain_graphs()
    diff = ColumnDiff(removed=[column_node_id(FCT, "orphan")])
    report = ImpactReport(impacted={column_node_id(FCT, "orphan"): []})
    comment = format_slack_comment(diff, report, base)
    assert "*fct_matches.orphan* → removed" in comment
    assert "• no downstream impact found" in comment


# --- point query: stitch impact --column (issue #86) ---------------------------------

KPI = "model.smitten.mart_board_kpis"


def fan_out_graph():
    """One column feeding two models, one of them bound through to two cards on a dashboard."""
    fct_col = column_node_id(FCT, "match_intensity")
    field = mb_field_node_id(101)
    nodes = [
        Node(
            node_id=FCT,
            node_type=NodeType.MODEL,
            name="fct_matches",
            schema_="MARTS",
            table="FCT_MATCHES",
        ),
        model(MART, "mart_engagement"),
        model(KPI, "mart_board_kpis"),
        col(FCT, "match_intensity"),
        col(FCT, "orphan"),
        col(MART, "match_intensity"),
        col(KPI, "match_intensity"),
        Node(node_id=field, node_type=NodeType.MB_FIELD, name="Match Intensity"),
        Node(
            node_id=mb_card_node_id(412),
            node_type=NodeType.MB_CARD,
            name="Match intensity by country",
            properties={"creator": "sverrir"},
        ),
        Node(node_id=mb_card_node_id(418), node_type=NodeType.MB_CARD, name="Weekly trend"),
        Node(node_id=mb_dashboard_node_id(9), node_type=NodeType.MB_DASHBOARD, name="Board"),
    ]
    edges = [
        edge(fct_col, column_node_id(MART, "match_intensity"), EdgeType.FEEDS),
        edge(fct_col, column_node_id(KPI, "match_intensity"), EdgeType.FEEDS),
        edge(column_node_id(MART, "match_intensity"), field, EdgeType.BINDS_TO),
        edge(field, mb_card_node_id(412), EdgeType.CONSUMED_BY),
        edge(field, mb_card_node_id(418), EdgeType.CONSUMED_BY),
        edge(mb_card_node_id(412), mb_dashboard_node_id(9), EdgeType.APPEARS_ON),
        # a relates_to edge must never widen the blast radius
        edge(fct_col, column_node_id(KPI, "unrelated"), EdgeType.RELATES_TO),
    ]
    return Graph(nodes=nodes, edges=edges)


def test_resolve_column_ref_accepts_model_column_and_node_id():
    graph = fan_out_graph()
    target = column_node_id(FCT, "match_intensity")
    for query in (
        "fct_matches.match_intensity",
        "FCT_MATCHES.MATCH_INTENSITY",
        f"{FCT}.match_intensity",
        "MARTS.FCT_MATCHES.match_intensity",
        target,
    ):
        assert resolve_column_ref(graph, query).node_id == target, query


def test_resolve_column_ref_accepts_unique_bare_column_name():
    graph = fan_out_graph()
    assert resolve_column_ref(graph, "orphan").node_id == column_node_id(FCT, "orphan")


def test_resolve_column_ref_reports_ambiguity_with_qualified_candidates():
    lookup = resolve_column_ref(fan_out_graph(), "match_intensity")
    assert lookup.node_id is None
    assert [ref.label for ref in lookup.candidates] == [
        "fct_matches.match_intensity",
        "mart_board_kpis.match_intensity",
        "mart_engagement.match_intensity",
    ]


def test_resolve_column_ref_suggests_near_misses():
    lookup = resolve_column_ref(fan_out_graph(), "fct_matches.match_intensety")
    assert lookup.node_id is None
    assert not lookup.candidates
    assert "fct_matches.match_intensity" in [ref.label for ref in lookup.suggestions]


def test_resolve_column_ref_on_a_model_offers_its_columns():
    lookup = resolve_column_ref(fan_out_graph(), "fct_matches")
    assert lookup.node_id is None
    assert lookup.matched_model == "fct_matches"
    assert [ref.label for ref in lookup.suggestions] == [
        "fct_matches.match_intensity",
        "fct_matches.orphan",
    ]


def test_resolve_column_ref_unknown_and_empty_queries():
    graph = fan_out_graph()
    assert resolve_column_ref(graph, "  ").node_id is None
    nothing = resolve_column_ref(graph, "zzz_no_such_thing")
    assert nothing.node_id is None and not nothing.candidates


def test_column_blast_radius_groups_the_whole_chain():
    graph = fan_out_graph()
    radius = column_blast_radius(graph, column_node_id(FCT, "match_intensity"))
    assert radius.label == "fct_matches.match_intensity"
    assert [ref.label for ref in radius.models] == ["mart_board_kpis", "mart_engagement"]
    assert [ref.label for ref in radius.columns] == [
        "mart_board_kpis.match_intensity",
        "mart_engagement.match_intensity",
    ]
    assert [ref.label for ref in radius.fields] == ["Match Intensity"]
    assert [(c.card_id, c.label, c.dashboards, c.owner) for c in radius.cards] == [
        (412, "Match intensity by country", ["Board"], "sverrir"),
        (418, "Weekly trend", [], None),
    ]
    assert [ref.label for ref in radius.dashboards] == ["Board"]
    assert not radius.truncated


def test_column_blast_radius_excludes_relates_to():
    graph = fan_out_graph()
    radius = column_blast_radius(graph, column_node_id(FCT, "match_intensity"))
    assert all("unrelated" not in ref.node_id for ref in radius.columns)


def test_column_blast_radius_of_a_leaf_column_is_empty():
    radius = column_blast_radius(fan_out_graph(), column_node_id(FCT, "orphan"))
    assert not (radius.models or radius.columns or radius.fields or radius.cards)
    assert format_blast_radius(radius) == "fct_matches.orphan\n  └ no downstream impact found"


def test_column_blast_radius_flags_truncation():
    radius = column_blast_radius(
        fan_out_graph(), column_node_id(FCT, "match_intensity"), max_depth=1
    )
    assert radius.truncated
    assert [ref.label for ref in radius.columns] == [
        "mart_board_kpis.match_intensity",
        "mart_engagement.match_intensity",
    ]
    assert not radius.cards
    assert "truncated at depth 1" in format_blast_radius(radius)


def test_format_blast_radius_renders_the_spec_tree():
    graph = fan_out_graph()
    radius = column_blast_radius(graph, column_node_id(FCT, "match_intensity"))
    assert format_blast_radius(radius) == (
        "fct_matches.match_intensity\n"
        "  ├ 2 downstream models: mart_board_kpis, mart_engagement\n"
        "  ├ 2 downstream columns:\n"
        "      mart_board_kpis.match_intensity\n"
        "      mart_engagement.match_intensity\n"
        "  ├ 1 Metabase field: Match Intensity\n"
        "  ├ 2 Metabase cards:\n"
        "      #412 Match intensity by country  (Board, sverrir)\n"
        "      #418 Weekly trend\n"
        "  └ 1 dashboard: Board"
    )
