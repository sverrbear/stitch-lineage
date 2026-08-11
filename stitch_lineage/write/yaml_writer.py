"""ruamel round-trip writer for model YAML (SPEC.md sections 4 and 8.2).

The one non-negotiable rule: a `stitch apply` diff contains the inserted declaration and
nothing else. Comments, quoting, anchors, blank lines and key order of everything we do not
touch survive byte-identically. Two mechanisms enforce it:

  * the emitter is configured from the file's own indentation style (dash-indented and
    compact block sequences both round-trip), and
  * every file is proof-round-tripped before it is edited -- load + dump the pristine text
    and compare bytes. A file stitch cannot reproduce exactly is reported as unappliable
    instead of being reformatted.

Planning is separate from writing: `plan_writes` returns the new text for every target file
(accumulating multiple relationships per file), `WritePlan.diff` renders the preview and
`apply_plan` performs the atomic writes.
"""

import difflib
import os
import re
import tempfile
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import Any, Literal

from ruamel.yaml import YAML, YAMLError
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.scalarstring import LiteralScalarString

from stitch_lineage.config import RelationshipsConfig, WriteConfig
from stitch_lineage.io.staged_store import StagedChange, StagedDescription, StagedRelationship

__all__ = [
    "EntryResult",
    "FileEdit",
    "ModelWriteability",
    "WritePlan",
    "YamlWriteError",
    "apply_plan",
    "model_writeability",
    "plan_migration",
    "plan_writes",
]

_DASH_RE = re.compile(r"^(?P<indent>[ ]*)-[ \t]")
_REF_RE = re.compile(r"""ref\(\s*['"]([^'"]+)['"]\s*(?:,\s*['"]([^'"]+)['"]\s*)?\)""")
_TESTS_KEYS = ("data_tests", "tests")

EntryStatus = Literal["planned", "unchanged", "failed"]


class YamlWriteError(Exception):
    """A staged relationship cannot be written; the message names why and what to do."""


@dataclass(frozen=True)
class FileEdit:
    """The full before/after text of one schema file -- the unit of both diff and write."""

    path: Path
    original: str
    updated: str

    @property
    def changed(self) -> bool:
        return self.original != self.updated

    def diff(self, root: Path | None = None) -> str:
        label = self.path
        if root is not None:
            try:
                label = self.path.relative_to(root)
            except ValueError:
                label = self.path
        name = Path(label).as_posix()
        return "".join(
            difflib.unified_diff(
                self.original.splitlines(keepends=True),
                self.updated.splitlines(keepends=True),
                fromfile=f"a/{name}",
                tofile=f"b/{name}",
            )
        )


@dataclass(frozen=True)
class EntryResult:
    """What planning decided about one staged change.

    planned   -- the edit is in the plan and the entry clears from the store once written
    unchanged -- the repo already says this; nothing to write, entry clears
    failed    -- unappliable (no schema file, unknown model, conflicting declaration); the
                 entry stays staged and the message says why
    """

    entry: StagedChange
    status: EntryStatus
    path: Path | None = None
    message: str | None = None


@dataclass(frozen=True)
class WritePlan:
    edits: list[FileEdit]
    results: list[EntryResult]

    @property
    def failures(self) -> list[EntryResult]:
        return [result for result in self.results if result.status == "failed"]

    @property
    def unchanged(self) -> list[EntryResult]:
        return [result for result in self.results if result.status == "unchanged"]

    @property
    def planned(self) -> list[EntryResult]:
        return [result for result in self.results if result.status == "planned"]

    def ids_for(self, paths: set[Path]) -> set[str]:
        """Ids that clear from the staging store once `paths` are written.

        Entries whose declaration was already in the repo clear too -- there is nothing
        left for them to do, whichever files ended up being written.
        """
        ids = {result.entry.id for result in self.unchanged}
        ids |= {result.entry.id for result in self.planned if result.path in paths}
        return ids

    def diff(self, root: Path | None = None) -> str:
        return "".join(edit.diff(root) for edit in self.edits if edit.changed)


