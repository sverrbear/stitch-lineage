# stitch

**dbt ↔ Metabase column lineage.**

stitch answers "where does this column go, and what uses it?" for the half of the stack where the answer usually isn't visible. It reads your dbt artifacts and the Metabase API and traces column lineage end to end — source column → staging → mart → Metabase field → card → dashboard. The result is `.stitch/graph.json`, a plain local file next to your dbt project: search it from the terminal, explore it in the browser (`stitch serve`), export it for agents. Local-first: no server, no warehouse backend, no hosted anything — and nothing generated needs to be committed. (Teams that want git history of the graph can commit it; nothing requires that.)

> Full design in [SPEC.md](SPEC.md) (draft v0.4).

## Quickstart

```bash
pip install stitch-lineage
# or straight from GitHub:
pip install git+https://github.com/sverrbear/stitch-lineage.git
```

Drop a `stitch.yml` at your dbt project root:

```yaml
dbt:
  project_dir: .
  target_path: target/
  auto_docs: true                       # run `dbt docs generate` at the start of every build

metabase:
  url: ${STITCH_METABASE_URL}
  api_key: ${STITCH_METABASE_API_KEY}   # env reference only; a literal key is an error
  databases:
    - metabase_name: "Analytics"        # display name in Metabase
      dbt_database: analytics           # database per the dbt manifest
      table_prefix: ${USER_PREFIX}_     # optional: prefix on dbt physical table names
                                        # absent in the BI database, stripped before
                                        # matching (dev artifacts vs prod Metabase)

serve:
  erd_default_scope: "schema:marts"     # optional: ERD scope to open on ("tag:core" works too)

output:
  dir: .stitch/
```

Then, with `auto_docs: true`, one command does everything:

```bash
stitch build                     # dbt docs generate + resolve dbt + Metabase into .stitch/graph.json
```

Or keep the two steps explicit (typical in CI, where `dbt docs generate` runs its own way):

```bash
dbt docs generate                # produce target/manifest.json + catalog.json
stitch build --no-docs           # resolve only; --docs/--no-docs overrides auto_docs either way

stitch build --no-metabase       # dbt side only; reuses the existing Metabase side

stitch search order_total        # find models, columns, fields, cards, dashboards
stitch search order_total --json # JSON lines for piping

stitch serve                     # local lineage + ERD app on http://127.0.0.1:8787

stitch suggest                   # relationships worth declaring, strongest evidence first
stitch suggest --json            # JSON lines for piping

stitch apply                     # write staged relationships into model YAML (diff, then confirm)
stitch apply --dry-run           # show the diff and stop
stitch apply --yes               # skip the confirmation prompt

stitch doctor                    # config, artifacts, graph, Metabase connectivity
stitch doctor --list-databases   # database names visible to the API key
stitch doctor --unbound          # dbt models with no bound Metabase table
stitch doctor --untraced         # columns sqlglot could not trace

stitch export --format jsonl     # flat nodes.jsonl/edges.jsonl for agents/warehouses
stitch export --format site      # static build of the app, graph inlined, host anywhere
```

Commands that don't call the Metabase API (`build --no-metabase`, `search`, `suggest`, `export`, `doctor --unbound/--untraced`) work without the `STITCH_METABASE_*` env vars set. Add `.stitch/` to your `.gitignore` — the graph is a local artifact.

## The app

`stitch serve` opens a local, read-only browser app over the same `graph.json`: search everything (models, columns, Metabase fields, cards, dashboards) with `/` and `⌘K`, per-node detail panels, the end-to-end column lineage view from source column to dashboard, and a scoped ERD of declared relationships. Every node carries the badge of the system it lives in — Snowflake on the warehouse side, Metabase on the BI side — so a glance shows where one ends and the other begins. Cards deep-link back into Metabase.

`stitch export --format site` writes the same app as a static directory with the graph inlined into `index.html` — no server, no API. Drop it on any static host for people who will never run a CLI.

## Declaring relationships: plan, then apply

Relationships you declare in the app never touch your repo directly. They are staged to `.stitch/staged_relationships.yml` (local, like the rest of `.stitch/`), and `stitch apply` materializes them into your model YAML as a separate, reviewable step:

```bash
stitch apply --dry-run           # exactly what would change, and nothing else
stitch apply                     # same diff, then a confirmation prompt
```

```diff
--- a/models/marts/_schema.yml
+++ b/models/marts/_schema.yml
       - name: customer_id
         description: 'Who placed the order'
+        data_tests:
+          - relationships:
+              to: ref('dim_customers')
+              field: customer_id
+              config:
+                severity: warn
```

The write is deliberately conservative:

- **Insert-only.** Comments, quoting, key order and blank lines survive byte-identically — the diff contains the declaration and nothing else. A file stitch cannot reproduce exactly is reported as unappliable instead of being reformatted.
- **Never invents files.** The target comes from the manifest's `patch_path`; a model with no schema YAML is reported, not scaffolded.
- **Respects your edits.** A target file with uncommitted changes is refused unless you pass `--force`.
- **`relationships.write_to`** picks the written form: `relationships_test` (a dbt `relationships` test on the FK column) or `meta` (dbt-metabase interop keys, so FK sync into Metabase keeps working).

Applied entries clear from the staging store; anything that could not be applied stays staged and is reported with the reason.

### Where to start: `stitch suggest`

Starting from zero declared relationships, `stitch suggest` tells you which ones are worth declaring first:

```bash
stitch suggest
```
```
 source         score  from                     to                        why
 implicit_join  204    fct_user_activity.user_id  dim_users.user_id       204 cards join through it
 naming         0.5    dim_subscriptions.user_id  dim_users.user_id       names the 'user' grain
```

Two sources of candidates. **Implicit joins** come from Metabase itself: when a card reaches a column by joining through an FK, that join is recorded in the graph, so the score is the number of cards already relying on a relationship nobody wrote down. **Naming** is the weaker `<entity>_id` → matching-grain-model convention, always ranked below a single witnessing card.

Pairs you have already declared in the repo, already staged, or dismissed in the app never come back.

## Coverage report

Every build prints what it could and couldn't resolve, so a thin graph is a documented limitation instead of a mystery:

```
models bound         142/147   (5 unmatched -> stitch doctor --unbound)
MBQL cards           218/218
native SQL cards     0/41   unsupported in v0
dashboards           19/19
dbt column lineage   1842/1901 columns traced   (37 inferred via star-expansion, 22 unresolved -> stitch doctor --untraced)
```

Native SQL cards are counted but not resolved in Phase 0 (they are Phase 3); MBQL cards resolve exactly, including card-on-card sources. Both query formats are handled: the legacy `dataset_query.query` shape and the MBQL 5 (`lib/type` + `stages`) shape modern Metabase returns.

## Phases

| Phase | Scope | Status |
|---|---|---|
| **0** | `build` (dbt column lineage via sqlglot + MBQL cards), deterministic `graph.json` + `--check`, coverage report, recursive `impact` + GitHub Action template (impact shelved by default), `search` CLI, `doctor` | **shipped** |
| **1** | `serve`: search + detail panels, end-to-end lineage view, catalog, read-only ERD; `export --format site` | **shipped** |
| 2 | Editable ERD canvas: YAML write-back with diff preview, suggestion layer | planned |
| 3 | Native SQL cards via sqlglot, rename heuristics, `--verify-lineage` | planned |

## Shelved: PR impact comments

stitch can diff two graphs and walk the downstream blast radius — "this rename breaks 4 cards on 2 dashboards" — as a PR comment (`stitch impact --format github-comment`) or a Slack deploy alert (`--format slack`; templates in [`action/`](action/)). That workflow needs a baseline `graph.json` committed on the base branch, which conflicts with keeping the graph purely local, so it's shelved as the default story for now: the command is hidden from `--help` but fully functional if you keep your own baselines.

## Built on

stitch deliberately reuses the conventions of the tools next to it rather than reinventing them (SPEC §2): relationship metadata is written in `dbt-metabase`'s and `dbterd`'s meta keys, so those tools keep working unchanged on a stitch-annotated repo.

- [sqlglot](https://github.com/tobymao/sqlglot) — SQL parsing and column-level lineage over dbt compiled SQL
- [dbt-metabase](https://github.com/gouline/dbt-metabase) — FK/semantic-type/description sync into Metabase; stitch's `metabase.fk_target_*` relationship meta keys are interop-compatible with it by design
- [dbterd](https://github.com/datnguye/dbterd) — ERD conventions; stitch matches its `relationship_type` meta key
- [typer](https://github.com/fastapi/typer) + [pydantic](https://github.com/pydantic/pydantic) + [ruamel.yaml](https://sourceforge.net/projects/ruamel-yaml/) + [requests](https://github.com/psf/requests) + [rich](https://github.com/Textualize/rich) — CLI, data contracts/validation, round-trip YAML write-back (Phase 2), Metabase API access, terminal output
- [FastAPI](https://github.com/fastapi/fastapi) + [uvicorn](https://github.com/encode/uvicorn) + [React Flow](https://github.com/xyflow/xyflow) — `stitch serve`; the SPA ships prebuilt in the wheel, so installing never needs a node toolchain

## Development

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

pytest          # unit + end-to-end tests
ruff check .    # lint
lint-imports    # architecture seams (SPEC.md §4)
```

The app's source lives in [`stitch_lineage/app/frontend/`](stitch_lineage/app/frontend/) (its own README covers the stack). Its built `dist/` is committed and bundled into the wheel — rebuild it with `npm run build` there whenever `src/` changes.

MIT licensed.
