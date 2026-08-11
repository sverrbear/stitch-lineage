"""Data contracts for graph.json (SPEC.md section 5).

Every module in the codebase codes against these models. Field aliases exist because
"schema" and "from" are reserved (pydantic method / Python keyword); construct with
either the field name (schema_, from_) or the alias -- serialization always uses the
alias via model_dump(by_alias=True).
"""

import hashlib
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class NodeType(StrEnum):
    SOURCE = "source"
    MODEL = "model"
    COLUMN = "column"
    MB_FIELD = "mb_field"
    MB_CARD = "mb_card"
    MB_DASHBOARD = "mb_dashboard"


class EdgeType(StrEnum):
    REFERENCES = "references"
    FEEDS = "feeds"
    BINDS_TO = "binds_to"
    CONSUMED_BY = "consumed_by"
    APPEARS_ON = "appears_on"
    RELATES_TO = "relates_to"


class Confidence(StrEnum):
    EXACT = "exact"
    PARSED = "parsed"
    INFERRED = "inferred"
    FUZZY = "fuzzy"
    DECLARED = "declared"
    VALIDATED = "validated"


class DataTypeSource(StrEnum):
    """Where a column's `data_type` came from -- the waterfall in resolve.types.

    Ordered by authority: the dbt build artifacts describe the relation this project
    builds, Metabase's field metadata describes the one the warehouse actually has,
    and an inferred type is a parse result nobody has confirmed. A column with no
    data_type carries no source at all -- absence is the honest answer, never a
    fourth "unknown" value pretending to be provenance.
    """

    CATALOG = "catalog"
    METABASE = "metabase"
    INFERRED = "inferred"


def column_node_id(model_unique_id: str, column_name: str) -> str:
    """Node id for a dbt column: '{model_unique_id}::{column_name}', column lowercased."""
    return f"{model_unique_id}::{column_name.lower()}"


def mb_field_node_id(field_id: int) -> str:
    return f"mb_field::{field_id}"


def mb_card_node_id(card_id: int) -> str:
    return f"mb_card::{card_id}"


def mb_dashboard_node_id(dashboard_id: int) -> str:
    return f"mb_dash::{dashboard_id}"


def relationship_id(from_model: str, from_column: str, to_model: str, to_column: str) -> str:
    """Deterministic id for a relationship's endpoints.

    Endpoints only: re-staging the same column pair with a different cardinality is the
    same relationship, so it dedupes instead of stacking up a second entry.

    It lives here, not in io/staged_store.py where it is used most, because
    graph/suggest.py must mint the SAME id for a suggested pair -- accepting or
    dismissing a suggestion has to survive a graph rebuild -- and graph/ may not import
    io/ (SPEC.md section 4). One definition, no drift.
    """
    payload = f"{from_model}.{from_column}->{to_model}.{to_column}".lower()
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


class Node(BaseModel):
    """A vertex in the lineage graph: dbt source/model/column or Metabase field/card/dashboard.

    node_id conventions (use the helper functions above):
      source/model -> dbt unique_id (e.g. "model.smitten.fct_matches")
      column       -> "{model_unique_id}::{column_name}" (column lowercased)
      mb_field     -> "mb_field::{field_id}"
      mb_card      -> "mb_card::{card_id}"
      mb_dashboard -> "mb_dash::{dashboard_id}"

    properties holds type-specific extras (tags, materialization, collection_id,
    card creator, archived flag) so the core payload stays uniform.
    """

    model_config = ConfigDict(populate_by_name=True)

    node_id: str
    node_type: NodeType
    name: str
    database: str | None = None
    schema_: str | None = Field(default=None, alias="schema")
    table: str | None = None
    column: str | None = None
    data_type: str | None = None
    # provenance for data_type, set together with it and absent whenever it is None
    data_type_source: DataTypeSource | None = None
    description: str | None = None
    owner: str | None = None
    properties: dict[str, Any] = Field(default_factory=dict)


class Edge(BaseModel):
    """A directed edge in the lineage graph.

    Direction is ALWAYS data flow, upstream -> downstream: `from_` is the upstream
    node, `to` is the downstream node. Impact traversal (graph/impact.downstream)
    walks from -> to; a single edge type pointing the wrong way silently corrupts
    every impact report. `relates_to` is a declaration, not a flow -- it is excluded
    from impact traversal and rendered in the ERD only.

    evidence records why the edge exists (manifest path, MBQL fragment, parsed SQL
    span) so `parsed`/`fuzzy`/`inferred` edges can be audited and rendered
    differently from `exact` ones.
    """

    model_config = ConfigDict(populate_by_name=True)

    from_: str = Field(alias="from")
    to: str
    edge_type: EdgeType
    confidence: Confidence
    evidence: dict[str, Any] = Field(default_factory=dict)


class Coverage(BaseModel):
    """Resolution coverage counters written into graph.json and printed by `stitch build`.

    All fields default so partial construction works (e.g. a --no-metabase build
    fills only the dbt-side numbers).
    """

    models_bound: int = 0
    models_total: int = 0
    # models kept out of the bind denominator by metabase.exclude_packages /
    # exclude_models -- package plumbing nobody expects to find in Metabase, so
    # counting it as "unbound" only makes the tool look worse than it is
    models_excluded: int = 0
    mbql_cards_resolved: int = 0
    mbql_cards_total: int = 0
    native_cards_resolved: int = 0
    native_cards_total: int = 0
    dashboards: int = 0
    dashboards_total: int = 0
    columns_traced: int = 0
    columns_total: int = 0
    columns_inferred: int = 0
    # mb fields whose dbt relation supplied no column inventory -- skipped, never fabricated
    unverified_field_count: int = 0
    # seed/snapshot deps dropped from `references` (neither is a node type in the graph)
    seed_snapshot_dependencies: int = 0
    unbound_models: list[str] = Field(default_factory=list)
    unresolved_cards: list[int] = Field(default_factory=list)
    unresolved_field_refs: list[dict[str, Any]] = Field(default_factory=list)
    untraced_columns: list[str] = Field(default_factory=list)
    dangling_relationships: list[str] = Field(default_factory=list)


class Graph(BaseModel):
    """The whole of graph.json.

    generated_at, dbt_invocation_id and metabase_version are volatile header fields:
    io.graph_store.graphs_semantically_equal ignores them, which is what powers
    `stitch build --check`.
    """

    model_config = ConfigDict(populate_by_name=True)

    schema_version: int = 1
    generated_at: str | None = None
    dbt_invocation_id: str | None = None
    metabase_version: str | None = None
    coverage: Coverage = Field(default_factory=Coverage)
    nodes: list[Node] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)
