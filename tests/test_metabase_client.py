import json
from pathlib import Path

import pytest
import requests
import responses

from stitch_lineage.io.metabase_client import (
    MetabaseAPIError,
    MetabaseClient,
    _version_tuple,
    load_cached,
)

FIXTURES = Path(__file__).parent / "fixtures" / "metabase"
BASE = "http://mb.local"


def fixture(name: str) -> dict | list:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def make_client(**kwargs) -> MetabaseClient:
    kwargs.setdefault("backoff", 0)
    return MetabaseClient(url=f"{BASE}/", api_key="mb_test_key", **kwargs)


def register_fetch_all() -> None:
    responses.get(f"{BASE}/api/session/properties", json=fixture("session_properties"))
    responses.get(f"{BASE}/api/database", json=fixture("databases"))
    responses.get(f"{BASE}/api/database/2/metadata", json=fixture("database_metadata_2"))
    responses.get(f"{BASE}/api/card", json=fixture("cards"))
    responses.get(f"{BASE}/api/dashboard", json=fixture("dashboards"))
    responses.get(f"{BASE}/api/dashboard/301", json=fixture("dashboard_301"))
    responses.get(f"{BASE}/api/dashboard/302", json=fixture("dashboard_302"))
    responses.get(f"{BASE}/api/collection", json=fixture("collections"))


@responses.activate
def test_auth_header_and_url_normalization():
    responses.get(f"{BASE}/api/session/properties", json=fixture("session_properties"))
    client = make_client()
    assert client.url == BASE
    assert client.assert_version() == "v0.53.2"
    assert responses.calls[0].request.headers["x-api-key"] == "mb_test_key"


@pytest.mark.parametrize(
    ("tag", "expected"),
    [
        ("v0.53.2", (53, 2)),
        ("v1.53.2", (53, 2)),
        ("v1.53.x", (53,)),
        ("0.49", (49,)),
    ],
)
def test_version_tuple(tag, expected):
    assert _version_tuple(tag) == expected


@responses.activate
def test_assert_version_accepts_enterprise_tag():
    responses.get(f"{BASE}/api/session/properties", json={"version": {"tag": "v1.53.x"}})
    assert make_client().assert_version() == "v1.53.x"


@responses.activate
def test_assert_version_rejects_old_version():
    responses.get(f"{BASE}/api/session/properties", json={"version": {"tag": "v0.48.4"}})
    with pytest.raises(MetabaseAPIError, match=r"v0\.48\.4.*0\.49"):
        make_client().assert_version()


@responses.activate
def test_assert_version_rejects_missing_tag():
    responses.get(f"{BASE}/api/session/properties", json={"version": {}})
    with pytest.raises(MetabaseAPIError, match="version tag"):
        make_client().assert_version()


@responses.activate
def test_retry_on_500_then_success():
    responses.get(f"{BASE}/api/card", status=500)
    responses.get(f"{BASE}/api/card", json=[{"id": 1}])
    assert make_client().list_cards() == [{"id": 1}]
    assert len(responses.calls) == 2


@responses.activate
def test_retry_on_429_then_success():
    responses.get(f"{BASE}/api/card", status=429)
    responses.get(f"{BASE}/api/card", json=[])
    assert make_client().list_cards() == []
    assert len(responses.calls) == 2


@responses.activate
def test_retries_exhausted_raises_with_status_and_url():
    for _ in range(3):
        responses.get(f"{BASE}/api/card", status=503)
    with pytest.raises(MetabaseAPIError, match=r"HTTP 503.*/api/card.*3 attempts"):
        make_client().list_cards()
    assert len(responses.calls) == 3


@responses.activate
def test_4xx_fails_immediately_without_retry():
    responses.get(f"{BASE}/api/card", status=401)
    with pytest.raises(MetabaseAPIError, match=r"HTTP 401.*/api/card"):
        make_client().list_cards()
    assert len(responses.calls) == 1


