"""Candidate relationships nobody has declared yet (SPEC.md section 9, issue #30).

Two evidence sources, strongest first:

1. **implicit_join** -- Metabase `consumed_by` edges carrying `evidence.implicit_join`.
   Each one names an FK field a card joined *through* (MBQL `opts["source-field"]`);
   the field node's `fk_target_field_id` names what it points at. Both ends map back
   through `binds_to` to dbt columns, so the pair is a relationship real users are
   already querying. Score = number of witnessing cards.
2. **naming** -- an `<entity>_id` column whose entity names another model's grain
   (`user_id` -> `dim_users.user_id` / `.id`). A convention, not evidence, so it always
   scores below a single witnessing card.

Suggestions are excluded when the pair is already declared (`relates_to`), already
staged, previously dismissed, or a self-join. Declared/staged exclusion ignores
direction: the reverse of a declared pair is the same relationship, not a new one.

Seam rule: pure function over Graph plus caller-supplied exclusions -- the staged store
and the dismissal list are read by io/ and passed in, so graph/ stays io-free.
"""

import re
from collections import defaultdict
from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel, Field, model_validator

from stitch_lineage.graph.schema import (
    EdgeType,
    Graph,
    Node,
    NodeType,
    relationship_id,
)

__all__ = ["NAMING_SCORE", "Suggestion", "suggest"]

# a naming-convention hit is a guess; one card actually joining through the FK outranks it
NAMING_SCORE = 0.5

# dbt layer prefixes stripped (repeatedly) to get at a model's grain: dim_users, viz_dim_users
# and stg_users all have grain "user"
_LAYER_PREFIX = re.compile(r"^(viz_|stg_|int_|fct_|dim_|mart_|bdg_|base_|agg_|rpt_)+")

_ID_SUFFIX = "_id"


class Suggestion(BaseModel):
    """One candidate relationship, keyed the same way a staged one is.

    `id` is the staged-relationship endpoint hash, so accepting a suggestion (POST to
    /api/staged-relationships) yields an entry with this exact id, and a dismissal
    recorded against it still matches after the next `stitch build`.
    """

    id: str = ""
    from_model: str
    from_column: str
    to_model: str
    to_column: str
    cardinality_guess: str = "many-to-one"
    source: str
    score: float
    evidence: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _derive_id(self) -> "Suggestion":
        self.id = relationship_id(self.from_model, self.from_column, self.to_model, self.to_column)
        return self

    def sort_key(self) -> tuple[Any, ...]:
        return (
            -self.score,
            self.from_model.lower(),
            self.from_column.lower(),
            self.to_model.lower(),
            self.to_column.lower(),
        )


def _singular(word: str) -> str:
    """Crude English de-pluralization -- enough for dbt table names, deliberately not a
    dependency on an inflection library."""
    if word.endswith("ies") and len(word) > 3:
        return f"{word[:-3]}y"
    if word.endswith(("ses", "xes", "zes", "ches", "shes")):
        return word[:-2]
    if word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _grains(model_name: str) -> set[str]:
    """The entity names a model could plausibly be the grain of."""
    name = model_name.lower()
    stripped = _LAYER_PREFIX.sub("", name) or name
    return {_singular(name), _singular(stripped)}


def _pair_key(from_model: str, from_column: str, to_model: str, to_column: str) -> frozenset[str]:
    """Direction-free key: a declared A.x -> B.y also covers B.y -> A.x."""
    return frozenset({f"{from_model}.{from_column}".lower(), f"{to_model}.{to_column}".lower()})


def _owner(column_node_id: str) -> str:
    return column_node_id.rpartition("::")[0]


def _column_name(column_node_id: str) -> str:
    return column_node_id.rpartition("::")[2]


def _model_columns(graph: Graph) -> tuple[dict[str, Node], dict[str, dict[str, Node]]]:
    """(model node by uid, {model uid: {lowercased column name: column node}}).

    Only NodeType.MODEL owners: a suggestion has to be acceptable, and both the staging
    API and `stitch apply` resolve dbt model names -- a source column could never be
    written back.
    """
    models = {node.node_id: node for node in graph.nodes if node.node_type is NodeType.MODEL}
    columns: dict[str, dict[str, Node]] = defaultdict(dict)
    for node in graph.nodes:
        if node.node_type is not NodeType.COLUMN:
            continue
        owner = _owner(node.node_id)
        if owner in models:
            columns[owner][node.name.lower()] = node
    return models, columns


