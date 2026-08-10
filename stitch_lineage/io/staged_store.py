"""Read/write the staged stores -- the plan half of plan/apply (SPEC.md section 8.2).

Editing in the app never touches the repo: the change lands here, survives restarts, and
`stitch apply` materializes it into model YAML. Two sibling files under output.dir, never
committed: `staged_relationships.yml` (drawn edges) and `staged_descriptions.yml` (column
and model documentation edits).

Determinism contract: entries sorted by their sort_key, field order fixed, trailing newline
-- so the files diff cleanly and repeated writes of the same set are byte-identical. Writes
are atomic (temp file + os.replace) because `stitch serve` rewrites a whole file on every
POST/PUT/DELETE while `stitch apply` may be reading it.
"""

import hashlib
import os
import tempfile
from io import StringIO
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator
from ruamel.yaml import YAML, YAMLError

from stitch_lineage.graph.schema import relationship_id

__all__ = [
    "DESCRIPTIONS_FILENAME",
    "STAGED_FILENAME",
    "StagedChange",
    "StagedDescription",
    "StagedRelationship",
    "StagedStoreError",
    "add_staged",
    "description_id",
    "descriptions_path",
    "drop_descriptions",
    "drop_staged",
    "read_descriptions",
    "read_staged",
    "relationship_id",
    "remove_description",
    "remove_staged",
    "replace_staged",
    "staged_path",
    "upsert_description",
    "write_descriptions",
    "write_staged",
]

STAGED_FILENAME = "staged_relationships.yml"
DESCRIPTIONS_FILENAME = "staged_descriptions.yml"
SCHEMA_VERSION = 1

_HEADER = (
    "# stitch staged relationships -- drawn in `stitch serve`, materialized by `stitch apply`.\n"
    "# Local state (SPEC.md section 8.2): never commit this file.\n"
)
_DESCRIPTIONS_HEADER = (
    "# stitch staged descriptions -- edited in `stitch serve`, materialized by `stitch apply`.\n"
    "# Local state (SPEC.md section 8.2): never commit this file.\n"
)


class StagedStoreError(Exception):
    """A staged store file exists but is unusable; the message names the fix."""


def description_id(entity: str, column: str | None) -> str:
    """Deterministic id for a documentation edit: one per entity+column.

    Namespaced away from relationship_id so the two stores can never mint the same id, and
    keyed on the target only -- re-editing the same description replaces it (last write wins)
    instead of stacking up a second entry.
    """
    payload = f"description:{entity}.{column or ''}".lower()
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
    def kind(self) -> str:
        return "relationship"

    @property
    def label(self) -> str:
        return (
            f"{self.from_model}.{self.from_column} -> {self.to_model}.{self.to_column} "
            f"({self.cardinality})"
        )

    def sort_key(self) -> tuple[str, ...]:
        return (self.from_model, self.from_column, self.to_model, self.to_column, self.id)


class StagedDescription(BaseModel):
    """One staged documentation edit: the new description for a model or one of its columns.

    `entity` is a dbt model NAME and `column` is None for the model's own description, the
    same name-based addressing the relationship store uses. `id` is derived from the target
    and recomputed on every load, so a value read from the file is overwritten rather than
    trusted -- and editing the same target twice replaces the entry instead of duplicating it.
    """

    model_config = ConfigDict(extra="ignore")

    id: str = ""
    entity: str
    column: str | None = None
    new_description: str
    created_at: str | None = None

    @model_validator(mode="after")
    def _derive_id(self) -> "StagedDescription":
        self.id = description_id(self.entity, self.column)
        return self

    @property
    def kind(self) -> str:
        return "description"

    @property
    def label(self) -> str:
        target = f"{self.entity}.{self.column}" if self.column else self.entity
        return f"{target} description"

    def sort_key(self) -> tuple[str, ...]:
        return (self.entity, self.column or "", self.id)


# every staged change `stitch apply` can materialize -- the writer plans over the union
StagedChange = StagedRelationship | StagedDescription


def staged_path(output_dir: Path) -> Path:
    return Path(output_dir) / STAGED_FILENAME


def descriptions_path(output_dir: Path) -> Path:
    return Path(output_dir) / DESCRIPTIONS_FILENAME


def _yaml() -> YAML:
    yaml = YAML(typ="safe")
    yaml.default_flow_style = False
    yaml.representer.sort_base_mapping_type_on_output = False
    yaml.allow_unicode = True
    return yaml


def _read_entries(path: Path, key: str, model: type[BaseModel], noun: str) -> list[Any]:
    """Load one staged store, sorted. A missing file is an empty store, not an error."""
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
    items = raw.get(key) or []
    if not isinstance(items, list):
        raise StagedStoreError(f"{path}: '{key}' must be a list")
    entries = []
    for item in items:
        if not isinstance(item, dict):
            raise StagedStoreError(f"{path}: every {noun} must be a mapping")
        try:
            entries.append(model.model_validate(item))
        except ValueError as exc:
            raise StagedStoreError(f"{path}: invalid {noun} {item!r}: {exc}") from exc
    return sorted(entries, key=lambda entry: entry.sort_key())


