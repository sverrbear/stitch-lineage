# Design spec — dbt ↔ Metabase column lineage and interactive ERD

**Working name:** `stitch` (placeholder)
**License:** MIT
**Distribution:** Python package on PyPI (`pip install stitch-lineage`). Local-first: no server, no warehouse backend, no hosted anything. The dbt repo is the database.
**Status:** draft v0.4 — supersedes v0.3 (drops the Snowflake backend; carries forward its fixes: recursive impact, pinned edge directions, coverage reporting, seam enforcement)

---

## 1. Problem

Two gaps no open-source tool fills:

1. **Column lineage stops at the warehouse boundary.** `dbdocs`, `dbterd`, dbt Fusion and the dbt VS Code extension all compute column lineage inside the dbt DAG. None know that `fct_matches.match_intensity` feeds four cards on the Board dashboard. "If I rename this column, what breaks?" is unanswerable for the half of the stack where the damage shows up.

2. **Relationships have no authoring surface.** dbt can express foreign keys three ways and every ERD tool renders from one of them, but the only way to *create* one is hand-editing YAML. No visual editor writes back to code.

## 2. Not building

Mature, MIT-licensed, reuse rather than reimplement.

| Capability | Tool | Decision |
|---|---|---|
| dbt → Metabase FK / semantic type / description push | `dbt-metabase` (600★, v1.7.5, May 2026) | Reuse in CI. Our written relationships tests are its input — free FK sync to Metabase. |
| Metabase cards → dbt exposures (model-level) | `dbt-metabase exposures` | Reuse; we extend the idea to column level. |
| ERD conventions | `dbterd` | Match its `meta.relationship_type` convention for interop. |
| SQL column lineage | `sqlglot` | Phase 3 dependency (native SQL cards). |

Greenfield: **column-level BI lineage** and **the visual editor that writes back to dbt YAML**.

## 3. Architecture

Everything is a file in (or derived from) the dbt repo. One CLI, three commands that matter.

```
 dbt repo
 ├─ models/**/*.yml          ◄── source of truth for relationships (write-back target)
 ├─ target/manifest.json     ──┐
 ├─ target/catalog.json      ──┤
 │                             ├──► stitch build ──► .stitch/graph.json  (committed baseline)
 Metabase API ─────────────────┘                └──► .stitch/coverage.json
 │
 ├─ .stitch/
 │   ├─ graph.json           ◄── the database. Deterministic, committed.
 │   ├─ layout.yml           ◄── ERD positions + saved views. Committed.
 │   └─ cache/               ◄── raw Metabase payloads. Gitignored.
 │
 stitch serve   ──► localhost app: catalog, column lineage, ERD editor
 │                   edits write YAML directly into models/ (with diff preview)
 │
 stitch impact  ──► CI: build graph on the PR branch, diff vs committed graph.json
                     from the base branch, walk downstream, comment on the PR
```

Why local-first wins here:

- **Zero standing infrastructure.** Nothing to host, secure, authenticate or pay for. `pip install`, point at the repo, go.
- **The repo is already the source of truth for relationships** — dbt YAML. Storing the derived graph next to it means truth and derivation travel together, branch together, and diff together.
- **Impact analysis needs no database.** The baseline is the `graph.json` committed on `main`; the candidate is built from the PR branch in CI. `git` is the versioning system, because it already is.
- **Direct write-back returns.** The editor runs on the machine that has the repo checked out, so drawing an edge can write the YAML right there — no proposals table, no second GitHub Action, no key-pair CI auth. Review still happens, because the write lands on a branch and goes through a normal PR.

What is knowingly given up relative to the warehouse version, so it's a decision and not an accident:

- **Long run history.** Git gives you graph-at-every-commit, which is actually richer than N retained runs — but ad-hoc queries over history ("when did this card first touch this column") mean checking out old commits, not writing SQL.
- **Warehouse-side agent access.** Cortex Agent can't query a JSON file in a repo. Mitigation: `graph.json` has a stable, documented schema and `stitch export --format jsonl` emits agent-friendly flat records; a Snowflake-loading script is a documented recipe, not a product feature.
- **Multi-user concurrent editing.** The editor is single-user-at-a-time by nature. For a team of one this costs nothing; for a bigger team, last-writer-wins on `layout.yml` and git conflicts on YAML are the (acceptable) story.

## 4. Package layout and the seam

