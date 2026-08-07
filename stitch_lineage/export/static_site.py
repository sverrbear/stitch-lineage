"""Read-only static build of the SPA with graph.json inlined (SPEC.md section 9)."""

import json
import shutil
from pathlib import Path
from typing import Any

from stitch_lineage.app import StitchAppError, frontend_dist

_MARKER = "/* __STITCH_INLINE_DATA__ */"


def _script_literal(payload: Any) -> str:
    """Compact deterministic JSON that cannot terminate the surrounding <script> element."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).replace("</", "<\\/")


def export_site(graph_path: Path, out_dir: Path, metabase_url: str | None) -> Path:
    """Copy the built SPA into out_dir with the graph inlined; return out_dir.

    The graph is passed through as parsed JSON rather than re-validated, so a graph
    written by a newer stitch still exports. Raises StitchAppError when the frontend
    build is missing, ValueError when graph.json does not parse.
    """
    dist = frontend_dist()
    index = (dist / "index.html").read_text(encoding="utf-8")
    if _MARKER not in index:
        raise StitchAppError(
            f"{dist / 'index.html'} has no {_MARKER} injection point -- "
            "rebuild the frontend with 'npm run build'"
        )
    graph = json.loads(graph_path.read_text(encoding="utf-8"))
    meta = {
        "metabase_url": metabase_url,
        "generated_at": graph.get("generated_at"),
        "schema_version": graph.get("schema_version", 1),
    }
    inlined = index.replace(
        _MARKER,
        f"window.__STITCH_GRAPH__ = {_script_literal(graph)};\n"
        f"      window.__STITCH_META__ = {_script_literal(meta)};",
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(dist, out_dir, dirs_exist_ok=True)
    (out_dir / "index.html").write_text(inlined, encoding="utf-8")
    return out_dir
