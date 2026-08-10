"""FastAPI app behind `stitch serve`: the built SPA, the read-only graph API, staging
and suggestions.

The SPA fetches the relative paths `api/graph` and `api/meta` and routes on the hash
fragment, so serving dist/ statically needs no SPA fallback route.

Staging (SPEC.md section 8.2) is the only write surface, and it writes local state only --
`.stitch/staged_relationships.yml` and `.stitch/layout.yml`, never the dbt repo. Those
routes register only when a staged_path is passed (i.e. by `stitch serve`); the static
export has no server at all, so it stays read-only by construction. All repo writes live
in `stitch apply`.
"""

from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict

from stitch_lineage.app import StitchAppError, frontend_dist
from stitch_lineage.graph.schema import Graph, NodeType, column_node_id
from stitch_lineage.graph.suggest import Suggestion, suggest
from stitch_lineage.io.graph_store import read_graph
from stitch_lineage.io.layout_store import (
    LAYOUT_FILENAME,
    LayoutStoreError,
    add_dismissed,
    read_dismissed,
)
from stitch_lineage.io.staged_store import (
    StagedRelationship,
    StagedStoreError,
    add_staged,
    read_staged,
    remove_staged,
)

__all__ = ["StitchAppError", "create_app", "frontend_dist"]

# v1 stores simple (single column pair) relationships only; many-to-many is not a stored
# shape (SPEC.md section 8.1) -- it is what the ERD renders from two many-to-one edges.
_CARDINALITIES = ("many-to-one", "one-to-many", "one-to-one")
_SHAPES = ("simple",)


class StagedRelationshipRequest(BaseModel):
    """POST body for staging a drawn edge; dbt model NAMES, not unique_ids."""

    model_config = ConfigDict(extra="forbid")

    from_model: str
    from_column: str
    to_model: str
    to_column: str
    cardinality: str = "many-to-one"
    shape: str = "simple"


def _load_graph(graph_path: Path) -> Graph:
    """Read graph.json per request so a `stitch build` in another terminal shows up on reload."""
    if not graph_path.is_file():
        raise HTTPException(
            status_code=503,
            detail=f"graph not found at {graph_path} -- run 'stitch build' first",
        )
    try:
        return read_graph(graph_path)
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=f"{graph_path} does not parse: {exc}") from exc


def _read_staged_or_503(staged_path: Path) -> list[StagedRelationship]:
    try:
        return read_staged(staged_path)
    except StagedStoreError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _suggestions(graph_path: Path, staged_path: Path, layout: Path) -> list[Suggestion]:
    """Rank suggestions with the local exclusions applied -- the API never returns a pair
    that is already staged or was dismissed."""
    graph = _load_graph(graph_path)
    staged = [
        (entry.from_model, entry.from_column, entry.to_model, entry.to_column)
        for entry in _read_staged_or_503(staged_path)
    ]
    try:
        dismissed = read_dismissed(layout)
    except LayoutStoreError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return suggest(graph, staged, dismissed)


def _model_uid(graph: Graph, name: str) -> str:
    """Resolve a dbt model name to its unique_id, or 422 with the reason."""
    matches = [
        node.node_id
        for node in graph.nodes
        if node.node_type is NodeType.MODEL and node.name.lower() == name.lower()
    ]
    if not matches:
        raise HTTPException(
            status_code=422,
            detail=f"unknown model '{name}' -- not in graph.json (run 'stitch build')",
        )
    if len(matches) > 1:
        raise HTTPException(
            status_code=422,
            detail=f"model name '{name}' is ambiguous in graph.json ({len(matches)} matches)",
        )
    return matches[0]


def _validate(graph: Graph, payload: StagedRelationshipRequest) -> None:
    """422 unless both endpoints exist in the graph and the shape is one v1 can write."""
    if payload.cardinality not in _CARDINALITIES:
        raise HTTPException(
            status_code=422,
            detail=f"unsupported cardinality '{payload.cardinality}' "
            f"(expected: {', '.join(_CARDINALITIES)})",
        )
    if payload.shape not in _SHAPES:
        raise HTTPException(
            status_code=422,
            detail=f"unsupported shape '{payload.shape}' (expected: {', '.join(_SHAPES)})",
        )
    node_ids = {node.node_id for node in graph.nodes}
    for model, column, role in (
        (payload.from_model, payload.from_column, "from"),
        (payload.to_model, payload.to_column, "to"),
    ):
        uid = _model_uid(graph, model)
        if column_node_id(uid, column) not in node_ids:
            raise HTTPException(
                status_code=422,
                detail=f"{role} column '{column}' is not a column of model '{model}'",
            )
    if (
        payload.from_model.lower() == payload.to_model.lower()
        and payload.from_column.lower() == payload.to_column.lower()
    ):
        raise HTTPException(
            status_code=422, detail="a relationship must join two different columns"
        )