```
stitch_lineage/
  cli.py               # typer: build, serve, impact, doctor, export
  config.py            # stitch.yml → validated pydantic model
  resolve/             # ─┐ pure: parsed dicts in → nodes/edges out
    dbt.py             #  │ no I/O imports of any kind
    metabase.py        #  │ unit-testable offline against cached payloads
    bind.py            # ─┘
  io/
    metabase_client.py # requests + retry + cache; the only file that does HTTP
    artifacts.py       # reads manifest/catalog from target/
    graph_store.py     # read/write .stitch/graph.json, deterministic ordering
  graph/
    schema.py          # node/edge pydantic models, carries schema_version
    impact.py          # recursive downstream walk over graph.json
  write/
    yaml_writer.py     # ruamel round-trip; diff preview; the only file that
                       # touches models/**/*.yml
  app/
    server.py          # FastAPI serving the SPA + a small local API
    frontend/          # React + React Flow, built to static assets, bundled
                       # into the wheel — `pip install` includes the UI,
                       # no npm required at install time
  export/
    static_site.py     # read-only build of the app for hosting anywhere
```

Seam rules, enforced with `import-linter` in CI, not discipline:

- `resolve/` imports neither `requests` nor filesystem code. Dicts in, node/edge lists out.
- Only `io/metabase_client.py` performs HTTP. Only `write/yaml_writer.py` touches model YAML.
- `graph/impact.py` and `app/` depend on `graph/schema.py` and `graph.json` — never on `resolve/` or `io/`.

This is the one non-negotiable rule. It's what makes a Looker or BigQuery resolver a new module instead of a fork, and it's what kept the resolver identical across three architecture rewrites of this spec.

## 5. Graph model

`graph.json` — one file, deterministic ordering (nodes sorted by `node_id`, edges by `(from, to, type)`, keys sorted) so that regeneration without semantic change produces a zero diff. Gzip-optional; plain JSON by default because reviewable diffs are the point of committing it.

```jsonc
{
  "schema_version": 1,
  "generated_at": "2026-08-06T05:30:00Z",     // excluded from diff-noise: see below
  "dbt_invocation_id": "…",
  "metabase_version": "0.53.2",
  "coverage": { /* §7.4 */ },
  "nodes": [ { "node_id": "...", "node_type": "...", ... } ],
  "edges": [ { "from": "...", "to": "...", "edge_type": "...",
               "confidence": "...", "evidence": { ... } } ]
}
```

Diff hygiene: volatile fields (`generated_at`, invocation id) live in a header block at the top of the file so a no-change rebuild touches two lines, not none — an honest tradeoff; `--check` mode (build, compare, exit nonzero on drift) is how CI verifies the committed baseline is current.

**Nodes**

| Type | `node_id` | Source |
|---|---|---|
| `source` / `model` | dbt `unique_id`, e.g. `model.smitten.fct_matches` | manifest |
| `column` | `{model_unique_id}::{column_name}` (lowercased) | catalog, manifest fallback |
| `mb_field` | `mb_field::{field_id}` | Metabase database metadata |
| `mb_card` | `mb_card::{card_id}` | `/api/card` |
| `mb_dashboard` | `mb_dash::{dashboard_id}` | `/api/dashboard` |

Node payload: `name`, `database/schema/table/column`, `data_type`, `description`, `owner`, plus a `properties` object for type-specific extras (tags, materialization, collection_id, card creator, archived flag).

**Edges — direction pinned: `from → to` is always data flow, upstream to downstream.** Impact traversal walks `from → to`; a single edge type pointing the wrong way silently corrupts every impact report.

| edge_type | from → to | Derived from | confidence |
|---|---|---|---|
| `references` | upstream model → downstream model | manifest `depends_on` | exact |
| `feeds` | upstream column → downstream column | sqlglot over dbt compiled SQL (core, §7.3) | exact / parsed / inferred |
| `binds_to` | dbt column → mb_field | name binding (§7.4) | exact / fuzzy |
| `consumed_by` | mb_field → mb_card | MBQL walk / SQL parse | exact / parsed |
| `appears_on` | mb_card → mb_dashboard | dashcards | exact |
| `relates_to` | FK column → referenced PK column | meta declaration, relationships test, or contract constraint (§8) | declared / validated |

`relates_to` is a declaration, not a flow — **excluded from impact traversal**, rendered in the ERD only.

