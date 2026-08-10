"""Read/write .stitch/layout.yml -- presentation state, not contract (SPEC.md section 9).

Layout is where the ERD's own memory lives: node positions and saved views later, and
dismissed suggestions now. Nothing here changes what the graph means, so it is local
state next to the rest of `.stitch/` and never touches model YAML.

Unknown top-level keys are preserved on write: this file is shared with whatever the
canvas stores, so a dismissal must not erase someone's positions. Writes are atomic
(temp file + os.replace) and deterministic (fixed key order, sorted ids, trailing
newline) for the same reasons as the staged store -- `stitch serve` rewrites the whole
file on every dismissal.
"""

import os
import tempfile
from io import StringIO
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML, YAMLError

__all__ = [
    "DISMISSED_KEY",
    "LAYOUT_FILENAME",
    "LayoutStoreError",
    "add_dismissed",
    "layout_path",
    "read_dismissed",
    "read_layout",
    "write_layout",
]

LAYOUT_FILENAME = "layout.yml"
DISMISSED_KEY = "dismissed_suggestions"
SCHEMA_VERSION = 1

_HEADER = (
    "# stitch layout -- ERD presentation state (SPEC.md section 9).\n"
    "# Local state: positions, saved views and dismissed suggestions.\n"
)


class LayoutStoreError(Exception):
    """layout.yml exists but is unusable; the message names the fix."""


def layout_path(output_dir: Path) -> Path:
    return Path(output_dir) / LAYOUT_FILENAME


def _yaml() -> YAML:
    yaml = YAML(typ="safe")
    yaml.default_flow_style = False
    yaml.representer.sort_base_mapping_type_on_output = False
    yaml.allow_unicode = True
    return yaml


def read_layout(path: Path) -> dict[str, Any]:
    """Load the whole document. A missing file is an empty layout, not an error."""
    path = Path(path)
    if not path.is_file():
        return {}
    try:
        raw = _yaml().load(path.read_text(encoding="utf-8"))
    except (YAMLError, OSError, UnicodeDecodeError) as exc:
        raise LayoutStoreError(
            f"could not parse {path}: {exc} -- delete the file to reset the ERD layout"
        ) from exc
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise LayoutStoreError(
            f"{path} must contain a YAML mapping -- delete the file to reset the ERD layout"
        )
    return raw


def read_dismissed(path: Path) -> list[str]:
    """Dismissed suggestion ids, sorted and deduped."""
    layout = read_layout(path)
    ids = layout.get(DISMISSED_KEY) or []
    if not isinstance(ids, list):
        raise LayoutStoreError(f"{path}: '{DISMISSED_KEY}' must be a list of suggestion ids")
    if not all(isinstance(entry, str) for entry in ids):
        raise LayoutStoreError(f"{path}: '{DISMISSED_KEY}' must contain strings only")
    return sorted(set(ids))


def write_layout(layout: dict[str, Any], path: Path) -> None:
    """Write the document atomically: schema_version first, then keys sorted, dismissals
    sorted -- so two writers holding the same layout produce the same bytes."""
    path = Path(path)
    payload: dict[str, Any] = {"schema_version": layout.get("schema_version", SCHEMA_VERSION)}
    payload.update({key: layout[key] for key in sorted(layout) if key != "schema_version"})
    if DISMISSED_KEY in payload:
        payload[DISMISSED_KEY] = sorted(set(payload[DISMISSED_KEY]))
    buffer = StringIO()
    _yaml().dump(payload, buffer)
    _atomic_write(path, _HEADER + buffer.getvalue())


def add_dismissed(suggestion_id: str, path: Path) -> bool:
    """Record a dismissal; return whether it was new. Everything else in the file survives."""
    layout = read_layout(path)
    dismissed = read_dismissed(path)
    if suggestion_id in dismissed:
        return False
    layout[DISMISSED_KEY] = [*dismissed, suggestion_id]
    write_layout(layout, path)
    return True


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