def _write_entries(entries: list[Any], path: Path, key: str, header: str) -> None:
    """Write one staged store atomically, deduped by id and deterministically ordered."""
    path = Path(path)
    unique: dict[str, Any] = {}
    for entry in entries:
        unique.setdefault(entry.id, entry)
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        key: [
            entry.model_dump(mode="json")
            for entry in sorted(unique.values(), key=lambda entry: entry.sort_key())
        ],
    }
    buffer = StringIO()
    _yaml().dump(payload, buffer)
    _atomic_write(path, header + buffer.getvalue())


def read_staged(path: Path) -> list[StagedRelationship]:
    """Load the relationship store, sorted. A missing file is an empty store, not an error."""
    return _read_entries(path, "relationships", StagedRelationship, "relationship")


def write_staged(entries: list[StagedRelationship], path: Path) -> None:
    """Write the relationship store atomically, deduped by id and deterministically ordered."""
    _write_entries(list(entries), path, "relationships", _HEADER)


def read_descriptions(path: Path) -> list[StagedDescription]:
    """Load the description store, sorted. A missing file is an empty store, not an error."""
    return _read_entries(path, "descriptions", StagedDescription, "description")


def write_descriptions(entries: list[StagedDescription], path: Path) -> None:
    """Write the description store atomically, deduped by id and deterministically ordered."""
    _write_entries(list(entries), path, "descriptions", _DESCRIPTIONS_HEADER)


def add_staged(entry: StagedRelationship, path: Path) -> tuple[StagedRelationship, bool]:
    """Stage `entry`; return (stored entry, created). An existing id is returned untouched.

    Re-staging an existing pair never rewrites its cardinality -- PUT
    /api/staged-relationships/{id} (or delete + re-post) changes one, so a stale UI cannot
    silently flip a declaration behind the user's back.
    """
    entries = read_staged(path)
    existing = next((item for item in entries if item.id == entry.id), None)
    if existing is not None:
        return existing, False
    write_staged([*entries, entry], path)
    return entry, True


def replace_staged(
    entry_id: str, entry: StagedRelationship, path: Path
) -> tuple[StagedRelationship, bool] | None:
    """Replace the entry at `entry_id` with `entry`; None when `entry_id` is not staged.

    Editing endpoints re-hashes the id, so this is a replace, not an in-place mutation: the
    old entry goes and the new one lands. Returns (stored entry, moved) -- `moved` says the
    id changed. When the new endpoints are already staged under another id, that existing
    entry wins (the edit collapses into it) and the edited one is dropped, so an edit can
    never create a duplicate pair.
    """
    entries = read_staged(path)
    if all(item.id != entry_id for item in entries):
        return None
    remaining = [item for item in entries if item.id != entry_id]
    collision = next((item for item in remaining if item.id == entry.id), None)
    stored = collision or entry
    write_staged([*remaining, stored], path)
    return stored, stored.id != entry_id


def upsert_description(entry: StagedDescription, path: Path) -> tuple[StagedDescription, bool]:
    """Stage a description edit; return (stored entry, created).

    Last write wins per entity+column: the id is derived from the target, so a second edit of
    the same description replaces the first instead of queueing behind it.
    """
    entries = read_descriptions(path)
    created = all(item.id != entry.id for item in entries)
    remaining = [item for item in entries if item.id != entry.id]
    write_descriptions([*remaining, entry], path)
    return entry, created


def remove_staged(entry_id: str, path: Path) -> bool:
    """Drop one relationship by id; return whether it was there."""
    return _remove(entry_id, path, read_staged, write_staged)


def remove_description(entry_id: str, path: Path) -> bool:
    """Drop one description edit by id; return whether it was there."""
    return _remove(entry_id, path, read_descriptions, write_descriptions)


def drop_staged(entry_ids: set[str], path: Path) -> int:
    """Drop every id in `entry_ids`; return how many were removed (`stitch apply` clearing)."""
    return _drop(entry_ids, path, read_staged, write_staged)


def drop_descriptions(entry_ids: set[str], path: Path) -> int:
    """Drop every id in `entry_ids` from the description store; return how many were removed."""
    return _drop(entry_ids, path, read_descriptions, write_descriptions)


def _remove(entry_id: str, path: Path, read: Any, write: Any) -> bool:
    entries = read(path)
    remaining = [entry for entry in entries if entry.id != entry_id]
    if len(remaining) == len(entries):
        return False
    write(remaining, path)
    return True


def _drop(entry_ids: set[str], path: Path, read: Any, write: Any) -> int:
    entries = read(path)
    remaining = [entry for entry in entries if entry.id not in entry_ids]
    removed = len(entries) - len(remaining)
    if removed:
        write(remaining, path)
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
