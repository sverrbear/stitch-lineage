"""Rewriting MBQL: the inverse of resolve/metabase.py's walk (SPEC.md section 14).

The resolver answers "which fields does this card read". This module answers "what does
this card look like once one of those fields is gone" -- repointed at a renamed field, or
with the dead clause cut out. It therefore reuses the resolver's shape vocabulary
(CLAUSE_KEYS, STAGE_CLAUSE_LABELS, ref_parts, collect_refs, query_kind) rather than
carrying a second opinion about where a field ref can hide: two walkers would diverge on
the first Metabase upgrade, and the one that diverged silently would be this one.

Pure: dicts in, dicts out. No HTTP, no filesystem, no config. Both dataset_query shapes
are handled by the same code paths -- legacy nested `query`/`source-query` and MBQL 5
`stages` -- because a card's shape is a Metabase version artifact, not a difference in
what the repair means.
"""

from collections.abc import Callable, Iterator
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from stitch_lineage.resolve.metabase import (
    CLAUSE_KEYS,
    NATIVE_QUERY,
    STAGE_CLAUSE_LABELS,
    STAGE_QUERY,
    Ref,
    collect_refs,
    is_native_stage,
    query_kind,
    ref_parts,
)

# The essentialness rule (issue #143), stated once: a clause is essential if removing it
# changes what the card IS ABOUT, not just how much it shows.
#
#   filter, order-by      one criterion among the criteria -- how much, not what
#   fields, breakout,     essential only when the dead reference is the SOLE member:
#   aggregation,          the sole aggregation or the sole dimension IS the card
#   joins.fields
#   expressions,          a custom column is a definition other clauses may build on,
#   joins.condition       and a join condition decides which rows exist at all
_NEVER_ESSENTIAL = frozenset({"filter", "order-by"})
_SOLE_MEMBER_ESSENTIAL = frozenset({"fields", "breakout", "aggregation", "joins.fields"})
_ALWAYS_ESSENTIAL = frozenset({"expressions", "joins.condition"})

_LIST_CLAUSES = frozenset({"fields", "breakout", "aggregation", "order-by", "joins.fields"})
_JOIN_LABELS = ("joins.condition", "joins.fields")

_KIND_LIST = "list"
_KIND_EXPRESSION = "expression"
_KIND_OPAQUE = "opaque"


@dataclass(frozen=True)
class DeadSet:
    """What a rewrite is asked to do, in Metabase's own terms.

    `field_ids`/`names` are references to repair by REMOVAL -- the column is gone and no
    replacement was declared. `field_map`/`name_map` are references to repair by
    REPOINTING -- a declared rename resolved to a live field. The two are disjoint by
    construction (mend/plan.py partitions them), which is what lets the rewrite repoint
    first and then treat whatever still matches as dead.

    Names are casefolded keys: a by-name ref spells the column as the query author typed
    it, and Metabase resolves those case-insensitively.

    `labels`/`rename_labels` are cosmetic but not optional in practice: "removed filter ->
    fct_orders.promo_code" is a line a card owner can act on, and "removed filter -> field
    102" is one they have to go and look up.
    """

    field_ids: frozenset[int] = frozenset()
    names: frozenset[str] = frozenset()
    field_map: dict[int, int] = field(default_factory=dict)
    name_map: dict[str, str] = field(default_factory=dict)
    labels: dict[Any, str] = field(default_factory=dict)
    rename_labels: dict[Any, str] = field(default_factory=dict)

    @property
    def empty(self) -> bool:
        return not (self.field_ids or self.names or self.field_map or self.name_map)

    @staticmethod
    def _key(target: Any) -> Any:
        return target.casefold() if isinstance(target, str) else target

    def label_for(self, target: Any) -> str | None:
        """How a matched ref should be named in a human-facing line."""
        key = self._key(target)
        if key in self.labels:
            return self.labels[key]
        if isinstance(target, int):
            return f"field {target}"
        if isinstance(target, str):
            return target
        return None

    def rename_label_for(self, target: Any) -> str:
        """How the repointed-to column should be named."""
        key = self._key(target)
        if key in self.rename_labels:
            return self.rename_labels[key]
        if isinstance(key, int) and key in self.field_map:
            return f"field {self.field_map[key]}"
        return str(self.name_map.get(key, "?"))


