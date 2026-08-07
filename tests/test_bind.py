from stitch_lineage.graph.schema import (
    Confidence,
    EdgeType,
    Node,
    NodeType,
    column_node_id,
    mb_field_node_id,
)
from stitch_lineage.resolve.bind import bind

MAP = [("Analytics", "ANALYTICS")]
FCT = "model.smitten.fct_matches"


def make_model(uid=FCT, name="fct_matches", database="ANALYTICS", schema="MARTS", table=None):
    return Node(
        node_id=uid,
        node_type=NodeType.MODEL,
        name=name,
        database=database,
        schema_=schema,
        table=table if table is not None else name.upper(),
    )


def make_field(field_id, column, table, schema="MARTS", database="Analytics"):
    return Node(
        node_id=mb_field_node_id(field_id),
        node_type=NodeType.MB_FIELD,
        name=column,
        database=database,
        schema_=schema,
        table=table,
        column=column,
    )


def make_column(uid, name, column=None):
    return Node(
        node_id=column_node_id(uid, name),
        node_type=NodeType.COLUMN,
        name=name,
        column=column if column is not None else name.upper(),
    )


def test_exact_bind():
    dbt_nodes = [make_model(), make_column(FCT, "match_intensity")]
    result = bind(dbt_nodes, [make_field(101, "MATCH_INTENSITY", "FCT_MATCHES")], MAP)
    assert len(result.edges) == 1
    edge = result.edges[0]
    assert edge.from_ == column_node_id(FCT, "match_intensity")
    assert edge.to == mb_field_node_id(101)
    assert edge.edge_type == EdgeType.BINDS_TO
    assert edge.confidence == Confidence.EXACT
    assert edge.evidence == {}
    assert result.models_bound == 1
    assert result.models_total == 1
    assert result.unbound_models == []
    assert result.case_mismatch_count == 0
    assert result.unverified_field_count == 0


def test_case_only_mismatch_stays_exact_with_evidence():
    model = make_model(table="fct_matches")
    dbt_nodes = [model, make_column(FCT, "match_intensity")]
    result = bind(dbt_nodes, [make_field(101, "MATCH_INTENSITY", "FCT_MATCHES")], MAP)
    assert len(result.edges) == 1
    edge = result.edges[0]
    assert edge.confidence == Confidence.EXACT
    assert edge.evidence == {"case_mismatch": True}
    assert result.case_mismatch_count == 1


def test_database_display_name_mapping():
    mapping = [("Analytics Warehouse", "ANALYTICS")]
    fields = [
        make_field(101, "MATCH_ID", "FCT_MATCHES", database="Analytics Warehouse"),
        make_field(102, "MATCH_ID", "FCT_MATCHES", database="Unmapped BI"),
    ]
    result = bind([make_model(), make_column(FCT, "match_id")], fields, mapping)
    assert [edge.to for edge in result.edges] == [mb_field_node_id(101)]


def test_unverified_columns_skipped_and_counted():
    # bound table but no column inventory: no fabricated edges, honest counter
    result = bind([make_model()], [make_field(101, "MATCH_INTENSITY", "FCT_MATCHES")], MAP)
    assert result.edges == []
    assert result.models_bound == 1
    assert result.unverified_field_count == 1


def test_no_cross_table_guessing():
    result = bind([make_model()], [make_field(101, "MATCH_ID", "FCT_SWIPES")], MAP)
    assert result.edges == []
    assert result.models_bound == 0
    assert result.unbound_models == [FCT]


def test_unbound_model_coverage_excludes_sources():
    source = Node(
        node_id="source.smitten.amplitude.events",
        node_type=NodeType.SOURCE,
        name="events",
        database="ANALYTICS",
        schema_="AMPLITUDE",
        table="EVENTS",
    )
    dbt_nodes = [
        make_model(),
        make_model(uid="model.smitten.fct_swipes", name="fct_swipes"),
        source,
        make_column(FCT, "match_id"),
        make_column("source.smitten.amplitude.events", "event_id"),
    ]
    fields = [
        make_field(101, "MATCH_ID", "FCT_MATCHES"),
        make_field(202, "EVENT_ID", "EVENTS", schema="AMPLITUDE"),
    ]
    result = bind(dbt_nodes, fields, MAP)
    assert result.models_total == 2
    assert result.models_bound == 1
    assert result.unbound_models == ["model.smitten.fct_swipes"]
    source_edges = [edge for edge in result.edges if edge.to == mb_field_node_id(202)]
    assert len(source_edges) == 1
    assert source_edges[0].from_ == column_node_id("source.smitten.amplitude.events", "event_id")


def test_column_nodes_gate_binding():
    dbt_nodes = [make_model(), make_column(FCT, "user_id")]
    result = bind(dbt_nodes, [make_field(101, "NOT_A_COLUMN", "FCT_MATCHES")], MAP)
    assert result.edges == []
    assert result.models_bound == 1  # table matched even though the column did not


