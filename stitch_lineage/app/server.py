"""FastAPI app behind `stitch serve`: the built SPA, the read-only graph API, staging,
suggestions and apply.

The SPA fetches the relative paths `api/graph` and `api/meta` and routes on the hash
fragment, so serving dist/ statically needs no SPA fallback route.

Staging (SPEC.md section 8.2) writes local state only -- `.stitch/staged_relationships.yml`,
`.stitch/staged_descriptions.yml` and `.stitch/layout.yml`, never the dbt repo. Repo writes
happen only through the apply endpoints (issue #72), which run the same engine
`stitch apply` runs (`stitch_lineage.apply`) with the same guards and no force path: this
module never touches `write/` itself. Both surfaces register only when `stitch serve` passes
the paths and the apply context; the static export has no server at all, so it stays
read-only by construction.
"""

from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict

from stitch_lineage import apply as apply_service
from stitch_lineage.app import StitchAppError, frontend_dist
from stitch_lineage.graph.schema import Graph, NodeType, column_node_id
from stitch_lineage.graph.suggest import Suggestion, suggest
from stitch_lineage.io.artifacts import StitchArtifactError
from stitch_lineage.io.graph_store import read_graph
from stitch_lineage.io.layout_store import (
    LAYOUT_FILENAME,
    LayoutStoreError,
    add_dismissed,
    read_dismissed,
)
from stitch_lineage.io.staged_store import (
    DESCRIPTIONS_FILENAME,
    StagedDescription,
    StagedRelationship,
    StagedStoreError,
    add_staged,
    read_descriptions,
    read_staged,
    remove_description,
    remove_staged,
    replace_staged,
    upsert_description,
)

__all__ = ["StitchAppError", "create_app", "frontend_dist"]

# v1 stores simple (single column pair) relationships only; many-to-many is not a stored
# shape (SPEC.md section 8.1) -- it is what the ERD renders from two many-to-one edges.
_CARDINALITIES = ("many-to-one", "one-to-many", "one-to-one")
_SHAPES = ("simple",)


class StagedRelationshipRequest(BaseModel):
    """POST/PUT body for a staged edge; dbt model NAMES, not unique_ids."""

    model_config = ConfigDict(extra="forbid")

    from_model: str
    from_column: str
    to_model: str
    to_column: str
    cardinality: str = "many-to-one"
    shape: str = "simple"


class StagedDescriptionRequest(BaseModel):
    """PUT body for a staged description edit; `column` omitted means the model's own."""

    model_config = ConfigDict(extra="forbid")

    entity: str
    column: str | None = None
    new_description: str


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


def _validate_description(graph: Graph, payload: StagedDescriptionRequest) -> None:
    """422 unless the target exists in the graph and the text is not blank."""
    if not payload.new_description.strip():
        raise HTTPException(
            status_code=422,
            detail="a description cannot be empty -- discard the staged edit instead",
        )
    uid = _model_uid(graph, payload.entity)
    if payload.column is None:
        return
    node_ids = {node.node_id for node in graph.nodes}
    if column_node_id(uid, payload.column) not in node_ids:
        raise HTTPException(
            status_code=422,
            detail=f"'{payload.column}' is not a column of model '{payload.entity}'",
        )


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _entry_payload(entry: StagedRelationship | StagedDescription) -> dict:
    """A staged change as the app sees it -- `kind` tells the workspace how to render it."""
    return {"kind": entry.kind, "label": entry.label, **entry.model_dump(mode="json")}


def _entry_problem(result, plan: apply_service.ApplyPlan) -> dict:
    return {
        "entry": _entry_payload(result.entry),
        "reason": result.message,
        "path": plan.relative(result.path) if result.path else None,
    }


def _plan_or_error(context: apply_service.ApplyContext) -> apply_service.ApplyPlan:
    """Plan the apply, translating the engine's errors into HTTP."""
    try:
        return context.plan()
    except StagedStoreError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except StitchArtifactError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except NotImplementedError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _preview_payload(plan: apply_service.ApplyPlan) -> dict:
    return {
        "write_to": plan.write_to,
        "staged": {
            "relationships": len(plan.changes.relationships),
            "descriptions": len(plan.changes.descriptions),
        },
        "files": [{"path": preview.path, "diff": preview.diff} for preview in plan.files()],
        "unappliable": [_entry_problem(result, plan) for result in plan.failures],
        "unchanged": [_entry_problem(result, plan) for result in plan.unchanged],
    }


