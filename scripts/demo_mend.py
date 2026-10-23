"""Reproducible browser review: real app/engine, synthetic graphs and in-memory Metabase.

Run from the repository with its dev dependencies installed:
    python scripts/demo_mend.py --port 8791
    python scripts/demo_mend.py --port 8792 --fail-query
    python scripts/demo_mend.py --port 8793 --fail-query --fail-rollback

Open /#/mend, map fct_orders.amount to fct_orders.amount_usd, review and approve.
GET /api/demo reports write/query counts. Restarting resets everything. No network client
is created and no real dbt project or credentials are read. All files live under /tmp.
"""

import argparse
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import uvicorn  # noqa: E402
from mend_browser_scenario import BrowserMetabase, project  # noqa: E402

from stitch_lineage.app.server import create_app  # noqa: E402
from stitch_lineage.mend_runtime import MendRuntime  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8791)
    parser.add_argument("--fail-query", action="store_true")
    parser.add_argument("--fail-rollback", action="store_true")
    parser.add_argument("--missing-baseline", action="store_true")
    args = parser.parse_args()
    directory = Path(tempfile.mkdtemp(prefix="stitch-mend-demo-"))
    config, graph = project(directory)
    if args.missing_baseline:
        graph.with_name("graph.prev.json").unlink()
    os.environ["STITCH_MEND_DEMO_KEY"] = "synthetic-demo-only"
    fake = BrowserMetabase()
    if args.fail_query:
        fake.query_results[401] = {"status": "failed", "error": "Synthetic query failure"}
    if args.fail_rollback:
        fake.fail_on = ("revert",)
    runtime = MendRuntime(config, graph, client_factory=lambda _: fake)
    app = create_app(graph, "https://metabase.example.test", mend=runtime, bind_host="127.0.0.1")

    @app.get("/api/demo")
    def state():
        return {
            "synthetic": True,
            "writes": len(fake.writes),
            "dashboard_writes": len(fake.dashboard_writes),
            "queries_run": len(fake.queries_run),
            "fetches": fake.fetches,
            "rollback_calls": len(fake.reverts),
        }

    # create_app ends with the SPA's catch-all mount; keep this demo-only route before it.
    app.router.routes.insert(0, app.router.routes.pop())

    print(f"Synthetic Mend demo: http://127.0.0.1:{args.port}/#/mend", flush=True)
    print(f"Counters: http://127.0.0.1:{args.port}/api/demo", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
