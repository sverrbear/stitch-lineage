# Public demo screenshots

Captured on 2026-10-27 from the actual bundled browser app at the default 1280 × 720
viewport, in dark theme. All names, identifiers, graphs and Metabase responses are synthetic.
The frontend matches main commit `c47667b`; no UI source or generated bundle was changed
for these images. Screenshots were captured directly in the browser without image editing.

Install the repository's development dependencies and build its frontend first:

```sh
python -m pip install -e '.[dev]'
cd stitch_lineage/app/frontend
npm ci
npm run build
cd ../../..
```

Use the Node version in `.nvmrc` for the frontend commands. Start each demo in a separate
terminal. It binds only to loopback and uses temporary files; Ctrl+C stops it. These scripts
do not load a real dbt project, a Metabase client or a stored API key.

## Lineage

```sh
python scripts/demo_lineage.py --port 8790
```

Open the printed URL. Expand **+ 1 upstream** on `stg_payments`, expand **+ 1 downstream**
on either revenue report, then choose **Show overview**. The six groups trace
`transactions.net_revenue` through two dbt models into two reports on one dashboard.
Capture the viewport as `assets/lineage.png`. The dataset is defined entirely inside
`scripts/demo_lineage.py`; links use the reserved `metabase.example.test` host.

## Mend review and success

```sh
python scripts/demo_mend.py --port 8791
```

Open `http://127.0.0.1:8791/#/mend`, enter `fct_orders.amount_usd` as the replacement for
`fct_orders.amount`, and choose **Review repairs**. Leave the other repair actions unchecked.
The app scrolls to the review: nine reports, one repair, eight manual reports. Capture
`assets/mend.png` with **Calculation** and **Dashboard filter · Order operations** visible.

Check **I have reviewed and approve 1 repair**, then **Apply 1 repair**. Capture the result as
`assets/demo/mend-success.png`: one completed change, zero needing attention, eight manual,
and one dashboard filter updated. `/api/demo` reports one card write, one dashboard write,
and one query execution. Restarting resets all synthetic state.

## Recovery

```sh
python scripts/demo_mend.py --port 8792 --fail-query --fail-rollback
```

Repeat the same review and approval at `http://127.0.0.1:8792/#/mend`. The result reports
**Rollback failed — action required**. Expand **Original query for recovery** and capture
`assets/demo/mend-recovery.png`. The exact query and original dashboard wiring remain
available for manual recovery. With only `--fail-query`, the app reports a successful
rollback attempt instead. These failures are injected into an in-memory fake; no actual
Metabase report is modified.
