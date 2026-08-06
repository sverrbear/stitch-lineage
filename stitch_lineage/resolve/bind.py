"""Bind Metabase tables/fields to dbt models/columns (SPEC.md section 7.5)."""

from pydantic import BaseModel, Field

from stitch_lineage.graph.schema import Edge, Node


class BindResult(BaseModel):
    """Output of bind: binds_to edges plus binding coverage counters.

    Coverage fields map 1:1 onto graph.schema.Coverage; the CLI copies them over.
    """

    edges: list[Edge] = Field(default_factory=list)
    models_bound: int = 0
    models_total: int = 0
    unbound_models: list[str] = Field(default_factory=list)


def bind(
    dbt_nodes: list[Node],
    mb_field_nodes: list[Node],
    database_map: list[tuple[str, str]],
) -> BindResult:
    """Produce `binds_to` edges (dbt column -> mb_field).

    dbt_nodes are the source/model Nodes from resolve_dbt (their column nodes are
    derivable via schema.column_node_id); mb_field_nodes are the mb_field Nodes from
    resolve_metabase; database_map is [(metabase_display_name, dbt_database), ...]
    from config metabase.databases.

    Match (database, schema, table) case-insensitively against the dbt side, honouring
    model alias; exact name match -> confidence exact, case-only or fuzzy match ->
    fuzzy (record what differed in evidence). Traps handled explicitly: Snowflake
    uppercase unquoted identifiers, Metabase display name != dbt database name (the
    map), multiple Metabase connections to one warehouse (multiple map entries).

    models_total counts dbt model/source nodes considered; models_bound those with at
    least one bound table; unbound_models the remaining unique_ids.

    Pure: nodes in, edges out. No filesystem or network access.
    """
    raise NotImplementedError
