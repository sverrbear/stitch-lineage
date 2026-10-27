<!-- Image and link targets are absolute on purpose: this README is the PyPI project page too,
     and PyPI resolves nothing relative to the repo. -->
<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/sverrbear/stitch-lineage/main/assets/logo-dark.svg">
    <img src="https://raw.githubusercontent.com/sverrbear/stitch-lineage/main/assets/logo-light.svg" width="300" alt="stitch">
  </picture>
</p>

[![image](https://img.shields.io/github/license/sverrbear/stitch-lineage.svg)](https://github.com/sverrbear/stitch-lineage/blob/main/LICENSE)
[![image](https://img.shields.io/pypi/v/stitch-lineage.svg)](https://pypi.org/project/stitch-lineage/)
[![image](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue.svg)](https://github.com/sverrbear/stitch-lineage/blob/main/pyproject.toml)
[![image](https://img.shields.io/github/actions/workflow/status/sverrbear/stitch-lineage/ci.yml?branch=main&label=test)](https://github.com/sverrbear/stitch-lineage/actions/workflows/ci.yml)

dbt ↔ Metabase column lineage — so you know what a column change breaks before you make it.

Use `stitch` to trace a column from its source table through your dbt models into the Metabase
fields, cards and dashboards that read it. It reads your dbt artifacts and the Metabase API and
writes one plain local file, `.stitch/graph.json`, which the CLI queries and the browser app draws.

stitch is **local-first**: no server, no warehouse backend, no hosted anything, and nothing it
generates needs to be committed. It reads Metabase, and the only thing it ever writes into your repo
is a relationship or description you explicitly asked it to apply. One command writes *out* —
[`stitch mend`](#repairing-what-broke-stitch-mend) plans supported repairs and, when you apply them,
re-executes rewritten queries and attempts rollback on execution failure. Declared renames are
the default; removing clauses or archiving cards requires explicit opt-in. Nothing else in the
tool touches Metabase with anything but a GET.

Requires **Python 3.11+**, a dbt project whose artifacts you can generate, and **Metabase 49 or
newer** (the first release with API keys).

This project is alpha, in active development — the badge above is the released version. Full design in
[SPEC.md](https://github.com/sverrbear/stitch-lineage/blob/main/SPEC.md) (v0.5); work is tracked as
[GitHub issues](https://github.com/sverrbear/stitch-lineage/issues).

<p align="center">
  <img src="https://raw.githubusercontent.com/sverrbear/stitch-lineage/main/assets/lineage.png" alt="The stitch lineage view: one column traced from its source table through dbt models into Metabase fields, cards and dashboards" width="100%">
</p>
<p align="center">
  <sub><code>stitch serve</code> — one column's blast radius, source to dashboard. Synthetic demo project.</sub>
</p>

## Table of Contents

1. [Getting Started](#getting-started)
2. [What breaks if I change this column?](#what-breaks-if-i-change-this-column)
3. [Repairing what broke: `stitch mend`](#repairing-what-broke-stitch-mend)
4. [The app](#the-app)
5. [Configuration](#configuration)
6. [Coverage](#coverage)
7. [Roadmap](#roadmap)
8. [Built on](#built-on)
9. [FAQ](#faq)
10. [Contributing](#contributing)
11. [License](#license)

## Getting Started

### Installation

```shell
pip install stitch-lineage
```

Releases are cut from tags, so PyPI is the stable channel. To ride `main` instead — alpha moves
fast, and this is what you want if you are following the issues — install from git:

```shell
pip install git+https://github.com/sverrbear/stitch-lineage.git
```

Either way the browser app ships prebuilt, so installing never needs a node toolchain.

### Usage

From inside your dbt project, run:

```shell
stitch init
```

Enter your **Metabase URL and API key**. Stitch finds your dbt project, reuses existing artifacts
(or offers to generate them using your existing dbt profile), matches the databases and schemas,
builds column lineage, and opens the app at a column with downstream reports. If there is no traced
report connection yet, it opens search with coverage diagnostics. No separate `build` or `serve`
command is needed on the first run. Keep the terminal running while using the app; Ctrl+C stops it.

Clear database matches are automatic; an ambiguous match gets a picker. Existing environment
credentials are reused. A key you enter is remembered in your **OS credential store**, scoped to
this project and Metabase instance. It never goes into `stitch.yml`, `.env`, or any project file.
Environment variables take precedence for CI. On systems without an available OS credential
store, setup still completes for the current session; reconnect with `stitch init` next time or
supply the key through the environment.

If setup is interrupted, rerun **the same command**. Stitch reuses your existing configuration,
remembered URL and credentials. A failed build does not open the browser. Use `stitch init --force`
only when you want to replace the configuration.

| Option | Behavior |
| --- | --- |
| `stitch init --no-serve` | Connect and build, then exit. |
| `stitch init --no-open` | Connect, build and serve without opening a browser. |
| `stitch init --configure-only` | Configure the connection without building or serving. |
| `stitch init --port 8790` | Use a different local app port. |

The first graph is the baseline for your next build. It is local and gitignored; SHA-keyed
history still requires a clean Git working tree. Setup reads Metabase and does not apply repairs.
GitHub Actions are an optional later step; init does not install a workflow.

For subsequent work, the individual commands remain available:

```shell
stitch build                     # refresh after your dbt artifacts change
stitch serve                     # reopen the current graph
```

`stitch build` needs `target/manifest.json` and `target/catalog.json`. Set `dbt.auto_docs: true`
to run `dbt docs generate` before each build, or run dbt yourself and reuse its artifacts:

```shell
dbt docs generate
stitch build --no-docs
```

Every other command reads that one graph file:

| Command | Description |
| ------- | ----------- |
| `stitch build` | Resolve dbt + Metabase into `graph.json`. `--no-metabase` does the dbt side only; `--check` exits 1 on drift against a committed graph. |
| `stitch impact` | What the last build changed, and what it hits. `--column <model.column>` for a point query, `--base <ref>` to diff a git ref. |
| `stitch mend` | Repair the Metabase cards a column change broke. `--plan` classifies and writes `.stitch/mend_plan.json`; `--apply` executes it. Metabase repairs are also available through Mend in the local app. |
| `stitch serve` | The local lineage and impact app on `127.0.0.1:8787` (`--port`, `--host`, `--no-open`). |
| `stitch search <term>` | Find models, columns, Metabase fields, cards and dashboards. |
| `stitch apply` | Write staged descriptions and existing staged relationships into model YAML (`--dry-run` shows the diff). |
| `stitch doctor` | Config, artifacts, graph and Metabase connectivity. `--unbound`, `--untraced`, `--unresolved-cards`, `--dead`, `--list-databases`, `--write-access`. |
| `stitch history` | The graph baselines past builds stored locally, keyed by commit sha. |
| `stitch export` | `--format jsonl` for agents and warehouses, `--format site` for a static build of the app. |
| `stitch --version` | Print the installed version. |

Metabase operations (`build`, connectivity checks, and CLI/browser Mend) use environment or saved
credentials. Commands that only read the local graph work offline. `--json` is available on
`impact --column`, `search`, `history` and `doctor --dead`, and
`impact --format json` emits the machine-readable diff. Add `.stitch/` to `.gitignore`; the graph is a local artifact.

## What breaks if I change this column?

That is the question stitch exists to answer, and it takes one command over the local graph — no
baseline, no git ref, no Metabase credentials, no CI job:

```
$ stitch impact --column fct_orders.discount_amount
fct_orders.discount_amount
  ├ 2 downstream models: fct_orders, mart_revenue_daily
  ├ 3 downstream columns:
      fct_orders.net_revenue
      mart_revenue_daily.discount_amount
      mart_revenue_daily.net_revenue
  ├ 4 Metabase fields: Discount Amount, Net Revenue, Total Discount, Total Net Revenue
  ├ 4 Metabase cards:
      #412 Discount impact by country  (Revenue review, dana)
      #418 Weekly discount trend  (Revenue review, dana)
      #503 Revenue by country  (Board KPIs, priya)
      #547 Net revenue after discounts  (Board KPIs, priya)
  └ 2 dashboards: Board KPIs, Revenue review
```

It takes `model.column`, a bare column name when that is unique, or a full node id; anything
ambiguous gets `search`-style suggestions rather than a guess.

The mirror question — *what did my last build just change?* — needs no argument. Every build copies
the graph it is about to overwrite to `.stitch/graph.prev.json` and closes with the difference,
staying silent when nothing changed:

```
since last build: 2 columns removed, 1 type-changed -> 4 cards on 2 dashboards affected (run 'stitch impact' for the tree)
```

`stitch impact` prints the tree behind that line. A rename shows up as a removal plus an addition:
ids are name-based, and the downstream card breaks either way until it is repointed.

**Baselines without committing anything.** Every build on a clean working tree also stores a
gzipped snapshot in `.stitch/history/<commit-sha>.json.gz`, so `--base` has somewhere local to look:

```shell
git switch main && stitch build     # history: stored baseline for a1b2c3d (1/20 kept)
git switch -c feat/rename-columns   # ... edit models, dbt docs generate ...
stitch build --no-docs
stitch impact --base main           # diffed against main's stored snapshot
```

`--base <ref>` resolves the ref to its merge-base with `HEAD`, then takes that commit's snapshot or
the nearest stored ancestor, and always prints the baseline it picked on stderr so it can never be a
guess. Builds with uncommitted changes store nothing and say so — that graph describes your working
tree, not the commit. `stitch history` lists what is stored.

In CI, `stitch impact --format github-comment` posts the same blast radius as a PR comment,
`--format slack` as a deploy alert (workflow templates in [`action/`](https://github.com/sverrbear/stitch-lineage/tree/main/action)), and
`--fail-on-impact` exits 1 when a column was removed or type-changed. That path does need a baseline
`graph.json` committed on the base branch, which is why it is not the default story.
`--format json` emits the same diff as data, which is what `stitch mend` runs on.

## Repairing what broke: `stitch mend`

`stitch mend` plans supported repairs and applies them when you explicitly run `--apply`.
By default it only repoints **declared renames**. Clause removal (`strip`) and archiving
(`archive`) are listed for a human unless you enable those actions in `stitch.yml`.

```
stitch mend --plan --rename fct_orders.amount=fct_orders.amount_usd
```

It reads the impact diff and gives every affected card exactly one action:

| Action | When | Write |
|---|---|---|
| **repoint** | you declared the rename, and the new column resolves to a live Metabase field | rewrite the field reference across every query stage and the dashboard filter wiring |
| **strip** | the dead column only appears in clauses the card is not *about* — a filter, one breakout of several, an order-by | delete that clause |
| **archive** | the dead column *is* the card — its only aggregation, its sole dimension | `archived: true`, never a delete |
| **notify** | personal collections, native SQL, actions you dialed off — anything mend will not guess at | nothing; listed for a human |

For a project that has explicitly opted into `auto: [repoint, strip, archive]`, a plan can look
like this. With the default `[repoint]`, the strip and archive entries become `notify` instead.

```
⚠ stitch mend: 6 cards affected -- 2 strip, 1 archive, 1 repoint, 2 notify

STRIP (2) -- a clause is deleted -- the card runs, under the same title, showing different
numbers. Re-execution cannot catch that; read these.
  #402 Orders, promo cohort  (Order operations, Order ops, dev)
      removed filter -> fct_orders.promo_code
      dashboard 'Order operations': 1 filter mapping dropped

ARCHIVE (1) -- the card's substance is gone -- archived, never deleted
  #403 Promo uptake  (Order ops, analyst)
      fct_orders.promo_code is essential to this card (aggregation)

REPOINT (1) -- a declared rename, followed to the new field
  #401 Revenue by month  (Order operations, Order ops, dev)
      repointed aggregation: fct_orders.amount -> fct_orders.amount_usd
```

The plan is a file (`.stitch/mend_plan.json`), it renders as `text`, `slack` or `github-comment`, and
it is deterministic — the same graphs and the same renames produce the same bytes. **The plan is the
dry run.** Nothing has been written yet.

```
stitch mend --apply           # THIS writes to Metabase
```

Apply first checks the **current** action policy and collection restrictions, including when
you load an older plan. It re-reads each eligible card and skips it if someone edited it since
planning. For a query rewrite it captures an available revision, writes, and **re-executes the
card**. If execution fails, it attempts rollback through Metabase's revision history, then by
restoring the captured query. Rollback can fail; the outcome reports that for manual recovery.
Every diff is printed, and any card that ends `failed` exits non-zero.

Execution validation does **not** prove that the report's meaning or numbers are correct.
Archive actions set a reversible flag and are not query-validated. Dashboard filter updates
can partially fail after a card query succeeds; the repaired card and any completed dashboard
updates remain in place, and the failure is reported.

Nothing is inferred: a repoint happens only where you declared `old=new`. A rename mend cannot follow
— because Metabase has not synced the new column yet, say — becomes a `notify`, never a strip, since
"I cannot find the new column" and "the column is gone" call for opposite repairs.

Autonomy is per action, in `stitch.yml`:

```yaml
mend:
  auto: [repoint]  # default; [] makes all actions notify-only
  notify_only_collections: ["*Personal*"]
```

Add `strip` only if you accept removing query clauses: a card with a filter removed can run
and show different numbers under the same title. Add `archive` only if you want affected
cards archived automatically when applying a plan. Both require an explicit choice.

**Upgrading:** omitted `mend.auto` now means `[repoint]`. Explicit existing lists are preserved,
including `[repoint, strip, archive]` and `[]`; change an old broad list to `[repoint]` to adopt
the safer policy. Saved plans obey the current restrictions, even with `--force`. Relaxing
policy does not turn an old `notify` entry into a repair: generate a new plan.

`stitch doctor --write-access` checks that
the API key can write, and writes nothing itself. The post-deploy workflow — sync, build, impact,
plan, Slack notice, apply, summary, in one job — is
[`action/stitch-mend.yml`](https://github.com/sverrbear/stitch-lineage/blob/main/action/stitch-mend.yml), which documents a human-gated variant alongside.

## The app

**Mend** in the top navigation brings repair into the local app. It compares the current
`.stitch/graph.json` with `graph.prev.json`; your first build establishes the baseline for
the next schema change. Refresh dbt artifacts, sync Metabase, and run `stitch build` after
the change. Each build replaces the previous snapshot, so do not rebuild simply to dismiss
a missing-baseline message. The app does not run the warehouse or monitor changes in the background.

Declare a removed column's replacement from the current graph, then **Review repairs**.
Planning reads Metabase without changing it and shows each affected report, query diff,
and dashboard filter edit. Only declared reference updates are selected by default, even
if `mend.auto` permits more. Clause removal and archiving require separate choices for
each review and remain unavailable when the project policy disables them. Native SQL,
protected collections, unreadable reports, and replacements not yet synced stay visible
for manual review.

After reviewing, explicitly approve the listed repairs. Approval applies only the server-held
preview, expires after 15 minutes, and is consumed once. A new review, server restart, or changed
graph/configuration requires another review. Current report queries, write permissions, protected
collections, and dashboard wiring are checked before their writes; there is no browser force
override. Results distinguish query execution, archiving, skipped/stale reports, failed writes,
successful rollback attempts and rollback failures. A query running does not prove unchanged
business meaning, and a dashboard failure is reported even if the card query was updated.
Failed/partial results retain the exact original query and dashboard wiring for manual recovery.
Save that recovery record before another completed apply or server restart replaces it.
Static exports remain read-only and contain no Mend credentials or live repair preview.

<p align="center">
  <img src="https://raw.githubusercontent.com/sverrbear/stitch-lineage/main/assets/mend.png" alt="Mend review of a synthetic revenue report: the calculation and dashboard filter change from amount to amount_usd, followed by explicit approval" width="100%">
</p>
<p align="center">
  <sub>Review the calculation and dashboard filter before approving a repair. Synthetic demo project.</sub>
</p>

For a reproducible synthetic review, install the development dependencies and run
`python scripts/demo_mend.py`. Open its localhost URL; `/api/demo` exposes write counters.
`--fail-query` and `--fail-query --fail-rollback` demonstrate both failure paths without a
real Metabase connection. [Screenshot reproduction](https://github.com/sverrbear/stitch-lineage/blob/main/assets/demo/README.md)
documents both public demos.

`stitch serve` opens a local browser app over the same file. Search with `/` and `⌘K`, then inspect
one object through **Summary** and **Lineage** under the same name. Switching views keeps the
selected column, model, card or dashboard in context; table-grain lineage still answers for the
selected column. Summary drafts and scroll position stay with that object while you inspect its
graph. Following another node opens that object's workspace.

Lineage opens at readable scale around the selection. **Focus selection** returns to that view;
**Show overview** fits the whole drawing. Traced columns are grouped under their models, with
counted upstream/downstream controls to reveal more. Metabase field bindings remain inspectable
inside each model and can also be shown as nodes. Impact totals still come from the full graph,
and grouped edges retain their weakest evidence.

Every node carries the badge of who manages it — dbt on what the pipeline builds, Snowflake on
the source tables your loaders land, Metabase on the BI side — so a glance separates the columns
you can rename from the ones that arrive as somebody else's decision. Cards deep-link back into
Metabase.

Search for a column, model or source to see its **downstream impact first**: exact counts of
Metabase cards and dashboards, with cards grouped under their dashboards and direct links to open
them. Archived consumers and non-exact lineage stay labelled. The counts describe dependencies in
the current graph, not a guarantee that a change will break a card or that an empty result is safe;
recorded trace gaps and partial lists are called out beside the answer. Definitions, upstream
dependencies, descriptions and relationship details remain below it.

Model and column descriptions can still be edited. Edits stage locally, then **Staged changes**
opens a standalone review page with diff preview and apply. `stitch apply` remains available in
the CLI. Previously staged relationships can also be reviewed, edited, discarded, and applied;
existing declarations remain readable as evidence in object details.

The ERD canvas and relationship suggestions have been retired. Old object Relationships links
open that object's Summary, and old global ERD links return to search. `stitch suggest` and the
suggestion API are removed. Existing `serve.erd_default_scope` settings are ignored, and existing
`.stitch/layout.yml` files are left untouched. Column lineage, `stitch mend`, and unused-downstream
analysis (`stitch doctor --dead`) remain supported.

`stitch export --format site` writes the same app as a static directory with the graph inlined into
`index.html` — no server, no API, and so read-only.

## Configuration

`stitch.yml` lives at your dbt project root. `stitch init` writes it; only the `metabase` section is
required. `${ENV_VAR}` references are interpolated at load time anywhere in the file.

```yaml
dbt:
  project_dir: .
  auto_docs: true                       # run `dbt docs generate` at the start of every build

metabase:
  url: ${STITCH_METABASE_URL}
  api_key: ${STITCH_METABASE_API_KEY}   # env reference only; a literal key is an error
  databases:
    - metabase_name: "Analytics"        # display name in Metabase
      dbt_database: analytics           # database per the dbt manifest
      table_prefix: ${USER_PREFIX}_     # optional, stripped before matching
  include_schemas: [marts, reporting]
  exclude_models: ["stg_*"]

output:
  history_retention: 20
```

### `dbt`

| Key | Required | Default | Description |
| --- | -------- | ------- | ----------- |
| `project_dir` | no | `.` | dbt project root, relative to `stitch.yml`. |
| `target_path` | no | `target/` | Where `manifest.json` and `catalog.json` live. |
| `auto_docs` | no | `false` | Run `dbt docs generate` at the start of every build. `--docs`/`--no-docs` overrides it per invocation. |
| `docs_args` | no | `[]` | Extra args for that command, e.g. `["--target", "prod"]`. |

### `metabase`

Required — stitch has no default Metabase to talk to.

| Key | Required | Default | Description |
| --- | -------- | ------- | ----------- |
| `url` | **yes** | — | Base URL of your Metabase instance. |
| `api_key` | **yes** | — | Must be a whole-value `${ENV_VAR}` reference; a literal key in the file is a startup error. |
| `databases` | **yes** | — | List of `metabase_name` ↔ `dbt_database` mappings, each with an optional `table_prefix` that is present on dbt's physical table names but absent in the BI database (dev artifacts against a prod-pointed Metabase) and stripped before matching. |
| `include_schemas` | no | `[]` (all) | Restrict binding to these schemas — normally where your marts live. |
| `exclude_collections` | no | `[]` | Metabase collections to skip entirely. |
| `exclude_packages` | no | `[]` | dbt packages not expected in Metabase (`elementary`, `dbt_artifacts`, …). They keep their lineage but leave the bind denominator, so "unbound" keeps meaning "expected and not found". |
| `exclude_models` | no | `[]` | The same, per model: `fnmatch` globs on the dbt model name, e.g. `["stg_*"]`. |
| `min_version` | no | `"0.49"` | Minimum Metabase version stitch will run against. |

### `relationships`

Controls existing relationship evidence and how `stitch apply` writes previously staged relationships.

| Key | Required | Default | Description |
| --- | -------- | ------- | ----------- |
| `write_to` | no | `relationships_test` | The written form: `meta` (dbt-metabase interop keys under the column's `config: meta:`) or `relationships_test` (a dbt `relationships` test on the FK column). `contract_constraint` is accepted by the config but not implemented — `apply` raises rather than write a shape it cannot round-trip. Contract constraints already in your repo are still *read* as relationship evidence. |
| `validated_test_severity` | no | `warn` | `severity:` on a written `relationships` test — warn, so a declaration never fails a pipeline. |
| `fk_meta_keys` | no | `["metabase.fk_target_table", "metabase.fk_target_field"]` | Exactly two keys: the target-table and target-field meta keys read and written for FK declarations. |
| `cardinality_meta_key` | no | `relationship_type` | Meta key carrying the cardinality — `dbterd`'s, by design. |

### `output`

| Key | Required | Default | Description |
| --- | -------- | ------- | ----------- |
| `dir` | no | `.stitch/` | Where the graph, staging stores, cache and history go. Local; gitignore it. |
| `history_retention` | no | `20` | SHA-keyed graph snapshots kept for `impact --base`; oldest pruned first. `0` turns history off and clears the directory. |
| `retain_cache_runs` | no | `3` | Metabase payload cache generations kept. |

### `serve`

Presentation defaults, shared by `stitch serve` and `export --format site`.

| Key | Required | Default | Description |
| --- | -------- | ------- | ----------- |
| `strip_model_prefixes` | no | `[]` | Routing prefixes stripped from a model's *display* name, e.g. `["viz_"]`. Purely cosmetic — ids, search keys and everything written back keep the real dbt name. |

### `mend`

What [`stitch mend`](#repairing-what-broke-stitch-mend) is allowed to do without asking.

| Key | Required | Default | Description |
| --- | -------- | ------- | ----------- |
| `slack_webhook` | no | none | Where the plan notice and the apply summary are posted. Must be an env reference (`${STITCH_SLACK_WEBHOOK_URL}`) — a literal URL in `stitch.yml` is an error, like a literal API key. |
| `auto` | no | `[repoint]` | Actions allowed by `--apply`. Add `strip` or `archive` to opt in; `[]` disables all writes. Current policy is checked again when applying saved plans. Explicit existing lists are preserved. `notify` is not a valid entry: it means no write. |
| `notify_only_collections` | no | `["*Personal*"]` | Globs on collection name or full path (personal collections match `*Personal*` whatever they are named). Cards here are never written to. |

## Coverage

Every build prints what it could and could not resolve, so a thin graph is a documented limitation
instead of a mystery:

```
models bound         142/147   (5 unmatched -> stitch doctor --unbound)
MBQL cards           218/218
native SQL cards     38/41   parsed
dashboards           19/19
dbt column lineage   1842/1901 columns traced   (37 inferred via star-expansion, 22 unresolved -> stitch doctor --untraced)
```

MBQL cards resolve exactly, including card-on-card sources. Native SQL cards resolve by parsing:
stitch substitutes the card's template tags (`{{variable}}`, `[[optional clauses]]`,
`{{snippet: name}}`, `{{#123-card}}`), parses the result with sqlglot, and maps every column that
lands on a table Metabase knows to that table's field — so a hand-written card's columns join the
same lineage chain an MBQL card's do, marked `parsed` rather than `exact`. Anything that will not
parse degrades to the tables it reads; no card is ever dropped, and no column is ever invented.
Both query shapes are handled, the legacy `dataset_query.query` and the MBQL 5 `stages` form modern
Metabase returns.

Build includes archived cards, dashboards and collections, with the same collection exclusions as
active content. Older Metabase releases refuse archived dashboard details; stitch retains those
dashboards and their archived flag but reports their membership as unresolved. Their card-to-dashboard
links are unavailable until an instance can serve those details. Archived cards and dashboard filter
mappings are excluded from `mend` repair plans.

Run the same edges backwards and you get estate hygiene: `stitch doctor --dead` reports unconsumed
columns, models feeding nothing, and archived cards still bound to live columns. It prints its own
caveat every time — stitch only sees Metabase, so a column no card reaches may still be read by
reverse ETL, a notebook or another BI tool. Candidates to review, never a delete queue.

## Roadmap

| Phase | Scope | Status |
|---|---|---|
| **0** | `build` (dbt column lineage via sqlglot + MBQL cards), deterministic `graph.json` + `--check`, coverage report, recursive `impact` incl. `--column` and SHA-keyed history, `search`, `doctor` incl. `--dead` | **shipped** |
| **1** | `serve`: search, impact-first summaries and lineage; `export --format site` | **shipped** |
| **2** | Description staging and apply; compatibility with existing staged relationships | **shipped** — ERD canvas, suggestions, and their planned extensions retired (#242) |
| 3 | Native SQL cards via sqlglot and MBQL 5 stages (**shipped**, ahead of phase order); rename heuristics, `--verify-lineage`, Metabase version matrix | ongoing |
| 4 | `stitch mend`: impact-driven card remediation — plan, Slack notice, guarded apply with rollback attempts; `doctor --write-access` | ongoing |

The issue tracker, not this table, is the operational truth.

## Built on

stitch reuses the conventions of the tools next to it rather than reinventing them (SPEC §2):
relationship metadata is written in `dbt-metabase`'s and `dbterd`'s meta keys, so those tools keep
working unchanged on a stitch-annotated repo.

- [sqlglot](https://github.com/tobymao/sqlglot) — SQL parsing and column-level lineage over dbt compiled SQL
- [dbt-metabase](https://github.com/gouline/dbt-metabase) — FK/semantic-type/description sync into Metabase; stitch's `metabase.fk_target_*` keys are interop-compatible by design
- [dbterd](https://github.com/datnguye/dbterd) — ERD conventions; stitch matches its `relationship_type` meta key
- [typer](https://github.com/fastapi/typer), [pydantic](https://github.com/pydantic/pydantic), [ruamel.yaml](https://sourceforge.net/projects/ruamel-yaml/), [requests](https://github.com/psf/requests), [rich](https://github.com/Textualize/rich) — CLI, data contracts, round-trip YAML write-back, Metabase API, terminal output
- [FastAPI](https://github.com/fastapi/fastapi), [uvicorn](https://github.com/encode/uvicorn), [React Flow](https://github.com/xyflow/xyflow) — `stitch serve`

## FAQ

### Does it need a server?

No. stitch is a CLI that writes one file and a local app that reads it. There is no service to
deploy, no database to provision and no account to create. `stitch serve` binds `127.0.0.1` by
default, and `stitch export --format site` produces a static directory you can host anywhere for
people who will never run a CLI.

### Will this slow down my dbt build?

By default, applying a relationship writes a dbt `relationships` test at `severity: warn`
(`relationships.validated_test_severity`). The test runs with your dbt tests and adds a query;
violations produce warnings. You can raise the severity yourself if you want failures to block
pipeline runs. Set `relationships.write_to: meta` to write metadata without adding a test.

### What about native SQL cards?

They are resolved, by parsing the SQL with sqlglot after substituting the card's template tags, and
they are counted separately in the coverage report as `parsed` rather than `exact`. This was phase 3
work that shipped early. Cards whose SQL will not parse degrade to the tables they read and are
listed by `stitch doctor --unresolved-cards` with the reason — never dropped, never guessed.

### Which Metabase versions are supported?

Metabase 49 and newer, which is where API keys arrive; stitch never wants a password. The floor is
`metabase.min_version` (default `"0.49"`) if you need to move it. Both the legacy and MBQL 5 query
shapes are handled, so modern instances resolve the same as older ones.

### Does `stitch build` run dbt?

Only if you ask it to. With `dbt.auto_docs: true` (or `--docs`) it runs `dbt docs generate` first,
which connects to your warehouse — **if your dbt profile uses MFA, expect a push notification**.
With `--no-docs` it reuses whatever is already in `target/` and touches nothing.

### Do I have to commit anything?

No. `.stitch/` is local and gitignored, including the per-commit baselines `impact --base` reads.
Teams that want git history of the graph can commit it, and the CI comment workflow does need that,
but nothing in the local flow requires it.

### Should I install from PyPI or from git?

PyPI unless you have a reason not to: `pip install stitch-lineage` gets you the last tagged release,
which is the version the docs describe. Install from git when you want a fix that has landed on
`main` but has not been tagged yet.

There was no PyPI package until 2026-08-12 — the SPEC called git-install deliberate, on the grounds
that pinning to a ref you chose keeps you in control of when the tool changes. That reasoning
applies to a ref, not to a registry: a tagged PyPI release is just as pinnable and far easier to
install. Releases publish from a `v*` tag through PyPI Trusted Publishing (OIDC, no API tokens), and
the workflow refuses to publish a wheel that does not contain the built app.

## Contributing

Contributions are welcome. Issues and PRs are the unit of work; the same checks CI runs are the ones
to run locally:

```shell
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

pytest -q             # unit + end-to-end tests
ruff check .          # lint
ruff format --check . # formatting
lint-imports          # architecture seams (SPEC.md §4)
```

The app's source lives in [`stitch_lineage/app/frontend/`](https://github.com/sverrbear/stitch-lineage/tree/main/stitch_lineage/app/frontend) and its own
README covers that stack. The built `dist/` is committed and bundled into the wheel — rebuild it
with `npm run build` there whenever `src/` changes.

## License

`stitch` is distributed under the terms of the [MIT license](https://github.com/sverrbear/stitch-lineage/blob/main/LICENSE).