def create_app(
    graph_path: Path,
    metabase_url: str | None,
    erd_default_scope: str | None = None,
    staged_path: Path | None = None,
    layout_path: Path | None = None,
    descriptions_path: Path | None = None,
    apply_context: apply_service.ApplyContext | None = None,
    strip_model_prefixes: list[str] | None = None,
    table_prefixes: list[str] | None = None,
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
    land in `layout_path` and staged description edits in `descriptions_path`, both
    defaulting to their file beside the staged store.

    `apply_context` enables the apply endpoints -- the only routes that write the dbt repo.
    They run the same engine as `stitch apply` and refuse dirty files with no force path
    (SPEC.md section 8.2), and /api/meta reports apply_enabled so the SPA can hide the button.

    `table_prefixes` are the per-database metabase.databases[].table_prefix values. Binding
    already strips them (resolve/bind.py); passing them on lets the app hide them from the
    physical names it DISPLAYS too, which on a dev target are the reader's own initials.
    """
    dist = frontend_dist()
    layout = layout_path or (staged_path.parent / LAYOUT_FILENAME if staged_path else None)
    descriptions = descriptions_path or (
        staged_path.parent / DESCRIPTIONS_FILENAME if staged_path else None
    )
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
                "table_prefixes": list(table_prefixes or []),
                "staging_enabled": staged_path is not None,
                "apply_enabled": apply_context is not None,
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

        @api.put("/api/staged-relationships/{relationship_id}")
        def api_staged_update(
            relationship_id: str, payload: StagedRelationshipRequest
        ) -> JSONResponse:
            # editing endpoints re-hashes the id, so this is a replace: `moved` tells the app
            # the entry it edited now lives under a different id (and the canvas must restyle)
            graph = _load_graph(graph_path)
            _validate(graph, payload)
            try:
                result = replace_staged(
                    relationship_id, StagedRelationship(**payload.model_dump()), staged_path
                )
            except StagedStoreError as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            if result is None:
                raise HTTPException(
                    status_code=404, detail=f"no staged relationship with id '{relationship_id}'"
                )
            stored, moved = result
            return JSONResponse({"relationship": stored.model_dump(mode="json"), "moved": moved})

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

    if descriptions is not None:

        @api.get("/api/staged-descriptions")
        def api_staged_descriptions() -> JSONResponse:
            try:
                entries = read_descriptions(descriptions)
            except StagedStoreError as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            return JSONResponse(
                {"descriptions": [entry.model_dump(mode="json") for entry in entries]}
            )

        @api.put("/api/staged-descriptions")
        def api_staged_description_upsert(payload: StagedDescriptionRequest) -> JSONResponse:
            # keyed on entity+column, so re-editing the same description replaces the staged
            # edit rather than queueing a second one behind it
            graph = _load_graph(graph_path)
            _validate_description(graph, payload)
            candidate = StagedDescription(**payload.model_dump(), created_at=_now())
            try:
                stored, created = upsert_description(candidate, descriptions)
            except StagedStoreError as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            return JSONResponse(
                {"description": stored.model_dump(mode="json"), "created": created},
                status_code=201 if created else 200,
            )

        @api.delete("/api/staged-descriptions/{description_id}")
        def api_staged_description_delete(description_id: str) -> Response:
            try:
                removed = remove_description(description_id, descriptions)
            except StagedStoreError as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            if not removed:
                raise HTTPException(
                    status_code=404, detail=f"no staged description with id '{description_id}'"
                )
            return Response(status_code=204)

    if apply_context is not None:

        @api.get("/api/writeability")
        def api_writeability() -> JSONResponse:
            """Which models a declaration can be written into, asked before staging (#132).

            The app uses this to withhold an affordance rather than take an edit and
            refuse it at apply time. A model missing from the map is one stitch has no
            opinion about; the app treats that as writable and lets apply have the
            final word, which is the behaviour that predates this endpoint.
            """
            try:
                entries = apply_context.writeability()
            except (StitchArtifactError, StagedStoreError) as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            return JSONResponse(
                {
                    "models": {
                        name: {
                            "writable": item.writable,
                            "reason": item.reason,
                            "path": item.path,
                        }
                        for name, item in entries.items()
                    }
                }
            )

        @api.post("/api/apply/preview")
        def api_apply_preview() -> JSONResponse:
            return JSONResponse(_preview_payload(_plan_or_error(apply_context)))

        @api.post("/api/apply")
        def api_apply() -> JSONResponse:
            plan = _plan_or_error(apply_context)
            # no force from the app, ever: a target file with uncommitted edits is refused and
            # reported, and overwriting it stays a `stitch apply --force` decision
            refused = apply_service.refusals(plan)
            outcome = apply_service.execute(plan, refused)
            graph = outcome.graph
            return JSONResponse(
                {
                    "written": [plan.relative(path) for path in outcome.written],
                    "refused": [
                        {
                            "path": plan.relative(path),
                            "reason": "the file has uncommitted changes -- commit or stash it, "
                            "or run 'stitch apply --force' from the CLI",
                        }
                        for path in outcome.refused
                    ],
                    "applied": outcome.applied,
                    "still_staged": outcome.still_staged,
                    "unappliable": [_entry_problem(result, plan) for result in plan.failures],
                    "graph": {
                        "patched": bool(graph and graph.patched),
                        "edges_added": graph.edges_added if graph else 0,
                        "descriptions_updated": graph.descriptions_updated if graph else 0,
                        "skipped": list(graph.skipped) if graph else [],
                        "note": graph.note if graph else None,
                    },
                }
            )

    api.mount("/", StaticFiles(directory=dist, html=True), name="app")
    return api