def plan_migration(
    manifest: dict[str, Any],
    project_dir: Path,
    relationships: RelationshipsConfig | None = None,
) -> WritePlan:
    """Rewrite `metabase.fk_*` meta declarations into the configured write form (#135).

    Repos that declared relationships before the default flipped (#134) carry the FK
    fact in two meta keys that only dbt-metabase reads. This restates each of them as
    the configured form and REMOVES the now-redundant keys, so the declaration lives
    in one place instead of two.

    What it deliberately does not touch:
      * `cardinality_meta_key` -- a relationships test has no field for arity, so that
        key is the only thing remembering it (#134);
      * model-level `stitch.relationships` entries -- composite and conceptual
        relationships have no test form to migrate INTO, so rewriting them would lose
        information rather than move it;
      * anything at all when `write_to` is already `meta`, which is a no-op by
        definition rather than a rewrite worth previewing.

    It also does not route through `write.strip_model_prefixes`, unlike `plan_writes`:
    a migration restates a declaration where it already sits and drops the two keys it
    replaces from that same column. Routing would write the test onto one model and
    strip the meta from another, leaving both halves wrong.

    Every guarantee `plan_writes` gives holds here, because it is the same machinery:
    the file is proof-round-tripped before it is touched, the emitter is configured
    from the file's own layout, and blank-line padding is restored and then asserted.
    Writing is still the caller's job via `apply_plan`, so the dirty-file guard and
    the diff-then-confirm flow are unchanged.
    """
    config = relationships or RelationshipsConfig()
    models = _model_index(manifest)
    project_dir = Path(project_dir)
    if config.write_to == "meta":
        return WritePlan(edits=[], results=[])

    originals: dict[Path, str] = {}
    current: dict[Path, str] = {}
    results: list[EntryResult] = []

    for entry in _declared_in_meta(models, config):
        try:
            path = _schema_path(entry, models, project_dir)
            if path not in current:
                text = _read(path)
                _assert_round_trips(path, text, entry)
                originals[path] = text
                current[path] = text
            updated = _migrate_entry(current[path], entry, models, config, path)
        except YamlWriteError as exc:
            results.append(EntryResult(entry=entry, status="failed", message=str(exc)))
            continue
        status: EntryStatus = "planned" if updated != current[path] else "unchanged"
        current[path] = updated
        results.append(
            EntryResult(
                entry=entry,
                status=status,
                path=path,
                message=None if status == "planned" else "already in the target form",
            )
        )

    edits = [
        FileEdit(path=path, original=originals[path], updated=text)
        for path, text in sorted(current.items())
        if originals[path] != text
    ]
    return WritePlan(edits=edits, results=results)


def _declared_in_meta(
    models: dict[str, dict[str, Any]], config: RelationshipsConfig
) -> list[StagedRelationship]:
    """Every simple relationship the manifest carries as `fk_meta_keys`, in a stable order.

    Read from the MANIFEST rather than by parsing YAML: the manifest is what the rest
    of stitch already trusts for "what does this repo declare", and it has already
    merged `config.meta` with legacy top-level `meta`.
    """
    table_key, field_key = config.fk_meta_keys[0], config.fk_meta_keys[1]
    found: list[StagedRelationship] = []
    for name in sorted(models):
        node = models[name]
        for column_name, column in sorted((node.get("columns") or {}).items()):
            meta = _column_meta(column)
            target = meta.get(table_key)
            if not target:
                continue
            to_model = str(target).split(".")[-1]
            to_column = str(meta.get(field_key) or column_name)
            found.append(
                StagedRelationship(
                    id=f"migrate:{name}.{column_name}",
                    from_model=str(node.get("name") or name),
                    from_column=str(column_name),
                    to_model=to_model,
                    to_column=to_column,
                    cardinality=str(meta.get(config.cardinality_meta_key) or "many-to-one"),
                )
            )
    return found


def _column_meta(column: dict[str, Any]) -> dict[str, Any]:
    """`config.meta` merged over legacy top-level `meta`, the way the resolver reads it."""
    merged: dict[str, Any] = {}
    top = column.get("meta")
    if isinstance(top, dict):
        merged.update(top)
    nested = (
        (column.get("config") or {}).get("meta") if isinstance(column.get("config"), dict) else None
    )
    if isinstance(nested, dict):
        merged.update(nested)
    return merged


def _migrate_entry(
    text: str,
    entry: StagedRelationship,
    models: dict[str, dict[str, Any]],
    config: RelationshipsConfig,
    path: Path,
) -> str:
    """Write the declaration in the configured form, then drop the keys it replaces.

    The form is `write_to`, exactly as `plan_writes` uses it -- a migration must land
    what a fresh apply would land, or the repo ends up with two spellings of the same
    thing. `meta` never reaches here (`plan_migration` returns early: migrating to the
    form you are already in is a no-op) and `contract_constraint` is refused with the
    same message the apply path gives it.

    Writing the test also writes `cardinality_meta_key` (#134); dropping the FK keys
    afterwards leaves that one behind on purpose, since it is the only record of the
    arity a `relationships` test cannot express.
    """
    if config.write_to == "contract_constraint":
        raise YamlWriteError(
            "relationships.write_to: contract_constraint is not implemented yet -- "
            "migrate with 'relationships_test' instead"
        )
    yaml = _yaml_for(text)
    document = _load(text, yaml, path)
    model_entry = _model_entry(document, entry.from_model)
    if model_entry is None:
        raise YamlWriteError(
            f"model '{entry.from_model}' has no entry in its schema file -- nothing to migrate"
        )
    column = _ensure_column(model_entry, entry.from_column)
    _write_relationships_test(column, document, entry, config)
    _drop_fk_meta(column, config)
    return _emit(text, document, yaml, path)


