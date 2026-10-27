"""Conservative rename hints, never declarations or changes to the impact diff.

Pure snapshot evidence only. A candidate must be newly added in the same dbt owner,
have a compatible observed type and an exact, non-retired Metabase binding, plus a close
name or identical exact upstream lineage. Callers still validate live bindings when
planning. Ranking is a display order, not a probability or proof of business meaning.
"""

import re
from collections import defaultdict
from difflib import SequenceMatcher

from pydantic import BaseModel, Field

from stitch_lineage.graph.impact import column_display, diff_columns
from stitch_lineage.graph.schema import Confidence, DataTypeSource, EdgeType, Graph, Node, NodeType


class RenameCandidate(BaseModel):
    id: str
    label: str
    evidence: list[str]
    also_suggested_for: list[str] = Field(default_factory=list)


class RenameSuggestion(BaseModel):
    column: str
    candidates: list[RenameCandidate]
    ambiguous: bool = False
    total_candidates: int = 0


def _type_family(node: Node) -> str | None:
    if not node.data_type or node.data_type_source is DataTypeSource.INFERRED:
        return None
    value = re.sub(r"\s+", "", node.data_type.upper()).split("(", 1)[0]
    if value in {"", "UNKNOWN", "ANY", "NULL", "TYPE/*", "TYPE/UNKNOWN"}:
        return None
    aliases = {
        "number": {"NUMBER", "NUMERIC", "DECIMAL", "FIXED", "TYPE/DECIMAL", "TYPE/NUMBER"},
        "integer": {
            "INT",
            "INTEGER",
            "BIGINT",
            "SMALLINT",
            "TINYINT",
            "BYTEINT",
            "TYPE/INTEGER",
            "TYPE/BIGINTEGER",
        },
        "float": {"FLOAT", "FLOAT4", "FLOAT8", "DOUBLE", "DOUBLEPRECISION", "REAL", "TYPE/FLOAT"},
        "text": {"TEXT", "VARCHAR", "CHAR", "CHARACTER", "STRING", "TYPE/TEXT"},
        "boolean": {"BOOLEAN", "BOOL", "TYPE/BOOLEAN"},
        "date": {"DATE", "TYPE/DATE"},
    }
    for family, spellings in aliases.items():
        if value in spellings:
            return family
    # Keep timestamp zones and unfamiliar types distinct rather than guessing aliases.
    return value


def _name_evidence(old: Node, new: Node) -> str | None:
    def tokens(node: Node) -> list[str]:
        name = node.column or node.node_id.rpartition("::")[2]
        return re.findall(r"[a-z0-9]+", re.sub(r"([a-z])([A-Z])", r"\1_\2", name).lower())

    before, after = tokens(old), tokens(new)
    if not before or not after:
        return None
    if "".join(before) == "".join(after):
        return "Same name apart from word separators"
    common = set(before) & set(after)
    # An id/date/flag suffix alone is not evidence that two columns mean the same thing.
    meaningful = common - {"id", "key", "at", "is", "has", "date", "time", "flag", "code"}
    if meaningful and len(common) / len(set(before) | set(after)) >= 0.5:
        return f"Shared name words: {', '.join(sorted(meaningful))}"
    a, b = "_".join(before), "_".join(after)
    if min(len(a), len(b)) >= 5 and SequenceMatcher(None, a, b).ratio() >= 0.86:
        return "Closely matching column names"
    return None


def _exact_inputs(graph: Graph) -> dict[str, set[str]]:
    inputs: dict[str, set[str]] = defaultdict(set)
    uncertain = set()
    for edge in graph.edges:
        if edge.edge_type is EdgeType.FEEDS:
            if edge.confidence is Confidence.EXACT:
                inputs[edge.to].add(edge.from_)
            else:
                uncertain.add(edge.to)
    return {node: sources for node, sources in inputs.items() if node not in uncertain}


def suggest_renames(baseline: Graph, current: Graph) -> list[RenameSuggestion]:
    """Return up to three ranked hints per removed column, including ambiguity.

    Multiple plausible targets and targets shared by several removals stay ambiguous;
    truncation never turns a crowded candidate set into an apparently unique match.
    Existing columns and cross-model moves are deliberately left to explicit mapping.
    Neither graph is changed. The planner never consumes this output automatically.
    """
    diff = diff_columns(baseline, current)
    old_nodes = {node.node_id: node for node in baseline.nodes}
    new_nodes = {node.node_id: node for node in current.nodes}
    bound = {
        edge.from_
        for edge in current.edges
        if edge.edge_type is EdgeType.BINDS_TO
        and edge.confidence is Confidence.EXACT
        and (field := new_nodes.get(edge.to)) is not None
        and field.node_type is NodeType.MB_FIELD
        and field.properties.get("active") is not False
        and field.properties.get("visibility") != "retired"
        and (column := new_nodes.get(edge.from_)) is not None
        and (field.column or "").casefold() == (column.column or column.name).casefold()
    }
    additions: dict[str, list[Node]] = defaultdict(list)
    for nid in diff.added:
        if nid in bound:
            additions[nid.rpartition("::")[0]].append(new_nodes[nid])
    before_inputs, after_inputs = _exact_inputs(baseline), _exact_inputs(current)
    groups: list[RenameSuggestion] = []
    offered_to: dict[str, list[str]] = defaultdict(list)
    for old_id in diff.removed:
        old = old_nodes[old_id]
        owner = old_id.rpartition("::")[0]
        family = _type_family(old)
        ranked = []
        owner_exists = all(
            owner in nodes and nodes[owner].node_type in (NodeType.MODEL, NodeType.SOURCE)
            for nodes in (old_nodes, new_nodes)
        )
        for new in additions[owner] if owner_exists else []:
            if family is None or family != _type_family(new):
                continue
            name = _name_evidence(old, new)
            sources = before_inputs.get(old_id)
            shared_lineage = bool(sources and sources == after_inputs.get(new.node_id))
            if not name and not shared_lineage:
                continue
            evidence = [
                "Added in the same dbt relation",
                f"Compatible snapshot types: {old.data_type} → {new.data_type}",
            ]
            if shared_lineage:
                labels = sorted(column_display(old_nodes, nid) for nid in sources)
                evidence.append(f"Same exact upstream columns: {', '.join(labels)}")
            if name:
                evidence.append(name)
            evidence.append("Exact Metabase field binding in the current snapshot")
            candidate = RenameCandidate(
                id=new.node_id, label=column_display(new_nodes, new.node_id), evidence=evidence
            )
            ranked.append((not shared_lineage, not bool(name), new.node_id, candidate))
            offered_to[new.node_id].append(old_id)
        candidates = [item[-1] for item in sorted(ranked)]
        groups.append(
            RenameSuggestion(column=old_id, candidates=candidates, total_candidates=len(candidates))
        )
    for group in groups:
        for candidate in group.candidates:
            candidate.also_suggested_for = [
                column_display(old_nodes, nid)
                for nid in offered_to[candidate.id]
                if nid != group.column
            ]
        group.ambiguous = group.total_candidates > 1 or any(
            c.also_suggested_for for c in group.candidates
        )
        group.candidates = group.candidates[:3]
    return groups
