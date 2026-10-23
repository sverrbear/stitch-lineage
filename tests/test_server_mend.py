"""Browser approval tests use the real planner/apply loop and synthetic Metabase only."""

import threading
from concurrent.futures import ThreadPoolExecutor

import mend_scenario as scenario
import pytest
from fastapi.testclient import TestClient
from mend_browser_scenario import BrowserMetabase, project

from stitch_lineage.app.server import create_app
from stitch_lineage.io.graph_store import previous_graph_path
from stitch_lineage.mend_runtime import MendRuntime

RENAMES = {scenario.AMOUNT: scenario.AMOUNT_USD}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("STITCH_MEND_DEMO_KEY", "synthetic-only")
    config, graph = project(tmp_path)
    fake = BrowserMetabase()
    now = [0]
    runtime = MendRuntime(config, graph, client_factory=lambda _: fake, clock=lambda: now[0])
    app = create_app(graph, "https://metabase.example.test", mend=runtime, bind_host="127.0.0.1")
    client = TestClient(app, base_url="http://127.0.0.1:8787")
    return client, fake, config, graph, runtime, now


def preview(client, **kwargs):
    response = client.post("/api/mend/preview", json={"renames": RENAMES, **kwargs})
    assert response.status_code == 200, response.text
    return response.json()


def apply(client, review):
    return client.post(
        "/api/mend/apply", json={"preview_id": review["preview_id"], "confirmed": True}
    )


def test_rename_review_approval_result_and_no_writes_before_approval(setup):
    client, fake, *_ = setup
    assert client.get("/api/meta").json()["mend_enabled"]
    context = client.get("/api/mend").json()
    assert context["baseline"]["file"] == "graph.prev.json"
    assert context["current"]["generated_at"].startswith("2026-08-11")
    assert len(context["removed"]) == 2
    review = preview(client)
    assert review["writing_count"] == 1
    cards = {c["card_id"]: c for c in review["plan"]["cards"]}
    assert len(cards) == 9
    assert cards[401]["action"] == "repoint"
    assert cards[401]["dashcards"][0]["after"] != cards[401]["dashcards"][0]["before"]
    assert "201" in review["diffs"]["401"]
    for cid in [402, 403, 404, 405, 406, 407, 408, 409]:
        assert cards[cid]["action"] == "notify"
    assert "notify-only" in cards[405]["reason"]
    assert "native" in cards[407]["reason"].lower()
    assert "cannot edit" in cards[408]["reason"]
    assert "Cannot read" in cards[409]["reason"]
    assert fake.writes == fake.dashboard_writes == fake.queries_run == []
    assert (
        client.post("/api/mend/apply", json={"preview_id": review["preview_id"]}).status_code == 422
    )
    assert (
        client.post(
            "/api/mend/apply", json={"preview_id": review["preview_id"], "confirmed": False}
        ).status_code
        == 422
    )
    result = apply(client, review)
    assert result.status_code == 200
    assert result.json()["outcome"]["cards"][0]["status"] == "applied"
    assert [c for c, _ in fake.writes] == [401]
    assert len(fake.dashboard_writes) == 1
    assert apply(client, review).status_code == 409
    assert len(fake.writes) == 1
    assert client.get(f"/api/mend/results/{review['preview_id']}").json() == result.json()


def test_destructive_opt_in_is_explicit_and_config_limited(setup):
    client, _, config, *_ = setup
    review = preview(client)
    assert review["writing_count"] == 1
    stripped = next(card for card in review["plan"]["cards"] if card["card_id"] == 402)
    assert "not selected for this review" in stripped["reason"]
    assert "not in mend.auto" not in stripped["reason"]
    assert preview(client, actions=["repoint", "strip"])["writing_count"] == 3
    assert preview(client, actions=["repoint", "archive"])["writing_count"] == 2
    config.write_text(config.read_text().replace("[repoint, strip, archive]", "[repoint]"))
    denied = client.post("/api/mend/preview", json={"renames": RENAMES, "actions": ["strip"]})
    assert denied.status_code == 422