def _drop_fk_meta(column: CommentedMap, config: RelationshipsConfig) -> None:
    """Remove the two FK meta keys, and any mapping they leave empty behind them.

    An empty `meta:` or `config:` left standing is exactly the kind of residue the
    migration exists to remove, so the containers go too -- but only when nothing
    else is in them. `cardinality_meta_key` counts as something else.
    """
    for holder in (
        column,
        column.get("config") if isinstance(column.get("config"), dict) else None,
    ):
        if not isinstance(holder, dict):
            continue
        meta = holder.get("meta")
        if not isinstance(meta, dict):
            continue
        for key in config.fk_meta_keys:
            meta.pop(key, None)
        if not meta:
            holder.pop("meta", None)
    block = column.get("config")
    if isinstance(block, dict) and not block:
        column.pop("config", None)


def plan_writes(
    entries: list[StagedChange],
    manifest: dict[str, Any],
    project_dir: Path,
    relationships: RelationshipsConfig | None = None,
    write: WriteConfig | None = None,
) -> WritePlan:
    """Compute the model-YAML edits that materialize `entries`; never touches disk state.

    `entries` mixes both staged change types -- relationship declarations and description
    edits -- and they are planned in order against a running copy of each file, so several
    changes landing in the same schema file accumulate into one edit and one diff.

    `write.strip_model_prefixes` decides which model owns each entry before any of that
    happens, so a declaration drawn on a presentation view lands on the model underneath
    it -- file, entry and `ref()` all follow the routed name together.

    Raises:
        NotImplementedError: relationships.write_to is contract_constraint (SPEC.md
            section 8.1 shape, not implemented in v1).
    """
    config = relationships or RelationshipsConfig()
    prefixes = (write or WriteConfig()).strip_model_prefixes
    if config.write_to == "contract_constraint":
        raise NotImplementedError(
            "relationships.write_to: contract_constraint is not implemented yet -- "
            "use 'relationships_test' or 'meta'"
        )
    models = _model_index(manifest)
    project_dir = Path(project_dir)

    originals: dict[Path, str] = {}
    current: dict[Path, str] = {}
    results: list[EntryResult] = []

    for entry in entries:
        try:
            path = _schema_path(entry, models, project_dir, prefixes)
            if path not in current:
                text = _read(path)
                _assert_round_trips(path, text, entry)
                originals[path] = text
                current[path] = text
            updated = _apply_entry(current[path], entry, models, config, path, prefixes)
        except YamlWriteError as exc:
            results.append(EntryResult(entry=entry, status="failed", message=str(exc)))
            continue
        status: EntryStatus = "planned" if updated != current[path] else "unchanged"
        current[path] = updated
        results.append(
            EntryResult(
                entry=entry,
                status=status,
                path=path,
                message=None if status == "planned" else _unchanged_message(entry),
            )
        )

    edits = [
        FileEdit(path=path, original=originals[path], updated=text)
        for path, text in sorted(current.items())
        if originals[path] != text
    ]
    return WritePlan(edits=edits, results=results)


def apply_plan(edits: list[FileEdit]) -> list[Path]:
    """Write every changed edit atomically; return the paths written, in order."""
    written = []
    for edit in edits:
        if not edit.changed:
            continue
        _atomic_write(edit.path, edit.updated)
        written.append(edit.path)
    return written


