# Readable Mend review

Reproduce with `python scripts/demo_mend.py --port 8797`, open `/#/mend`, declare
`fct_orders.amount` → `fct_orders.amount_usd`, and select **Review repairs**.

The screenshots use synthetic graphs and in-memory Metabase only:

- `mend-before.png`: the old review requires interpreting the query structure.
- `mend-after.png`: the calculation and dashboard filter show named before/after values.
- `named-diff.png`: field references are annotated while exact query JSON remains available.

Browser verification completed the synthetic approval/apply flow with one completed
repair, zero attention items, and eight manual reports. The integration test checks
that the submitted query is exactly the unannotated plan. The real saved graph was
used for read-only app regression checks; no live report was modified.