Every edge carries `confidence` and `evidence` (manifest path, MBQL fragment, parsed SQL span). The UI renders `parsed`/`fuzzy`/`inferred` edges visibly differently from `exact`. One phantom dependency presented as fact costs the tool its credibility permanently.

## 6. Configuration

### 6.0 `stitch init` — a wizard, not a scaffolder

Setup friction is a product feature. `init` derives everything derivable and asks only for what it cannot know:

1. Detect `dbt_project.yml` → project name, `target/` path. No manifest yet → offer to run `dbt docs generate`.
2. From the manifest: quoting/casing policy, databases and schemas models land in, model inventory. **Never ask a question the manifest answers.**
3. Ask the two unknowables: Metabase URL, API key. Key is env-only from the first second — init writes the `${STITCH_METABASE_API_KEY}` reference into config and a line into `.env.example`, never the value.
4. Immediately call Metabase, list its databases, and **propose the database mapping** by name-similarity against the manifest ("Metabase *Analytics* ↔ dbt *ANALYTICS* — confirm? [Y/n]"). Ambiguity → a real question; the common case → one keystroke.
5. Propose `include_schemas` from where marts actually live; write `stitch.yml`; append `.stitch/cache/` to `.gitignore`; drop the GitHub Action into `.github/workflows/` with a commented-out trigger.
6. Finish with a mini-doctor (Metabase reachable, version ≥ 49, manifest parses, model counts on both sides) and print the next command.

Target: repo → configured in under two minutes with four human inputs (URL, key, one mapping confirm, later un-commenting the Action). Every derived value is written into `stitch.yml` explicitly rather than defaulted invisibly, so the config file remains the full, inspectable truth.

### 6.1 `stitch.yml`

`stitch.yml` at the dbt project root. Committed. No secrets — the API key is env-only.

```yaml
dbt:
  project_dir: .
  target_path: target/
  # Identifier quoting/casing is NOT configurable — it is read from the
  # manifest's quoting config. dbt already knows; asking the user invites a
  # silent 0% bind rate that looks exactly like a broken tool.

metabase:
  url: ${STITCH_METABASE_URL}          # or literal https:// URL, it's not secret
  api_key: ${STITCH_METABASE_API_KEY}  # env interpolation ONLY; a literal key
                                        # here is a startup ERROR, not a warning
  min_version: "0.49"                   # API-key auth floor, asserted at startup
  databases:
    - metabase_name: "Analytics"        # display name in Metabase
      dbt_database: ANALYTICS           # database per the dbt manifest
  include_schemas: ["MARTS", "DIMS"]
  exclude_collections: ["Personal*", "Archive*"]

relationships:
  write_to: meta                        # | relationships_test | contract_constraint
  fk_meta_keys: [metabase.fk_target_table, metabase.fk_target_field]  # dbt-metabase interop
  cardinality_meta_key: relationship_type                             # dbterd interop
  validated_test_severity: warn         # used when a relationship is promoted to validated

output:
  dir: .stitch/
  retain_cache_runs: 3                  # raw Metabase payload snapshots
```

Rules carried forward:

- **No default that only makes sense at Smitten.** Required-and-unknowable values fail with the discovery path: `metabase.databases is required — run 'stitch doctor --list-databases'`.
- **Derive rather than configure** wherever dbt already knows the answer.
- Config precedence: CLI arg > env var > `stitch.yml`, matching `dbt-metabase` since both run in the same CI.

## 7. `stitch build` — ingestion and resolution

Reads dbt artifacts and the Metabase API, writes `graph.json`. Idempotent; safe to run anywhere the env vars exist.

### 7.1 dbt side

`target/manifest.json` → models, sources, columns, `references` edges from `depends_on`, `relates_to` edges from declared FKs (§8). `target/catalog.json` → column types; manifest columns as fallback for views/ephemerals absent from the catalog. Missing artifacts → error naming the fix (`run dbt docs generate`), never a partial graph.

### 7.2 Metabase side

| Endpoint | Purpose |
|---|---|
| `GET /api/database` | locate warehouse DB(s) by display name |
| `GET /api/database/:id/metadata?include_hidden=true` | the `field_id ↔ schema.table.column` map |
| `GET /api/card` | all cards: `dataset_query`, `collection_id`, `creator`, `archived` |
| `GET /api/dashboard`, `/api/dashboard/:id` | dashcards → card ids |
| `GET /api/collection` | tree, for collection filtering |