@dataclass(frozen=True)
class ClauseUse:
    """One clause a dead reference sits under, and whether losing it is survivable."""

    label: str
    essential: bool


@dataclass
class RewriteResult:
    """A rewritten `dataset_query`, and a human account of what changed.

    `query` is None when the card cannot be rewritten at all (native SQL, or a shape the
    walk does not recognise) -- never a partially-repaired query, because a query that
    runs while still carrying a dead reference is the failure mode that looks like success.
    """

    query: dict[str, Any] | None = None
    repointed: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    unsupported: str | None = None

    @property
    def changed(self) -> bool:
        return bool(self.repointed or self.removed)


@dataclass
class _Clause:
    """One clause of one query level, located well enough to be rewritten in place.

    `owner[key]` is the clause value; `label` is the shape-independent, stage-prefixed
    name the essentialness rule is applied to; `level` is the query or stage the clause
    belongs to, which is what aggregation-index remapping is scoped to.
    """

    label: str
    key: str
    owner: dict[str, Any]
    kind: str
    level: dict[str, Any]

    @property
    def value(self) -> Any:
        return self.owner.get(self.key)

    @property
    def base(self) -> str:
        return clause_base(self.label)


def clause_base(label: str) -> str:
    """The essentialness-rule name behind a walk label.

    "stage1.breakout" -> "breakout", "joins.source-query.stage2.joins.condition" ->
    "joins.condition". Prefixes record WHERE in the query a ref was found; the rule cares
    only WHAT kind of clause it was.
    """
    for join_label in _JOIN_LABELS:
        if label == join_label or label.endswith(f".{join_label}"):
            return join_label
    return label.rpartition(".")[2]


# --------------------------------------------------------------------------------------
# locating clauses in either shape
# --------------------------------------------------------------------------------------


def _legacy_kind(key: str) -> str:
    if key in _LIST_CLAUSES:
        return _KIND_LIST
    return _KIND_EXPRESSION if key == "filter" else _KIND_OPAQUE


def _stage_kind(label: str) -> str:
    if label in _LIST_CLAUSES:
        return _KIND_LIST
    # MBQL 5 pluralized `filter` -> `filters` and made it a LIST of expressions, so each
    # filter is its own element rather than an argument of one `and`
    return _KIND_LIST if label == "filter" else _KIND_OPAQUE


def _join_clauses(
    join: dict[str, Any], level: dict[str, Any], prefix: str, condition_key: str
) -> Iterator[_Clause]:
    if condition_key in join:
        yield _Clause(f"{prefix}joins.condition", condition_key, join, _KIND_OPAQUE, level)
    if isinstance(join.get("fields"), list):
        yield _Clause(f"{prefix}joins.fields", "fields", join, _KIND_LIST, level)


def _legacy_clauses(query: dict[str, Any], prefix: str) -> Iterator[_Clause]:
    """Every clause of a legacy query level, descending into joins and source-query."""
    for key in CLAUSE_KEYS:
        if key in query:
            yield _Clause(f"{prefix}{key}", key, query, _legacy_kind(key), query)
    joins = query.get("joins")
    for join in joins if isinstance(joins, list) else []:
        if not isinstance(join, dict):
            continue
        yield from _join_clauses(join, query, prefix, "condition")
        if isinstance(join.get("source-query"), dict):
            yield from _legacy_clauses(join["source-query"], f"{prefix}joins.source-query.")
    if isinstance(query.get("source-query"), dict):
        yield from _legacy_clauses(query["source-query"], f"{prefix}source-query.")


