# stitch frontend

React SPA for `stitch serve` and `stitch export --site` (SPEC.md §9). Search-first
catalog, per-node detail panels, end-to-end column lineage view, and a read-only ERD
over `.stitch/graph.json`.

## Stack

- Vite + React + TypeScript
- `@xyflow/react` (React Flow) for the lineage + ERD canvases
- `fuse.js` for the fuzzy search tier
- Hand-rolled CSS (CSS variables, light/dark), hand-rolled hash router — no other deps

## Data contract

The app acquires data in this order (see `src/lib/load.ts`):

1. **Static export**: `window.__STITCH_GRAPH__` / `window.__STITCH_META__` globals,
   inlined into `index.html` by replacing the `/* __STITCH_INLINE_DATA__ */` marker
   script body.
2. **Served mode**: `GET api/graph` (the graph.json body) and `GET api/meta`
   (`{"metabase_url": str|null, "generated_at": str|null, "schema_version": int,
   "erd_default_scope": str|null}`). Paths are **relative**, so the app works under
   any URL prefix.
3. **Dev fallback** (`npm run dev` only): `dev-public/dev-graph.json`.

Routing is hash-based (`#/node/<id>`, `#/lineage/<id>`, `#/erd/...`), so the server
needs **no SPA fallback route** — serving `dist/` statically is enough.

## Development

```bash
npm install
node scripts/make-dev-graph.mjs /path/to/dbt-repo/.stitch/graph.json  # optional
npm run dev            # http://localhost:5173, proxies /api -> http://localhost:8000
```

With a local `stitch serve` API on port 8000 the dev server proxies to it; without
one it falls back to the dev graph produced by `make-dev-graph.mjs` (gitignored —
it may contain private model names). If the source graph has no Metabase nodes
(a `--no-metabase` build) the script appends a few deterministic synthetic
fields/cards/dashboards so the BI half of the UI is exercisable.

## Build & test

```bash
npm run build          # tsc --noEmit + vite build -> dist/ (relative asset paths)
npm run typecheck      # tsc --noEmit
npm test               # vitest over the pure TS modules (src/lib/*.test.ts)
```

`dist/` is committed: the package is pip-installed from git and the wheel bundles
the built assets, so installing never requires a node toolchain. Rebuild `dist/`
whenever `src/` changes.

The test suite includes a scale smoke test (`src/lib/scale.test.ts`) that runs
against `dev-public/dev-graph.json` when present and skips silently otherwise.

## Layout

```
src/
  lib/        pure TS, no DOM (unit-tested): graph index + traversal, lineage
              extraction + layered layout, ERD scoping, search ranking, detail
              computations, naming/presentation rules, data loading
  components/ badges (inline Snowflake/Metabase SVG marks), search panel,
              command palette, header, shared bits
  pages/      Home (search-first), Node (detail panels), Lineage, ERD
  router.ts   hash router · theme.ts light/dark · data.tsx graph context
scripts/      make-dev-graph.mjs
dev-public/   dev-only static dir (never bundled into dist/)
```

## Staging (ERD drawing)

Dragging a column handle onto another in the ERD stages a relationship through
the `stitch serve` API (`src/lib/staging.ts`); nothing touches the dbt repo until
somebody runs `stitch apply`. The capability is detected, never assumed:
`GET /api/meta`'s `staging_enabled` is a definitive no when false, and otherwise
the endpoint is probed once — so a static export, or a `serve` that predates the
flag, renders a plain read-only ERD with no handles rather than a broken canvas.

## Naming rules

Every surface — panels, chips, canvases, search — takes a node's label and its
context from `src/lib/present.ts`. Nothing renders `node.name` directly.

- dbt entities read as **dbt names**. A column's context is the dbt **model** it
  belongs to, derived from the node id (always the dbt `unique_id`) — never
  `Node.table`, which is the physical alias and on dev artifacts carries the
  `USER_PREFIX` (`sis_stg_…`).
- The physical relation and the warehouse's own spelling of a column
  (`properties.warehouse_name`) are secondary detail rows, never labels.
- Metabase entities read as their Metabase display name, with the Metabase
  table (fields) or the collection (cards, dashboards) as context.
- A node synthesized for a dangling edge endpoint says what it is
  (`field 902`), never its raw id.
