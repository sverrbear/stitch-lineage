"""ERD scopes present in a graph (SPEC.md section 9).

The app groups the ERD one scope at a time -- by dbt schema or by dbt tag -- and
`serve.erd_default_scope` pins which one opens first. This mirrors the frontend's
`listScopes()` so `stitch serve` can tell a user their configured scope is a typo.
"""

from stitch_lineage.graph.schema import Graph, NodeType

_ERD_NODE_TYPES = (NodeType.MODEL, NodeType.SOURCE)


def erd_scopes(graph: Graph) -> set[str]:
    """Every `schema:<name>` / `tag:<name>` scope with at least one model or source."""
    scopes: set[str] = set()
    for node in graph.nodes:
        if node.node_type not in _ERD_NODE_TYPES:
            continue
        if node.schema_:
            scopes.add(f"schema:{node.schema_}")
        tags = node.properties.get("tags")
        if isinstance(tags, list):
            scopes.update(f"tag:{tag}" for tag in tags if str(tag))
    return scopes
