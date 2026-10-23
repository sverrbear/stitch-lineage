"""Read-only browser explanations. Annotated queries are never repair payloads."""

import json
import re
from typing import Any

from stitch_lineage.graph.impact import column_display
from stitch_lineage.graph.schema import EdgeType, Graph, NodeType
from stitch_lineage.mend.models import MendPlan
from stitch_lineage.mend.render import format_diff
from stitch_lineage.mend.rewrite import query_clause_values
from stitch_lineage.resolve.metabase import ref_parts

_ROLES = {
    "aggregation": "Calculation",
    "filter": "Filter",
    "breakout": "Grouping",
    "fields": "Displayed fields",
    "expressions": "Custom calculation",
    "order-by": "Sorting",
    "joins.condition": "Join condition",
    "joins.fields": "Joined fields",
}
_FUNCTIONS = {
    "sum": "Sum of",
    "avg": "Average of",
    "min": "Minimum of",
    "max": "Maximum of",
    "distinct": "Distinct count of",
}
_INFIX = {"=", "!=", ">", ">=", "<", "<=", "+", "-", "*", "/", "and", "or"}


def _labels(graph: Graph) -> dict[int, str]:
    nodes = {node.node_id: node for node in graph.nodes}
    labels = {
        int(node.node_id.split("::")[-1]): ".".join(
            part for part in (node.table, node.column or node.name) if part
        )
        for node in graph.nodes
        if node.node_type is NodeType.MB_FIELD and node.node_id.split("::")[-1].isdigit()
    }
    bound: dict[int, set[str]] = {}
    for edge in graph.edges:
        if edge.edge_type is EdgeType.BINDS_TO and edge.to.split("::")[-1].isdigit():
            bound.setdefault(int(edge.to.split("::")[-1]), set()).add(
                column_display(nodes, edge.from_)
            )
    for field_id, names in bound.items():
        if len(names) == 1:
            labels[field_id] = next(iter(names))
    return labels


def _field_name(target: Any, labels: dict[int, str]) -> str:
    if isinstance(target, int):
        return labels.get(target, f"Unknown field #{target}")
    return str(target)


def named_query(value: Any, labels: dict[int, str]) -> Any:
    """Copy the structure, annotating only field references, never numeric literals."""
    if isinstance(value, list):
        result = [named_query(item, labels) for item in value]
        if value and value[0] == "field":
            target, opts = ref_parts(value)
            position = 2 if len(value) > 2 and isinstance(value[1], dict) else 1
            if position < len(value) and isinstance(target, int):
                result[position] = f"{_field_name(target, labels)} [field {target}]"
            if opts and isinstance(opts.get("source-field"), int):
                opt_position = 1 if position == 2 else 2
                source = opts["source-field"]
                result[opt_position]["source-field"] = (
                    f"{_field_name(source, labels)} [field {source}]"
                )
        return result
    if isinstance(value, dict):
        return {key: named_query(item, labels) for key, item in value.items()}
    return value


def _expression(value: Any, labels: dict[int, str]) -> str | None:
    """Describe a small, known vocabulary; unknown semantics retain their structure."""
    if not isinstance(value, list):
        return None if isinstance(value, dict) else json.dumps(value, ensure_ascii=False)
    if not value:
        return "None"
    if not isinstance(value[0], str):
        parts = [_expression(item, labels) for item in value]
        return "; ".join(parts) if all(part is not None for part in parts) else None
    op = value[0]
    if op == "field":
        target, opts = ref_parts(value)
        # A bucketing, join or other option can change meaning. Do not hide it.
        if opts and set(opts) - {"base-type", "lib/uuid"}:
            return None
        return _field_name(target, labels)
    args = value[1:]
    if args and isinstance(args[0], dict):
        if set(args[0]) - {"lib/uuid"}:
            return None
        args = args[1:]
    parts = [_expression(arg, labels) for arg in args]
    if any(part is None for part in parts):
        return None
    if op == "count" and not args:
        return "Count of rows"
    if op in _FUNCTIONS and len(parts) == 1:
        return f"{_FUNCTIONS[op]} {parts[0]}"
    if op in _INFIX and (len(parts) == 2 or (op in {"and", "or"} and len(parts) > 2)):
        return "(" + f" {op} ".join(parts) + ")"
    if op in {"asc", "desc"} and len(parts) == 1:
        return f"{parts[0]} ({'ascending' if op == 'asc' else 'descending'})"
    if op == "dimension" and len(parts) == 1:
        return parts[0]
    return None


def _role(label: str) -> str:
    role = next(
        (_ROLES[key] for key in sorted(_ROLES, key=len, reverse=True) if label.endswith(key)), label
    )
    steps = [f"Step {int(step) + 1}" for step in re.findall(r"stage(\d+)\.", label)]
    if "source-query" in label:
        steps.insert(0, "Source query")
    return " · ".join([*steps, role])


def _change(label: str, before: Any, after: Any, old: dict, new: dict) -> dict[str, Any]:
    left, right = _expression(before, old), _expression(after, new)
    return {
        "label": label,
        "before": left if left is not None else json.dumps(named_query(before, old), indent=2),
        "after": right if right is not None else json.dumps(named_query(after, new), indent=2),
        "plain": left is not None and right is not None,
    }


def explain_plan(plan: MendPlan, baseline: Graph, current: Graph) -> dict[str, Any]:
    """Presentation alongside the exact plan/diff; no change to approval or apply."""
    baseline_labels, current_labels = _labels(baseline), _labels(current)
    reviews = {}
    for card in plan.cards:
        if card.after is None:
            continue
        old, new = dict(baseline_labels), baseline_labels | current_labels
        for ref in card.dead_refs:
            if ref.field_id is not None:
                old[ref.field_id] = ref.column
            if ref.new_field_id is not None and ref.rename_to:
                new[ref.new_field_id] = ref.rename_to
        before = query_clause_values(card.before or {})
        after = query_clause_values(card.after)
        changes = [
            _change(_role(label), before.get(label, []), after.get(label, []), old, new)
            for label in dict.fromkeys([*before, *after])
            if before.get(label) != after.get(label)
        ]
        dashboards = []
        for edit in card.dashcards:
            changes.append(
                _change(
                    f"Dashboard filter · {edit.dashboard_name}",
                    [mapping.get("target") for mapping in edit.before],
                    [mapping.get("target") for mapping in edit.after],
                    old,
                    new,
                )
            )
            dashboards.append(
                {
                    "dashboard_id": edit.dashboard_id,
                    "dashcard_id": edit.dashcard_id,
                    "before": named_query(edit.before, old),
                    "after": named_query(edit.after, new),
                }
            )
        reviews[str(card.card_id)] = {
            "changes": changes,
            "query_diff": format_diff(
                named_query(card.before, old), named_query(card.after, new), card.name
            ),
            "dashboards": dashboards,
        }
    return reviews