def create_app(
    graph_path: Path,
    metabase_url: str | None,
    erd_default_scope: str | None = None,
    staged_path: Path | None = None,
    layout_path: Path | None = None,
    strip_model_prefixes: list[str] | None = None,
) -> FastAPI:
    """Build the local app serving `graph_path`; `metabase_url` powers card deep links.

    `erd_default_scope` ("schema:<name>" / "tag:<name>", from serve.erd_default_scope)
    is passed through as configured -- the app falls back to its auto-picked scope and
    notes the mismatch when the graph has no such scope.

    `staged_path` enables the staging API (`stitch serve` passes it). Without it the app
    is read-only and /api/meta reports staging_enabled: false, which is how the SPA knows
    not to offer drawing.

    The suggestion API rides on the same switch: suggestions exist to be accepted or
    dismissed, and both write local state, so a read-only export has neither. Dismissals
    land in `layout_path`, defaulting to layout.yml beside the staged store.
    """
    dist = frontend_dist()
    layout = layout_path or (staged_path.parent / LAYOUT_FILENAME if staged_path else None)
    api = FastAPI(title="stitch", docs_url=None, redoc_url=None, openapi_url=None)

    @api.get("/api/graph")
    def api_graph() -> JSONResponse:
        graph = _load_graph(graph_path)
        return JSONResponse(graph.model_dump(mode="json", by_alias=True, exclude_none=True))

    @api.get("/api/meta")
    def api_meta() -> JSONResponse:
        graph = _load_graph(graph_path)
        return JSONResponse(
            {
                "metabase_url": metabase_url,
                "generated_at": graph.generated_at,
                "schema_version": graph.schema_version,
                "erd_default_scope": erd_default_scope,
                "strip_model_prefixes": list(strip_model_prefixes or []),
                "staging_enabled": staged_path is not None,
            }
        )

    if staged_path is not None and layout is not None:

        @api.get("/api/suggestions")
        def api_suggestions() -> JSONResponse:
            entries = _suggestions(graph_path, staged_path, layout)
            return JSONResponse(
                {"suggestions": [entry.model_dump(mode="json") for entry in entries]}
            )

        @api.post("/api/suggestions/{suggestion_id}/dismiss")
        def api_suggestion_dismiss(suggestion_id: str) -> Response:
            # only a live suggestion can be dismissed: an unknown id means the pair was
            # already dismissed, staged, declared or has left the graph entirely
            known = {entry.id for entry in _suggestions(graph_path, staged_path, layout)}
            if suggestion_id not in known:
                raise HTTPException(
                    status_code=404, detail=f"no suggestion with id '{suggestion_id}'"
                )
            try:
                add_dismissed(suggestion_id, layout)
            except LayoutStoreError as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            return Response(status_code=204)

    if staged_path is not None:

        @api.get("/api/staged-relationships")
        def api_staged_list() -> JSONResponse:
            entries = _read_staged_or_503(staged_path)
            return JSONResponse(
                {"relationships": [entry.model_dump(mode="json") for entry in entries]}
            )

        @api.post("/api/staged-relationships")
        def api_staged_add(payload: StagedRelationshipRequest) -> JSONResponse:
            graph = _load_graph(graph_path)
            _validate(graph, payload)
            candidate = StagedRelationship(
                **payload.model_dump(),
                created_at=datetime.now(UTC).isoformat(timespec="seconds"),
            )
            try:
                stored, created = add_staged(candidate, staged_path)
            except StagedStoreError as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            return JSONResponse(
                {"relationship": stored.model_dump(mode="json"), "created": created},
                status_code=201 if created else 200,
            )

        @api.delete("/api/staged-relationships/{relationship_id}")
        def api_staged_delete(relationship_id: str) -> Response:
            try:
                removed = remove_staged(relationship_id, staged_path)
            except StagedStoreError as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            if not removed:
                raise HTTPException(
                    status_code=404, detail=f"no staged relationship with id '{relationship_id}'"
                )
            return Response(status_code=204)

    api.mount("/", StaticFiles(directory=dist, html=True), name="app")
    return api