def _stage_clauses(stages: list[Any], prefix: str) -> Iterator[_Clause]:
    """Every clause of an MBQL 5 stage chain -- the flat equivalent of the above.

    Stage 0 keeps bare labels so a single-stage MBQL 5 query yields exactly the labels its
    legacy twin would; later stages are stage1./stage2./..., matching the resolver.
    """
    for index, stage in enumerate(stages):
        if not isinstance(stage, dict) or is_native_stage(stage):
            continue
        stage_prefix = prefix if index == 0 else f"{prefix}stage{index}."
        for key, label in STAGE_CLAUSE_LABELS.items():
            if key in stage:
                yield _Clause(f"{stage_prefix}{label}", key, stage, _stage_kind(label), stage)
        joins = stage.get("joins")
        for join in joins if isinstance(joins, list) else []:
            if not isinstance(join, dict):
                continue
            yield from _join_clauses(join, stage, stage_prefix, "conditions")
            join_stages = join.get("stages")
            if isinstance(join_stages, list):
                yield from _stage_clauses(join_stages, f"{stage_prefix}joins.source-query.")


def _clauses(dataset_query: dict[str, Any]) -> list[_Clause]:
    kind = query_kind(dataset_query)
    if kind == STAGE_QUERY:
        return list(_stage_clauses(dataset_query["stages"], ""))
    if kind is not None and kind != NATIVE_QUERY:
        return list(_legacy_clauses(dataset_query["query"], ""))
    return []


# --------------------------------------------------------------------------------------
# predicates over MBQL nodes
# --------------------------------------------------------------------------------------

_NodePredicate = Callable[[list[Any]], bool]


def _any_node(node: Any, predicate: _NodePredicate) -> bool:
    """Whether any list node anywhere inside `node` satisfies `predicate`."""
    if isinstance(node, list):
        if node and predicate(node):
            return True
        return any(_any_node(item, predicate) for item in node)
    if isinstance(node, dict):
        return any(_any_node(value, predicate) for value in node.values())
    return False


def _each_node(node: Any, visit: Callable[[list[Any]], None]) -> None:
    """Call `visit` on every list node inside `node`, outermost first."""
    if isinstance(node, list):
        if node:
            visit(node)
        for item in node:
            _each_node(item, visit)
    elif isinstance(node, dict):
        for value in node.values():
            _each_node(value, visit)


def _dead_predicate(dead: DeadSet) -> _NodePredicate:
    """True for a `field` ref naming something in the dead set, either argument order.

    opts["source-field"] counts: a ref that reaches its column through a foreign key
    whose field is gone is just as broken as a direct reference to it.
    """

    def predicate(node: list[Any]) -> bool:
        if node[0] != "field" or len(node) < 2:
            return False
        target, opts = ref_parts(node)
        if isinstance(target, int) and target in dead.field_ids:
            return True
        if isinstance(target, str) and target.casefold() in dead.names:
            return True
        source_field = opts.get("source-field") if isinstance(opts, dict) else None
        return isinstance(source_field, int) and source_field in dead.field_ids

    return predicate


def _aggregation_predicate(indices: set[int]) -> _NodePredicate:
    """True for a legacy ["aggregation", N] reference to one of `indices`.

    MBQL 5 references an aggregation by uuid rather than position, so it needs no
    remapping and this predicate never matches there.
    """

    def predicate(node: list[Any]) -> bool:
        if node[0] != "aggregation" or len(node) < 2:
            return False
        target, _ = ref_parts(node)
        return isinstance(target, int) and target in indices

    return predicate


def dead_refs_in(node: Any, dead: DeadSet) -> list[Any]:
    """Every dead-set target named anywhere inside `node`, deduped, order-stable."""
    predicate = _dead_predicate(dead)
    found: list[Any] = []

    def visit(item: list[Any]) -> None:
        if not predicate(item):
            return
        target, opts = ref_parts(item)
        for candidate in (target, (opts or {}).get("source-field")):
            key = candidate.casefold() if isinstance(candidate, str) else candidate
            matched = (isinstance(key, int) and key in dead.field_ids) or (
                isinstance(key, str) and key in dead.names
            )
            if matched and key not in found:
                found.append(key)

    _each_node(node, visit)
    return found


# --------------------------------------------------------------------------------------
# scan: which clauses hold a dead reference, and can the card survive losing them
# --------------------------------------------------------------------------------------


