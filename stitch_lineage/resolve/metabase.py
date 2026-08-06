"""Resolve raw Metabase payloads into graph nodes and edges (SPEC.md section 7.4)."""

from pydantic import BaseModel, Field

from stitch_lineage.graph.schema import Edge, Node
from stitch_lineage.payloads import MetabasePayload


class MetabaseResolution(BaseModel):
    """Output of resolve_metabase: the Metabase side of the graph plus coverage counters.

    Coverage fields map 1:1 onto graph.schema.Coverage; the CLI copies them over.
    """

    nodes: list[Node] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)
    mbql_cards_resolved: int = 0
    mbql_cards_total: int = 0
    native_cards_resolved: int = 0
    native_cards_total: int = 0
    dashboards: int = 0
    dashboards_total: int = 0
    unresolved_cards: list[int] = Field(default_factory=list)


def resolve_metabase(
    payload: MetabasePayload, exclude_collections: list[str]
) -> MetabaseResolution:
    """Build the Metabase side of the graph from raw API payloads.

    Produces:
      * mb_field Nodes (via schema.mb_field_node_id) from database_metadata, carrying
        database/schema/table/column so resolve.bind can match them to dbt models.
      * mb_card / mb_dashboard Nodes (mb_card_node_id / mb_dashboard_node_id) with
        collection_id, creator and archived in properties.
      * `consumed_by` edges (mb_field -> mb_card): walk MBQL dataset_query.query over
        fields, breakout, aggregation, filter, expressions, joins[].condition,
        joins[].fields and order-by; every ["field", <id>, opts] resolves through the
        metadata map, confidence exact. Card-on-card (source-table "card__123",
        Metabase models/metrics) resolves transitively, cycle-guarded with a visited
        set. Native SQL cards are Phase 3: count them in native_cards_total, resolve
        none, add their ids to unresolved_cards -- never drop a card silently.
      * `appears_on` edges (mb_card -> mb_dashboard) from dashcards, confidence exact.

    exclude_collections are glob patterns (e.g. "Personal*") matched against collection
    names; cards/dashboards in excluded collections are skipped entirely and count in
    no total.

    Pure: payload in, nodes/edges out. No filesystem or network access.
    """
    raise NotImplementedError