def test_column_case_mismatch_detected_via_column_nodes():
    quoted_col = make_column(FCT, "user_id", column="user_id")
    result = bind([make_model(), quoted_col], [make_field(101, "USER_ID", "FCT_MATCHES")], MAP)
    edge = result.edges[0]
    assert edge.confidence == Confidence.EXACT
    assert edge.evidence == {"case_mismatch": True}
    assert result.case_mismatch_count == 1


def test_fuzzy_underscore_fold():
    dbt_nodes = [make_model(), make_column(FCT, "user_id")]
    result = bind(dbt_nodes, [make_field(101, "USERID", "FCT_MATCHES")], MAP)
    assert len(result.edges) == 1
    edge = result.edges[0]
    assert edge.confidence == Confidence.FUZZY
    assert edge.from_ == column_node_id(FCT, "user_id")
    assert edge.evidence["dbt_column"] == "USER_ID"
    assert edge.evidence["mb_column"] == "USERID"
    assert result.case_mismatch_count == 0


def test_fuzzy_ambiguous_binds_nothing():
    dbt_nodes = [make_model(), make_column(FCT, "user_id"), make_column(FCT, "u_serid")]
    result = bind(dbt_nodes, [make_field(101, "USERID", "FCT_MATCHES")], MAP)
    assert result.edges == []


# --- table_prefix strip -----------------------------------------------------

PREFIX_MAP = [("Analytics", "ANALYTICS", "sis_")]


def test_table_prefix_strip_binds_dev_dbt_to_prod_metabase():
    # dev-target artifacts: physical table SIS_FCT_MATCHES; Metabase points at prod
    dbt_nodes = [make_model(table="SIS_FCT_MATCHES"), make_column(FCT, "match_intensity")]
    result = bind(dbt_nodes, [make_field(101, "MATCH_INTENSITY", "FCT_MATCHES")], PREFIX_MAP)
    assert len(result.edges) == 1
    edge = result.edges[0]
    assert edge.from_ == column_node_id(FCT, "match_intensity")
    assert edge.to == mb_field_node_id(101)
    assert edge.confidence == Confidence.EXACT
    assert edge.evidence == {"table_prefix_stripped": "sis_"}
    assert result.models_bound == 1
    assert result.case_mismatch_count == 0


def test_table_prefix_strip_is_case_insensitive():
    mapping = [("Analytics", "ANALYTICS", "SIS_")]
    dbt_nodes = [make_model(table="sis_FCT_MATCHES"), make_column(FCT, "match_id")]
    result = bind(dbt_nodes, [make_field(101, "MATCH_ID", "FCT_MATCHES")], mapping)
    assert len(result.edges) == 1
    assert result.edges[0].evidence == {"table_prefix_stripped": "SIS_"}
    assert result.case_mismatch_count == 0


def test_table_prefix_is_anchored_never_substring_stripped():
    dbt_nodes = [make_model(table="FCT_SIS_MATCHES"), make_column(FCT, "match_id")]
    result = bind(dbt_nodes, [make_field(101, "MATCH_ID", "FCT_MATCHES")], PREFIX_MAP)
    assert result.edges == []
    assert result.models_bound == 0
    assert result.unbound_models == [FCT]


def test_table_prefix_nonmatching_leaves_binding_unchanged():
    dbt_nodes = [make_model(table="FCT_MATCHES"), make_column(FCT, "match_id")]
    result = bind(dbt_nodes, [make_field(101, "MATCH_ID", "FCT_MATCHES")], PREFIX_MAP)
    assert len(result.edges) == 1
    assert result.edges[0].evidence == {}  # bound without stripping


def test_table_prefix_exact_table_match_wins_over_strip():
    plain = make_model(uid=FCT, name="fct_matches", table="FCT_MATCHES")
    prefixed = make_model(
        uid="model.smitten.sis_fct_matches", name="sis_fct_matches", table="SIS_FCT_MATCHES"
    )
    dbt_nodes = [
        plain,
        prefixed,
        make_column(FCT, "match_id"),
        make_column("model.smitten.sis_fct_matches", "match_id"),
    ]
    result = bind(dbt_nodes, [make_field(101, "MATCH_ID", "FCT_MATCHES")], PREFIX_MAP)
    assert len(result.edges) == 1
    assert result.edges[0].from_ == column_node_id(FCT, "match_id")
    assert result.edges[0].evidence == {}


def test_table_prefix_strip_keeps_case_mismatch_evidence():
    dbt_nodes = [make_model(table="sis_fct_matches"), make_column(FCT, "match_id")]
    result = bind(dbt_nodes, [make_field(101, "MATCH_ID", "FCT_MATCHES")], PREFIX_MAP)
    assert len(result.edges) == 1
    edge = result.edges[0]
    assert edge.confidence == Confidence.EXACT
    assert edge.evidence == {"table_prefix_stripped": "sis_", "case_mismatch": True}
    assert result.case_mismatch_count == 1


def test_two_tuple_database_map_still_supported():
    dbt_nodes = [make_model(table="SIS_FCT_MATCHES"), make_column(FCT, "match_id")]
    result = bind(dbt_nodes, [make_field(101, "MATCH_ID", "FCT_MATCHES")], MAP)
    assert result.edges == []  # no prefix configured -> no strip
    assert result.unbound_models == [FCT]
