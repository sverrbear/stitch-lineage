# Design spec — dbt ↔ Metabase column-change impact and Metabase repair

**Working name:** `stitch` (placeholder)
**License:** MIT
**Distribution:** `pip install stitch-lineage` from PyPI (tagged releases), or `pip install git+https://github.com/sverrbear/stitch-lineage.git` to ride `main`. Local-first: no server, no warehouse backend, no hosted anything. The dbt repo is the database.
**Status:** v0.5, amended 2026-10-22 — supersedes v0.4. Deltas from v0.4, decided during real-world rollout:

- **The graph is a purely local artifact.** `.stitch/` is gitignored in consumer repos; nothing generated is committed. The committed-baseline design (§3, §5, §10) remains documented but optional — teams that want git history of the graph can commit it, nothing requires it.
- **`stitch impact` is a local command.** Every build snapshots the graph it overwrites to `.stitch/graph.prev.json` — the default baseline — and closes with a one-line blast-radius summary whenever the two differ, so impact needs no committed graph and no git. `--base-file <path>` and `--base <git-ref>` still take an explicit baseline, in that precedence order. Only the PR-comment CI workflow (§10) stays shelved, since that one does require the committed baseline.
- **Description edits and existing staged relationships use plan/apply** — §8.2 below. The ERD authoring surface is retired.
- **Phases 0 and 1 are shipped**, including `stitch build --docs/auto_docs`, per-database `table_prefix` (dev artifacts bind to a prod-pointed BI database), manifest-columns fallback for the sqlglot schema map, and manager badges (dbt/Snowflake/Metabase marks) on every node in the app.
- **Amended 2026-08-10** (product review): the order of work and the boundaries of the product are written down — §12.1 priority order, §12.2 scope guardrails. Phases are unchanged.
- **Amended 2026-08-11** (distribution): **the "PyPI deliberately skipped" decision is reversed.** stitch publishes to PyPI as `stitch-lineage`, and `pip install stitch-lineage` is the primary install. The original reasoning — that installing from a ref you chose keeps you in control of when the tool changes — argues for pinning, not against a registry: a tagged release is just as pinnable and far easier to install, and git-install remains documented for riding `main`. Releases are tag-triggered: pushing a `v*` tag runs `.github/workflows/publish.yml`, which refuses to build unless the tag matches the version in `pyproject.toml`, refuses to publish a wheel that does not contain the built frontend (`app/frontend/dist/`), and uploads through PyPI Trusted Publishing (OIDC, `environment: pypi`) with no API token stored anywhere. Nothing else in the spec changes: distribution is a delivery channel, and local-first is unaffected.
- **Amended 2026-08-12** (badges, #187): the node badge answers **who manages this table**, not which system it lives in — §9's "System badges" becomes "Manager badges". dbt-produced nodes (models, seeds, snapshots) take the dbt mark; dbt `source`s, which land in Snowflake by something outside this repo, keep the Snowflake mark; columns take their table's manager; the Metabase side is unchanged. One mark for the whole warehouse was a distinction nobody could act on — the actionable fact is whether `dbt run` builds the thing you are about to change. Nothing else moves: the graph, the resolvers and the node types are untouched, this is what the app *says* about them.
- **Amended 2026-10-22** (product focus, #242): remove the ERD canvas and relationship suggestions, including their CLI/API, layout engine and dismissal store. Keep column lineage, impact, `mend`, and unused-downstream analysis (`doctor --dead`). Description editing and existing staged changes retain a standalone review/apply page. Retire ERD saved views and additional relationship drawing shapes from the roadmap.
- **Amended 2026-10-22** (Mend defaults, #246): only declared rename repoints are enabled by default. Strip and archive require explicit config; apply re-checks current action and collection policy for saved plans. Execution validation does not prove business meaning, and rollback is attempted rather than guaranteed.
- **Amended 2026-10-22** (onboarding, #244): `stitch init` connects, builds and opens the app; OS-stored credentials and existing configuration support retries. Environment credentials remain available for CI.
- **Amended 2026-10-22** (browser Mend, #247): local snapshot comparison, declared renames, query/dashboard review, explicit one-use approval and per-report recovery results are available in the browser. Browser defaults remain repoint-only regardless of a permissive CLI policy (§14.8).
- **Amended 2026-10-27** (assisted renames, #33): browser Mend offers conservative snapshot-based replacement suggestions with evidence and visible ambiguity. Choosing a suggestion explicitly declares that mapping for the next review; suggestions never change the impact diff or authorize writes (§14.8).

---

## 1. Problem

Stitch answers which downstream reports depend on a dbt column, identifies affected reports
when columns change, and helps update them through `stitch mend`. The same graph identifies
columns with no known downstream Metabase consumers for review (`doctor --dead`).

The product connects source columns through dbt models to Metabase fields, cards, and dashboards.
It reports coverage gaps beside its answer; dependencies alone do not guarantee breakage, and
no known Metabase consumers does not prove that a column is unused everywhere.

## 2. Not building

Mature, MIT-licensed, reuse rather than reimplement.

| Capability | Tool | Decision |
|---|---|---|
| dbt → Metabase FK / semantic type / description push | `dbt-metabase` (600★, v1.7.5, May 2026) | Reuse in CI. Our written relationships tests are its input — free FK sync to Metabase. |
| Metabase cards → dbt exposures (model-level) | `dbt-metabase exposures` | Reuse; we extend the idea to column level. |
| ERD conventions | `dbterd` | Match its `meta.relationship_type` convention for interop. |
| SQL column lineage | `sqlglot` | Compiled dbt SQL (§7.3) and Metabase native cards (§7.4). |

Product focus: **column-level BI impact, repair, and downstream usage**.

## 3. Architecture

Everything is a file in (or derived from) the dbt repo. The generated graph, baselines,
and staged changes are local and gitignored by default.

```
dbt artifacts + Metabase API -> stitch build -> .stitch/graph.json
                                              + local baselines/history
stitch impact -> changed columns and affected reports
stitch serve  -> search, impact-first Summary, column/table Lineage
stitch mend   -> plan, explicit apply, execution checks and rollback attempts in Metabase
stitch doctor --dead -> unused-downstream candidates for review
```

No standing database or hosted service is required. The app and CLI use the same graph.
Static exports share a read-only view; JSONL exports remain available for other consumers.
Description edits stage locally and use the existing guarded apply engine (§8.2).
The relationship canvas and suggestion workflow are outside the product scope.

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

`graph.json` — one file, deterministic ordering (nodes sorted by `node_id`, edges by `(from, to, type)`, header keys first and every other key sorted) so that regeneration without semantic change produces a zero diff. Gzip-optional; plain JSON by default because reviewable diffs are the point of committing it.

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

Node payload: `name`, `database/schema/table/column`, `data_type`, `data_type_source` (which source in §7.6's waterfall answered — absent when there is no type), `description`, `owner`, plus a `properties` object for type-specific extras (tags, materialization, collection_id, card creator, archived flag). On a model, `properties.materialization` is the manifest's `config.materialized` verbatim, and it is load-bearing rather than decorative: it is what says whether the node is a relation at all, which helps explain the node in its object details.

**Edges — direction pinned: `from → to` is always data flow, upstream to downstream.** Impact traversal walks `from → to`; a single edge type pointing the wrong way silently corrupts every impact report.

| edge_type | from → to | Derived from | confidence |
|---|---|---|---|
| `references` | upstream model → downstream model | manifest `depends_on` | exact |
| `feeds` | upstream column → downstream column | sqlglot over dbt compiled SQL (core, §7.3) | exact / parsed / inferred |
| `binds_to` | dbt column → mb_field | name binding (§7.4) | exact / fuzzy |
| `consumed_by` | mb_field → mb_card | MBQL walk / SQL parse | exact / parsed |
| `appears_on` | mb_card → mb_dashboard | dashcards | exact |
| `relates_to` | FK column → referenced PK column | meta declaration, relationships test, or contract constraint (§8) | declared / validated |

`relates_to` is a declaration, not a flow — **excluded from impact traversal**, shown as relationship evidence in object details.

Every edge carries `confidence` and `evidence` (manifest path, MBQL fragment, parsed SQL span). The UI renders `parsed`/`fuzzy`/`inferred` edges visibly differently from `exact`. One phantom dependency presented as fact costs the tool its credibility permanently.

## 6. Configuration

### 6.0 `stitch init` — connect, build, open (#244)

The first-run command ends in a working app over the user's own graph. The common path asks for
only the Metabase URL and API key; configured values are reused.

1. Detect `dbt_project.yml`, derive the project and artifact path, and reuse usable artifacts.
   Missing/unreadable artifacts or a fully uncompiled manifest trigger an offer to run
   `dbt docs generate` with the existing project/profile and configured `docs_args`. Explain that
   warehouse login may be required. Configuration-only mode requires the manifest, not the catalog.
2. Resolve credentials from the environment first, then the OS credential store, then a hidden
   prompt. Config parsing itself stays offline; only network commands recover a saved key.
   Keys entered in init are validated and saved in an OS store, scoped by resolved config path,
   Metabase URL and environment-variable name. Project files keep only the environment reference.
   The supported stores are macOS Keychain, Windows Credential Locker, Secret Service and KWallet
   through `keyring`; third-party plaintext fallbacks are not used. If storage is unavailable,
   finish in the current session and explain how to reconnect or supply environment credentials.
3. Match every model database in the manifest. Confident unique matches and inferred schemas
   are automatic; ambiguity gets a picker. Existing `stitch.yml` is reused without rewriting
   custom settings. `--force` explicitly replaces it. Record the URL in `.stitch/setup.json`
   so a connection failure does not discard it; no credential enters that checkpoint.
4. Write the derived configuration and a name-only `.env.example` entry; ignore `.stitch/`.
   Check connectivity and model matches. Do not install a GitHub Action during onboarding.
5. Run the existing full build pipeline, reusing artifacts already checked/generated. The first
   graph is the baseline for the next build; normal SHA-history rules are unchanged. On failure,
   leave configuration and saved credentials ready for another `stitch init`.
6. Start the existing localhost app and open a real column feeding an active Metabase card.
   Choose deterministically by nearest reverse data-flow path, excluding relationship edges,
   with the same 20-hop bound as impact. If no such column exists, open search/coverage instead.
   Ctrl+C ends the local server. Init never applies a repair or writes to Metabase.

`--configure-only` retains configuration-only setup; `--no-serve` builds and exits; `--no-open`
serves without opening a browser; `--port` selects the local port. The standalone build, serve,
impact, doctor and mend commands keep their existing responsibilities.

### 6.1 `stitch.yml`

`stitch.yml` at the dbt project root. Committed. No secrets — API-key references resolve from the environment or, for network commands, the OS credential store.

```yaml
dbt:
  project_dir: .
  target_path: target/
  auto_docs: true                       # run `dbt docs generate` at the start of
                                        # every build; default false, and
                                        # --docs/--no-docs overrides it either way
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
  exclude_packages: ["elementary"]      # dbt packages nobody expects to find in
                                        # Metabase. Their models keep their
                                        # lineage and their graph nodes; they
                                        # leave the BIND DENOMINATOR, so
                                        # "unbound" means "we expected to find
                                        # this in Metabase and did not"
  exclude_models: ["stg_*"]             # same, per model: fnmatch globs on the
                                        # dbt model name

relationships:
  write_to: relationships_test          # | meta | contract_constraint — the DEFAULT
  fk_meta_keys: [metabase.fk_target_table, metabase.fk_target_field]  # dbt-metabase interop,
                                        # used only by write_to: meta
  cardinality_meta_key: relationship_type   # dbterd interop; also where the test form
                                        # keeps the arity a test cannot express
  validated_test_severity: warn         # every test stitch writes carries this, explicitly
  test_argument_style: auto             # | flat | arguments — where `to`/`field` sit under
                                        # `relationships:`. dbt 1.10 nests them in an
                                        # `arguments:` mapping; auto reuses whatever the
                                        # file already does and falls back to flat

write:
  strip_model_prefixes: ["viz_"]        # presentation-layer prefixes: a declaration staged
                                        # on `viz_dim_users` is WRITTEN onto `dim_users`.
                                        # Distinct from serve.strip_model_prefixes, which
                                        # only hides the prefix on screen

serve:
                                        # unknown scopes warn and fall back to the
                                        # auto-picked one, they are never an error

output:
  dir: .stitch/
  retain_cache_runs: 3                  # raw Metabase payload snapshots
  history_retention: 20                 # SHA-keyed graph baselines in .stitch/history/;
                                        # 0 turns history off and clears the directory
```

Rules carried forward:

- **No default that only makes sense at Smitten.** Required-and-unknowable values fail with the discovery path: `metabase.databases is required — run 'stitch doctor --list-databases'`.
- **Derive rather than configure** wherever dbt already knows the answer.
- Config precedence: CLI arg > env var > `stitch.yml`, matching `dbt-metabase` since both run in the same CI.

## 7. `stitch build` — ingestion and resolution

Reads dbt artifacts and the Metabase API, writes `graph.json`. Idempotent; safe to run anywhere the env vars exist.

### 7.1 dbt side

`target/manifest.json` → models, sources, columns, `references` edges from `depends_on`, `relates_to` edges from declared FKs (§8). `target/catalog.json` → column types; manifest columns as fallback for views/ephemerals absent from the catalog. Missing artifacts → error naming the fix (`run dbt docs generate`), never a partial graph.

A **model's column set** comes from its compiled SQL, not from the catalog: the outermost projection (§7.3) unioned with its `schema.yml` columns, with the catalog supplying data types for matching names only. The catalog describes the *warehouse*, and a PR that drops a column has not deployed yet — a catalog-authoritative set would make pre-deploy removals invisible to `stitch impact` until after the damage. Models resolve in dependency order so a removal reaches the `select *` downstream of it in the same build. Fall back to the catalog set (manifest columns when the relation is absent from the catalog) whenever the projection cannot be pinned down — unparseable SQL, no `compiled_code`, an unexpandable star, or an output sqlglot can only call `_col_0`. Never an empty set. **Sources** have no SQL, so they stay catalog-then-manifest.

### 7.2 Metabase side

| Endpoint | Purpose |
|---|---|
| `GET /api/database` | locate warehouse DB(s) by display name |
| `GET /api/database/:id/metadata?include_hidden=true` | the `field_id ↔ schema.table.column` map |
| `GET /api/card`, `?f=archived` | active and archived cards: `dataset_query`, `collection_id`, `creator`, `archived` |
| `GET /api/dashboard`, `?f=archived`, `/api/dashboard/:id` | active and archived dashboards; dashcards → card ids |
| `GET /api/collection`, `?archived=true` | active and archived collections, for collection filtering |
| `GET /api/native-query-snippet` | snippet SQL behind a `{{snippet: name}}` tag (§7.4). Best-effort: an instance that will not serve it degrades those cards, not the build |

Auth: API key header, Metabase 49+ asserted at startup. Raw responses land in `.stitch/cache/{timestamp}/` before parsing (gitignored, last 3 kept): resolution bugs get debugged against stored payloads, and `resolve/metabase.py` gets unit tests from real fixtures. Incremental via `updated_at` high-water mark; target 5k cards under 60s warm.

The default card/dashboard listing excludes archived objects even though its filter is named `all`; build merges the active and archived lists by ID and caches both responses. Archived collections participate in the same collection exclusions. Older Metabase releases return an explicit `404` / `error_code: archived` for archived dashboard details: retain the listing summary and archived flag, but count its unavailable card membership as unresolved. Where details are available, archived dashboard edges are resolved normally. `mend` excludes archived cards and archived dashboard parameter mappings from repair plans.

### 7.3 Column lineage inside the dbt DAG — core, not deferred

Without `feeds` edges the chain has a hole in the middle: a renamed staging column would only be traced to Metabase if a card touched it *directly*, while its real blast radius runs through the marts built on it. Column lineage must be continuous — source column → staging → mart → mb_field → card → dashboard — so this is Phase 0 scope.

Mechanics: for every model, take `compiled_code` from the manifest (Jinja already rendered — this is the *easy* SQL-parsing problem, unlike Metabase native cards with their template tags) and run `sqlglot.lineage` per output column with `dialect="snowflake"`, schema-qualified from the catalog so unaliased columns resolve. Each traced input column becomes a `feeds` edge, `confidence: exact` for plain projections and renames, `parsed` for expressions (a column derived from `amount * fx_rate` gets edges from both inputs).

The catalog is authoritative but incomplete: a *dev* catalog only describes the relations that developer happens to have built, and an upstream missing from the schema map takes its whole downstream subtree untraced with it. Relations absent from the catalog therefore fall back to their **manifest columns** (`schema.yml` docs) — types are unknown there, names are all resolution needs. Nothing is invented: a relation documented in neither stays out of the map and its consumers stay untraced.

Known hard cases, handled explicitly rather than discovered:

- **`SELECT *` and dbt macros like `dbt_utils.star`** — post-compilation these are literal stars; expand against the upstream schema (its resolved column set, seeded from the catalog), honouring Snowflake's `EXCLUDE`/`RENAME`. Expanded against manifest columns instead, or unexpandable (upstream in neither) and falling back to name-matching columns across the edge → `confidence: inferred` either way: a star resolved against documentation is a name match however it is dressed up.
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

**MBQL (exact).** `dataset_query.query` carries structured field refs. Walk `fields`, `breakout`, `aggregation`, `filter`, `expressions`, `joins[].condition`, `joins[].fields`, `order-by`. Each `["field", <id>, opts]` resolves through the metadata map — no parsing, no ambiguity. `opts.source-field` (implicit join via an existing FK) remains evidence used by query resolution; it does not generate relationship suggestions.

**MBQL 5 / "lib" (exact).** Modern Metabase (Cloud v-latest) returns `{"lib/type": "mbql/query", "stages": [...]}` instead of `type`/`query`, and both shapes are live in the wild — older self-hosted instances still emit the legacy one, so the shape is detected per card. Differences: refs put the options map in the middle (`["field", {opts}, <id-or-name>]`); a query is a flat chain of stages instead of nested `source-query`; `filter`/`condition` become `filters`/`conditions`; a card source is `source-card: N`; a join carries its own `stages`; native SQL is a `mbql.stage/native` first stage. Clause labels in `evidence` stay shape-independent (`filter`, `joins.condition`, …) so a Metabase upgrade does not rewrite the graph; stages after the first are prefixed `stage1.`, `stage2.`, ….

**Card-on-card (exact, recursive).** `source-table: "card__123"`, MBQL 5 `source-card: 123` and `["metric", …]` refs (Metabase models and metrics) resolve transitively, cycle-guarded with a visited set.

**Native SQL (`confidence: parsed`).** Substitute the template tags, then sqlglot with the Snowflake dialect; unqualified columns and `select *` resolve against a schema map built from the Metabase field metadata of the tables the SQL names (the card's `database` id pins the connection, so the warehouse database in a three-part name is dropped rather than checked — it is not the Metabase display name). Substitution rules, each chosen for lineage rather than execution: `{{var}}` → a neutral literal by declared tag type; `[[optional]]` → **contents kept, brackets dropped**, because a column referenced only inside an optional filter still breaks the card when it is renamed and a blast radius that misses it is a false negative; `{{snippet: name}}` → the snippet's SQL inlined recursively from `GET /api/native-query-snippet` (best-effort: an instance that will not serve it degrades that card, not the build); `{{#123-card}}` → recorded as a card-on-card source and routed through the same transitive machinery MBQL uses, substituting a column-less subquery placeholder so nothing physical is attributed to it. A **field-filter (`dimension`) tag** names a field id outright, so that one edge is `exact` — it is metadata, not a parse.

Edges are ordinary `consumed_by` (mb_field → mb_card): lineage composes through the existing `binds_to` hop with no new edge type, and a native card is a first-class source for card-on-card resolution. A `via` edge inheriting a native card's fields is downgraded to `parsed` — presenting a parsed chain as exact is the phantom-dependency failure mode of §5. Parse failure → degrade to table-level: the card keeps its node, records the physical tables its SQL names on `properties.native_tables`, and itemizes the reason in the unresolved list. A card that resolves only partly still emits what it resolved. Never drop a card silently, never invent a column.

### 7.5 Binding Metabase tables to dbt models

Match `(database, schema, table)` against manifest `relation_name`, honouring `alias`. Explicit handling for the silent-0%-bind traps: Snowflake's uppercase unquoted identifiers (compare case-insensitively, warn on case-only mismatch), display-name ≠ dbt database name (the `databases` map), multiple Metabase connections to one warehouse (allow a list).

**Coverage** written into `graph.json` and printed:

```
models bound       142/147   (5 unmatched → stitch doctor --unbound, 30 excluded by config)
MBQL cards         218/218   exact
native SQL cards    38/41    parsed
dashboards          19/19
dbt column lineage  2551/3293 columns traced   (1329 inferred via star-expansion, 52% of traced)
```

**The denominator is a claim.** `models bound` must read as "models we expected to find in Metabase and did not", so `metabase.exclude_packages` / `exclude_models` (§6.1) take a monitoring package's internal tables out of the ratio entirely — neither bound nor unbound, counted on their own `excluded` qualifier. Exclusion is a *counting* rule only: excluded models keep their nodes, their column lineage, and their `binds_to` edges if Metabase does happen to have them.

**Weak evidence is labelled, not averaged in.** `columns_inferred` is traced by star-expansion or name match rather than parsed out of the SQL, and on a real project it is the majority of the traced count — so the share rides next to the ratio in both the CLI output and the app's coverage block, never folded silently into one number.

**A zero is a claim too — and usually about the artifacts.** Column lineage is a sqlglot pass over `compiled_code`, so a parse-only artifact set (a `dbt parse`, or a docs run with `--no-compile`) traces nothing at all, and the ratio alone reports that as `0/2385 columns traced` — indistinguishable from SQL stitch could not handle. `models_compiled` / `models_uncompiled` are therefore counted separately from `models_bound`, and the build says so out loud, naming the count and `dbt docs generate`. `stitch doctor` asks the same question of the manifest and **fails** when no model carries compiled SQL: artifacts that parse are not artifacts that can be traced, and the check that only reported the former was the one that let §7.3 stay dark on the reference deployment.

Coverage reporting is what turns "the graph looks thin" from a bug report into a documented limitation evaluable in thirty seconds. Non-negotiable in Phase 0.

### 7.6 Data type resolution — a waterfall, with provenance

A column's `data_type` has more than one possible source, and asking only one of them reports `unknown` for types the warehouse has known all along. The dev target's `catalog.json` only describes relations *that* developer built; Metabase, meanwhile, syncs the whole prod schema. Resolve in order, first hit wins, and record which source answered on `data_type_source`:

1. **`catalog`** — this build's dbt artifacts: `catalog.json`, else a `data_type` declared in `schema.yml`.
2. **`metabase`** — the warehouse type (`database_type`, falling back to Metabase's `base_type`) of the field this column binds to. **Exact bindings only**: a fuzzy bind matched on squashed underscores and case is good enough to draw an edge a human reads in context, not to assert a type as fact about a column we know we guessed at.
3. **`inferred`** — sqlglot `annotate_types` over the compiled SQL. Opt-in (`stitch build --infer-types`), because the types come back in sqlglot's canonical spelling rather than the warehouse's (a Snowflake `NUMBER` reads back `DECIMAL(38, 0)`). Expressions that annotate to `UNKNOWN`/`NULL` are dropped, not recorded — "parsed it and learned nothing" is the same state as never asking.
4. Otherwise **unknown**: `data_type` stays null, `data_type_source` is absent, and `unknown_type_reason` keeps saying *why* (#122).

**Precedence lives in one place** (`resolve/types.py`), applied after binding. The inference pass runs earlier, inside the dbt resolver where the compiled SQL and the schema map are, but hands its results over as *candidates* — a parse guess must not outrank the warehouse's own answer merely by being computed first.

**Provenance is shown, not averaged in** (the confidence-visibility principle): the detail panel prints the source under the type — *from the dbt catalog* / *from Metabase sync* / *inferred from expression* — because a `NUMBER(38,0)` the warehouse reported and a `DOUBLE` sqlglot worked out are not the same claim, and only one of them is safe to act on. `stitch build` prints the same split:

```
column types        2874/3293 typed   (1902 catalog, 972 Metabase, 419 unknown)
```

## 8. Relationships: storage and write-back

### 8.1 Storage format

Existing relationship metadata, dbt `relationships` tests, and contract constraints remain
inputs to resolution. The graph retains their `relates_to` edges, including declared/validated
provenance, and object details may show them as text. Those edges are not data-flow edges and
remain excluded from lineage/impact traversal.

Simple relationship metadata uses `metabase.fk_target_table`, `metabase.fk_target_field`, and
`relationship_type` for interoperability with dbt-metabase and dbterd. The existing staged store
and writer continue supporting single-column relationships so earlier staged work is not lost.
Composite/conceptual drawing tools, bridge-creation gestures, and saved ERD layouts are retired
plans; this change adds no new relationship shapes.

### 8.2 Write-back — staged, then `stitch apply` (v0.5, supersedes direct write)

Editing in the app never touches the repo. The flow is plan/apply (issues #24 + #27, extended to documentation by #70 and to in-app apply by #72):

1. Editing a column or model description stages to `.stitch/staged_descriptions.yml`.
   **Staged changes** (`#/staged`) reviews these edits and any previously staged relationships
   without a canvas. Existing entries can be edited/discarded and previewed before apply.
   A static export shows no editing navigation and explains that staged review requires `serve`.
2. **`stitch apply`** materializes everything staged into the repo in ONE run — relationships and descriptions, one diff preview, one confirmation, one clearing pass. Relationships go on the FK column in the form `relationships.write_to` selects (test by default; meta / contract available); a description is set on the model or column entry (created if the key is absent, emitted as a `|` block when it is multi-line, and left alone when the repo already says it):
   - a diff preview is always shown before writing; `--dry-run` shows it and stops; a confirmation prompt gates the write (`--yes` skips)
   - `ruamel.yaml` round-trip mode — comments, quoting, key order preserved. A PR that reformats every line of a hand-maintained schema file gets the tool banned; this is non-negotiable.
   - target file from manifest `patch_path`; no YAML entry for the column → insert in catalog column order; no YAML file for the model → report it as unappliable; never invent a file
   - refuse to write on a dirty target file (uncommitted changes) unless `--force` — the user's edits outrank ours
   - applied entries clear from their store; the next `stitch build` reads them back from the manifest (relationships as `relates_to`, validated; descriptions as node descriptions)
   - a successful apply also **patches `graph.json` in place** with what it wrote — the `relates_to` edges (confidence per the written form: validated for a test, declared for meta; evidence `source: stitch apply`) and the new node descriptions — so the app shows them on a refresh. Rewritten through `io/graph_store` so determinism holds, idempotent (an edge already present, or a description that already matches, rewrites nothing), skipped with a note when there is no graph or the target is not in it, and `--no-graph-update` opts out. The patch never rebuilds inside apply — `dbt parse` would drop compiled SQL and `dbt docs generate` hits the warehouse — and it never touches the previous-build snapshot, since neither a `relates_to` edge nor a description is a column change with a blast radius to report. `--build` runs a real `stitch build` afterwards for full reconciliation.
   **Which model owns the write** — `write.strip_model_prefixes`. A repo whose BI layer is a set of views (`viz_dim_users` over `dim_users`) gives stitch no choice about what to bind: Metabase sees the view, so existing staged entries may name that view. It is not the name that should be written. The view's YAML is generated from the models underneath it in every repo that has one, so a declaration written there is regenerated away before anyone runs dbt — and where the convention is "no tests on the presentation layer", it was never allowed to land there in the first place. Configuring the prefix routes the write to the model underneath: the schema file, the model entry and the `ref()` in the emitted test all follow the routed name together, and both endpoints route independently. Routing is conditional on the stripped model existing in the manifest, so a model that genuinely owns a prefixed name keeps its own declaration, and it applies to staged descriptions on the same terms — the ownership question is the same one. `stitch migrate-relationships` is deliberately exempt: it restates a declaration where it already sits and strips the keys it replaces from that same column, so routing would write one half of the change to a different file.

3. The plan/guard/clear/patch behaviour lives in **`stitch_lineage/apply.py`**, the engine both callers drive: `stitch apply` renders it to a console, `stitch serve` serialises it over HTTP (`POST /api/apply/preview`, `POST /api/apply`). Neither reimplements it, so "the CLI and the app do the same thing" is structural rather than aspirational. Only that engine may import `write/` (import-linter contract), and the app never gets a force path — overwriting uncommitted edits stays a CLI decision. The static export gets no server at all, so it remains read-only by construction.

4. **`stitch migrate-relationships`** rewrites declarations already written as `fk_meta_keys` into the form `relationships.write_to` selects, and removes the two keys it replaces — `cardinality_meta_key` stays, being the only thing a `relationships` test cannot express. It redraws nothing and infers nothing: only declarations the manifest already carries are rewritten, so it sees what the last `dbt docs generate` saw. Composite and conceptual `stitch.relationships` entries are left alone, having no test form to migrate into. Same ceremony as apply — round-trip proof, one diff preview, one confirmation, files with uncommitted changes refused — and no store to clear, since nothing was staged.

Existing `.stitch/layout.yml` files are left untouched but no longer read or written. The suggestion command, API, engine, and dismissal store are removed. Legacy `serve.erd_default_scope` keys are ignored.

## 9. UI — `stitch serve`

FastAPI on localhost serving a prebuilt React SPA (bundled in the wheel; installing the package requires no node toolchain). React Flow for the canvas. The frontend talks only to the local API; the local API reads `graph.json`, writes the staged stores, and reaches model YAML only through the apply engine (§8.2). `/api/meta` advertises `staging_enabled` and `apply_enabled` so the SPA hides what a given deployment cannot do.

**Object workspace.** Search opens an object's Summary. Summary and Lineage share one qualified
identity header; changing views keeps the selected object, its description draft, summary scroll,
and lineage grain. The Lineage canvas stays lazy-loaded. There is no Relationships tab or ERD.
Old `#/node/:id/relationships` bookmarks open that object's Summary, ignoring obsolete scope/focus
parameters; old `#/erd` bookmarks return to search. Malformed URL encoding remains recoverable.

**Search — first version, not later polish.** One search box, keyboard-first (`/` to focus, `⌘K` palette), querying everything in `graph.json`: dbt models, columns, Metabase fields, cards and dashboards — by name, description, tag, and card/dashboard title. Results grouped by node type, ranked exact-prefix > word-boundary > fuzzy, with type-ahead. Selecting a result opens its workspace Summary:

- **Column** → downstream impact first: exact distinct card/dashboard counts, then collapsed dashboard groups with their cards and Metabase links. Downstream models and fields, SQL definition, upstream sources, metadata, editable description and declared relationships follow. A long description must never precede the impact answer.
- **Card / dashboard** → deep link into Metabase, creator, collection, archived flag, and the reverse view: every dbt column the visual ultimately depends on, with confidence flags.
- **Model / source** → the same impact-first card/dashboard answer, then metadata, editable description, declared relationships, dependencies and columns.

**The impact answer is graph-scoped.** It reports dependencies, not a prediction that a change will break them. An empty result is explicitly limited to this graph; unresolved lineage may hide consumers. Recorded upstream trace gaps sit beside the answer (a literal with no upstream column is not a gap). Exact counts stay independent of bounded displayed lists, which are labelled when partial. Cards shared by several dashboards count once in the headline and appear in each relevant group; cards on no dashboard keep their own group. Archived cards and dashboards remain in the counts and are visibly labelled, and non-exact card paths retain their confidence tags.

Implementation is deliberately boring: a Smitten-sized graph is a few thousand nodes, so search is client-side over an in-memory fuzzy index (fuse.js-class library, final pick at build time) built from `graph.json` at page load. No search server, no index files, identical behaviour in `stitch serve` and the static export. If someone points this at 50k cards one day, sharding the index by node type is the escape hatch — not v1's problem.

`stitch search <query>` in the CLI hits the same data for terminal use and scripting (`--json` for piping), which also means search exists from Phase 0, before any UI ships.

**Column lineage view.** The Lineage link in an object's workspace shows the upstream chain to sources and downstream through models into fields, cards and dashboards. The shared identity and Summary link remain in place; no duplicate title or separate Details bounce is needed. Clicking a graph node's body re-roots the selected object while keeping the grain; its name opens the object's Summary or Metabase destination. Card nodes deep-link into Metabase and show creator + archived status.

**Readable diagram defaults.** Automatic framing never scales below 1:1. If the drawing cannot fit at readable scale, it frames the selection; an explicit **Show overview** may zoom out to fit everything. **Focus selection** returns to readable framing. Column lineage groups only the columns in the selected trace under their owning models, initially shows the selected group and up to two neighbours in each direction, and exposes every remaining adjacent group through counted expansion controls (four groups per click). Columns expand inside their model; field bindings are inspectable there or as explicit field nodes. Grouped edges retain their contributing evidence and the weakest confidence among contributions. Full-graph impact counts remain independent of the displayed subset and the existing trace cap stays labelled. Branch expansion anchors the selected group and preserves the viewport.

**Manager badges.** Every node in the lineage view carries the mark of whoever **manages** it — not of the system it happens to sit in. The dbt mark on everything the pipeline produces (models, and the seeds and snapshots that reach the app as their dependents' upstreams); the **Snowflake mark on dbt `source`s**, which land in the warehouse by something nobody in this repo controls; the Metabase mark on fields, cards and dashboards. A column takes its table's manager. So a glance up a chain shows two boundaries rather than one: where the data arrived, where dbt starts owning it, and where the BI layer begins.

"Which system does it live in" was the wrong question, and answering it hid the right one. Sources and marts both live in Snowflake, so one warehouse mark told a reader nothing they could act on — while the fact that decides whether a change is theirs to make at all is *who builds this*: `dbt run` creates a mart and can rename its columns; it cannot touch a source table, and a rename there arrives as someone else's decision.

Because a 14px mark in a brand color is not a claim a reader should have to decode — and some readers cannot — the words carry the same answer wherever the mark appears: matching `<title>`/`aria-label` on the mark itself, a **managed by** row in the warehouse-side detail panels, and the canvas legend. Search results and detail panels reuse the same badges; `managerOf` (one classifier, unit-tested per node type) is the only thing that decides. Assets live in `assets/logos/` (SVGs in brand colors; nominative use — the marks belong to their owners).

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

Ship the GitHub Action in the repo. This brings the impact answer into the change-review workflow.

A scheduled nightly job runs full `stitch build` (with Metabase) on main and commits the refreshed `graph.json` — so the baseline tracks Metabase-side drift (new cards, archived dashboards) without any human remembering to rebuild.

**SHA-keyed local history — a baseline without committing one (#87).** Every `stitch build` on a clean working tree gzips the graph it just wrote into `.stitch/history/<commit-sha>.json.gz`, keyed by HEAD, retained `output.history_retention` deep (oldest pruned first, insertion order in an `index.json` — mtimes are not a contract). `stitch impact --base <ref>` then resolves *locally first*: `<ref>` → its merge-base with HEAD → that commit's snapshot, or the nearest stored ancestor of it; only on a miss does it fall back to the committed `graph.json` at the ref, and a miss on both names the fix. Which baseline was used is printed on stderr — never inferred, and never mixed into the stdout payload that `--format github-comment` pipes. Builds with a dirty tree store nothing and say so: such a snapshot describes the working tree rather than the commit, and used as a baseline it would report no impact on exactly the changes it contains. `stitch history` lists what is stored (sha, time, node/edge counts, commit subject). This is §12.2's "CI artifact keyed by commit SHA" fallback in local form — it restores §10's PR-diff workflow with nothing committed, inside the gitignored `.stitch/`.

**The point query — ask before you edit.** `stitch impact --column fct_matches.match_intensity` (#86) runs the same downstream walk (`from → to`, `relates_to` excluded, depth-capped) over the *current* `graph.json`: no baseline, no git, no Metabase credentials, because one graph is all a point query needs. The diff answers "what did my change break"; this answers "what breaks if I change this" — the question that gets asked before the edit rather than after it. Same grouped counts as the comment above (models, columns, fields, cards with dashboard and owner), `--json` for piping; input is `model.column`, a bare column name when it is unique in the graph, or a full node id, and ambiguity lists the qualified candidates rather than guessing which of three `match_intensity` columns was meant. Local impact — this plus the previous-build baseline (#53), which is also what brings `impact` back onto `--help` — is the top of the priority order (§12.1).

## 11. Agent surface

`graph.json` has a stable documented schema — that alone makes it agent-consumable (Claude, Cortex, whatever reads files). `stitch export --format jsonl` additionally emits flat one-record-per-line nodes and edges for easy loading anywhere, including a `COPY INTO` recipe for teams that want it queryable in Snowflake. A recipe in the docs, not a product surface — the lesson of v0.2/v0.3 is that the warehouse backend is a consumer of this tool's output, not its home.

## 12. Phasing

| Phase | Scope | Status |
|---|---|---|
| **0** | `build`: dbt models **+ column lineage via sqlglot on compiled SQL** + MBQL cards; `graph.json` deterministic + `--check`; coverage report incl. lineage trace rate; recursive `impact` — previous-build baseline and build summary (#53), `--column` point query (#86), SHA-keyed local history (#87) — + GitHub Action; `stitch search` (CLI); `doctor` basics, plus `doctor --dead` for estate hygiene — unconsumed columns, models feeding nothing, archived-but-bound cards (#88) | **shipped** (only the committed-baseline PR-comment workflow stays optional — §10, #36) |
| **1** | `serve`: search, impact-first summaries, lineage; static export | **shipped** |
| **2** | Description editing, staged review and apply; existing relationship compatibility | **shipped** — ERD/suggestions and planned canvas extensions retired (#242) |
| **3** | Metabase **native SQL** cards via sqlglot + template-tag substitution (#32, shipped; modern Metabase also emits **MBQL 5 lib/stages** for saved questions — #22, shipped ahead of phase order); rename heuristics (#33), `--verify-lineage` over ACCESS_HISTORY (#34), Metabase version matrix (#35) | ongoing |
| **4** | **`stitch mend`** — impact-driven card remediation (§14, #143): `impact --format json`, a deterministic plan with one action per affected card (repoint / strip / archive / notify), per-action autonomy, and an apply loop that checks current policy, validates rewritten queries and attempts rollback on execution failure; `doctor --write-access`; the one-job post-deploy Action template | ongoing |

Work is tracked as GitHub issues; the tracker, not this table, is the operational truth.

### 12.1 Priority order (2026-08-10)

Phases describe scope, not sequence. The current order of work cuts across them:

1. ~~**Local impact.** The previous-build baseline plus a blast-radius summary on every build (#53), then the point query `stitch impact --column` — a blast radius askable *before* an edit, not only as a diff after one (#86) — and SHA-keyed local history (#87). This returns §10's killer feature to the local-only world where the graph now lives; the committed baseline stays the CI variant for teams that keep one.~~ Shipped: all three, and the README now opens on the blast radius rather than the catalog (§13, #89). The committed baseline remains the CI variant (#36).
2. ~~**`stitch init`** (#29, §6.0). Every install after the first one starts here, and setup friction is a product feature.~~ Shipped: the wizard derives from `dbt_project.yml` and the manifest instead of asking (§6.0).
3. ~~**Metabase native SQL cards** (#32, §7.4). `native SQL cards 0/41` was the honest headline limit of the tool for every shop that is not MBQL-only.~~ Shipped: native cards parse (§7.4).

**All three delivered as of 2026-08-11.** Sequence from here is the tracker's, not this section's.

A broken core lineage chain outranks cosmetic work. ERD and relationship suggestion work is retired; impact, mend, downstream usage, and coverage remain the priorities.

### 12.2 Scope guardrails — what stitch is not

Each of these was a reasonable-sounding idea. They are decided against, not deferred.

- **One lineage canvas, no ERD authoring.** Search is the entry point; Summary and Lineage are contextual object views. Relationship suggestions, drawing, saved canvas layouts, and extra relationship shapes are outside scope.
- **Not a dbt YAML IDE.** `stitch apply` writes description edits and previously staged relationships (§8). They share one staged review page, writer and diff preview (§8.2); no further YAML key follows them in on that precedent.
- **The home page stays a search box and a few numbers.** Search is the entry point (§9). A home page that grows tiles is a dashboard, and a dashboard is a thing nobody opens twice.
- **One BI tool until the Metabase story is complete.** Looker, Tableau and the rest are a new `resolve/` module by construction (§4) — that is what the seam is for, not an invitation to use it while column lineage into Metabase is unfinished.
- **No warehouse backend, no hosted anything.** Standing since v0.2/v0.3 (§3, §11): the warehouse is a consumer of `graph.json`, not its home.

## 13. Risks

- **Committed generated file friction.** `.stitch/graph.json` in git means occasional merge conflicts (regenerate-and-recommit resolves them — document it) and reviewers seeing a machine file in diffs. Deterministic ordering keeps diffs semantic; `--check` in CI keeps it honest. If it proves hateful in practice, the fallback is storing the baseline as a CI artifact keyed by commit SHA — costs the git-native diffing, keeps everything else. That is where it landed: v0.5 made the graph local, and #87 (§12.1) is that fallback in local form — snapshots keyed by SHA inside gitignored `.stitch/`.
- **Native SQL coverage — not a configuration problem.** Smitten is *almost* MBQL-only (8 native cards of 953), so the tool gets built against its easiest case while most Metabase shops live in the hard one. Closed as a gap (§7.4 resolves native cards), but the *exposure* remains: the resolver is exercised by a handful of real cards, and a shop that is native-first will find its edges here first. Coverage reporting is what keeps that legible — a `native SQL cards 12/41` line is a bug report with a number attached.
- **Frontend bundling.** Shipping a prebuilt SPA in the wheel means a JS build step in *release* CI and wheel size in the tens of MB. Acceptable; the alternative (npm at install time) is not, for a pip package.
- **Metabase API drift.** Pin 49+ (API keys), keep a tested version matrix, treat every response shape as untrusted, keep raw payloads for repro.
- **Adjacency to `dbt-metabase`.** If it grows column-level exposures, Phase 0's differentiation shrinks. Open an issue with the maintainer early — Keep the differentiation in column impact and Metabase repair.
- **Sole maintainer with a day job.** One maintainer is a repo, not a project, unless the CI feature earns contributors. Optimize the README for the impact-comment screenshot — done (#89): it opens on `impact --column` output, and the local commands need no CI to demo.
- **Scope creep, one reasonable feature at a time.** No single addition looks like a mistake; the cost shows up as a second half-finished surface competing for the same maintainer. §12.2 is the answer, and its test is the core chain — a new surface waits until column → field → card → dashboard is complete and correct, not until the backlog is empty.

## 14. `stitch mend` — closing the loop impact opens

§10 names what a schema change broke. Nothing repaired it. In the pilot repo one feature-deprecation PR silently broke **29 live cards across 6 dashboards**: roughly two-thirds were cards that should have been archived with the dead feature, the rest healthy general-purpose cards that merely referenced one removed column — a dead filter, one series among many. All of the damage was mechanical to describe, most of it mechanical to repair, and none of it was repaired, because the repair surface was 29 hand-edits in a browser.

`mend` is that repair, as a plan a human reads and a machine applies.

```
prod dbt rebuild ──► Metabase sync_schema ──► stitch build ──► stitch impact --format json
                                                                     │  (columns non-empty)
                                                               stitch mend --plan
                                                                     │
                                                     Slack notice (webhook): the full plan
                                                                     │
                                                               stitch mend --apply
                                                                     │
                                                 query rewrite: write ► execute ► attempt rollback on error
                                                                     │
                                                               Slack summary
```

One post-deploy job. No artifact handoff, no environment gate, no runner held pending.

**Explicit apply and conservative defaults (#246).** `--plan` writes no Metabase content; `--apply` is the explicit write step. Only `repoint` is enabled by default, and only for a declared rename. Teams may opt into `strip` or `archive` in `mend.auto`, or add a separate human gate using the variant in `action/stitch-mend.yml`. The staleness guard, query execution checks, rollback attempts and per-card outcomes remain in every apply path; none can prove that a report still answers the intended business question.

### 14.1 The plan — one action per card

Input is the impact diff plus an optional **declared** rename map (`--rename fct_orders.amount=fct_orders.amount_usd`, repeatable). Browser suggestions (#33) feed this map only after the user chooses a candidate. The planner never treats an inferred match as a declared rename. Declared-only is what keeps the plan deterministic and the blame legible.

| Action | When | Write |
|---|---|---|
| **repoint** | the column is in the rename map *and* its new column resolves to a live Metabase field | rewrite `["field", old_id, …]` → new field id across every query stage *and* the dashcard `parameter_mappings` |
| **strip** | the removed column appears only in non-essential clauses | delete the dead clause (a filter; one breakout of several; an order-by; one aggregation of several) |
| **archive** | the card's essential substance is dead — its only aggregation, its sole dimension | `archived: true`, never a delete |
| **notify** | the card is in a collection mend must not touch, its action is dialed out of `mend.auto`, it is native SQL, or it is reached only through another card | none — listed for a human |

**The essentialness rule, stated once:** a clause is essential if removing it changes what the card *is about*, not just how much it shows. Sole aggregation → essential. One filter among the criteria → not. `expressions` and join conditions are always essential (a custom column is a definition other clauses build on; a join condition decides which rows exist at all), so a dead reference inside one refuses the rewrite rather than deleting it.

Three consequences worth stating because each was a decision:

- **A card can carry two repairs, and is labelled by the worse one.** A card holding both a renamed and a removed reference is a `strip` whose diff also carries the repoint — leaving the dead reference behind to keep the label pure would ship a query that does not run. If `strip` is dialed out, the whole card downgrades to notify.
- **An unresolvable declared rename never decays into a strip.** If Metabase has not synced the new column yet, there is no field id to point at — and stripping the clause would delete a reference to a column that still exists under another name. Those cards become `notify`, and the plan says which rename it could not follow.
- **Removing one aggregation of several renumbers the rest.** A legacy `["aggregation", N]` elsewhere in the query is remapped, and a reference to a *removed* aggregation takes its own clause with it. Without that, the card still runs and answers a different question — the exact failure this feature exists to prevent.

The plan is a deterministic file (`.stitch/mend_plan.json`): per card the id, action, reason, before/after `dataset_query`, dashcard edits, the `updated_at` observed at plan time and the latest revision id. Ordering is card-on-card dependency depth then card id, so a card is repaired after the card it sources. Nothing volatile is in the body — no `generated_at` — so the same graphs and the same rename map produce a byte-identical plan. Renderers: `--format slack | github-comment | text`, all three from one gathering, with **strip first** because it is the one action whose wrongness is silent.

### 14.2 Autonomy — per action, not per plan

```yaml
mend:
  slack_webhook: ${STITCH_SLACK_WEBHOOK_URL}   # env reference only, like metabase.api_key
  auto: [repoint]                              # default; strip/archive require explicit opt-in
  notify_only_collections: ["*Personal*"]       # never written to; listed for humans
```

Only `repoint` is enabled by default. `strip` can remove a filter and leave a report running with different numbers under the same title; re-execution cannot detect that semantic change. `archive` hides the card until someone unarchives it. Both require explicit opt-in. `notify` is deliberately not configurable: it is the absence of a write, not an autonomy level.

**Migration:** an omitted `auto` now resolves to `[repoint]`; existing explicit lists, including the old `[repoint, strip, archive]` and an empty `[]`, are preserved. To adopt the safer policy, replace an explicit broad list with `[repoint]`. An empty list disables every write.

### 14.3 Apply — current policy, execution checks and rollback attempts

Before writing, apply checks each action against **current** `mend.auto`, never the saved plan's allowlist. Disallowed actions become `notify` outcomes without API reads or writes. Existing `notify` entries stay notify even if policy is relaxed; re-plan to authorize a new repair. When collection restrictions are configured, apply fetches the current collection listing and checks each freshly read card's current collection id using the same name, path and personal-collection matching as planning. Unreadable collection policy fails closed. `--force` cannot bypass action or collection restrictions.

For each eligible card, in order: **staleness guard** (re-read the card; changed `updated_at` → skip as `stale`; `--force` overrides this guard only) → **snapshot** an available revision id → **`PUT /api/card/:id`** with only what changed → **validate query rewrites** by re-executing the card (read the HTTP status, query `status`, and `error`) → **attempt rollback on execution failure** through the revisions API, falling back to restoring the captured `before` query. Rollback can itself fail; the outcome records that failure for manual recovery. Successful execution does not validate business meaning or numeric correctness.

Archive actions set the flag and are not re-executed; they can be reversed by unarchiving in Metabase. A dashcard write reads the dashboard back and replaces only the named dashcards, preserving unrelated changes. Dashboard writes can partially fail after the card query passes execution validation. That failure does **not** revert the card or previously completed dashboard writes; the summary names the failure and how many dashcards were updated. Apply is not a transaction across cards and dashboards.

The summary — applied / archived / stale / failed / skipped / notify, every card named, `failed` first and strips next — goes to Slack when requested and to stdout. Any `failed` exits non-zero. **The plan is the dry run**; apply additionally prints a unified diff of every rewritten `dataset_query`.

`stitch doctor --write-access` is the pre-flight: who the API key is, how many cards report `can_write`, whether revision history is readable, and what `mend.auto` allows. It writes nothing and does not guarantee rollback will succeed.

### 14.4 Why this is post-deploy, not PR-time

Repointing needs the **new** field ids, which exist only after the warehouse has rebuilt and Metabase has synced. So mend runs in the post-deploy job. A PR-time `mend --plan --format github-comment` preview (no apply) is a cheap later addition now that the plan half exists.

### 14.5 The seam

Two additions to §4, both of them the existing rules holding rather than bending:

- The HTTP seam now reads **only `io/` clients speak HTTP** — `io/metabase_client.py` and `io/slack_webhook.py`. What the rule protects was never the file count; it is that HTTP lives at the edge behind a named client so everything above it is testable with no network. Two clients, and no more without a spec change.
- `mend/` splits the same way `resolve/` and `io/` do: `plan`, `rewrite`, `render` and `models` are **pure** (graphs and raw payloads in, a plan out) and `apply` is the only module that reaches `io/`. Both are `import-linter` contracts. Rewriting reuses the resolver's own shape vocabulary rather than carrying a second opinion about where a field ref can hide — rewriting is the inverse of the walk, and two walkers would diverge on the first Metabase upgrade.

### 14.6 Out of scope

- Rewriting **native SQL card text**. Resolution is not rewriting; SQL surgery via sqlglot is its own feature. A native card that references a dead column is reported, never edited.
- Automatic rename declarations. User-confirmed suggestions (#33) can feed the declared map; inference alone never authorizes a repair.
- Any hosted approval service, or a Slack app with a callback endpoint (§12.2).
- Changing what a card *means* beyond removing references to columns that no longer exist.

### 14.7 Risks

- **Semantic correctness is not execution success.** Even a declared rename can point at a column whose meaning changed. Strip and archive require explicit opt-in, and clause removal can silently change the answer. Inspect the plan and the resulting report; a successful query is not evidence that its numbers are correct.
- **Partial writes and failed rollback.** Card and dashboard writes are not transactional. Query failures trigger rollback attempts, which can fail. Dashboard failures leave the repaired card and any successful dashboard updates in place. Outcomes report these cases for manual recovery; reversal is never guaranteed.
- **Revisions API drift.** Same posture as the standing Metabase-API risk (§13): version matrix, untrusted response shapes, raw payloads retained. If an instance will not serve revisions, mend says so in `doctor --write-access` and attempts to restore the captured query instead.

### 14.8 Local browser review (#247)

The local app's Mend page compares `graph.prev.json` with `graph.json`, names both build
times, and distinguishes missing baseline from a comparison with no removed columns.
It does not rebuild artifacts, sync Metabase, or continuously monitor changes. A declared
rename selects an actual current graph column. Planning checks its field against live
Metabase metadata; missing, inactive or renamed fields remain manual.

Replacement suggestions (#33) are pure, deterministic comparisons of those snapshots:
only added columns in the same dbt relation, with compatible non-inferred types and an
exact field binding not marked retired in the current snapshot, qualify. A candidate also needs
concrete name similarity or identical exact upstream columns; weak and missing evidence
produce no suggestion. Evidence names its snapshot provenance, not a live check or proof
of equivalent business meaning. Multiple plausible replacements and targets shared by
several removals are visibly ambiguous; at most three candidates are shown, with the
total retained. These hints do not modify removed/added impact facts or preselect any
mapping. Choosing one only fills that removed column's declared mapping and invalidates
any existing review; the normal live metadata check and explicit approval still follow.
Existing choices survive snapshot reload for columns still removed; vanished targets
remain visibly invalid until corrected. Context and suggestion rendering make no Metabase
requests and no writes. Snapshot field bindings do not prove current field activity; the
existing live preview checks remain authoritative. CLI declarations and the planner's policy
remain unchanged.

The browser starts with repoint only, constrained by current `mend.auto`. Strip and archive
need explicit per-review opt-ins and explain changed results/hidden reports. Plans show
affected reports including protected, unreadable and unsupported ones; executable repairs
show query before/after and dashboard parameter edits. Approval follows the actionable
reports, with manual work visible separately. Query execution is not semantic validation.

The CLI injects a Mend runtime through a protocol; `app/` never imports planning resolution
or the Metabase client. The runtime reuses the same planner/apply engine and credential
restoration. One server-held preview is active at a time, expires after 15 minutes, and
binds graph/configuration bytes and resolved config inputs to an opaque identifier. Any
new planning attempt invalidates the older preview. Changes to form inputs invalidate the
visible preview immediately. Apply accepts that identifier and explicit confirmation only;
clients cannot supply rewritten queries or force flags. A nonblocking lock refuses overlapping
planning/apply requests, and approval is consumed before applying so retries cannot repeat it.
Server restarts require replanning. The last completed result can be recovered by preview ID
after a lost response; this is process-local, not a persistent audit service.
Failed/partial outcomes retain the reviewed original/attempted queries and dashboard mappings
for manual recovery until another completed apply or restart replaces the result.

Apply verifies the snapshot/config fingerprint, current collection restrictions, exact
reviewed card queries, timestamps, write access, and existing dashboard conflict guards.
The result retains per-card stale/skipped/failed/manual outcomes, distinguishes successful
rollback from rollback failure, and treats dashboard partial failures as failures. Static
exports advertise Mend disabled; their client also refuses all Mend requests even if hosted
beside an API. Existing same-origin and loopback Host protections cover the routes.
