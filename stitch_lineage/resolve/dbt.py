"""Resolve dbt manifest + catalog into graph nodes and edges (SPEC.md sections 7.1, 7.3, 8.1)."""

from typing import Any

from pydantic import BaseModel, Field

from stitch_lineage.graph.schema import Edge, Node


class DbtResolution(BaseModel):
    """Output of resolve_dbt: the dbt side of the graph plus its coverage counters.

    Coverage fields map 1:1 onto graph.schema.Coverage (columns_traced/columns_total/
    columns_inferred/untraced_columns/dangling_relationships); the CLI copies them over.
    """

    nodes: list[Node] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)
    columns_traced: int = 0
    columns_total: int = 0
    columns_inferred: int = 0
    untraced_columns: list[str] = Field(default_factory=list)
    dangling_relationships: list[str] = Field(default_factory=list)


def resolve_dbt(manifest: dict[str, Any], catalog: dict[str, Any]) -> DbtResolution:
    """Build the dbt side of the graph from parsed manifest.json and catalog.json.

    Produces:
      * source/model Nodes (node_id = dbt unique_id) from manifest nodes/sources.
      * column Nodes (node_id via schema.column_node_id) -- types from the catalog,
        manifest columns as fallback for views/ephemerals absent from the catalog.
      * `references` edges (upstream model -> downstream model) from manifest
        depends_on, confidence exact.
      * `feeds` edges (upstream column -> downstream column) via sqlglot.lineage over
        each model's compiled_code, dialect="snowflake", schema-qualified from the
        catalog. Plain projections/renames -> confidence exact; expressions -> parsed
        (one edge per input column); star-expansion fallback by name-matching ->
        inferred. Unparseable model -> fail soft: keep its `references` edges, add its
        columns to untraced_columns, never blank the graph. Ephemeral hops attribute
        to the parent model; VARIANT sub-paths land on the VARIANT column.
      * `relates_to` edges (FK column -> referenced PK column) read from column meta
        (metabase.fk_target_table/field + relationship_type), model-level
        stitch.relationships meta, existing relationships tests, and contract
        constraints. Meta-only -> confidence declared; backed by a relationships
        test -> validated. Declarations that point at a missing model/column go to
        dangling_relationships (formatted "model.column -> target"), not the edge list.

    Pure: dicts in, nodes/edges out. No filesystem or network access.
    """
    raise NotImplementedError