Auth: API key header, Metabase 49+ asserted at startup. Raw responses land in `.stitch/cache/{timestamp}/` before parsing (gitignored, last 3 kept): resolution bugs get debugged against stored payloads, and `resolve/metabase.py` gets unit tests from real fixtures. Incremental via `updated_at` high-water mark; target 5k cards under 60s warm.

### 7.3 Column lineage inside the dbt DAG — core, not deferred

Without `feeds` edges the chain has a hole in the middle: a renamed staging column would only be traced to Metabase if a card touched it *directly*, while its real blast radius runs through the marts built on it. Column lineage must be continuous — source column → staging → mart → mb_field → card → dashboard — so this is Phase 0 scope.

Mechanics: for every model, take `compiled_code` from the manifest (Jinja already rendered — this is the *easy* SQL-parsing problem, unlike Metabase native cards with their template tags) and run `sqlglot.lineage` per output column with `dialect="snowflake"`, schema-qualified from the catalog so unaliased columns resolve. Each traced input column becomes a `feeds` edge, `confidence: exact` for plain projections and renames, `parsed` for expressions (a column derived from `amount * fx_rate` gets edges from both inputs).

Known hard cases, handled explicitly rather than discovered:

- **`SELECT *` and dbt macros like `dbt_utils.star`** — post-compilation these are literal stars; expand against the upstream catalog schema. Unexpandable (upstream missing from catalog) → fall back to name-matching columns across the edge, `confidence: inferred`.
- **Ephemeral models** — compiled inline into their parents as CTEs; sqlglot traces through CTEs natively, but the intermediate hop attributes to the parent model. Acceptable: lineage endpoints stay correct.
- **Unparseable model** — fail soft: emit model-level `references` only, add the model to the unresolved list, keep building. One exotic PIVOT must not blank the whole graph.
- **Lateral flatten / VARIANT paths** — Snowflake-specific and common in event models; sqlglot handles most, and `column:path` sub-column lineage is explicitly out of scope (the edge lands on the VARIANT column, which is the right conservative grain).

Optional corroboration, off by default: Snowflake's `ACCOUNT_USAGE.ACCESS_HISTORY` records actual column-level lineage from executed queries (Enterprise edition and up). `stitch build --verify-lineage` compares parsed edges against observed ones and reports divergence — parser bugs surface as data instead of user complaints. A check, not a source: parsing stays primary because it works on branches that haven't run yet, which is the whole point of PR impact analysis.

Coverage output grows a line:

```
dbt column lineage   1,842/1,901 columns traced   (37 inferred via star-expansion,
                                                    22 unresolved → stitch doctor --untraced)
```

### 7.4 Card → column resolution

**MBQL (exact).** `dataset_query.query` carries structured field refs. Walk `fields`, `breakout`, `aggregation`, `filter`, `expressions`, `joins[].condition`, `joins[].fields`, `order-by`. Each `["field", <id>, opts]` resolves through the metadata map — no parsing, no ambiguity. `opts.source-field` (implicit join via an existing FK) is itself relationship evidence → suggestion layer.

**MBQL 5 / "lib" (exact).** Modern Metabase (Cloud v-latest) returns `{"lib/type": "mbql/query", "stages": [...]}` instead of `type`/`query`, and both shapes are live in the wild — older self-hosted instances still emit the legacy one, so the shape is detected per card. Differences: refs put the options map in the middle (`["field", {opts}, <id-or-name>]`); a query is a flat chain of stages instead of nested `source-query`; `filter`/`condition` become `filters`/`conditions`; a card source is `source-card: N`; a join carries its own `stages`; native SQL is a `mbql.stage/native` first stage. Clause labels in `evidence` stay shape-independent (`filter`, `joins.condition`, …) so a Metabase upgrade does not rewrite the graph; stages after the first are prefixed `stage1.`, `stage2.`, ….

**Card-on-card (exact, recursive).** `source-table: "card__123"`, MBQL 5 `source-card: 123` and `["metric", …]` refs (Metabase models and metrics) resolve transitively, cycle-guarded with a visited set.

**Native SQL (Phase 3, `confidence: parsed`).** Substitute `{% snippet %}`, `{{var}}`, `[[optional]]`, `{{#123-card}}` template tags, then sqlglot with the Snowflake dialect; unqualified columns resolved against the catalog schema. Parse failure → degrade to table-level, record in the unresolved list. Never drop a card silently.

