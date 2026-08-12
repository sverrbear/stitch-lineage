"""ERD scopes present in a graph, and what the ERD draws as a table (SPEC.md section 9).

The app groups the ERD one scope at a time -- by dbt schema or by dbt tag -- and
`serve.erd_default_scope` pins which one opens first. This mirrors the frontend's
`listScopes()` so `stitch serve` can tell a user their configured scope is a typo.
"""

from stitch_lineage.graph.schema import Graph, Node, NodeType

__all__ = ["NON_TABLE_MATERIALIZATIONS", "erd_scopes", "is_erd_table"]

_ERD_NODE_TYPES = (NodeType.MODEL, NodeType.SOURCE)

# Materializations that are not relations, so the ERD never draws them (#191).
# A Snowflake semantic view is a semantic-layer DEFINITION -- tables, joins and
# metrics declared as DDL over the facts and dims underneath it -- not a table
# with columns you join on, so an ERD card would state a shape it does not have.
# The node itself stays: its `references` are real lineage, and it keeps its
# search hit, its node page and its place in impact.
NON_TABLE_MATERIALIZATIONS = frozenset({"semantic_view"})


def is_erd_table(node: Node) -> bool:
    """Whether the ERD draws this node as a table card.

    The one rule for ERD membership on this side of the wire -- scope listing here,
    and `graph/suggest.py`, which proposes edges between exactly these. It mirrors
    the frontend's `isErdTable()` in `lib/erd.ts`; the two must agree, or the CLI
    offers a scope the canvas then refuses to open.
    """
    if node.node_type not in _ERD_NODE_TYPES:
        return False
    return node.properties.get("materialization") not in NON_TABLE_MATERIALIZATIONS


def erd_scopes(graph: Graph) -> set[str]:
    """Every `schema:<name>` / `tag:<name>` scope with at least one ERD table."""
    scopes: set[str] = set()
    for node in graph.nodes:
        if not is_erd_table(node):
            continue
        if node.schema_:
            scopes.add(f"schema:{node.schema_}")
        tags = node.properties.get("tags")
        if isinstance(tags, list):
            scopes.update(f"tag:{tag}" for tag in tags if str(tag))
    return scopes
