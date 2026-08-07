"""The local app: the prebuilt React SPA plus the FastAPI server that hosts it.

Seam (SPEC.md section 4): app/ consumes graph.json via graph/schema and io/graph_store --
never resolve/ or io/metabase_client. This module deliberately imports nothing but the
stdlib so `stitch export --format site` can locate the bundled build without pulling in
FastAPI.
"""

from pathlib import Path

_DIST = Path(__file__).resolve().parent / "frontend" / "dist"


class StitchAppError(Exception):
    """The bundled frontend build is missing or unusable."""


def frontend_dist() -> Path:
    """Directory of the built SPA shipped inside the wheel."""
    if not (_DIST / "index.html").is_file():
        raise StitchAppError(
            f"frontend not built -- no index.html in {_DIST}: run 'npm run build' in "
            "stitch_lineage/app/frontend, or install stitch from a release wheel"
        )
    return _DIST