### 7.5 Binding Metabase tables to dbt models

Match `(database, schema, table)` against manifest `relation_name`, honouring `alias`. Explicit handling for the silent-0%-bind traps: Snowflake's uppercase unquoted identifiers (compare case-insensitively, warn on case-only mismatch), display-name ≠ dbt database name (the `databases` map), multiple Metabase connections to one warehouse (allow a list).

**Coverage** written into `graph.json` and printed:

```
models bound       142/147   (5 unmatched → stitch doctor --unbound)
MBQL cards         218/218   exact
native SQL cards     0/41    unsupported in v0
dashboards          19/19
```

Coverage reporting is what turns "the graph looks thin" from a bug report into a documented limitation evaluable in thirty seconds. Non-negotiable in Phase 0.

## 8. Relationships: storage and write-back

### 8.1 Storage format

**Relationships are user-declared, first and foremost.** You create them by drawing in the ERD or writing YAML by hand; no test, constraint, or database FK is ever required for a relationship to exist. Tests are an optional per-relationship upgrade (below), never the entry ticket. The resolver *also* reads relationships that happen to be expressed as existing `relationships` tests or contract constraints — a repo with history renders completely — but the tool never requires that route.

Declarations live in model YAML `meta` — the repo-as-database principle: the relationship is data, next to the thing it describes, with zero build-time side effects.

**Three relationship shapes**, because real models aren't all single-column FKs:

**1. Simple (column pair)** — column-level meta, reusing `dbt-metabase`'s keys so FK sync into Metabase works unchanged, plus `dbterd`'s cardinality key:

```yaml
columns:
  - name: user_id
    config:
      meta:
        metabase.fk_target_table: dims.dim_users
        metabase.fk_target_field: user_id
        relationship_type: many-to-one
```

**2. Composite (multi-column) and 3. conceptual (table-level, no key)** — model-level meta under a `stitch.relationships` key, since neither fits a single column's meta and no existing tool has a convention for them:

```yaml
models:
  - name: fct_matches
    config:
      meta:
        stitch.relationships:
          - to: dim_user_markets
            type: many-to-one
            columns:                      # composite: ordered column pairs
              - [user_id, user_id]
              - [country_code, country_code]
          - to: fct_funnel_events
            type: related                 # conceptual: no key, ERD-only
            note: "Both grains describe the match funnel; see The Brain / Decisions"
```

Many-to-many is not a stored shape — it's what the ERD *renders* when a bridge table has two many-to-one edges out. The editor offers "create many-to-many via bridge…" as a gesture, and writes two simple relationships on the bridge model. Storing M:N directly would put the same fact in two places.

**Interop degrades explicitly, never silently:** simple relationships sync to Metabase via dbt-metabase and export to dbterd. Composite ones render in the ERD and count in impact analysis, but Metabase's FK model can't hold them (its own tracker says as much) — `stitch doctor` lists what didn't sync and why. Conceptual edges are ERD and documentation only, excluded from impact traversal and Metabase sync by definition.

**Two tiers, orthogonal to shape:**

| Tier | Stored as | Means | ERD rendering |
|---|---|---|---|
| **declared** (default) | meta only | "these relate" — documentation, zero build cost | solid edge |
| **validated** (opt-in, simple + composite only) | meta **plus** a `relationships` test at `severity: warn` | dbt checks referential integrity each run | solid edge + check badge |

The honest cost of declared-only: nothing exercises it against data. Mitigated structurally — `stitch build` statically validates every declaration against the manifest (target model exists, columns exist, types join-compatible; conceptual edges: target exists) and dangling declarations are build warnings in coverage output. Static checks catch typos; only the validated tier catches orphaned rows, which is exactly what the promote toggle is for.

The edge modal exposes all of this: shape is inferred from what you dragged (column→column = simple; multi-select = composite; table header→table header = conceptual), cardinality is a dropdown, "also add integrity test" is an off-by-default checkbox. `relationships.write_to` in config remains for teams that want tests or contract constraints as the written form of *simple* relationships.

### 8.2 Write-back — direct, with guardrails

The editor runs where the repo is checked out, so it writes YAML directly. Guardrails, not ceremony:

