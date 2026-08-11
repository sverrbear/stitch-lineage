"""The apply engine: staged changes -> model YAML -> patched graph (SPEC.md section 8.2).

`stitch apply` and the app's apply endpoints (issue #72) must behave identically -- same
plan, same dirty-file guard, same clearing, same graph patch -- so the behaviour lives here
and the two callers only render it. The CLI prints; the API serialises; neither reimplements.

Nothing in this module talks to a console or raises HTTP: `build_plan` and `execute` return
data, and the errors they raise (StagedStoreError, StitchArtifactError, NotImplementedError)
are the ones the callers already translate. `write/` stays a YAML writer -- composing graph
edges and driving the guards is this layer's job.
"""

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from stitch_lineage.config import StitchConfig
from stitch_lineage.graph.schema import (
    Confidence,
    Edge,
    EdgeType,
    Graph,
    NodeType,
    column_node_id,
)
from stitch_lineage.io.artifacts import load_manifest
from stitch_lineage.io.graph_store import merge_edges, read_graph, write_graph
from stitch_lineage.io.staged_store import (
    DESCRIPTIONS_FILENAME,
    STAGED_FILENAME,
    StagedChange,
    StagedDescription,
    StagedRelationship,
    drop_descriptions,
    drop_staged,
    read_descriptions,
    read_staged,
)
from stitch_lineage.write.yaml_writer import (
    EntryResult,
    FileEdit,
    ModelWriteability,
    WritePlan,
    apply_plan,
    model_writeability,
    plan_migration,
    plan_writes,
)

__all__ = [
    "ApplyContext",
    "ApplyOutcome",
    "ApplyPaths",
    "ApplyPlan",
    "FilePreview",
    "GraphPatch",
    "ModelWriteability",
    "StagedChanges",
    "build_plan",
    "execute",
    "is_dirty",
    "migration_plan",
    "patch_graph",
    "paths_for",
    "read_changes",
    "refusals",
    "writeability",
]


@dataclass(frozen=True)
class ApplyPaths:
    """Everywhere apply reads from and writes to, derived once from stitch.yml."""

    config: Path
    project_dir: Path
    relationships_store: Path
    descriptions_store: Path
    graph: Path


@dataclass(frozen=True)
class StagedChanges:
    """Both staged stores, read together -- one apply run materialises all of it."""

    relationships: list[StagedRelationship] = field(default_factory=list)
    descriptions: list[StagedDescription] = field(default_factory=list)

    @property
    def all(self) -> list[StagedChange]:
        """Relationships first, descriptions second -- the order the plan is built in."""
        return [*self.relationships, *self.descriptions]

    def __len__(self) -> int:
        return len(self.relationships) + len(self.descriptions)


@dataclass(frozen=True)
class FilePreview:
    """One file's pending change: a repo-relative path and its unified diff."""

    path: str
    diff: str


@dataclass(frozen=True)
class GraphPatch:
    """What the in-place graph.json patch did (issue #68), or why it did nothing."""

    carried: int = 0
    edges_added: int = 0
    descriptions_updated: int = 0
    skipped: list[str] = field(default_factory=list)
    note: str | None = None

    @property
    def patched(self) -> bool:
        """Whether the graph now carries the applied changes -- what the caller may promise."""
        return self.carried > 0


@dataclass(frozen=True)
class ApplyPlan:
    """A planned apply: what would be written, what cannot be, and where."""

    paths: ApplyPaths
    changes: StagedChanges
    plan: WritePlan
    write_to: str

    @property
    def edits(self) -> list[FileEdit]:
        return self.plan.edits

    @property
    def failures(self) -> list[EntryResult]:
        return self.plan.failures

    @property
    def unchanged(self) -> list[EntryResult]:
        return self.plan.unchanged

    @property
    def planned(self) -> list[EntryResult]:
        return self.plan.planned

    def relative(self, path: Path) -> str:
        """`path` as the repo sees it -- what a diff header and an API response should show."""
        root = self.paths.project_dir.resolve()
        try:
            return Path(path).resolve().relative_to(root).as_posix()
        except ValueError:
            return Path(path).as_posix()

    def files(self) -> list[FilePreview]:
        root = self.paths.project_dir.resolve()
        return [
            FilePreview(path=self.relative(edit.path), diff=edit.diff(root))
            for edit in self.edits
            if edit.changed
        ]

    def applied_entries(self, paths: set[Path]) -> list[StagedChange]:
        """The staged changes the repo carries once `paths` are written.

        Exactly the set that clears from the stores (WritePlan.ids_for), so the stores and the
        graph patch can never disagree about what was applied.
        """
        ids = self.plan.ids_for(paths)
        return [result.entry for result in self.plan.results if result.entry.id in ids]


@dataclass(frozen=True)
class ApplyContext:
    """What the app needs to run an apply: the config it was started with.

    `stitch serve` hands one to create_app; without it the apply endpoints do not exist, which
    is how the static export (and any read-only embedding) stays unable to write the repo.
    """

    config: Path
    cfg: StitchConfig

    def plan(self) -> "ApplyPlan":
        return build_plan(self.config, self.cfg)

    def writeability(self) -> dict[str, ModelWriteability]:
        return writeability(self.config, self.cfg)


