"""Raw Metabase payload bundle shared by io/ (producer) and resolve/ (consumer).

Lives outside both packages on purpose: resolve/ must never import stitch_lineage.io
(seam rule, SPEC.md section 4), yet both sides need the same type for the handoff.
All payloads are untransformed API responses -- resolution logic belongs in
resolve/metabase.py, not here.
"""

from typing import Any

from pydantic import BaseModel, Field


class MetabasePayload(BaseModel):
    """Everything `stitch build` needs from Metabase, exactly as the API returned it.

    Produced by io.metabase_client.MetabaseClient.fetch_all (which also snapshots the
    same payloads to .stitch/cache/{timestamp}/ per SPEC.md section 7.2), consumed by
    resolve.metabase.resolve_metabase.

    Fields:
      metabase_version:  from the session/properties endpoint, copied into the graph header.
      databases:         GET /api/database -- only the configured warehouse databases.
      database_metadata: GET /api/database/:id/metadata?include_hidden=true, keyed by
                         database id -- the field_id <-> schema.table.column map.
      cards:             GET /api/card (each with dataset_query, collection_id, creator,
                         archived).
      dashboards:        full GET /api/dashboard/:id payloads (dashcards included).
      collections:       GET /api/collection tree, for collection filtering.
      snippets:          GET /api/native-query-snippet -- the SQL behind a
                         `{{snippet: name}}` tag in a native card. Empty when the
                         instance does not expose the endpoint; a native card using an
                         unavailable snippet degrades rather than failing the build.
    """

    metabase_version: str | None = None
    databases: list[dict[str, Any]] = Field(default_factory=list)
    database_metadata: dict[int, dict[str, Any]] = Field(default_factory=dict)
    cards: list[dict[str, Any]] = Field(default_factory=list)
    dashboards: list[dict[str, Any]] = Field(default_factory=list)
    collections: list[dict[str, Any]] = Field(default_factory=list)
    snippets: list[dict[str, Any]] = Field(default_factory=list)