1. Drawing an edge opens a modal: target column confirm, cardinality, severity.
2. **A YAML diff preview is always shown before writing.** No silent mutation of a git repo, ever.
3. `ruamel.yaml` round-trip mode — comments, quoting, key order preserved. A PR that reformats every line of a hand-maintained schema file gets the tool banned; this is non-negotiable.
4. Target file from manifest `patch_path`; no YAML entry for the column → insert in catalog column order; no YAML file for the model → create per convention.
5. Refuse to write on a dirty target file (uncommitted changes) unless `--force` — the user's edits outrank ours.
6. Optional: `stitch serve --branch stitch/relationships` checks out a branch first, and a "commit & push" button in the UI shells out to git. Review then happens as a normal PR. Default is write-to-working-tree, since a solo operator reviews their own diff anyway.

Layout is presentation, not contract: node positions and saved views live in `.stitch/layout.yml`, committed, never in model YAML.

## 9. UI — `stitch serve`

FastAPI on localhost serving a prebuilt React SPA (bundled in the wheel; installing the package requires no node toolchain). React Flow for the canvas. The frontend talks only to the local API; the local API reads `graph.json` and calls `write/yaml_writer.py`.

**ERD canvas.** Tables as nodes with expandable columns; solid edges are declared relationships read from the repo. Three creation gestures: drag column-handle → column-handle (simple), multi-select column pairs (composite), drag table header → table header (conceptual). The modal infers the shape, offers cardinality, and shows the exact YAML diff before writing (§8.2). Saved views scope the canvas by dbt tag or schema — `marts_revenue` opens as its own diagram, never a 200-node hairball.

**Suggestion layer.** Dashed candidate edges from: (a) Metabase implicit-join `source-field` usage — users are already joining these tables; (b) naming conventions (`*_id` → `dim_*.id` etc.); (c) Phase 3: join predicates observed in native card SQL. Accept opens the write-back modal; dismiss records to `layout.yml` so it stays dismissed. This is what makes the initial backfill an hour of accepting suggestions instead of an afternoon of dragging.

**Search — first version, not later polish.** One search box, keyboard-first (`/` to focus, `⌘K` palette), querying everything in `graph.json`: dbt models, columns, Metabase fields, cards and dashboards — by name, description, tag, and card/dashboard title. Results grouped by node type, ranked exact-prefix > word-boundary > fuzzy, with type-ahead. Selecting a result opens its detail panel:

- **Column** → type, description, tests, its model, upstream sources, and the downstream fan-out with counts — "consumed by 4 cards on 2 dashboards" is the headline; the list and the lineage view are one click.
- **Card / dashboard** → deep link into Metabase, creator, collection, archived flag, and the reverse view: every dbt column the visual ultimately depends on, with confidence flags.
- **Model** → columns, declared relationships, fan-in and fan-out.

Implementation is deliberately boring: a Smitten-sized graph is a few thousand nodes, so search is client-side over an in-memory fuzzy index (fuse.js-class library, final pick at build time) built from `graph.json` at page load. No search server, no index files, identical behaviour in `stitch serve` and the static export. If someone points this at 50k cards one day, sharding the index by node type is the escape hatch — not v1's problem.

`stitch search <query>` in the CLI hits the same data for terminal use and scripting (`--json` for piping), which also means search exists from Phase 0, before any UI ships.

**Column lineage view.** Reached from search or by clicking any node: upstream chain to sources, downstream through models into fields, cards, dashboards. Card nodes deep-link into Metabase and show creator + archived status.

**System badges.** Every node in the lineage view and ERD carries the logo of the system it lives in — the Snowflake mark on dbt sources/models/columns (the warehouse side), the Metabase mark on fields, cards and dashboards — so a glance up a chain shows exactly where the warehouse ends and the BI layer begins. Search results and detail panels reuse the same badges. Assets live in `assets/logos/` (SVGs in brand colors; nominative use — the marks belong to their owners).

**Static export.** `stitch export --site` builds the same SPA read-only with `graph.json` inlined — deployable to any static host for people who will never run a CLI. Editing requires `serve`; viewing doesn't.

## 10. `stitch impact` — the CI feature

The baseline is the `graph.json` committed on the base branch. No database, no run registry — git is the versioning.

```
# in the PR's CI job, after dbt compile/docs generate on the branch:
stitch build --no-metabase        # re-resolve dbt side; reuse baseline's MB payload
stitch impact --base origin/main --format github-comment
```

Mechanics:

