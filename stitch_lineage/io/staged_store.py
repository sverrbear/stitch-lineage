"""Read/write .stitch/staged_relationships.yml -- the plan half of plan/apply (SPEC.md section 8.2).

Drawing an edge in the app never touches the repo: the declaration lands here, survives
restarts, and `stitch apply` materializes it into model YAML. The file lives with the rest
of the local state under output.dir and is never committed.

Determinism contract: entries sorted by (from_model, from_column, to_model, to_column, id),
field order fixed, trailing newline -- so the file diffs cleanly and repeated writes of the
same set are byte-identical. Writes are atomic (temp file + os.replace) because `stitch serve`
rewrites the whole file on every POST/DELETE while `stitch apply` may be reading it.
"""

import hashlib
import os
import tempfile
from io import StringIO
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator
from ruamel.yaml import YAML, YAMLError

__all__ = [
    "STAGED_FILENAME",
    "StagedRelationship",
    "StagedStoreError",
    "add_staged",
    "drop_staged",
    "read_staged",
    "relationship_id",
    "remove_staged",
    "staged_path",
    "write_staged",
]

STAGED_FILENAME = "staged_relationships.yml"
SCHEMA_VERSION = 1

_HEADER = (
    "# stitch staged relationships -- drawn in `stitch serve`, materialized by `stitch apply`.\n"
    "# Local state (SPEC.md section 8.2): never commit this file.\n"
)


class StagedStoreError(Exception):
    """staged_relationships.yml exists but is unusable; the message names the fix."""


def relationship_id(from_model: str, from_column: str, to_model: str, to_column: str) -> str:
    """Deterministic id for a relationship's endpoints.

    Endpoints only: re-staging the same column pair with a different cardinality is the
    same relationship, so it dedupes instead of stacking up a second entry.
    """
    payload = f"{from_model}.{from_column}->{to_model}.{to_column}".lower()
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


class StagedRelationship(BaseModel):
    """One staged declaration: a column pair, its cardinality, and its shape.

    Models are dbt model NAMES (not unique_ids) -- the app draws on names and the writer
    resolves them against the manifest at apply time, so a package rename never strands
    the store. `id` is derived from the endpoints and recomputed on every load; a value
    read from the file is overwritten rather than trusted.
    """

    model_config = ConfigDict(extra="ignore")

    id: str = ""
    from_model: str
    from_column: str
    to_model: str
    to_column: str
    cardinality: str = "many-to-one"
    shape: str = "simple"
    created_at: str | None = None

    @model_validator(mode="after")
    def _derive_id(self) -> "StagedRelationship":
        self.id = relationship_id(self.from_model, self.from_column, self.to_model, self.to_column)
        return self

    @property
    def label(self) -> str:
        return (
            f"{self.from_model}.{self.from_column} -> {self.to_model}.{self.to_column} "
            f"({self.cardinality})"
        )

    def sort_key(self) -> tuple[str, ...]:
        return (self.from_model, self.from_column, self.to_model, self.to_column, self.id)


def staged_path(output_dir: Path) -> Path:
    return Path(output_dir) / STAGED_FILENAME


def _yaml() -> YAML:
    yaml = YAML(typ="safe")
    yaml.default_flow_style = False
    yaml.representer.sort_base_mapping_type_on_output = False
    yaml.allow_unicode = True
    return yaml


def read_staged(path: Path) -> list[StagedRelationship]:
    """Load the store, sorted. A missing file is an empty store, not an error."""
    path = Path(path)
    if not path.is_file():
        return []
    try:
        raw = _yaml().load(path.read_text(encoding="utf-8"))
    except (YAMLError, OSError, UnicodeDecodeError) as exc:
        raise StagedStoreError(
            f"could not parse {path}: {exc} -- delete the file to reset the staging store"
        ) from exc
    if raw is None:
        return []
    if not isinstance(raw, dict):
        raise StagedStoreError(
            f"{path} must contain a YAML mapping -- delete the file to reset the staging store"
        )
    items = raw.get("relationships") or []
    if not isinstance(items, list):
        raise StagedStoreError(f"{path}: 'relationships' must be a list")
    entries = []
    for item in items:
        if not isinstance(item, dict):
            raise StagedStoreError(f"{path}: every relationship must be a mapping")
        try:
            entries.append(StagedRelationship.model_validate(item))
        except ValueError as exc:
            raise StagedStoreError(f"{path}: invalid relationship {item!r}: {exc}") from exc
    return sorted(entries, key=StagedRelationship.sort_key)


def write_staged(entries: list[StagedRelationship], path: Path) -> None:
    """Write the store atomically, deduped by id and deterministically ordered."""
    path = Path(path)
    unique: dict[str, StagedRelationship] = {}
    for entry in entries:
        unique.setdefault(entry.id, entry)
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "relationships": [
            entry.model_dump(mode="json")
            for entry in sorted(unique.values(), key=StagedRelationship.sort_key)
        ],
    }
    buffer = StringIO()
    _yaml().dump(payload, buffer)
    _atomic_write(path, _HEADER + buffer.getvalue())


def add_staged(entry: StagedRelationship, path: Path) -> tuple[StagedRelationship, bool]:
    """Stage `entry`; return (stored entry, created). An existing id is returned untouched.

    Re-staging an existing pair never rewrites its cardinality -- the caller deletes and
    re-posts to change one, so a stale UI cannot silently flip a declaration.
    """
    entries = read_staged(path)
    existing = next((item for item in entries if item.id == entry.id), None)
    if existing is not None:
        return existing, False
    write_staged([*entries, entry], path)
    return entry, True


def remove_staged(entry_id: str, path: Path) -> bool:
    """Drop one entry by id; return whether it was there."""
    entries = read_staged(path)
    remaining = [entry for entry in entries if entry.id != entry_id]
    if len(remaining) == len(entries):
        return False
    write_staged(remaining, path)
    return True


def drop_staged(entry_ids: set[str], path: Path) -> int:
    """Drop every id in `entry_ids`; return how many were removed (`stitch apply` clearing)."""
    entries = read_staged(path)
    remaining = [entry for entry in entries if entry.id not in entry_ids]
    removed = len(entries) - len(remaining)
    if removed:
        write_staged(remaining, path)
    return removed


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