def scan_query(dataset_query: Any, dead: DeadSet) -> dict[Any, list[ClauseUse]]:
    """Per dead target (field id or casefolded column name), the clauses it sits under.

    A target absent from the result is not referenced by this card's own query -- it may
    still reach the card through a source card, which is a repair of that card, not this one.
    """
    uses: dict[Any, list[ClauseUse]] = {}
    if not isinstance(dataset_query, dict) or dead.empty:
        return uses
    predicate = _dead_predicate(dead)
    for clause in _clauses(dataset_query):
        value = clause.value
        if clause.kind == _KIND_LIST and isinstance(value, list):
            hit = [element for element in value if _any_node(element, predicate)]
            survivors = len(value) - len(hit)
            essential = _essential(clause.base, survivors)
            elements = hit
        else:
            if not _any_node(value, predicate):
                continue
            essential = _essential(clause.base, 0)
            elements = [value]
        for element in elements:
            for target in dead_refs_in(element, dead):
                entry = uses.setdefault(target, [])
                if not any(use.label == clause.label for use in entry):
                    entry.append(ClauseUse(label=clause.label, essential=essential))
    return uses


def _essential(base: str, survivors: int) -> bool:
    if base in _NEVER_ESSENTIAL:
        return False
    if base in _ALWAYS_ESSENTIAL:
        return True
    if base in _SOLE_MEMBER_ESSENTIAL:
        return survivors == 0
    # a clause label the walk knows but this rule does not: treat as essential rather
    # than delete something whose meaning has not been reasoned about
    return True


def essential_targets(uses: dict[Any, list[ClauseUse]]) -> list[Any]:
    """Dead targets this card cannot lose -- the archive decision, in one call."""
    return [target for target, clause_uses in uses.items() if any(u.essential for u in clause_uses)]


# --------------------------------------------------------------------------------------
# rewrite
# --------------------------------------------------------------------------------------


def rewrite_query(dataset_query: Any, dead: DeadSet) -> RewriteResult:
    """Repoint what was renamed, cut out what is gone, and report both.

    Order matters: repointing runs first and rewrites those refs to live field ids, so the
    strip pass sees only references that are genuinely dead. Aggregation indices are fixed
    last, because removing one aggregation of several renumbers the rest and a legacy
    ["aggregation", N] elsewhere in the query would otherwise silently point at a
    different measure -- a card that still runs and answers a different question.
    """
    if not isinstance(dataset_query, dict):
        return RewriteResult(unsupported="card has no dataset_query")
    kind = query_kind(dataset_query)
    if kind == NATIVE_QUERY:
        return RewriteResult(unsupported="native SQL card -- rewriting SQL text is out of scope")
    if kind is None:
        return RewriteResult(unsupported="dataset_query shape not recognised")
    if dead.empty:
        return RewriteResult(query=deepcopy(dataset_query))

    query = deepcopy(dataset_query)
    repointed = _repoint(query, dead)
    removed, blocked = _strip(query, dead, legacy=kind != STAGE_QUERY)
    if blocked:
        # a dead reference the rewrite is not willing to delete (a custom column, a join
        # condition). Returning the half-repaired query would ship something that runs and
        # is wrong, so the whole card falls back to notify.
        return RewriteResult(unsupported=f"dead reference in {', '.join(blocked)}")
    return RewriteResult(query=query, repointed=repointed, removed=removed)


def _collected(node: Any) -> list[Ref]:
    """Every field ref inside `node`, via the resolver's own collector."""
    refs: list[Ref] = []
    collect_refs(node, "", refs, {}, [])
    return refs


def _repoint(query: dict[str, Any], dead: DeadSet) -> list[str]:
    """Rewrite renamed refs in place, across every clause of every level.

    Mutates the raw ref lists the collector handed back, which is why this reuses
    collect_refs rather than re-walking: the collector already knows every place a ref
    can be, including inside join conditions and expression definitions.
    """
    if not (dead.field_map or dead.name_map):
        return []
    changes: list[str] = []
    for clause in _clauses(query):
        for ref in _collected(clause.value):
            # the collector emits a synthetic ref for opts["source-field"]; the real one
            # lives in the parent ref's opts, rewritten below
            if ref.is_source_field:
                continue
            _repoint_ref(ref, dead, clause.label, changes)
    return changes


