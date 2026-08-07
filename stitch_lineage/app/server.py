"""FastAPI app behind `stitch serve`: the built SPA plus a two-endpoint read-only API.

The SPA fetches the relative paths `api/graph` and `api/meta` and routes on the hash
fragment, so serving dist/ statically needs no SPA fallback route.
"""

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from stitch_lineage.app import StitchAppError, frontend_dist
from stitch_lineage.graph.schema import Graph
from stitch_lineage.io.graph_store import read_graph

__all__ = ["StitchAppError", "create_app", "frontend_dist"]


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


def create_app(
    graph_path: Path, metabase_url: str | None, erd_default_scope: str | None = None
) -> FastAPI:
    """Build the local app serving `graph_path`; `metabase_url` powers card deep links.

    `erd_default_scope` ("schema:<name>" / "tag:<name>", from serve.erd_default_scope)
    is passed through as configured -- the app falls back to its auto-picked scope and
    notes the mismatch when the graph has no such scope.
    """
    dist = frontend_dist()
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
            }
        )

    api.mount("/", StaticFiles(directory=dist, html=True), name="app")
    return api