def _model_index(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Manifest models keyed by lowercased name; ambiguous names are dropped on purpose."""
    index: dict[str, dict[str, Any]] = {}
    ambiguous: set[str] = set()
    for node in (manifest.get("nodes") or {}).values():
        if node.get("resource_type") != "model":
            continue
        name = str(node.get("name") or "").lower()
        if not name:
            continue
        if name in index:
            ambiguous.add(name)
        index[name] = node
    for name in ambiguous:
        index.pop(name, None)
    return index


def _unchanged_message(entry: StagedChange) -> str:
    if isinstance(entry, StagedDescription):
        return "the repo already has this description"
    return "already declared in the repo"


def _owner_model(
    entry: StagedChange, models: dict[str, dict[str, Any]], prefixes: list[str]
) -> str:
    """The model whose schema file the change is written into."""
    drawn = entry.entity if isinstance(entry, StagedDescription) else entry.from_model
    return _routed(drawn, models, prefixes)


def _routed(name: str, models: dict[str, dict[str, Any]], prefixes: list[str]) -> str:
    """The model a declaration drawn on `name` belongs to (write.strip_model_prefixes).

    Metabase only ever sees the presentation view, so `viz_dim_users` is the only name
    stitch can bind a relationship to -- but the view is not what the repo maintains, and
    in a repo whose viz YAML is generated from the models underneath, a declaration
    written onto the view is regenerated away before anyone runs dbt.

    Routing is conditional on the stripped model actually existing in the manifest, so a
    repo that genuinely owns a model called `viz_something` keeps it, and a configured
    prefix that matches nothing costs nothing. The manifest's own spelling is returned:
    slicing the string would carry the view's casing onto the model's name.
    """
    for prefix in prefixes:
        if not prefix or not name.lower().startswith(prefix.lower()):
            continue
        node = models.get(name[len(prefix) :].lower())
        if node:
            return str(node.get("name") or name[len(prefix) :])
    return name


def _model_or_fail(name: str, models: dict[str, dict[str, Any]], role: str) -> dict[str, Any]:
    node = models.get(name.lower())
    if node is None:
        raise YamlWriteError(
            f"{role} model '{name}' is not in the manifest (or its name is ambiguous) -- "
            "run 'dbt docs generate' and rebuild"
        )
    return node


def _schema_path(
    entry: StagedChange,
    models: dict[str, dict[str, Any]],
    project_dir: Path,
    prefixes: list[str] | None = None,
) -> Path:
    model = _owner_model(entry, models, prefixes or [])
    node = _model_or_fail(model, models, "source")
    patch_path = node.get("patch_path")
    if not patch_path:
        raise YamlWriteError(
            f"model '{model}' has no schema YAML file -- stitch does not invent "
            "one: add a models: entry for it in a _schema.yml, then re-run 'stitch apply'"
        )
    relative = str(patch_path).split("://", 1)[-1]
    path = (Path(project_dir) / relative).resolve()
    if not path.is_file():
        raise YamlWriteError(
            f"schema file {path} for model '{model}' does not exist -- "
            "re-run 'dbt docs generate' so the manifest matches the repo"
        )
    return path


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise YamlWriteError(f"could not read {path}: {exc}") from exc


@dataclass(frozen=True)
class ModelWriteability:
    """Whether a model's declarations can be written, decided before anything is staged.

    The app asks this at load so it can withhold the affordance rather than take an
    edit and refuse it at apply time (#132). `reason` is written to be shown to a
    person in a tooltip, so it says what is wrong with the FILE, not with the edit.
    """

    model: str
    writable: bool
    reason: str | None = None
    path: str | None = None


def model_writeability(
    manifest: dict[str, Any], project_dir: Path, write: WriteConfig | None = None
) -> dict[str, ModelWriteability]:
    """Per-model write-ability for every model in the manifest, keyed by lowercased name.

    One round-trip proof per FILE, not per model -- a schema file with forty models in
    it is parsed once.

    A model that routes under `write.strip_model_prefixes` is judged on the file its
    writes will land in, not the one it is drawn on: the reason is shown to a person
    about to edit, so it has to be about the file that edit will actually touch.
    """
    models = _model_index(manifest)
    prefixes = (write or WriteConfig()).strip_model_prefixes
    project_dir = Path(project_dir)
    per_file: dict[Path, str | None] = {}
    out: dict[str, ModelWriteability] = {}

    for name, drawn in models.items():
        owner = _routed(name, models, prefixes)
        node = models.get(owner.lower(), drawn)
        patch_path = node.get("patch_path")
        if not patch_path:
            out[name] = ModelWriteability(
                model=name,
                writable=False,
                reason=(
                    "this model has no schema YAML file yet -- add a '- name: "
                    f"{owner}' entry under models: in a _schema.yml first"
                ),
            )
            continue
        relative = str(patch_path).split("://", 1)[-1]
        path = (project_dir / relative).resolve()
        if path not in per_file:
            per_file[path] = _file_refusal(path)
        refusal = per_file[path]
        out[name] = ModelWriteability(
            model=name, writable=refusal is None, reason=refusal, path=relative
        )
    return out


def _file_refusal(path: Path) -> str | None:
    """None when stitch can edit this schema file, else the reason it cannot."""
    if not path.is_file():
        return (
            f"its schema file {path.name} is not in the repo -- re-run 'dbt docs "
            "generate' so the manifest matches"
        )
    try:
        text = _read(path)
    except YamlWriteError as exc:
        return str(exc)
    try:
        if _round_trips(text, path):
            return None
    except YamlWriteError as exc:
        return str(exc)
    return (
        f"stitch cannot edit {path.name} without reformatting it -- its layout does not "
        "survive a round trip, most often a file that mixes two list-indentation styles. "
        "Edit it by hand, or normalise the indentation and rebuild."
    )


def _yaml_for(text: str) -> YAML:
    """A round-trip YAML configured from the file's own layout."""
    sequence, offset = _detect_sequence_indent(text)
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.allow_unicode = True
    # ruamel re-wraps at 80 columns by default, which would reflow long descriptions
    yaml.width = 1_000_000
    yaml.indent(mapping=2, sequence=sequence, offset=offset)
    yaml.explicit_start = text.lstrip().startswith("---")
    return yaml


def _detect_sequence_indent(text: str) -> tuple[int, int]:
    """(sequence, offset) for ruamel, read off the first block sequence in the file.

    dbt schema files come in two conventions -- dash indented under its key (offset 2) and
    dash flush with its key (offset 0). Emitting the wrong one reformats every list in the
    file, so it is detected rather than assumed.
    """
    lines = text.splitlines()
    for index, line in enumerate(lines):
        match = _DASH_RE.match(line)
        if match is None:
            continue
        dash_indent = len(match.group("indent"))
        parent_indent = 0
        for previous in reversed(lines[:index]):
            stripped = previous.strip()
            if not stripped or stripped.startswith("#"):
                continue
            indent = len(previous) - len(previous.lstrip(" "))
            if indent < dash_indent or (indent == dash_indent and not _DASH_RE.match(previous)):
                parent_indent = indent
            break
        offset = max(dash_indent - parent_indent, 0)
        return offset + 2, offset
    return 2, 0


def _load(text: str, yaml: YAML, path: Path) -> Any:
    try:
        return yaml.load(text)
    except YAMLError as exc:
        raise YamlWriteError(f"could not parse {path}: {exc}") from exc


def _dump(document: Any, yaml: YAML) -> str:
    buffer = StringIO()
    yaml.dump(document, buffer)
    return buffer.getvalue()


def _is_blank(line: str) -> bool:
    return line.strip() == ""


def _blank_spellings(original: str, pristine: str) -> dict[int, str]:
    """Line index in the emitter's output -> how the author actually spells that blank line.

    Read off the PRISTINE round trip, where the two texts are the same lines in the
    same order, so the correspondence is an index and not a guess.
    """
    before = original.splitlines(keepends=True)
    plain = pristine.splitlines(keepends=True)
    if len(before) != len(plain):
        return {}
    return {
        index: before[index]
        for index in range(len(plain))
        if before[index] != plain[index]
        and _is_blank(before[index].rstrip("\n"))
        and _is_blank(plain[index].rstrip("\n"))
    }


def _restore_blank_padding(original: str, rendered: str, yaml: YAML, path: Path) -> str:
    """Put back the indentation ruamel strips from otherwise-blank lines.

    The case this exists for: a long `description: |` with paragraphs in it has blank
    lines between them, and they are written at the block's own indentation --

        description: |
          Orders, one row per line item.
        ......                              <- six spaces, not an empty line
          Rebuilt nightly by the core pipeline.

    YAML strips block indentation, so the string is identical either way; ruamel
    re-emits that line as truly empty. Nobody can see the difference and no dbt run
    depends on it, but the round-trip proof is byte-for-byte on purpose -- so the
    bytes are restored rather than the guarantee waived. On the Smitten repo two such
    lines were the whole of what stood between a 2156-line intermediate/_schema.yml
    and every write into it (#132).

    The alignment is emitter-output against emitter-output -- the pristine round trip
    against the edited one -- because those differ ONLY by what this edit inserted.
    Aligning the padded original against the edited output instead puts a padded
    blank line next to an inserted block into the same changed region, and the line
    silently loses its padding: exactly the bug this comment exists to prevent.
    """
    try:
        pristine = _dump(_load(original, yaml, path), yaml)
    except YamlWriteError:
        return rendered
    spelling = _blank_spellings(original, pristine)
    if not spelling:
        return rendered
    plain = pristine.splitlines(keepends=True)
    out = rendered.splitlines(keepends=True)
    matcher = difflib.SequenceMatcher(
        None,
        [line.rstrip("\n") for line in plain],
        [line.rstrip("\n") for line in out],
        autojunk=False,
    )
    for tag, i1, _i2, j1, j2 in matcher.get_opcodes():
        if tag != "equal":
            continue
        for offset in range(j2 - j1):
            replacement = spelling.get(i1 + offset)
            if replacement is None:
                continue
            keep_newline = out[j1 + offset].endswith("\n")
            out[j1 + offset] = replacement.rstrip("\n") + ("\n" if keep_newline else "")
    return "".join(out)


def _cannot_round_trip(path: Path, entry: StagedChange | None) -> str:
    """Why the file is refused, and the remedy for the change that asked for it."""
    remedy = (
        "edit the description in the file by hand"
        if isinstance(entry, StagedDescription)
        else "add the relationship by hand"
    )
    return (
        f"{path} cannot be edited without reformatting it (its layout does not survive a "
        f"round trip -- most often a file that mixes two list-indentation styles) -- {remedy} "
        "and discard the staged entry"
    )


def _round_trips(text: str, path: Path) -> bool:
    """Can stitch reproduce this file byte-for-byte, blank-line padding included?"""
    yaml = _yaml_for(text)
    document = _load(text, yaml, path)
    # An empty schema file has no layout to preserve; it fails later, and far more
    # clearly, on having no models: entry to write into.
    if document is None:
        return True
    rendered = _dump(document, yaml)
    if rendered in (text, text + "\n"):
        return True
    repaired = _restore_blank_padding(text, rendered, yaml, path)
    return repaired in (text, text + "\n")


def _assert_round_trips(path: Path, text: str, entry: StagedChange | None = None) -> None:
    """Refuse files stitch cannot reproduce byte-for-byte -- never reformat a repo file."""
    if _round_trips(text, path):
        return
    raise YamlWriteError(_cannot_round_trip(path, entry))


def _assert_no_blank_churn(path: Path, before: str, after: str) -> None:
    """SPEC 8.2, enforced rather than intended.

    `_restore_blank_padding` is a repair, and a repair can be wrong. This is the
    post-condition: if a line we never meant to touch still came back respelled,
    refuse the write instead of quietly reformatting the author's file.
    """
    a = [line.rstrip("\n") for line in before.splitlines(keepends=True)]
    b = [line.rstrip("\n") for line in after.splitlines(keepends=True)]
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        if all(_is_blank(line) for line in a[i1:i2]) and all(_is_blank(line) for line in b[j1:j2]):
            raise YamlWriteError(_cannot_round_trip(path, None))


def _apply_entry(
    text: str,
    entry: StagedChange,
    models: dict[str, dict[str, Any]],
    config: RelationshipsConfig,
    path: Path,
    prefixes: list[str] | None = None,
) -> str:
    yaml = _yaml_for(text)
    document = _load(text, yaml, path)
    prefixes = prefixes or []
    owner = _owner_model(entry, models, prefixes)
    model_entry = _model_entry(document, owner)
    if model_entry is None:
        raise YamlWriteError(
            f"model '{owner}' has no entry in its schema file -- add "
            f"'- name: {owner}' under models: first"
        )
    if isinstance(entry, StagedDescription):
        _write_description(model_entry, entry)
        return _emit(text, document, yaml, path)
    to_model = _routed(entry.to_model, models, prefixes)
    target = _model_or_fail(to_model, models, "target")
    column = _ensure_column(model_entry, entry.from_column)
    if config.write_to == "meta":
        _write_meta(column, entry, target, config, to_model)
    else:
        _write_relationships_test(column, document, entry, config, to_model)
    return _emit(text, document, yaml, path)


def _emit(text: str, document: Any, yaml: YAML, path: Path) -> str:
    """Dump the edited document, then put back what the emitter respells but must not.

    The repair runs against the text this edit started from, so the file's own blank
    lines come back exactly as their author wrote them; `_assert_no_blank_churn` is
    the proof that they did.
    """
    updated = _restore_blank_padding(text, _dump(document, yaml), yaml, path)
    _assert_no_blank_churn(path, text, updated)
    return updated


def _write_description(model_entry: CommentedMap, entry: StagedDescription) -> None:
    """Set `description:` on the model or one of its columns, creating the key if needed.

    Assigning through an existing key keeps its position, its comments and -- because ruamel
    round-trip carries the node's style -- its quoting: replacing a `"quoted"` description
    writes a quoted one back, so the diff is the text and nothing else.
    """
    target = model_entry if entry.column is None else _ensure_column(model_entry, entry.column)
    existing = target.get("description")
    value = _description_scalar(entry.new_description)
    if "description" in target:
        if existing is not None and str(existing).rstrip("\n") == str(value).rstrip("\n"):
            return
        target["description"] = value
        if isinstance(value, LiteralScalarString) and not isinstance(existing, LiteralScalarString):
            _absorb_line_break(target, "description")
        return
    # a fresh key goes right after name:, where dbt convention puts it
    keys = list(target)
    position = keys.index("name") + 1 if "name" in keys else 0
    target.insert(position, "description", value)


def _description_scalar(text: str) -> Any:
    """Multi-line descriptions are emitted as literal block scalars, not quoted one-liners.

    A `\\n` inside a plain or quoted scalar would come back out as an escape or a folded
    line; `|` keeps the text readable in the repo and round-trips unchanged. The trailing
    newline is what makes ruamel choose `|` over `|-`, and both mean the same string here --
    the comparison above ignores it.
    """
    if "\n" not in text:
        return text
    return LiteralScalarString(text if text.endswith("\n") else text + "\n")


def _absorb_line_break(target: CommentedMap, key: str) -> None:
    """A block scalar ends with its own line break, so a blank line remembered after `key`
    would be emitted twice. Drop the duplicate: the author's one blank line stays one."""
    token = target.ca.items.get(key)
    comment = token[2] if token else None
    if comment is None:
        return
    if set(comment.value) <= {"\n"} and comment.value.startswith("\n\n"):
        comment.value = comment.value[1:]


def _model_entry(document: Any, model_name: str) -> CommentedMap | None:
    models = document.get("models") if isinstance(document, dict) else None
    if not isinstance(models, list):
        return None
    for item in models:
        if isinstance(item, dict) and str(item.get("name") or "").lower() == model_name.lower():
            return item
    return None


def _ensure_column(model_entry: CommentedMap, column_name: str) -> CommentedMap:
    columns = model_entry.get("columns")
    if columns is None:
        columns = CommentedSeq()
        model_entry["columns"] = columns
    if not isinstance(columns, list):
        raise YamlWriteError(
            f"columns: on model '{model_entry.get('name')}' is not a list -- fix the schema file"
        )
    for item in columns:
        if isinstance(item, dict) and str(item.get("name") or "").lower() == column_name.lower():
            return item
    column = CommentedMap()
    column["name"] = column_name
    columns.append(column)
    return column


def _tests_key(column: CommentedMap, document: Any) -> str:
    """Reuse whichever tests key the column (then the file) already uses; else dbt 1.8+ form."""
    for key in _TESTS_KEYS:
        if key in column:
            return key
    counts = {key: _count_key(document, key) for key in _TESTS_KEYS}
    if counts["tests"] > counts["data_tests"]:
        return "tests"
    return "data_tests"


def _count_key(node: Any, key: str) -> int:
    if isinstance(node, dict):
        return (key in node) + sum(_count_key(value, key) for value in node.values())
    if isinstance(node, list):
        return sum(_count_key(item, key) for item in node)
    return 0


def _ref_target(value: Any) -> str | None:
    match = _REF_RE.search(str(value))
    if match is None:
        return None
    return (match.group(2) or match.group(1)).lower()


def _test_arguments(relationships: Any) -> dict[str, Any] | None:
    """The `to`/`field` mapping of a relationships test, in whichever dbt spells it.

    dbt 1.10 moved test arguments under `arguments:`; before that they sat directly on
    the test. Reading both is what keeps a repo already on the new form from having a
    second, identical test appended underneath the one it already declares.
    """
    if not isinstance(relationships, dict):
        return None
    arguments = relationships.get("arguments")
    return arguments if isinstance(arguments, dict) else relationships


def _argument_style(column: CommentedMap, document: Any, config: RelationshipsConfig) -> str:
    """Where this file wants a relationships test's `to`/`field`: "arguments" or "flat".

    Decided the way `_tests_key` decides its key: reuse what the column already does,
    then what the rest of the file does. The fallback is flat, which every dbt version
    reads -- writing `arguments:` into a repo on dbt < 1.10 would break its build, so it
    is only ever written where the repo has shown it can be read.
    """
    if config.test_argument_style != "auto":
        return config.test_argument_style
    for scope in (column, document):
        style = _declared_argument_style(scope)
        if style is not None:
            return style
    return "flat"


def _declared_argument_style(node: Any) -> str | None:
    """The form used by the first test under `node` that spells its arguments at all.

    Read from ANY test, not just `relationships`: a `dbt_utils` test nesting under
    `arguments:` proves this repo runs a dbt that reads the form, which is the whole of
    what the flat fallback protects against. A schema file usually declares its first
    relationship long after its first uniqueness test, so looking only at relationships
    would fall back to flat in exactly the repos that have already moved on.
    """
    if isinstance(node, dict):
        for key in _TESTS_KEYS:
            style = _tests_argument_style(node.get(key))
            if style is not None:
                return style
        children: Any = node.values()
    elif isinstance(node, list):
        children = node
    else:
        return None
    for child in children:
        style = _declared_argument_style(child)
        if style is not None:
            return style
    return None


def _tests_argument_style(tests: Any) -> str | None:
    """The form one `data_tests:`/`tests:` list demonstrates, or None if it shows neither.

    A bare `- not_null` and a test carrying nothing but `config:` are both silent on the
    question -- they are stepped over rather than counted as flat.
    """
    if not isinstance(tests, list):
        return None
    for item in tests:
        if not isinstance(item, dict):
            continue
        for value in item.values():
            if not isinstance(value, dict):
                continue
            if isinstance(value.get("arguments"), dict):
                return "arguments"
            if set(value) - {"config"}:
                return "flat"
    return None


def _write_relationships_test(
    column: CommentedMap,
    document: Any,
    entry: StagedRelationship,
    config: RelationshipsConfig,
    to_model: str | None = None,
) -> None:
    to_model = to_model or entry.to_model
    key = _tests_key(column, document)
    tests = column.get(key)
    if tests is None:
        tests = CommentedSeq()
        column[key] = tests
    if not isinstance(tests, list):
        raise YamlWriteError(
            f"{key}: on column '{entry.from_column}' is not a list -- fix the schema file"
        )
    for item in tests:
        if not isinstance(item, dict):
            continue
        existing = _test_arguments(item.get("relationships"))
        if existing is None:
            continue
        if (
            _ref_target(existing.get("to")) == to_model.lower()
            and str(existing.get("field") or "").lower() == entry.to_column.lower()
        ):
            return
    arguments = CommentedMap()
    arguments["to"] = f"ref('{to_model}')"
    arguments["field"] = entry.to_column
    relationships = CommentedMap()
    if _argument_style(column, document, config) == "arguments":
        relationships["arguments"] = arguments
    else:
        relationships.update(arguments)
    if config.validated_test_severity:
        # Explicit, and warn by default: a relationship stitch inferred and a human
        # drew must never be the reason someone's pipeline goes red (#134). Setting
        # validated_test_severity to "" opts out and writes a bare test.
        severity = CommentedMap()
        severity["severity"] = config.validated_test_severity
        # a sibling of `arguments:`, not one of them -- severity configures the test,
        # it is not an argument to it, in either dbt spelling
        relationships["config"] = severity
    test = CommentedMap()
    test["relationships"] = relationships
    tests.append(test)
    _write_cardinality(column, entry, config)


def _write_cardinality(
    column: CommentedMap, entry: StagedRelationship, config: RelationshipsConfig
) -> None:
    """Give the arity a home next to the test that carries the FK fact (#134).

    A `relationships` test says these two columns join. It cannot say whether the
    join is many-to-one or one-to-one -- dbt has no field for that -- so drawing a
    one-to-one and rebuilding used to hand back a many-to-one, and the ERD drew the
    wrong thing. The cardinality therefore keeps the one meta key it already had,
    and ONLY that key: none of the `metabase.fk_*` keys come with it, because the
    test is now the declaration and duplicating it there is what #135 exists to
    clean up.
    """
    if not entry.cardinality:
        return
    meta = _ensure_meta(column)
    meta[config.cardinality_meta_key] = entry.cardinality


def _write_meta(
    column: CommentedMap,
    entry: StagedRelationship,
    target: dict[str, Any],
    config: RelationshipsConfig,
    to_model: str | None = None,
) -> None:
    to_model = to_model or entry.to_model
    table_key, field_key = config.fk_meta_keys[0], config.fk_meta_keys[1]
    meta = _ensure_meta(column)
    schema = str(target.get("schema") or "")
    value = f"{schema}.{to_model}" if schema else to_model
    existing = meta.get(table_key)
    if existing is not None and str(existing).split(".")[-1].lower() != to_model.lower():
        raise YamlWriteError(
            f"column '{entry.from_column}' already declares {table_key}: {existing} -- "
            "remove the existing declaration or edit it by hand"
        )
    meta[table_key] = value
    meta[field_key] = entry.to_column
    if entry.cardinality:
        meta[config.cardinality_meta_key] = entry.cardinality


def _ensure_meta(column: CommentedMap) -> CommentedMap:
    """Legacy top-level `meta:` wins when the file already uses it; otherwise config.meta."""
    meta = column.get("meta")
    if isinstance(meta, dict):
        return meta
    config_block = column.get("config")
    if config_block is None:
        config_block = CommentedMap()
        column["config"] = config_block
    if not isinstance(config_block, dict):
        raise YamlWriteError(
            f"config: on column '{column.get('name')}' is not a mapping -- fix the schema file"
        )
    meta = config_block.get("meta")
    if meta is None:
        meta = CommentedMap()
        config_block["meta"] = meta
    if not isinstance(meta, dict):
        raise YamlWriteError(
            f"config.meta on column '{column.get('name')}' is not a mapping -- fix the schema file"
        )
    return meta


def _atomic_write(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