def writeability(config_path: Path, cfg: StitchConfig) -> dict[str, ModelWriteability]:
    """Which models stitch can write a declaration into, before anything is staged.

    The app asks this at load so an edit it cannot honour is never offered (#132).
    It reads the manifest and the schema files; it writes nothing.

    Raises:
        StitchArtifactError: the dbt manifest is missing or unparseable.
    """
    paths = paths_for(config_path, cfg)
    manifest = load_manifest(config_path.parent / cfg.dbt.project_dir / cfg.dbt.target_path)
    return model_writeability(manifest, paths.project_dir, cfg.write)


def paths_for(config_path: Path, cfg: StitchConfig) -> ApplyPaths:
    output_dir = config_path.parent / cfg.output.dir
    return ApplyPaths(
        config=config_path,
        project_dir=config_path.parent / cfg.dbt.project_dir,
        relationships_store=output_dir / STAGED_FILENAME,
        descriptions_store=output_dir / DESCRIPTIONS_FILENAME,
        graph=output_dir / "graph.json",
    )


def read_changes(paths: ApplyPaths) -> StagedChanges:
    """Read both staged stores. Raises StagedStoreError if either file is unusable."""
    return StagedChanges(
        relationships=read_staged(paths.relationships_store),
        descriptions=read_descriptions(paths.descriptions_store),
    )


def build_plan(config_path: Path, cfg: StitchConfig) -> ApplyPlan:
    """Read the stores and the manifest, and plan every write they imply.

    Raises:
        StagedStoreError: a staged store exists but is unusable.
        StitchArtifactError: the dbt manifest is missing or unparseable.
        NotImplementedError: relationships.write_to is contract_constraint.
    """
    paths = paths_for(config_path, cfg)
    changes = read_changes(paths)
    manifest = load_manifest(config_path.parent / cfg.dbt.project_dir / cfg.dbt.target_path)
    plan = plan_writes(changes.all, manifest, paths.project_dir, cfg.relationships, cfg.write)
    return ApplyPlan(paths=paths, changes=changes, plan=plan, write_to=cfg.relationships.write_to)


def migration_plan(config_path: Path, cfg: StitchConfig) -> ApplyPlan:
    """Plan the rewrite of meta-form declarations into the configured form (#135).

    Shaped as an ApplyPlan so the CLI's preview, dirty-file guard and confirmation
    are the same code paths `stitch apply` uses -- a migration writes model YAML, so
    it gets the same ceremony. Its `changes` are empty: nothing is staged, the
    declarations being rewritten are already in the repo.

    Raises:
        StitchArtifactError: the dbt manifest is missing or unparseable.
    """
    paths = paths_for(config_path, cfg)
    manifest = load_manifest(config_path.parent / cfg.dbt.project_dir / cfg.dbt.target_path)
    plan = plan_migration(manifest, paths.project_dir, cfg.relationships)
    return ApplyPlan(
        paths=paths,
        changes=StagedChanges(relationships=[], descriptions=[]),
        plan=plan,
        write_to=cfg.relationships.write_to,
    )


