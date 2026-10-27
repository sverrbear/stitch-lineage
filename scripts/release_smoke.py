"""Validate an installed release wheel against this checkout, using synthetic data only.

Run with a fresh environment that contains the built wheel, pytest and httpx (the latter
two are fixture/TestClient dependencies). Do not install Stitch editable in that environment:
    /tmp/stitch-wheel/bin/python scripts/release_smoke.py dist/stitch_lineage-0.5.0-py3-none-any.whl

The script refuses to import Stitch from this checkout, checks every bundled frontend byte,
starts the installed CLI/server, and exercises approved repair and both recovery outcomes.
It never constructs a network Metabase client or reads a developer's project/credentials.
"""

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import tomllib
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path)
    args = parser.parse_args()

    from fastapi.testclient import TestClient

    import stitch_lineage
    from stitch_lineage.app import frontend_dist
    from stitch_lineage.app.server import create_app
    from stitch_lineage.mend_runtime import MendRuntime

    imported = Path(stitch_lineage.__file__).resolve()
    assert not imported.is_relative_to(ROOT), f"Expected installed wheel, imported {imported}"
    expected = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    assert stitch_lineage.__version__ == expected
    prefix = "stitch_lineage/app/frontend/dist/"
    source = ROOT / prefix
    files = {
        p.relative_to(source).as_posix(): p.read_bytes() for p in source.rglob("*") if p.is_file()
    }
    installed = frontend_dist()
    with zipfile.ZipFile(args.wheel) as archive:
        packaged = {
            name.removeprefix(prefix): archive.read(name)
            for name in archive.namelist()
            if name.startswith(prefix) and not name.endswith("/")
        }
    assert packaged == files, "Wheel frontend differs from the reviewed checkout"
    assert {
        p.relative_to(installed).as_posix(): p.read_bytes()
        for p in installed.rglob("*")
        if p.is_file()
    } == files, "Installed frontend differs from the wheel"
    assert "index.html" in files and sum(name.startswith("assets/") for name in files) >= 2
    print(f"Installed {expected}: all {len(files)} frontend files match checkout and wheel")

    # Only synthetic fixtures enter sys.path; the package must stay wheel-installed.
    sys.path.insert(0, str(ROOT / "tests"))
    import mend_scenario as scenario
    from mend_browser_scenario import BrowserMetabase, project

    os.environ["STITCH_MEND_DEMO_KEY"] = "synthetic-release-smoke-only"
    executable = Path(sys.executable).parent / "stitch"
    with tempfile.TemporaryDirectory(prefix="stitch-release-smoke-") as temporary:
        directory = Path(temporary)
        config, graph = project(directory)
        version = subprocess.check_output([str(executable), "--version"], cwd=directory, text=True)
        assert expected in version
        impact = json.loads(
            subprocess.check_output(
                [str(executable), "impact", "--config", str(config), "--format", "json"],
                cwd=directory,
                text=True,
            )
        )
        assert len(impact["columns"]) == 2
        print("Installed CLI: version and two-column rename/removal impact passed")
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        with tempfile.TemporaryFile(mode="w+") as log:
            process = subprocess.Popen(
                [
                    str(executable),
                    "serve",
                    "--config",
                    str(config),
                    "--port",
                    str(port),
                    "--no-open",
                ],
                cwd=directory,
                stdout=log,
                stderr=log,
            )
            try:
                base = f"http://127.0.0.1:{port}"
                deadline = time.monotonic() + 15
                while True:
                    try:
                        with urllib.request.urlopen(f"{base}/api/meta", timeout=1) as response:
                            assert json.load(response)["mend_enabled"]
                        break
                    except (urllib.error.URLError, TimeoutError):
                        if process.poll() is not None or time.monotonic() >= deadline:
                            log.seek(0)
                            raise AssertionError(
                                f"Installed server did not start: {log.read()}"
                            ) from None
                        time.sleep(0.1)
                with urllib.request.urlopen(f"{base}/api/graph", timeout=5) as response:
                    assert json.load(response)["nodes"]
                for name, content in files.items():
                    with urllib.request.urlopen(f"{base}/{name}", timeout=5) as response:
                        assert response.read() == content, name
                print("Installed server: graph/meta APIs and every frontend asset passed over HTTP")
            finally:
                process.terminate()
                process.wait(timeout=10)

        for mode in ("success", "query-failure", "rollback-failure"):
            fake = BrowserMetabase()
            if mode != "success":
                fake.query_results[401] = {"status": "failed", "error": "Synthetic release failure"}
            if mode == "rollback-failure":
                fake.fail_on = ("revert",)
            runtime = MendRuntime(config, graph, client_factory=lambda _, fake=fake: fake)
            app = create_app(
                graph, "https://metabase.example.test", mend=runtime, bind_host="127.0.0.1"
            )
            with TestClient(app, base_url="http://127.0.0.1:8787") as client:
                assert len(client.get("/api/mend").json()["removed"]) == 2
                response = client.post(
                    "/api/mend/preview", json={"renames": {scenario.AMOUNT: scenario.AMOUNT_USD}}
                )
                assert response.status_code == 200, response.text
                review = response.json()
                assert review["writing_count"] == 1
                assert len(review["plan"]["cards"]) == 9
                assert fake.writes == fake.dashboard_writes == fake.queries_run == []
                approval = {"preview_id": review["preview_id"], "confirmed": True}
                assert (
                    client.post(
                        "/api/mend/apply", json={"preview_id": review["preview_id"]}
                    ).status_code
                    == 422
                )
                assert fake.writes == fake.dashboard_writes == fake.queries_run == []
                response = client.post("/api/mend/apply", json=approval)
                assert response.status_code == 200, response.text
                result = response.json()
                outcome = next(
                    card for card in result["outcome"]["cards"] if card["card_id"] == 401
                )
                reviewed = next(card for card in review["plan"]["cards"] if card["card_id"] == 401)
                assert fake.writes[0][1]["dataset_query"] == reviewed["after"]
                assert fake.queries_run == [401]
                if mode == "success":
                    assert outcome["status"] == "applied" and len(fake.dashboard_writes) == 1
                    assert fake.cards[401]["dataset_query"] == reviewed["after"]
                    assert (
                        fake.dashboards[10]["dashcards"][0]["parameter_mappings"]
                        == reviewed["dashcards"][0]["after"]
                    )
                else:
                    assert outcome["status"] == "failed"
                    assert outcome["reverted"] is (mode == "query-failure")
                    recovery = result["recovery"]["401"]
                    for field in ("before", "after", "dashcards"):
                        assert recovery[field] == reviewed[field]
                    assert fake.dashboard_writes == []
                    restored = "before" if mode == "query-failure" else "after"
                    assert fake.cards[401]["dataset_query"] == reviewed[restored]
                writes = len(fake.writes)
                assert client.post("/api/mend/apply", json=approval).status_code == 409
                assert len(fake.writes) == writes
                assert client.get(f"/api/mend/results/{review['preview_id']}").json() == result
                print(f"Installed Mend {mode}: review, one-use approval, result retrieval passed")


if __name__ == "__main__":
    main()
