# Design spec — dbt ↔ Metabase column lineage and interactive ERD

**Working name:** `stitch` (placeholder)
**License:** MIT
**Distribution:** `pip install git+https://github.com/sverrbear/stitch-lineage.git` (PyPI deliberately skipped). Local-first: no server, no warehouse backend, no hosted anything. The dbt repo is the database.
**Status:** v0.5 — supersedes v0.4. Deltas from v0.4, decided during real-world rollout:

- **The graph is a purely local artifact.** `.stitch/` is gitignored in consumer repos; nothing generated is committed. The committed-baseline design (§3, §5, §10) remains documented but optional — teams that want git history of the graph can commit it, nothing requires it.
- **`stitch impact` is a local command.** Every build snapshots the graph it overwrites to `.stitch/graph.prev.json` — the default baseline — and closes with a one-line blast-radius summary whenever the two differ, so impact needs no committed graph and no git. `--base-file <path>` and `--base <git-ref>` still take an explicit baseline, in that precedence order. Only the PR-comment CI workflow (§10) stays shelved, since that one does require the committed baseline.
- **Phase 2 is a plan/apply model, not direct write-back** — §8.2 below. Drawings stage locally; an explicit `stitch apply` writes dbt `relationships` tests.
- **Phases 0 and 1 are shipped**, including `stitch build --docs/auto_docs`, per-database `table_prefix` (dev artifacts bind to a prod-pointed BI database), manifest-columns fallback for the sqlglot schema map, and system badges (Snowflake/Metabase marks) on every node in the app.
- **Amended 2026-08-10** (product review): the order of work and the boundaries of the product are written down — §12.1 priority order, §12.2 scope guardrails. Phases are unchanged.
- **Amended 2026-08-11** (status reconciliation): §12's table is brought current. Phase 2 shipped — staged relationships and `stitch apply` (#24/#27), staged descriptions and apply from the app (#70/#71/#72), the suggestion layer (#30) — except `layout.yml` saved views and node positions (#31), which are not built; phase 3's native SQL (#32) and MBQL 5 (#22) shipped, leaving #33/#34/#35. §12.1's three priorities are all delivered. Scope, phase boundaries and §12.2's guardrails are unchanged: this records what shipped, it decides nothing new.

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
| SQL column lineage | `sqlglot` | Compiled dbt SQL (§7.3) and Metabase native cards (§7.4). |

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
 │   ├─ layout.yml           ◄── ERD positions, saved views, dismissed suggestions. Local (§8.2).
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

Node payload: `name`, `database/schema/table/column`, `data_type`, `data_type_source` (which source in §7.6's waterfall answered — absent when there is no type), `description`, `owner`, plus a `properties` object for type-specific extras (tags, materialization, collection_id, card creator, archived flag).

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
5. Propose `include_schemas` from where marts actually live; write `stitch.yml`; append `.stitch/` to `.gitignore` — the whole directory, since v0.5 made all of it local; drop the GitHub Action into `.github/workflows/` with its real trigger commented out and `workflow_dispatch` standing in (a workflow with no `on:` block is an Actions error, not a disarmed workflow).
6. Finish with a mini-doctor (Metabase reachable, version ≥ 49, manifest parses, model counts on both sides) and print the next command.

The Action drop is best-effort: `action/` lives in the repo, not in the wheel (`packages = ["stitch_lineage"]`), so a pip-installed `init` skips that step and says so rather than pretending. Open question, not decided here: promote `action/` to package data, or leave the templates a source-checkout convenience and let pip users copy them from the repo.

Target: repo → configured in under two minutes with four human inputs (URL, key, one mapping confirm, one schemas confirm) — arming the Action is a later manual step, not one of the four. Every derived value is written into `stitch.yml` explicitly rather than defaulted invisibly, so the config file remains the full, inspectable truth.

### 6.1 `stitch.yml`

`stitch.yml` at the dbt project root. Committed. No secrets — the API key is env-only.

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

serve:
  erd_default_scope: "schema:MARTS"     # | tag:<name> — ERD scope the app opens on;
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
| `GET /api/card` | all cards: `dataset_query`, `collection_id`, `creator`, `archived` |
| `GET /api/dashboard`, `/api/dashboard/:id` | dashcards → card ids |
| `GET /api/collection` | tree, for collection filtering |
| `GET /api/native-query-snippet` | snippet SQL behind a `{{snippet: name}}` tag (§7.4). Best-effort: an instance that will not serve it degrades those cards, not the build |

Auth: API key header, Metabase 49+ asserted at startup. Raw responses land in `.stitch/cache/{timestamp}/` before parsing (gitignored, last 3 kept): resolution bugs get debugged against stored payloads, and `resolve/metabase.py` gets unit tests from real fixtures. Incremental via `updated_at` high-water mark; target 5k cards under 60s warm.

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

**MBQL (exact).** `dataset_query.query` carries structured field refs. Walk `fields`, `breakout`, `aggregation`, `filter`, `expressions`, `joins[].condition`, `joins[].fields`, `order-by`. Each `["field", <id>, opts]` resolves through the metadata map — no parsing, no ambiguity. `opts.source-field` (implicit join via an existing FK) is itself relationship evidence → suggestion layer.

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

**Two tiers, orthogonal to shape.** The tier is a property of what is ON DISK, not of who drew it — the resolver reads a repo it did not write, so the tier has to be readable from the YAML alone:

| Tier | Stored as | Means | ERD rendering |
|---|---|---|---|
| **declared** | a meta declaration, or a contract constraint | "these relate" — documentation, zero build cost | solid edge |
| **validated** | a `relationships` test (at `severity: warn`) | dbt checks referential integrity on every run | solid edge + ✓ badge |

Since #134 the default written form IS the test, so a relationship drawn in the app and applied comes back **validated** on the next build. That is the point: the arrangement that costs nothing to maintain is also the one that is checked. `write_to: meta` still writes the declared tier for repos that want documentation without a test in their DAG.

What the ✓ therefore means, precisely: *dbt is checking this join's referential integrity.* Not "a human drew it" (the app's staged styling says that, before apply) and not "stitch is confident" (that is the confidence scale on inferred edges). An edge with no ✓ is one nothing exercises against data.

**A written test never fails a pipeline.** Every test stitch writes carries `severity: warn` explicitly, from `validated_test_severity`. A relationship someone drew in a diagramming tool must not be the reason a deploy goes red; a warning is the honest signal, and the team can promote it by hand.

**Arity lives beside the test, not in it.** A `relationships` test states that two columns join and has no field for many-to-one versus one-to-one, so the cardinality is written to `cardinality_meta_key` on the FK column and read back from there. Without that, drawing a one-to-one and rebuilding hands back a many-to-one.

The honest cost of declared-only: nothing exercises it against data. Mitigated structurally — `stitch build` statically validates every declaration against the manifest (target model exists, columns exist, types join-compatible; conceptual edges: target exists) and dangling declarations are build warnings in coverage output. Static checks catch typos; only the validated tier catches orphaned rows.

The edge modal exposes all of this: shape is inferred from what you dragged (column→column = simple; multi-select = composite; table header→table header = conceptual), and cardinality is a dropdown.

### 8.2 Write-back — staged, then `stitch apply` (v0.5, supersedes direct write)

Editing in the app never touches the repo. The flow is plan/apply (issues #24 + #27, extended to documentation by #70 and to in-app apply by #72):

1. Drawing an edge opens a modal: target column confirm, cardinality, shape. Confirming SAVES the declaration to a local staged store (`.stitch/staged_relationships.yml` — lives with the rest of the local state, never committed). Staged edges render visibly pending in the ERD and survive restarts. A staged relationship can be edited before it is applied (cardinality, or the endpoints — which re-hashes its id, so an edit is a replace that dedupes against what is already staged).
   Editing a column or model **description** stages the same way, into the sibling `.stitch/staged_descriptions.yml`: one entry per entity+column, keyed on the target so re-editing replaces (last write wins) rather than queueing.
2. **`stitch apply`** materializes everything staged into the repo in ONE run — relationships and descriptions, one diff preview, one confirmation, one clearing pass. Relationships go on the FK column in the form `relationships.write_to` selects (test by default; meta / contract available); a description is set on the model or column entry (created if the key is absent, emitted as a `|` block when it is multi-line, and left alone when the repo already says it):
   - a diff preview is always shown before writing; `--dry-run` shows it and stops; a confirmation prompt gates the write (`--yes` skips)
   - `ruamel.yaml` round-trip mode — comments, quoting, key order preserved. A PR that reformats every line of a hand-maintained schema file gets the tool banned; this is non-negotiable.
   - target file from manifest `patch_path`; no YAML entry for the column → insert in catalog column order; no YAML file for the model → create per convention
   - refuse to write on a dirty target file (uncommitted changes) unless `--force` — the user's edits outrank ours
   - applied entries clear from their store; the next `stitch build` reads them back from the manifest (relationships as `relates_to`, validated; descriptions as node descriptions)
   - a successful apply also **patches `graph.json` in place** with what it wrote — the `relates_to` edges (confidence per the written form: validated for a test, declared for meta; evidence `source: stitch apply`) and the new node descriptions — so the app shows them on a refresh. Rewritten through `io/graph_store` so determinism holds, idempotent (an edge already present, or a description that already matches, rewrites nothing), skipped with a note when there is no graph or the target is not in it, and `--no-graph-update` opts out. The patch never rebuilds inside apply — `dbt parse` would drop compiled SQL and `dbt docs generate` hits the warehouse — and it never touches the previous-build snapshot, since neither a `relates_to` edge nor a description is a column change with a blast radius to report. `--build` runs a real `stitch build` afterwards for full reconciliation.
3. The plan/guard/clear/patch behaviour lives in **`stitch_lineage/apply.py`**, the engine both callers drive: `stitch apply` renders it to a console, `stitch serve` serialises it over HTTP (`POST /api/apply/preview`, `POST /api/apply`). Neither reimplements it, so "the CLI and the app do the same thing" is structural rather than aspirational. Only that engine may import `write/` (import-linter contract), and the app never gets a force path — overwriting uncommitted edits stays a CLI decision. The static export gets no server at all, so it remains read-only by construction.

4. **`stitch migrate-relationships`** rewrites declarations already written as `fk_meta_keys` into the form `relationships.write_to` selects, and removes the two keys it replaces — `cardinality_meta_key` stays, being the only thing a `relationships` test cannot express. It redraws nothing and infers nothing: only declarations the manifest already carries are rewritten, so it sees what the last `dbt docs generate` saw. Composite and conceptual `stitch.relationships` entries are left alone, having no test form to migrate into. Same ceremony as apply — round-trip proof, one diff preview, one confirmation, files with uncommitted changes refused — and no store to clear, since nothing was staged.

Layout is presentation, not contract: node positions, saved views and suggestion dismissals live in `.stitch/layout.yml` (local like the rest of `.stitch/`), never in model YAML.

## 9. UI — `stitch serve`

FastAPI on localhost serving a prebuilt React SPA (bundled in the wheel; installing the package requires no node toolchain). React Flow for the canvas. The frontend talks only to the local API; the local API reads `graph.json`, writes the staged stores, and reaches model YAML only through the apply engine (§8.2). `/api/meta` advertises `staging_enabled` and `apply_enabled` so the SPA hides what a given deployment cannot do.

**ERD canvas.** Tables as nodes with expandable columns; solid edges are declared relationships read from the repo. Three creation gestures: drag column-handle → column-handle (simple), multi-select column pairs (composite), drag table header → table header (conceptual). The modal infers the shape, offers cardinality, and shows the exact YAML diff before writing (§8.2). Saved views scope the canvas by dbt tag or schema — `marts_revenue` opens as its own diagram, never a 200-node hairball.

**Suggestion layer.** Dashed candidate edges from: (a) Metabase implicit-join `source-field` usage — users are already joining these tables; (b) naming conventions (`*_id` → `dim_*.id` etc.); (c) not built: join predicates observed in native card SQL — that SQL is parsed now (§7.4, #32), but nothing mines it for candidates, so `suggest` ships (a) and (b) only. Accept opens the write-back modal; dismiss records to `layout.yml` so it stays dismissed. This is what makes the initial backfill an hour of accepting suggestions instead of an afternoon of dragging.

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

**SHA-keyed local history — a baseline without committing one (#87).** Every `stitch build` on a clean working tree gzips the graph it just wrote into `.stitch/history/<commit-sha>.json.gz`, keyed by HEAD, retained `output.history_retention` deep (oldest pruned first, insertion order in an `index.json` — mtimes are not a contract). `stitch impact --base <ref>` then resolves *locally first*: `<ref>` → its merge-base with HEAD → that commit's snapshot, or the nearest stored ancestor of it; only on a miss does it fall back to the committed `graph.json` at the ref, and a miss on both names the fix. Which baseline was used is printed on stderr — never inferred, and never mixed into the stdout payload that `--format github-comment` pipes. Builds with a dirty tree store nothing and say so: such a snapshot describes the working tree rather than the commit, and used as a baseline it would report no impact on exactly the changes it contains. `stitch history` lists what is stored (sha, time, node/edge counts, commit subject). This is §12.2's "CI artifact keyed by commit SHA" fallback in local form — it restores §10's PR-diff workflow with nothing committed, inside the gitignored `.stitch/`.

**The point query — ask before you edit.** `stitch impact --column fct_matches.match_intensity` (#86) runs the same downstream walk (`from → to`, `relates_to` excluded, depth-capped) over the *current* `graph.json`: no baseline, no git, no Metabase credentials, because one graph is all a point query needs. The diff answers "what did my change break"; this answers "what breaks if I change this" — the question that gets asked before the edit rather than after it. Same grouped counts as the comment above (models, columns, fields, cards with dashboard and owner), `--json` for piping; input is `model.column`, a bare column name when it is unique in the graph, or a full node id, and ambiguity lists the qualified candidates rather than guessing which of three `match_intensity` columns was meant. Local impact — this plus the previous-build baseline (#53), which is also what brings `impact` back onto `--help` — is the top of the priority order (§12.1).

## 11. Agent surface

`graph.json` has a stable documented schema — that alone makes it agent-consumable (Claude, Cortex, whatever reads files). `stitch export --format jsonl` additionally emits flat one-record-per-line nodes and edges for easy loading anywhere, including a `COPY INTO` recipe for teams that want it queryable in Snowflake. A recipe in the docs, not a product surface — the lesson of v0.2/v0.3 is that the warehouse backend is a consumer of this tool's output, not its home.

## 12. Phasing

| Phase | Scope | Status |
|---|---|---|
| **0** | `build`: dbt models **+ column lineage via sqlglot on compiled SQL** + MBQL cards; `graph.json` deterministic + `--check`; coverage report incl. lineage trace rate; recursive `impact` — previous-build baseline and build summary (#53), `--column` point query (#86), SHA-keyed local history (#87) — + GitHub Action; `stitch search` (CLI); `doctor` basics, plus `doctor --dead` for estate hygiene — unconsumed columns, models feeding nothing, archived-but-bound cards (#88) | **shipped** (only the committed-baseline PR-comment workflow stays optional — §10, #36) |
| **1** | `serve`: **search + detail panels** (the entry point), end-to-end column lineage view, catalog, read-only ERD; `export --format site` | **shipped** |
| **2** | Editable canvas → **staged relationships + `stitch apply`** (§8.2, issues #24/#27), staged descriptions and apply from the app (#70/#71/#72), suggestion layer (#30); `layout.yml` holds dismissed suggestions, but saved views and node positions (#31) are not built, and composite/conceptual shapes (#55) are backlogged | **shipped** except `layout.yml` saved views (#31) |
| **3** | Metabase **native SQL** cards via sqlglot + template-tag substitution (#32, shipped; modern Metabase also emits **MBQL 5 lib/stages** for saved questions — #22, shipped ahead of phase order); rename heuristics (#33), `--verify-lineage` over ACCESS_HISTORY (#34), Metabase version matrix (#35) | ongoing |
| **4** | **`stitch mend`** — impact-driven card remediation (§14, #143): `impact --format json`, a deterministic plan with one action per affected card (repoint / strip / archive / notify), per-action autonomy, and an apply loop whose every write is snapshotted, re-executed and auto-reverted; `doctor --write-access`; the one-job post-deploy Action template | ongoing |

Work is tracked as GitHub issues; the tracker, not this table, is the operational truth.

### 12.1 Priority order (2026-08-10)

Phases describe scope, not sequence. The current order of work cuts across them:

1. ~~**Local impact.** The previous-build baseline plus a blast-radius summary on every build (#53), then the point query `stitch impact --column` — a blast radius askable *before* an edit, not only as a diff after one (#86) — and SHA-keyed local history (#87). This returns §10's killer feature to the local-only world where the graph now lives; the committed baseline stays the CI variant for teams that keep one.~~ Shipped: all three, and the README now opens on the blast radius rather than the catalog (§13, #89). The committed baseline remains the CI variant (#36).
2. ~~**`stitch init`** (#29, §6.0). Every install after the first one starts here, and setup friction is a product feature.~~ Shipped: the wizard derives from `dbt_project.yml` and the manifest instead of asking (§6.0).
3. ~~**Metabase native SQL cards** (#32, §7.4). `native SQL cards 0/41` was the honest headline limit of the tool for every shop that is not MBQL-only.~~ Shipped: native cards parse (§7.4).

**All three delivered as of 2026-08-11.** Sequence from here is the tracker's, not this section's.

Canvas and ERD polish ranked below all three, and a broken core chain still outranks all cosmetic work — that ordering principle stands. Its example has expired: card detail showed no source columns (#25) and now shows them, so canvas and restyle work is no longer sitting behind a broken chain.

### 12.2 Scope guardrails — what stitch is not

Each of these was a reasonable-sounding idea. They are decided against, not deferred.

- **No third canvas.** The ERD is the canvas; the column lineage view is the flow view. The global overview/pipeline map is removed (#83) — its rollup lib survives because the lineage grain toggle uses it. Home's entry points are ERD and lineage.
- **Not a dbt YAML IDE.** `stitch apply` writes **relationships** (§8). Description editing exists only because it rides the same staged store, the same writer and the same diff preview (§8.2); it earns no UI surface of its own, and no further YAML key follows it in on that precedent.
- **The home page stays a search box and a few numbers.** Search is the entry point (§9). A home page that grows tiles is a dashboard, and a dashboard is a thing nobody opens twice.
- **One BI tool until the Metabase story is complete.** Looker, Tableau and the rest are a new `resolve/` module by construction (§4) — that is what the seam is for, not an invitation to use it while column lineage into Metabase is unfinished.
- **No warehouse backend, no hosted anything.** Standing since v0.2/v0.3 (§3, §11): the warehouse is a consumer of `graph.json`, not its home.

## 13. Risks

- **Committed generated file friction.** `.stitch/graph.json` in git means occasional merge conflicts (regenerate-and-recommit resolves them — document it) and reviewers seeing a machine file in diffs. Deterministic ordering keeps diffs semantic; `--check` in CI keeps it honest. If it proves hateful in practice, the fallback is storing the baseline as a CI artifact keyed by commit SHA — costs the git-native diffing, keeps everything else. That is where it landed: v0.5 made the graph local, and #87 (§12.1) is that fallback in local form — snapshots keyed by SHA inside gitignored `.stitch/`.
- **Native SQL coverage — not a configuration problem.** Smitten is *almost* MBQL-only (8 native cards of 953), so the tool gets built against its easiest case while most Metabase shops live in the hard one. Closed as a gap (§7.4 resolves native cards), but the *exposure* remains: the resolver is exercised by a handful of real cards, and a shop that is native-first will find its edges here first. Coverage reporting is what keeps that legible — a `native SQL cards 12/41` line is a bug report with a number attached.
- **Frontend bundling.** Shipping a prebuilt SPA in the wheel means a JS build step in *release* CI and wheel size in the tens of MB. Acceptable; the alternative (npm at install time) is not, for a pip package.
- **Metabase API drift.** Pin 49+ (API keys), keep a tested version matrix, treat every response shape as untrusted, keep raw payloads for repro.
- **Adjacency to `dbt-metabase`.** If it grows column-level exposures, Phase 0's differentiation shrinks. Open an issue with the maintainer early — Phase 0 might be strongest as an upstream contribution plus this repo owning the viewer/editor.
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
                                                 per card: write ► validate ► revert on error
                                                                     │
                                                               Slack summary
```

One post-deploy job. No artifact handoff, no environment gate, no runner held pending.

**Why no approval gate (decided 2026-08-11, #143).** A plan rots while it waits: every hour, more cards drift and silently degrade to `stale`, so a slow approval is a *partial* repair. The gate was never the safety mechanism either — safety is the staleness guard, the revision snapshot, post-write re-execution, auto-revert, notify-only collections and a summary that names every card, all of which are retained. And for **repoint**, the largest action class, human approval already happened: the rename was made deliberately in a reviewed PR and declared explicitly on the command line. A second button approves nothing new. The honest exception is **strip**, which is handled by per-action autonomy rather than by gating the whole plan. The gated flow survives as a documented variant in `action/stitch-mend.yml`.

### 14.1 The plan — one action per card

Input is the impact diff plus an optional **declared** rename map (`--rename fct_orders.amount=fct_orders.amount_usd`, repeatable). No inference: that is #33, and it can feed this map later. Declared-only is what keeps the plan deterministic and the blame legible.

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
  auto: [repoint, strip, archive]               # remove one -> it downgrades to notify
  notify_only_collections: ["*Personal*"]       # never written to; listed for humans
```

All three on by default. `strip` is the one worth a decision: a card with a dead filter removed does not *look* broken — it runs, and shows different numbers under the same title, which re-execution validation cannot catch. Some owners would rather see the error card and decide. `notify` is deliberately not configurable: it is the absence of a write, not an autonomy level.

### 14.3 Apply — every write reversible

Per card, in order: **staleness guard** (re-read the card; `updated_at` moved since plan time → skip as `stale`, because a human edited it and their edit outranks ours — the same principle as `stitch apply`'s dirty-file refusal; `--force` overrides) → **snapshot** the revision id → **`PUT /api/card/:id`** with only what changed → **validate** by re-executing the card (a failure arrives as a status code, a `status` that is not a success word, *or* an `error` beside a happy status — all three are read) → **revert** through the revisions API, falling back to restoring the `before` query the plan captured, and saying loudly when it could neither.

Archive actions set the flag and are not re-executed: the query did not change, so a run would prove nothing, and Metabase's archive is already reversible. A dashcard write is done by reading the dashboard back and replacing only the named dashcards — `PUT /api/dashboard/:id` takes the whole array, and sending the plan's copy would silently revert anything else that moved. A dashcard failure does **not** revert the card: the card is repaired and proved to run, and undoing that because a dashboard write failed helps nobody.

The summary — applied / archived / stale / failed / skipped / notify, every card named, `failed` first because it is an alarm and strips next because they are silent — goes to Slack and to stdout. Any `failed` exits non-zero. **The plan is the dry run**; apply additionally prints a unified diff of every `dataset_query` it writes into the job log, so a CI log answers "what exactly changed in card #412" without opening Metabase.

`stitch doctor --write-access` is the pre-flight: who the API key is, how many cards report `can_write`, whether revision history (the revert target) is readable, and what `mend.auto` will actually do. It writes nothing.

### 14.4 Why this is post-deploy, not PR-time

Repointing needs the **new** field ids, which exist only after the warehouse has rebuilt and Metabase has synced. So mend runs in the post-deploy job. A PR-time `mend --plan --format github-comment` preview (no apply) is a cheap later addition now that the plan half exists.

### 14.5 The seam

Two additions to §4, both of them the existing rules holding rather than bending:

- The HTTP seam now reads **only `io/` clients speak HTTP** — `io/metabase_client.py` and `io/slack_webhook.py`. What the rule protects was never the file count; it is that HTTP lives at the edge behind a named client so everything above it is testable with no network. Two clients, and no more without a spec change.
- `mend/` splits the same way `resolve/` and `io/` do: `plan`, `rewrite`, `render` and `models` are **pure** (graphs and raw payloads in, a plan out) and `apply` is the only module that reaches `io/`. Both are `import-linter` contracts. Rewriting reuses the resolver's own shape vocabulary rather than carrying a second opinion about where a field ref can hide — rewriting is the inverse of the walk, and two walkers would diverge on the first Metabase upgrade.

### 14.6 Out of scope

- Rewriting **native SQL card text**. Resolution is not rewriting; SQL surgery via sqlglot is its own feature. A native card that references a dead column is reported, never edited.
- Rename **inference** (#33 can feed the map later).
- Any hosted approval service, or a Slack app with a callback endpoint (§12.2).
- Changing what a card *means* beyond removing references to columns that no longer exist.

### 14.7 Risks

- **This writes to user-authored BI content without a human gate.** The protections are structural rather than ceremonial: every write is snapshotted against Metabase's revision history, validated by re-execution, auto-reverted on error, confined to declared renames and dead references, and named card-by-card in the summary. The worst case is a revert, not a loss. Trust erodes on the first silent bad strip — which is exactly why `strip` is dialable and why the summary shows strips before anything that merely worked.
- **Revisions API drift.** Same posture as the standing Metabase-API risk (§13): version matrix, untrusted response shapes, raw payloads retained. If an instance will not serve revisions, mend says so in `doctor --write-access` and reverts by restoring the captured query instead.