def test_browser_apply_passes_current_project_policy_to_the_shared_engine(setup, monkeypatch):
    from stitch_lineage import mend_runtime
    from stitch_lineage.mend.models import MendAction

    client, fake, *_ = setup
    review = preview(client)
    assert review["plan"]["auto"] == ["repoint"]
    engine = mend_runtime.apply_plan
    received = {}

    def capture(plan, writer, **policy):
        received.update(policy)
        return engine(plan, writer, **policy)

    monkeypatch.setattr(mend_runtime, "apply_plan", capture)
    response = apply(client, review)
    assert response.status_code == 200
    assert received == {
        "auto": [MendAction.REPOINT, MendAction.STRIP, MendAction.ARCHIVE],
        "notify_only_collections": ["*Personal*"],
    }
    # Current config permits more actions, but only the reviewed repoint may write.
    assert [card_id for card_id, _ in fake.writes] == [401]


@pytest.mark.parametrize("mode", ["missing", "renamed", "inactive"])
def test_target_must_still_exist_in_live_metabase_metadata(setup, mode):
    client, fake, *_ = setup
    fields = fake.payload.database_metadata[1]["tables"][0]["fields"]
    if mode == "missing":
        fields.pop(0)
    elif mode == "renamed":
        fields[0]["name"] = "another_column"
    else:
        fields[0]["active"] = False
    review = preview(client)
    assert review["writing_count"] == 0
    assert review["plan"]["unresolved_renames"]
    assert fake.writes == []


def test_absent_baseline_is_not_a_zero_impact_claim(setup):
    client, fake, _, graph, *_ = setup
    previous_graph_path(graph).unlink()
    assert client.get("/api/mend").json()["state"] == "missing-baseline"
    assert client.post("/api/mend/preview", json={}).status_code == 409
    assert fake.fetches == 0


def test_missing_credentials_provides_reconnect_instructions(setup, monkeypatch):
    client, fake, *_ = setup
    monkeypatch.delenv("STITCH_MEND_DEMO_KEY")
    monkeypatch.setattr("stitch_lineage.mend_runtime.restore_api_key", lambda _: None)
    response = client.post("/api/mend/preview", json={"renames": RENAMES})
    assert response.status_code == 503
    assert "stitch init" in response.json()["detail"]
    assert fake.fetches == 0


def test_new_failed_review_invalidates_old_approval(setup):
    client, fake, *_ = setup
    first = preview(client)
    fake.fail_on = ("fetch_all",)
    assert client.post("/api/mend/preview", json={"renames": RENAMES}).status_code == 503
    assert apply(client, first).status_code == 409
    assert fake.writes == []


def test_snapshot_changes_during_planning_discard_the_preview(setup):
    client, fake, _, graph, *_ = setup
    first = preview(client)
    fake.on_fetch = lambda: graph.write_text(graph.read_text() + "\n")
    response = client.post("/api/mend/preview", json={"renames": RENAMES})
    assert response.status_code == 409
    assert apply(client, first).status_code == 409
    assert fake.writes == []


def test_credential_restore_is_reused_without_exposing_key(setup, monkeypatch):
    client, fake, *_ = setup
    monkeypatch.delenv("STITCH_MEND_DEMO_KEY")

    def restore(cfg):
        cfg.metabase.api_key = "synthetic-restored-key"
        cfg.metabase.missing_env = []

    monkeypatch.setattr("stitch_lineage.mend_runtime.restore_api_key", restore)
    review = preview(client)
    assert "synthetic-restored-key" not in str(review)
    response = apply(client, review)
    assert response.status_code == 200
    assert "synthetic-restored-key" not in response.text
    assert len(fake.writes) == 1


def test_comparison_without_removed_columns_is_explicit(setup):
    client, _, _, graph, *_ = setup
    previous_graph_path(graph).write_bytes(graph.read_bytes())
    context = client.get("/api/mend").json()
    assert context["state"] == "no-removed-columns"
    assert context["baseline"] is not None
    assert context["removed"] == []


@pytest.mark.parametrize("drift", ["graph", "baseline", "config", "environment"])
def test_changed_inputs_refuse_approval(setup, monkeypatch, drift):
    client, fake, config, graph, *_ = setup
    review = preview(client)
    if drift == "environment":
        monkeypatch.setenv("STITCH_MEND_DEMO_KEY", "new-synthetic-key")
    else:
        path = {"graph": graph, "baseline": previous_graph_path(graph), "config": config}[drift]
        path.write_text(path.read_text() + "\n")
    response = apply(client, review)
    assert response.status_code == 409
    assert "changed" in response.json()["detail"]
    assert fake.writes == []