def is_dirty(path: Path) -> bool:
    """Whether `path` has uncommitted changes (untracked counts). Not a repo -> nothing to guard."""
    result = subprocess.run(
        ["git", "status", "--porcelain", "--", path.name],
        cwd=path.parent,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return False
    return bool(result.stdout.strip())


@dataclass(frozen=True)
class ApplyOutcome:
    """What an executed apply did: files written, files refused, stores cleared, graph patched."""

    written: list[Path] = field(default_factory=list)
    refused: list[Path] = field(default_factory=list)
    cleared: set[str] = field(default_factory=set)
    still_staged: int = 0
    graph: GraphPatch | None = None

    @property
    def applied(self) -> int:
        return len(self.cleared)


def refusals(plan: ApplyPlan, *, force: bool = False) -> list[FileEdit]:
    """The edits the dirty-file guard refuses: target files with uncommitted changes.

    Separate from `execute` so a caller can report them before it writes anything -- the CLI
    prints them and prompts on what is left, the API returns them per file -- and so the guard
    runs exactly once per apply. `force` is a CLI-only escape: overwriting someone's
    uncommitted edits is not a decision the app gets to make.
    """
    if force:
        return []
    return [edit for edit in plan.edits if edit.changed and is_dirty(edit.path)]


def execute(plan: ApplyPlan, refused: list[FileEdit], *, update_graph: bool = True) -> ApplyOutcome:
    """Write everything the guard did not refuse, clear what applied, and patch the graph.

    `refused` comes from `refusals` -- passed in rather than recomputed so the files the caller
    reported are exactly the files that are skipped.
    """
    writable = [edit for edit in plan.edits if edit.changed and edit not in refused]

    written = apply_plan(writable)
    applied_paths = {edit.path for edit in writable}
    entries = plan.applied_entries(applied_paths)
    cleared = {entry.id for entry in entries}
    drop_staged(
        {entry.id for entry in entries if isinstance(entry, StagedRelationship)},
        plan.paths.relationships_store,
    )
    drop_descriptions(
        {entry.id for entry in entries if isinstance(entry, StagedDescription)},
        plan.paths.descriptions_store,
    )
    graph = patch_graph(plan.paths.graph, entries, plan.write_to) if update_graph else None
    return ApplyOutcome(
        written=written,
        refused=[edit.path for edit in refused if edit.changed],
        cleared=cleared,
        still_staged=len(read_changes(plan.paths)),
        graph=graph,
    )


def patch_graph(graph_path: Path, entries: list[StagedChange], write_to: str) -> GraphPatch:
    """Inject the applied changes into graph.json so the app shows them on a refresh.

    A rebuild inside apply is the wrong tool: `dbt parse` regenerates the manifest without
    compiled SQL (column lineage would crater) and `dbt docs generate` hits the warehouse --
    `stitch apply --build` exists for users who want that. The next real build reconciles the
    patch from the manifest, which already contains what apply just wrote.

    The previous-build snapshot is deliberately untouched: a `relates_to` edge is excluded
    from impact traversal and a description is not a column change, so a patch has no blast
    radius to report, and overwriting the snapshot would throw away the answer to "what did my
    last build change".
    """
    if not entries:
        return GraphPatch()
    if not graph_path.is_file():
        return GraphPatch(
            note=f"no graph at {graph_path} to update -- run 'stitch build' to see these "
            "changes in the app"
        )
    try:
        graph = read_graph(graph_path)
    except ValueError as exc:
        return GraphPatch(
            note=f"{graph_path} does not parse ({exc}) -- run 'stitch build' to rebuild it"
        )

    models = model_node_ids(graph)
    edges, edge_skips = relates_to_edges(graph, models, entries, write_to)
    updated, carried_descriptions, description_skips = _set_descriptions(graph, models, entries)
    added = merge_edges(graph, edges)
    if added or updated:
        write_graph(graph, graph_path)
    return GraphPatch(
        carried=len(edges) + carried_descriptions,
        edges_added=added,
        descriptions_updated=updated,
        skipped=[*edge_skips, *description_skips],
    )


def model_node_ids(graph: Graph) -> dict[str, str]:
    """Model name (lowercased) -> node id. Ambiguous names are dropped, as in the writer."""
    ids: dict[str, str] = {}
    ambiguous: set[str] = set()
    for node in graph.nodes:
        if node.node_type is not NodeType.MODEL:
            continue
        name = node.name.lower()
        if name in ids:
            ambiguous.add(name)
        ids[name] = node.node_id
    for name in ambiguous:
        ids.pop(name, None)
    return ids


def relates_to_edges(
    graph: Graph, models: dict[str, str], entries: list[StagedChange], write_to: str
) -> tuple[list[Edge], list[str]]:
    """The `relates_to` edges for the relationships apply materialised, and what it skipped.

    Confidence mirrors the form that was written, so the patch shows what the next build will
    read back: a `relationships` test is validated, a meta declaration is declared. An edge is
    skipped when either endpoint is not a column node in this graph -- exactly the case the
    resolver reports as a dangling relationship -- because inventing the nodes would put
    something in the graph the repo does not contain.
    """
    columns = {node.node_id for node in graph.nodes if node.node_type is NodeType.COLUMN}
    confidence = Confidence.VALIDATED if write_to == "relationships_test" else Confidence.DECLARED
    edges: list[Edge] = []
    skipped: list[str] = []
    for entry in entries:
        if not isinstance(entry, StagedRelationship):
            continue
        from_model = models.get(entry.from_model.lower())
        to_model = models.get(entry.to_model.lower())
        from_id = column_node_id(from_model, entry.from_column) if from_model else None
        to_id = column_node_id(to_model, entry.to_column) if to_model else None
        if from_id not in columns or to_id not in columns:
            skipped.append(entry.label)
            continue
        evidence: dict[str, Any] = {"source": "stitch apply", "write_to": write_to}
        if write_to == "meta":
            evidence["relationship_type"] = entry.cardinality
        edges.append(
            Edge(
                from_=from_id,
                to=to_id,
                edge_type=EdgeType.RELATES_TO,
                confidence=confidence,
                evidence=evidence,
            )
        )
    return edges, skipped


def _set_descriptions(
    graph: Graph, models: dict[str, str], entries: list[StagedChange]
) -> tuple[int, int, list[str]]:
    """Write the applied descriptions onto their graph nodes.

    Returns (nodes changed, entries the graph carries, skipped labels). A description already
    matching is carried but not counted as a change, so an all-idempotent patch rewrites
    nothing.
    """
    nodes = {node.node_id: node for node in graph.nodes}
    updated = 0
    carried = 0
    skipped: list[str] = []
    for entry in entries:
        if not isinstance(entry, StagedDescription):
            continue
        model = models.get(entry.entity.lower())
        node_id = None
        if model is not None:
            node_id = model if entry.column is None else column_node_id(model, entry.column)
        node = nodes.get(node_id) if node_id else None
        if node is None:
            skipped.append(entry.label)
            continue
        carried += 1
        if node.description != entry.new_description:
            node.description = entry.new_description
            updated += 1
    return updated, carried, skipped