def _implicit_join_pairs(
    graph: Graph,
    models: dict[str, Node],
    columns: dict[str, dict[str, Node]],
) -> list[Suggestion]:
    fields = {node.node_id: node for node in graph.nodes if node.node_type is NodeType.MB_FIELD}
    # mb_field -> the dbt columns bound to it (fuzzy binding can produce more than one)
    bound: dict[str, list[Node]] = defaultdict(list)
    for edge in graph.edges:
        if edge.edge_type is not EdgeType.BINDS_TO:
            continue
        owner = _owner(edge.from_)
        column = columns.get(owner, {}).get(_column_name(edge.from_))
        if column is not None:
            bound[edge.to].append(column)

    # (from column node, to column node) -> witnessing card node ids
    witnesses: dict[tuple[str, str], set[str]] = defaultdict(set)
    field_ids: dict[tuple[str, str], tuple[str, str]] = {}
    for edge in graph.edges:
        if edge.edge_type is not EdgeType.CONSUMED_BY or not edge.evidence.get("implicit_join"):
            continue
        fk_field = fields.get(edge.from_)
        if fk_field is None:
            continue
        target_id = fk_field.properties.get("fk_target_field_id")
        if not isinstance(target_id, int):
            # Metabase knows the field is joined through but not what it points at;
            # inventing a target would be worse than dropping the candidate
            continue
        target_node_id = f"mb_field::{target_id}"
        for from_column in bound.get(edge.from_, []):
            for to_column in bound.get(target_node_id, []):
                key = (from_column.node_id, to_column.node_id)
                witnesses[key].add(edge.to)
                field_ids[key] = (edge.from_, target_node_id)

    suggestions = []
    for (from_id, to_id), cards in witnesses.items():
        from_model, to_model = models[_owner(from_id)], models[_owner(to_id)]
        if from_model.node_id == to_model.node_id:
            continue
        from_column = columns[from_model.node_id][_column_name(from_id)]
        to_column = columns[to_model.node_id][_column_name(to_id)]
        source_field, target_field = field_ids[(from_id, to_id)]
        suggestions.append(
            Suggestion(
                from_model=from_model.name,
                from_column=from_column.name,
                to_model=to_model.name,
                to_column=to_column.name,
                source="implicit_join",
                score=float(len(cards)),
                evidence={
                    "card_ids": sorted(cards),
                    "mb_source_field": source_field,
                    "mb_target_field": target_field,
                },
            )
        )
    return suggestions


def _naming_pairs(
    models: dict[str, Node],
    columns: dict[str, dict[str, Node]],
) -> list[Suggestion]:
    by_grain: dict[str, list[str]] = defaultdict(list)
    for uid, model in models.items():
        for grain in _grains(model.name):
            by_grain[grain].append(uid)

    suggestions = []
    for uid, model in models.items():
        own_grains = _grains(model.name)
        for column in columns.get(uid, {}).values():
            name = column.name.lower()
            if not name.endswith(_ID_SUFFIX) or len(name) <= len(_ID_SUFFIX):
                continue
            entity = _singular(name[: -len(_ID_SUFFIX)])
            if entity in own_grains:
                # the model's own key, not a foreign one (dim_users.user_id)
                continue
            for target_uid in by_grain.get(entity, []):
                if target_uid == uid:
                    continue
                target_columns = columns.get(target_uid, {})
                target = target_columns.get(name) or target_columns.get("id")
                if target is None:
                    continue
                suggestions.append(
                    Suggestion(
                        from_model=model.name,
                        from_column=column.name,
                        to_model=models[target_uid].name,
                        to_column=target.name,
                        source="naming",
                        score=NAMING_SCORE,
                        evidence={"entity": entity, "matched_column": target.name},
                    )
                )
    return suggestions


def _declared_pairs(graph: Graph, models: dict[str, Node]) -> set[frozenset[str]]:
    declared = set()
    for edge in graph.edges:
        if edge.edge_type is not EdgeType.RELATES_TO:
            continue
        from_model, to_model = models.get(_owner(edge.from_)), models.get(_owner(edge.to))
        if from_model is None or to_model is None:
            continue
        declared.add(
            _pair_key(
                from_model.name,
                _column_name(edge.from_),
                to_model.name,
                _column_name(edge.to),
            )
        )
    return declared


def suggest(
    graph: Graph,
    staged: Iterable[tuple[str, str, str, str]] = (),
    dismissed: Iterable[str] = (),
) -> list[Suggestion]:
    """Rank undeclared relationship candidates, best evidence first.

    `staged` is (from_model, from_column, to_model, to_column) per staged entry and
    `dismissed` a list of suggestion ids -- both come from `.stitch/` via io/, which
    this module may not read itself.

    A pair found by both sources is emitted once, as the implicit-join one: real usage
    beats a naming guess, and the score reflects it.
    """
    models, columns = _model_columns(graph)
    blocked = _declared_pairs(graph, models) | {_pair_key(*entry) for entry in staged}
    dismissed_ids = set(dismissed)

    ranked: dict[str, Suggestion] = {}
    for suggestion in [
        *_implicit_join_pairs(graph, models, columns),
        *_naming_pairs(models, columns),
    ]:
        key = _pair_key(
            suggestion.from_model,
            suggestion.from_column,
            suggestion.to_model,
            suggestion.to_column,
        )
        if key in blocked or suggestion.id in dismissed_ids:
            continue
        existing = ranked.get(suggestion.id)
        if existing is None or suggestion.score > existing.score:
            ranked[suggestion.id] = suggestion
    return sorted(ranked.values(), key=Suggestion.sort_key)