def test_replaced_expired_and_unknown_previews_never_apply(setup):
    client, fake, _, _, _, now = setup
    first = preview(client)
    second = preview(client)
    assert apply(client, first).status_code == 409
    now[0] = 901
    assert apply(client, second).status_code == 410
    assert apply(client, {"preview_id": "client-invented"}).status_code == 409
    assert fake.writes == []


@pytest.mark.parametrize(
    "mutation", ["query", "timestamp", "protected", "unknown", "missing", "invalid", "archived"]
)
def test_live_card_drift_is_reported_without_overwriting(setup, mutation):
    client, fake, *_ = setup
    review = preview(client)
    card = fake.cards[401]
    if mutation == "query":
        card["dataset_query"]["query"]["limit"] = 12
    elif mutation == "timestamp":
        card["updated_at"] = "2026-08-12T00:00:00Z"
    elif mutation in {"protected", "unknown", "invalid"}:
        card["collection_id"] = {"protected": 9, "unknown": 99, "invalid": []}[mutation]
    elif mutation == "missing":
        del card["collection_id"]
    else:
        card["archived"] = True
    result = apply(client, review).json()["outcome"]["cards"][0]
    assert result["status"] == ("stale" if mutation in {"query", "timestamp"} else "skipped")
    assert fake.writes == []


@pytest.mark.parametrize("rollback_fails", [False, True])
def test_query_failure_and_rollback_failure_remain_distinguishable(setup, rollback_fails):
    client, fake, *_ = setup
    review = preview(client)
    fake.query_results[401] = {"status": "failed", "error": "synthetic query failure"}
    if rollback_fails:
        fake.fail_on = ("revert",)
    response = apply(client, review)
    assert response.status_code == 200
    outcome = response.json()["outcome"]["cards"][0]
    assert outcome["status"] == "failed"
    assert outcome["reverted"] is not rollback_fails
    assert ("COULD NOT REVERT" in outcome["detail"]) is rollback_fails
    recovery = response.json()["recovery"]["401"]
    reviewed = next(card for card in review["plan"]["cards"] if card["card_id"] == 401)
    assert recovery["before"] == reviewed["before"]
    assert recovery["after"] == reviewed["after"]
    assert recovery["dashcards"] == reviewed["dashcards"]
    assert client.get(f"/api/mend/results/{review['preview_id']}").json() == response.json()


def test_dashboard_conflict_is_not_full_success(setup):
    client, fake, *_ = setup
    review = preview(client)
    fake.dashboards[10]["dashcards"][0]["parameter_mappings"] = []
    result = apply(client, review).json()
    outcome = result["outcome"]["cards"][0]
    assert outcome["status"] == "failed"
    assert not outcome["reverted"]
    assert len(fake.writes) == 1
    assert fake.dashboard_writes == []
    assert result["recovery"]["401"]["dashcards"][0]["before"]


def test_concurrent_apply_and_plan_are_refused_and_double_click_writes_once(setup):
    client, fake, *_ = setup
    review = preview(client)
    entered, release = threading.Event(), threading.Event()
    original = fake.update_card

    def blocked(card_id, changes):
        entered.set()
        assert release.wait(5)
        return original(card_id, changes)

    fake.update_card = blocked
    with ThreadPoolExecutor() as pool:
        future = pool.submit(apply, client, review)
        assert entered.wait(5)
        try:
            assert apply(client, review).status_code == 409
            assert client.post("/api/mend/preview", json={}).status_code == 409
        finally:
            release.set()
        assert future.result().status_code == 200
    assert len(fake.writes) == 1


def test_origin_host_and_arbitrary_query_guards(setup):
    client, fake, *_ = setup
    review = preview(client)
    data = {"preview_id": review["preview_id"], "confirmed": True}
    assert client.post("/api/mend/apply", json={**data, "plan": {}}).status_code == 422
    assert client.post("/api/mend/apply", json={**data, "force": True}).status_code == 422
    assert (
        client.post(
            "/api/mend/apply", json=data, headers={"Origin": "https://other.test"}
        ).status_code
        == 403
    )
    assert client.get("/api/mend", headers={"Host": "rebinding.test"}).status_code == 403
    assert fake.writes == []


def test_without_injected_runtime_the_app_is_read_only(setup):
    _, _, _, graph, *_ = setup
    client = TestClient(create_app(graph, None))
    assert client.get("/api/meta").json()["mend_enabled"] is False
    assert client.post("/api/mend/preview", json={}).status_code == 405
    assert client.post("/api/mend/apply", json={}).status_code == 405
