# Qualified column search

Synthetic before/after screenshots use `python scripts/demo_mend.py` and the home
search query `fct_orders.amount_usd`. Before: no matches. After: the exact column
appears first and Enter opens its Summary. The built frontend is included in the PR.

Also verified Home and the command palette against the existing read-only real
graph (6,220 nodes, 10,524 edges). The formerly failing qualified-column query now
puts the exact column above fuzzy model results; Enter opens the expected Summary
in both surfaces. Real graph contents are not included in these fixtures.
