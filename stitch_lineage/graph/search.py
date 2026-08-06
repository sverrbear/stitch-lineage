"""Search everything in graph.json (SPEC.md section 9, CLI from Phase 0)."""

from pydantic import BaseModel

from stitch_lineage.graph.schema import Graph, NodeType


class SearchResult(BaseModel):
    node_id: str
    node_type: NodeType
    name: str
    score: float = 0.0
    matched_field: str = "name"


def search(graph: Graph, query: str, limit: int = 20) -> list[SearchResult]:
    """Rank nodes against a free-text query.

    Searches every node type by name, description and properties (tags, card/dashboard
    titles). Ranking: exact-prefix > word-boundary > fuzzy; ties broken by node_id for
    determinism. Case-insensitive. Returns at most `limit` results, best first, with
    matched_field naming which attribute matched.
    """
    raise NotImplementedError
