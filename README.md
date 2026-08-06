# stitch

**dbt ↔ Metabase column lineage.**

stitch answers "if I rename this column, what breaks?" for the half of the stack where the damage actually shows up. It reads your dbt artifacts and the Metabase API, traces column lineage end to end — source column → staging → mart → Metabase field → card → dashboard — and stores the result as a deterministic, committed `.stitch/graph.json`. In CI it diffs the PR branch's graph against the one committed on the base branch and posts the downstream blast radius as a PR comment. Local-first: no server, no warehouse backend, no hosted anything. The dbt repo is the database.

> Full design in [SPEC.md](SPEC.md) (draft v0.4).

## The PR comment

This is the whole point. On every PR that changes a model, stitch comments:

```
⚠ 1 column removed or renamed

stg_payments.amount_usd → removed
  ├ 2 downstream models: fct_orders, mart_payments
  └ 3 Metabase cards:
      #201 Orders overview  (Orders Board, Sverrir)
      #204 Revenue per customer  (Sverrir)
      #209 Legacy KPIs  (Sverrir)

_Renames appear as remove+add: a renamed column shows up here as removed._
```

A ready-to-copy workflow template lives in [`action/stitch-impact.yml`](action/stitch-impact.yml). The impact job needs no Metabase credentials — it reuses the Metabase side of the committed baseline graph.

## Quickstart

Install from source for now (PyPI release pending):

```bash
git clone https://github.com/<you>/stitch-lineage && cd stitch-lineage
pip install .
```

Drop a `stitch.yml` at your dbt project root:

```yaml
dbt:
  project_dir: .
  target_path: target/

metabase:
  url: ${STITCH_METABASE_URL}
  api_key: ${STITCH_METABASE_API_KEY}   # env reference only; a literal key is an error
  databases:
    - metabase_name: "Analytics"        # display name in Metabase
      dbt_database: analytics           # database per the dbt manifest

output:
  dir: .stitch/
```

Then:

```bash
dbt docs generate                # produce target/manifest.json + catalog.json

stitch build                     # resolve dbt + Metabase into .stitch/graph.json
stitch build --check             # CI: exit 1 if the committed graph.json is stale
stitch build --no-metabase       # dbt side only; reuses the committed Metabase side

stitch impact --base origin/main --format github-comment
stitch impact --base origin/main --fail-on-impact   # red check on removed columns

stitch search order_total        # find models, columns, fields, cards, dashboards
stitch search order_total --json # JSON lines for piping

stitch doctor                    # config, artifacts, graph, Metabase connectivity
stitch doctor --list-databases   # database names visible to the API key
stitch doctor --unbound          # dbt models with no bound Metabase table
stitch doctor --untraced         # columns sqlglot could not trace

stitch export --format jsonl     # flat nodes.jsonl/edges.jsonl for agents/warehouses
```

Commands that don't call the Metabase API (`build --no-metabase`, `impact`, `search`, `export`, `doctor --unbound/--untraced`) work without the `STITCH_METABASE_*` env vars set — CI impact jobs need no secrets beyond warehouse access for `dbt docs generate`.

## Coverage report

Every build prints what it could and couldn't resolve, so a thin graph is a documented limitation instead of a mystery:

```
models bound         142/147   (5 unmatched -> stitch doctor --unbound)
MBQL cards           218/218
native SQL cards     0/41   unsupported in v0
dashboards           19/19
dbt column lineage   1842/1901 columns traced   (37 inferred via star-expansion, 22 unresolved -> stitch doctor --untraced)
```

Native SQL cards are counted but not resolved in Phase 0 (they are Phase 3); MBQL cards resolve exactly, including card-on-card sources.

## Phases

| Phase | Scope | Status |
|---|---|---|
| **0** | `build` (dbt column lineage via sqlglot + MBQL cards), deterministic `graph.json` + `--check`, coverage report, recursive `impact` + GitHub Action template, `search` CLI, `doctor` | **shipped** |
| 1 | `serve`: search + detail panels, lineage view, catalog, read-only ERD; `export --site` | planned |
| 2 | Editable ERD canvas: YAML write-back with diff preview, suggestion layer | planned |
| 3 | Native SQL cards via sqlglot, rename heuristics, `--verify-lineage` | planned |

## Development

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

pytest          # unit + end-to-end tests
ruff check .    # lint
lint-imports    # architecture seams (SPEC.md §4)
```

MIT licensed.