def _repoint_ref(ref: Ref, dead: DeadSet, label: str, changes: list[str]) -> None:
    raw = ref.raw
    if not isinstance(raw, list):
        return
    index = 2 if len(raw) > 2 and isinstance(raw[1], dict) else 1
    target = ref.target
    if isinstance(target, int) and target in dead.field_map:
        raw[index] = dead.field_map[target]
        changes.append(f"{label}: {dead.label_for(target)} -> {dead.rename_label_for(target)}")
    elif isinstance(target, str) and target.casefold() in dead.name_map:
        raw[index] = dead.name_map[target.casefold()]
        changes.append(f"{label}: {dead.label_for(target)} -> {dead.rename_label_for(target)}")
    opts = ref.opts
    if isinstance(opts, dict):
        source_field = opts.get("source-field")
        if isinstance(source_field, int) and source_field in dead.field_map:
            opts["source-field"] = dead.field_map[source_field]
            changes.append(
                f"{label}: implicit join through {dead.label_for(source_field)} "
                f"-> {dead.rename_label_for(source_field)}"
            )


def _strip(query: dict[str, Any], dead: DeadSet, *, legacy: bool) -> tuple[list[str], list[str]]:
    """Delete every clause element that still references something dead.

    Returns (removed labels, blocked clause labels). A blocked clause is one holding a
    dead reference that this module will not delete -- an expression definition or a join
    condition -- which the caller turns into a refusal to rewrite the card at all.
    """
    if not (dead.field_ids or dead.names):
        return [], []
    predicate = _dead_predicate(dead)
    removed: list[str] = []
    blocked: list[str] = []
    aggregation_drops: list[tuple[dict[str, Any], set[int]]] = []
    for clause in _clauses(query):
        value = clause.value
        if clause.kind == _KIND_LIST and isinstance(value, list):
            kept: list[Any] = []
            dropped_indices: set[int] = set()
            for index, element in enumerate(value):
                if _any_node(element, predicate):
                    dropped_indices.add(index)
                    removed.append(_removal_label(clause.label, element, dead))
                else:
                    kept.append(element)
            if not dropped_indices:
                continue
            if kept:
                clause.owner[clause.key] = kept
            else:
                clause.owner.pop(clause.key, None)
            if clause.base == "aggregation":
                aggregation_drops.append((clause.level, dropped_indices))
        elif clause.kind == _KIND_EXPRESSION:
            replacement, dropped = _strip_boolean(value, predicate)
            for element in dropped:
                removed.append(_removal_label(clause.label, element, dead))
            if not dropped:
                continue
            if replacement is None:
                clause.owner.pop(clause.key, None)
            else:
                clause.owner[clause.key] = replacement
        elif _any_node(value, predicate):
            # opaque clauses (expressions, join conditions) are classified essential, so a
            # plan should never ask for this -- refuse rather than delete a definition
            blocked.append(clause.label)
    if legacy:
        for level, indices in aggregation_drops:
            removed.extend(_remap_aggregations(level, indices))
    return removed, blocked


def _split_boolean(expr: list[Any]) -> tuple[list[Any], list[Any]]:
    """(head, arguments) of an and/or node, for either shape.

    MBQL 5 carries an options map at index 1 (["and", {"lib/uuid": ...}, a, b]); legacy
    puts the arguments straight after the operator (["and", a, b]).
    """
    if len(expr) > 1 and isinstance(expr[1], dict):
        return expr[:2], expr[2:]
    return expr[:1], expr[1:]


def _strip_boolean(expr: Any, predicate: _NodePredicate) -> tuple[Any, list[Any]]:
    """Remove offending conditions from a legacy `filter`.

    An and/or losing arguments collapses: one survivor replaces the whole node (an `and`
    of one is not valid MBQL), no survivors removes the clause. A bare condition that
    references something dead takes the clause with it.
    """
    if isinstance(expr, list) and expr and expr[0] in ("and", "or"):
        head, args = _split_boolean(expr)
        kept = [arg for arg in args if not _any_node(arg, predicate)]
        dropped = [arg for arg in args if _any_node(arg, predicate)]
        if not dropped:
            return expr, []
        if not kept:
            return None, dropped
        if len(kept) == 1:
            return kept[0], dropped
        return [*head, *kept], dropped
    if _any_node(expr, predicate):
        return None, [expr]
    return expr, []


