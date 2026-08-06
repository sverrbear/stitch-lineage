"""Search everything in graph.json (SPEC.md section 9, CLI from Phase 0)."""

from difflib import SequenceMatcher

from pydantic import BaseModel

from stitch_lineage.graph.schema import Graph, Node, NodeType

_FUZZY_FLOOR = 0.6


class SearchResult(BaseModel):
    """One ranked hit. context is a compact locator: the model name for a column,
    the collection for a card/dashboard, schema.table for fields and models."""

    node_id: str
    node_type: NodeType
    name: str
    score: float = 0.0
    matched_field: str = "name"
    context: str | None = None


def _id_tail(node_id: str) -> str:
    tail = node_id.rpartition("::")[2]
    return tail.rpartition(".")[2]


def _searchable_text(node: Node) -> list[tuple[str, str]]:
    fields = [("name", node.name), ("node_id", _id_tail(node.node_id))]
    if node.description:
        fields.append(("description", node.description))
    for key in ("title", "collection_name", "collection"):
        value = node.properties.get(key)
        if isinstance(value, str) and value:
            fields.append((f"properties.{key}", value))
    tags = node.properties.get("tags")
    if isinstance(tags, list) and tags:
        fields.append(("properties.tags", " ".join(str(tag) for tag in tags)))
    return fields


def _starts_word(text: str, query: str) -> bool:
    idx = text.find(query)
    while idx > 0:
        if not text[idx - 1].isalnum():
            return True
        idx = text.find(query, idx + 1)
    return idx == 0


def _match(node: Node, query: str) -> tuple[int, str, float] | None:
    name = node.name.casefold()
    if name == query:
        return 5, "name", 5.0
    if name.startswith(query):
        return 4, "name", 4.0
    if _starts_word(name, query):
        return 3, "name", 3.0
    for field, value in _searchable_text(node):
        if query in value.casefold():
            return 2, field, 2.0
    ratio = SequenceMatcher(None, query, name).ratio()
    if ratio >= _FUZZY_FLOOR:
        return 1, "name", round(ratio, 3)
    return None


def _context(node: Node, nodes_by_id: dict[str, Node]) -> str | None:
    if node.node_type is NodeType.COLUMN:
        owner, sep, _ = node.node_id.rpartition("::")
        if not sep:
            return None
        parent = nodes_by_id.get(owner)
        return parent.name if parent else owner.rsplit(".", 1)[-1]
    if node.node_type in (NodeType.MB_CARD, NodeType.MB_DASHBOARD):
        for key in ("collection_name", "collection"):
            value = node.properties.get(key)
            if isinstance(value, str) and value:
                return value
        return None
    if node.table:
        return f"{node.schema_}.{node.table}" if node.schema_ else node.table
    return None


def search(graph: Graph, query: str, limit: int = 20) -> list[SearchResult]:
    """Rank nodes against a free-text query.

    Searches every node type by name, node_id tail, description and properties
    (tags, title, collection name). Ranking tiers: exact name > name prefix >
    word-boundary in name > substring in any field > fuzzy (difflib ratio >= 0.6
    against the name). Within a tier results sort by score descending (which orders
    fuzzy hits by ratio), then name and node_id for determinism. Case-insensitive.
    Returns at most `limit` results, best first, with matched_field naming which
    attribute matched.
    """
    needle = query.strip().casefold()
    if not needle:
        return []
    nodes_by_id = {node.node_id: node for node in graph.nodes}
    hits: list[tuple[int, Node, str, float]] = []
    for node in graph.nodes:
        match = _match(node, needle)
        if match is not None:
            hits.append((match[0], node, match[1], match[2]))
    hits.sort(key=lambda hit: (-hit[0], -hit[3], hit[1].name.casefold(), hit[1].node_id))
    return [
        SearchResult(
            node_id=node.node_id,
            node_type=node.node_type,
            name=node.name,
            score=score,
            matched_field=field,
            context=_context(node, nodes_by_id),
        )
        for _tier, node, field, score in hits[:limit]
    ]
