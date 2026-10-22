# Readable diagram review

These before/after captures use the same synthetic graph and a 1280 × 720 viewport.
The baseline is commit `c0bb189`; the after images show this change's default views.
No customer graph data is included.

Generate the fixture with:

```sh
node stitch_lineage/app/frontend/scripts/make-diagram-review.mjs /tmp/stitch-demo-graph.json
```

Open `model.demo.fct_orders::net_revenue` in Lineage and Relationships. The fixture
has a broad downstream trace and two wide joined tables, illustrating the old
fit-to-view behavior and the new compact, readable defaults.
