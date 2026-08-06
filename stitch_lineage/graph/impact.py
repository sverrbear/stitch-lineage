"""Impact analysis: diff two graphs, walk the downstream blast radius (SPEC.md section 10)."""

from pydantic import BaseModel, Field

from stitch_lineage.graph.schema import Graph, NodeType


class TypeChange(BaseModel):
    node_id: str
    old_type: str | None = None
    new_type: str | None = None


class ColumnDiff(BaseModel):
    """Column-level changes between two graphs, as column node ids.

    A rename surfaces as removed + added -- name-based ids cannot tell the difference,
    and the conservative reading is correct (the downstream card genuinely breaks
    until repointed). State this in output, never hide it.
    """

    removed: list[str] = Field(default_factory=list)
    type_changed: list[TypeChange] = Field(default_factory=list)
    added: list[str] = Field(default_factory=list)


class ImpactedNode(BaseModel):
    """One downstream node, with its shortest path from the start node."""

    node_id: str
    node_type: NodeType
    depth: int
    path: list[str] = Field(default_factory=list)


class ImpactReport(BaseModel):
    """Downstream fan-out per start node; each list sorted by (depth, node_id)."""

    start_node_ids: list[str] = Field(default_factory=list)
    impacted: dict[str, list[ImpactedNode]] = Field(default_factory=dict)
    max_depth: int = 20


def diff_columns(base: Graph, candidate: Graph) -> ColumnDiff:
    """Compare column nodes between the baseline and candidate graphs.

    removed: column node ids present in base, absent from candidate.
    type_changed: same node id in both, different data_type.
    added: present in candidate only.
    """
    raise NotImplementedError


def downstream(graph: Graph, start_node_ids: list[str], max_depth: int = 20) -> ImpactReport:
    """Recursive downstream walk over from -> to edges, EXCLUDING relates_to.

    Edge direction is pinned upstream -> downstream (see schema.Edge), so following
    from -> to is following data flow. Depth-capped at max_depth; a node reachable by
    several routes is deduped to its shortest path. Start ids missing from the graph
    yield an empty entry, not an error (a just-added column has no baseline edges).
    """
    raise NotImplementedError


def format_github_comment(diff: ColumnDiff, report: ImpactReport, graph: Graph) -> str:
    """Render the PR comment per SPEC.md section 10.

    Markdown: a headline count of removed/renamed columns, then per column a tree of
    downstream models and Metabase cards (card title, dashboard, creator), using
    `graph` to look up node names and properties. Renames stated as remove+add.
    Non-exact confidences flagged. Empty diff -> a short "no downstream impact" line.
    """
    raise NotImplementedError
