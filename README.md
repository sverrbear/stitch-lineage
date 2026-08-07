# stitch

**dbt ↔ Metabase column lineage.**

stitch answers "where does this column go, and what uses it?" for the half of the stack where the answer usually isn't visible. It reads your dbt artifacts and the Metabase API and traces column lineage end to end — source column → staging → mart → Metabase field → card → dashboard. The result is `.stitch/graph.json`, a plain local file next to your dbt project: search it from the terminal, explore it in the browser (`stitch serve`, shipping next), export it for agents. Local-first: no server, no warehouse backend, no hosted anything — and nothing generated needs to be committed. (Teams that want git history of the graph can commit it; nothing requires that.)

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

stitch serve                     # local lineage + ERD app (Phase 1, shipping next)

stitch doctor                    # config, artifacts, graph, Metabase connectivity
stitch doctor --list-databases   # database names visible to the API key
stitch doctor --unbound          # dbt models with no bound Metabase table
stitch doctor --untraced         # columns sqlglot could not trace

stitch export --format jsonl     # flat nodes.jsonl/edges.jsonl for agents/warehouses
```

Commands that don't call the Metabase API (`build --no-metabase`, `search`, `export`, `doctor --unbound/--untraced`) work without the `STITCH_METABASE_*` env vars set. Add `.stitch/` to your `.gitignore` — the graph is a local artifact.

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
| **0** | `build` (dbt column lineage via sqlglot + MBQL cards), deterministic `graph.json` + `--check`, coverage report, recursive `impact` + GitHub Action template (impact shelved by default), `search` CLI, `doctor` | **shipped** |
| 1 | `serve`: search + detail panels, lineage view, catalog, read-only ERD; `export --site` | planned |
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

## Development

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

pytest          # unit + end-to-end tests
ruff check .    # lint
lint-imports    # architecture seams (SPEC.md §4)
```

MIT licensed.