@responses.activate
def test_connection_error_is_retried():
    responses.get(f"{BASE}/api/card", body=requests.ConnectionError("boom"))
    responses.get(f"{BASE}/api/card", json=[])
    assert make_client().list_cards() == []
    assert len(responses.calls) == 2


@responses.activate
def test_list_endpoints_tolerate_wrapped_and_bare_shapes():
    responses.get(f"{BASE}/api/dashboard", json={"data": [{"id": 301}]})
    responses.get(f"{BASE}/api/collection", json=[{"id": 7}])
    client = make_client()
    assert client.list_dashboards() == [{"id": 301}]
    assert client.list_collections() == [{"id": 7}]


@responses.activate
def test_unexpected_shape_raises():
    responses.get(f"{BASE}/api/card", json={"nope": True})
    with pytest.raises(MetabaseAPIError, match="unexpected response shape"):
        make_client().list_cards()


@responses.activate
def test_fetch_all_bundle():
    register_fetch_all()
    payload = make_client().fetch_all(["Analytics"])

    assert payload.metabase_version == "v0.53.2"
    assert [db["name"] for db in payload.databases] == ["Analytics"]
    assert set(payload.database_metadata) == {2}
    assert len(payload.cards) == 9
    assert [dash["id"] for dash in payload.dashboards] == [301, 302]
    assert "dashcards" in payload.dashboards[0]
    assert "ordered_cards" in payload.dashboards[1]
    assert any(col["name"] == "Marts" for col in payload.collections)
    metadata_call = next(
        call for call in responses.calls if "/api/database/2/metadata" in call.request.url
    )
    assert "include_hidden=true" in metadata_call.request.url


@responses.activate
def test_fetch_all_unknown_database_lists_available_names():
    responses.get(f"{BASE}/api/session/properties", json=fixture("session_properties"))
    responses.get(f"{BASE}/api/database", json=fixture("databases"))
    with pytest.raises(MetabaseAPIError, match=r"Warehouse.*Analytics.*Sample Database"):
        make_client().fetch_all(["Warehouse"])


@responses.activate
def test_cache_write_and_load_cached_round_trip(tmp_path):
    register_fetch_all()
    cache_dir = tmp_path / "cache"
    payload = make_client(cache_dir=cache_dir).fetch_all(["Analytics"])

    runs = [p for p in cache_dir.iterdir() if p.is_dir()]
    assert len(runs) == 1
    names = {p.name for p in runs[0].iterdir()}
    assert names == {
        "session_properties.json",
        "databases.json",
        "database_metadata_2.json",
        "cards.json",
        "dashboards.json",
        "dashboard_301.json",
        "dashboard_302.json",
        "collections.json",
        "payload.json",
    }
    assert json.loads((runs[0] / "databases.json").read_text())["total"] == 2

    loaded = load_cached(cache_dir)
    assert loaded is not None
    assert loaded.model_dump() == payload.model_dump()


@responses.activate
def test_cache_prunes_to_retain(tmp_path):
    register_fetch_all()
    cache_dir = tmp_path / "cache"
    old_runs = [
        cache_dir / "2020-01-01T00-00-00-000000Z",
        cache_dir / "2020-01-02T00-00-00-000000Z",
        cache_dir / "2020-01-03T00-00-00-000000Z",
    ]
    for run in old_runs:
        run.mkdir(parents=True)
        (run / "payload.json").write_text("{}", encoding="utf-8")

    make_client(cache_dir=cache_dir, retain=2).fetch_all(["Analytics"])
    remaining = sorted(p.name for p in cache_dir.iterdir() if p.is_dir())
    assert len(remaining) == 2
    assert remaining[0] == "2020-01-03T00-00-00-000000Z"
    assert remaining[1] > "2020-01-03"


def test_load_cached_missing_or_empty(tmp_path):
    assert load_cached(tmp_path / "nope") is None
    empty = tmp_path / "cache"
    empty.mkdir()
    assert load_cached(empty) is None
    (empty / "run-without-payload").mkdir()
    assert load_cached(empty) is None
