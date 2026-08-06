# stitch

**dbt ↔ Metabase column lineage and interactive ERD.**

Column lineage that doesn't stop at the warehouse boundary: source column → staging → mart → Metabase field → card → dashboard. Plus a visual ERD editor that writes relationships back to dbt YAML.

> Status: pre-alpha. See [SPEC.md](SPEC.md) for the full design (draft v0.4).

```bash
pip install stitch-lineage   # not yet published
```

- Local-first: no server, no warehouse backend. The dbt repo is the database.
- `stitch build` — resolve dbt artifacts + the Metabase API into a committed, deterministic `.stitch/graph.json`
- `stitch impact` — CI PR comments: "this rename breaks 4 cards on 2 dashboards"
- `stitch serve` — local app: catalog, column lineage, ERD editor with YAML write-back
- MIT licensed
