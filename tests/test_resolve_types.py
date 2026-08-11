"""The data type waterfall: catalog > metabase > inferred > unknown (issue #149)."""

import pytest

from stitch_lineage.graph.schema import (
    Confidence,
    DataTypeSource,
    Edge,
    EdgeType,
    Node,
    NodeType,
    column_node_id,
    mb_field_node_id,
)
from stitch_lineage.resolve.types import apply_type_waterfall

MODEL = "model.demo.fct_orders"
COLUMN = column_node_id(MODEL, "order_total")
FIELD = mb_field_node_id(102)


def column(node_id=COLUMN, data_type=None, source=None, properties=None):
    return Node(
        node_id=node_id,
        node_type=NodeType.COLUMN,
        name=node_id.rpartition("::")[2],
        column=node_id.rpartition("::")[2],
        data_type=data_type,
        data_type_source=source,
        properties=properties or {},
    )


def field(node_id=FIELD, base_type="type/Float", database_type="FLOAT"):
    properties = {} if database_type is None else {"database_type": database_type}
    return Node(
        node_id=node_id,
        node_type=NodeType.MB_FIELD,
        name="Order Total",
        column="order_total",
        data_type=base_type,
        properties=properties,
    )


def binds(from_=COLUMN, to=FIELD, confidence=Confidence.EXACT):
    return Edge(from_=from_, to=to, edge_type=EdgeType.BINDS_TO, confidence=confidence)


def only(result, node_id=COLUMN):
    return next(n for n in result.nodes if n.node_id == node_id)


def test_catalog_type_wins_over_metabase():
    # the dev built this relation: its own warehouse's answer outranks the BI tool's copy
    result = apply_type_waterfall(
        [column(data_type="NUMBER(38,0)", source=DataTypeSource.CATALOG), field()],
        [binds()],
    )
    node = only(result)
    assert node.data_type == "NUMBER(38,0)"
    assert node.data_type_source is DataTypeSource.CATALOG
    assert (result.from_catalog, result.from_metabase, result.unknown) == (1, 0, 0)


def test_metabase_fills_a_column_the_catalog_never_built():
    result = apply_type_waterfall([column(), field()], [binds()])
    node = only(result)
    assert node.data_type == "FLOAT"
    assert node.data_type_source is DataTypeSource.METABASE
    assert (result.from_metabase, result.unknown) == (1, 0)


def test_metabase_beats_inferred():
    result = apply_type_waterfall([column(), field()], [binds()], inferred_types={COLUMN: "DOUBLE"})
    assert only(result).data_type == "FLOAT"
    assert only(result).data_type_source is DataTypeSource.METABASE
    assert result.from_inferred == 0


def test_inferred_applies_only_when_nothing_else_answered():
    result = apply_type_waterfall([column()], [], inferred_types={COLUMN: "DOUBLE"})
    node = only(result)
    assert node.data_type == "DOUBLE"
    assert node.data_type_source is DataTypeSource.INFERRED
    assert (result.from_inferred, result.unknown) == (1, 0)


def test_unknown_stays_unknown_when_no_source_has_it():
    result = apply_type_waterfall([column()], [])
    node = only(result)
    assert node.data_type is None
    # absence, not a fourth provenance value pretending to be one
    assert node.data_type_source is None
    assert result.unknown == 1


def test_unbound_column_skips_the_metabase_step():
    # a field exists, but nothing binds this column to it -- never borrow a type by name
    result = apply_type_waterfall([column(), field()], [])
    assert only(result).data_type is None
    assert result.unknown == 1


def test_fuzzy_binding_does_not_assert_a_type():
    """A fuzzy bind matched on squashed underscores/case: good enough to draw a lineage
    edge, not good enough to state a fact about a column we know we guessed at."""
    result = apply_type_waterfall([column(), field()], [binds(confidence=Confidence.FUZZY)])
    assert only(result).data_type is None
    assert result.unknown == 1


def test_database_type_preferred_over_base_type():
    result = apply_type_waterfall([column(), field(database_type="NUMBER(38,0)")], [binds()])
    assert only(result).data_type == "NUMBER(38,0)"


def test_base_type_stands_in_when_the_sync_recorded_no_database_type():
    result = apply_type_waterfall([column(), field(database_type=None)], [binds()])
    node = only(result)
    assert node.data_type == "type/Float"
    assert node.data_type_source is DataTypeSource.METABASE


def test_field_with_no_type_at_all_leaves_the_column_unknown():
    result = apply_type_waterfall([column(), field(base_type=None, database_type=None)], [binds()])
    assert only(result).data_type is None
    assert result.unknown == 1


@pytest.mark.parametrize("order", [(103, 104), (104, 103)])
def test_multiple_exact_bindings_resolve_deterministically(order):
    """Two Metabase connections onto one warehouse: the answer must not depend on
    which edge the resolver happened to emit first."""
    fields = [
        field(node_id=mb_field_node_id(103), database_type="NUMBER(38,0)"),
        field(node_id=mb_field_node_id(104), database_type="FLOAT"),
    ]
    edges = [binds(to=mb_field_node_id(i)) for i in order]
    result = apply_type_waterfall([column(), *fields], edges)
    assert only(result).data_type == "NUMBER(38,0)"  # mb_field::103 sorts first


def test_filling_a_type_clears_the_reason_it_was_unknown():
    """`unknown_type_reason` explains an ABSENT type -- a node carrying both a FLOAT and
    a reason it has no type renders as a contradiction in the app."""
    result = apply_type_waterfall(
        [column(properties={"unknown_type_reason": "relation_not_in_catalog"}), field()],
        [binds()],
    )
    node = only(result)
    assert node.data_type == "FLOAT"
    assert "unknown_type_reason" not in node.properties


def test_unknown_column_keeps_the_reason_it_was_unknown():
    result = apply_type_waterfall(
        [column(properties={"unknown_type_reason": "relation_not_in_catalog"})], []
    )
    assert only(result).properties["unknown_type_reason"] == "relation_not_in_catalog"


def test_other_node_types_pass_through_untouched():
    nodes = [
        Node(node_id=MODEL, node_type=NodeType.MODEL, name="fct_orders"),
        field(),
    ]
    result = apply_type_waterfall(nodes, [binds()])
    assert result.nodes == nodes
    assert (result.from_catalog, result.from_metabase, result.unknown) == (0, 0, 0)


def test_counts_cover_every_column_exactly_once():
    nodes = [
        column(node_id=column_node_id(MODEL, "a"), data_type="INT", source=DataTypeSource.CATALOG),
        column(node_id=column_node_id(MODEL, "b")),
        column(node_id=column_node_id(MODEL, "c")),
        column(node_id=column_node_id(MODEL, "d")),
        field(),
    ]
    edges = [binds(from_=column_node_id(MODEL, "b"))]
    result = apply_type_waterfall(nodes, edges, {column_node_id(MODEL, "c"): "DOUBLE"})
    assert (result.from_catalog, result.from_metabase, result.from_inferred, result.unknown) == (
        1,
        1,
        1,
        1,
    )