1. Load baseline `graph.json` from the merge-base commit (`git show origin/main:.stitch/graph.json`).
2. Build the candidate graph from the branch's artifacts. `--no-metabase` reuses the baseline's Metabase-side nodes/edges — the PR changes dbt, not Metabase, and CI shouldn't need Metabase credentials to comment on a model rename.
3. Changed set = columns removed (id present in base, absent in candidate) + type-changed (same id, different `data_type`). A **rename** surfaces as remove+add — name-based IDs can't tell the difference, and the conservative reading is correct: the downstream card genuinely breaks until repointed. Stated in output, not hidden.
4. **Recursive** downstream walk over baseline edges (`from → to`, excluding `relates_to`), depth-capped at 20, deduped to shortest path. With `feeds` edges in the core build, the chain is unbroken end to end: `stg_payments.amount → fct_revenue.net_revenue → mb_field → card #412 → Board dashboard`. One hop is a correctness bug, and so is a missing middle: both were true of earlier drafts of this spec.
5. Comment:

```
⚠ 3 columns removed or renamed

fct_matches.match_intensity → removed
  ├ 2 downstream models: mart_engagement, mart_board_kpis
  └ 4 Metabase cards:
      #412 Match intensity by country  (Board dashboard, sverrir)
      #418 Weekly intensity trend      (Board dashboard, sverrir)
```

Ship the GitHub Action in the repo. This is the feature that earns adoption; the ERD earns affection.

A scheduled nightly job runs full `stitch build` (with Metabase) on main and commits the refreshed `graph.json` — so the baseline tracks Metabase-side drift (new cards, archived dashboards) without any human remembering to rebuild.

## 11. Agent surface

`graph.json` has a stable documented schema — that alone makes it agent-consumable (Claude, Cortex, whatever reads files). `stitch export --format jsonl` additionally emits flat one-record-per-line nodes and edges for easy loading anywhere, including a `COPY INTO` recipe for teams that want it queryable in Snowflake. A recipe in the docs, not a product surface — the lesson of v0.2/v0.3 is that the warehouse backend is a consumer of this tool's output, not its home.

## 12. Phasing

| Phase | Scope | Estimate |
|---|---|---|
| **0** | `build`: dbt models **+ column lineage via sqlglot on compiled SQL** + MBQL cards; `graph.json` deterministic + `--check`; coverage report incl. lineage trace rate; recursive `impact` + GitHub Action; `stitch search` (CLI); `doctor` basics | ~1 week (was a long weekend; sqlglot edge cases are the growth) |
| **1** | `serve`: **search + detail panels** (the entry point), end-to-end column lineage view, catalog, read-only ERD; `export --site` | +2 weeks |
| **2** | Editable canvas: write-back with diff preview, dirty-file guard, suggestion layer, `layout.yml` | +2–3 weeks |
| **3** | Metabase **native SQL** cards via sqlglot + template-tag substitution, rename heuristics, `--verify-lineage` (ACCESS_HISTORY), Metabase version matrix, `--branch` git flow | ongoing |

Phase 0 is the whole bet, unchanged across three architectures: if the PR comment doesn't change behaviour, the canvas won't save the project.

## 13. Risks

- **Committed generated file friction.** `.stitch/graph.json` in git means occasional merge conflicts (regenerate-and-recommit resolves them — document it) and reviewers seeing a machine file in diffs. Deterministic ordering keeps diffs semantic; `--check` in CI keeps it honest. If it proves hateful in practice, the fallback is storing the baseline as a CI artifact keyed by commit SHA — costs the git-native diffing, keeps everything else.
- **Native SQL coverage — not a configuration problem.** Smitten is MBQL-only; the tool gets built against its easiest case while most Metabase shops live in the hard one. Coverage reporting from Phase 0 makes the gap legible; sqlglot is Phase 3 and the README says so.
- **Frontend bundling.** Shipping a prebuilt SPA in the wheel means a JS build step in *release* CI and wheel size in the tens of MB. Acceptable; the alternative (npm at install time) is not, for a pip package.
- **Metabase API drift.** Pin 49+ (API keys), keep a tested version matrix, treat every response shape as untrusted, keep raw payloads for repro.
- **Adjacency to `dbt-metabase`.** If it grows column-level exposures, Phase 0's differentiation shrinks. Open an issue with the maintainer early — Phase 0 might be strongest as an upstream contribution plus this repo owning the viewer/editor.
- **Sole maintainer with a day job.** One maintainer is a repo, not a project, unless the CI feature earns contributors. Optimize the README for the impact-comment screenshot.
