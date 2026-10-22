"""Choose a useful first column from actual downstream report dependencies."""

from collections import deque

from stitch_lineage.graph.schema import EdgeType, Graph, NodeType


def first_impact_column(graph: Graph) -> str | None:
    """Nearest column feeding an active card, with stable ties and impact's depth bound.

    One reverse walk keeps this linear even for large graphs. Relationships are not
    data flow. No result means open search; never pretend an unbound column has impact.
    """
    nodes = {node.node_id: node for node in graph.nodes}
    upstream: dict[str, list[str]] = {}
    for edge in graph.edges:
        if edge.edge_type is not EdgeType.RELATES_TO:
            upstream.setdefault(edge.to, []).append(edge.from_)
    starts = sorted(
        node.node_id
        for node in graph.nodes
        if node.node_type is NodeType.MB_CARD and not node.properties.get("archived")
    )
    seen = set(starts)
    queue = deque((node_id, 0) for node_id in starts)
    while queue:
        node_id, depth = queue.popleft()
        node = nodes.get(node_id)
        if node and node.node_type is NodeType.COLUMN:
            return node_id
        if depth >= 20:
            continue
        for parent in sorted(upstream.get(node_id, [])):
            if parent not in seen:
                seen.add(parent)
                queue.append((parent, depth + 1))
    return None