def _remap_aggregations(level: dict[str, Any], removed: set[int]) -> list[str]:
    """Fix legacy ["aggregation", N] references after aggregations were removed.

    Two things must happen or the card lies: a reference to a REMOVED aggregation takes
    its own clause element with it, and a reference to a SURVIVING aggregation shifts down
    by however many were removed before it.
    """
    notes: list[str] = []
    stale = _aggregation_predicate(removed)
    shift = {
        index: index - sum(1 for gone in removed if gone < index)
        for index in range(_aggregation_count(level) + len(removed))
        if index not in removed
    }
    for clause in _legacy_clauses(level, ""):
        if clause.base == "aggregation" or clause.owner is not level:
            continue
        value = clause.value
        if clause.kind == _KIND_LIST and isinstance(value, list):
            kept = [element for element in value if not _any_node(element, stale)]
            if len(kept) != len(value):
                notes.append(f"{clause.label}: reference to a removed aggregation")
                if kept:
                    clause.owner[clause.key] = kept
                else:
                    clause.owner.pop(clause.key, None)
        elif clause.kind == _KIND_EXPRESSION:
            replacement, dropped = _strip_boolean(value, stale)
            if dropped:
                notes.append(f"{clause.label}: reference to a removed aggregation")
                if replacement is None:
                    clause.owner.pop(clause.key, None)
                else:
                    clause.owner[clause.key] = replacement

    def renumber(node: list[Any]) -> None:
        if node[0] != "aggregation" or len(node) < 2:
            return
        target, _ = ref_parts(node)
        if not isinstance(target, int) or target not in shift or shift[target] == target:
            return
        index = 2 if len(node) > 2 and isinstance(node[1], dict) else 1
        node[index] = shift[target]

    for clause in _legacy_clauses(level, ""):
        if clause.owner is level and clause.base != "aggregation":
            _each_node(clause.value, renumber)
    return notes


def _aggregation_count(level: dict[str, Any]) -> int:
    aggregation = level.get("aggregation")
    return len(aggregation) if isinstance(aggregation, list) else 0


def _removal_label(label: str, element: Any, dead: DeadSet) -> str:
    """ "filter -> promo_code": the clause that went, named by what killed it."""
    targets = [dead.label_for(target) for target in dead_refs_in(element, dead)]
    named = ", ".join(target for target in targets if target)
    return f"{label} -> {named}" if named else label


# --------------------------------------------------------------------------------------
# dashcard parameter mappings
# --------------------------------------------------------------------------------------


def rewrite_parameter_mappings(
    mappings: Any, dead: DeadSet
) -> tuple[list[dict[str, Any]], list[str]]:
    """Repoint or drop the filter wiring on one dashcard.

    A dashboard filter mapped at a renamed column is repointed like any other ref; one
    mapped at a column that is simply gone is removed, because a widget wired to nothing
    breaks the dashboard even when every card on it was repaired. Returns (mappings,
    notes) and leaves the input untouched.
    """
    if not isinstance(mappings, list):
        return [], []
    predicate = _dead_predicate(dead)
    result: list[dict[str, Any]] = []
    notes: list[str] = []
    for mapping in mappings:
        if not isinstance(mapping, dict):
            result.append(mapping)
            continue
        copied = deepcopy(mapping)
        changes: list[str] = []
        for ref in _collected(copied.get("target")):
            if not ref.is_source_field:
                _repoint_ref(ref, dead, "parameter_mapping", changes)
        if _any_node(copied.get("target"), predicate):
            notes.append(f"parameter_mapping dropped -> {_removal_label('target', copied, dead)}")
            continue
        notes.extend(changes)
        result.append(copied)
    return result, notes
