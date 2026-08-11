"""Metabase API client: HTTP + retry + payload caching (SPEC.md section 7.2).

This is the ONLY module in the codebase allowed to import requests (enforced by
import-linter). All methods return raw, untransformed API payloads; resolution
happens in resolve/metabase.py against the MetabasePayload bundle.
"""

import json
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests

from stitch_lineage.payloads import MetabasePayload

_RETRY_ATTEMPTS = 3
_PAYLOAD_FILE = "payload.json"


class MetabaseAPIError(Exception):
    """Metabase unreachable, auth rejected, version below minimum, or a non-2xx response."""


def _version_tuple(tag: str) -> tuple[int, ...]:
    """Comparable version from a Metabase tag ("v0.53.2", enterprise "v1.53.x", "0.49").

    The leading 0/1 is the edition, not a version component -- the meaningful part is
    the trailing major.minor. Non-numeric components ("x") end the tuple.
    """
    parts = tag.strip().lstrip("vV").split(".")
    if parts and parts[0] in ("0", "1"):
        parts = parts[1:]
    numbers: list[int] = []
    for part in parts:
        if not part.isdigit():
            break
        numbers.append(int(part))
    return tuple(numbers)


def _http_error(response: requests.Response) -> str:
    """ "HTTP 400: message" -- Metabase's own words when it has any, for the mend summary."""
    detail = ""
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        for key in ("message", "error", "cause"):
            value = body.get(key)
            if isinstance(value, str) and value:
                detail = value
                break
    elif isinstance(body, str) and body:
        detail = body
    return f"HTTP {response.status_code}: {detail}" if detail else f"HTTP {response.status_code}"


def _as_list(payload: Any, endpoint: str) -> list[dict[str, Any]]:
    """Tolerate both bare-list and {"data": [...]} response shapes (varies by version)."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and isinstance(payload.get("data"), list):
        return payload["data"]
    raise MetabaseAPIError(f"unexpected response shape from {endpoint}: {type(payload).__name__}")


def load_cached(cache_dir: Path) -> MetabasePayload | None:
    """Load the newest cached fetch_all run, or None if no usable run exists.

    Offline-debugging helper: the raw per-endpoint snapshots sit next to payload.json
    for repro, this reads the bundle. Not used by `stitch build --no-metabase`, which
    reuses the committed baseline graph.json rather than the payload cache.
    """
    if not cache_dir.is_dir():
        return None
    for run_dir in sorted((p for p in cache_dir.iterdir() if p.is_dir()), reverse=True):
        payload_path = run_dir / _PAYLOAD_FILE
        if payload_path.is_file():
            return MetabasePayload.model_validate_json(payload_path.read_text(encoding="utf-8"))
    return None


class MetabaseClient:
    """Talks to one Metabase instance via API-key auth (Metabase 49+).

    Auth is the x-api-key header. When cache_dir is set, every fetch_all run snapshots
    the raw responses to {cache_dir}/{timestamp}/ before parsing and prunes to the
    newest `retain` runs (config output.retain_cache_runs, default 3). Cached payloads
    are the unit-test fixtures for resolve/metabase.py.
    """

    def __init__(
        self,
        url: str,
        api_key: str,
        cache_dir: Path | None = None,
        min_version: str = "0.49",
        retain: int = 3,
        timeout: float = 30.0,
        backoff: float = 0.5,
    ) -> None:
        self.url = url.rstrip("/")
        self.api_key = api_key
        self.cache_dir = cache_dir
        self.min_version = min_version
        self.retain = retain
        self.timeout = timeout
        self.backoff = backoff
        self._session = requests.Session()
        self._session.headers["x-api-key"] = api_key

    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, str] | None = None,
        json_body: Any = None,
    ) -> Any:
        """One HTTP call with retry, for every verb this client uses.

        Retrying a write is safe because every write mend issues is idempotent: a PUT
        carries the whole `dataset_query` (or `archived: true`), and a revert names one
        revision id. A retried write can therefore only re-assert the state it was already
        asking for -- what it cannot do is stack up a different result.
        """
        url = f"{self.url}{path}"
        last_error = ""
        for attempt in range(_RETRY_ATTEMPTS):
            try:
                response = self._session.request(
                    method, url, params=params, json=json_body, timeout=self.timeout
                )
            except requests.RequestException as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            else:
                if response.ok:
                    if not response.content:
                        return None
                    try:
                        return response.json()
                    except ValueError as exc:
                        raise MetabaseAPIError(f"non-JSON response from {url}: {exc}") from exc
                last_error = _http_error(response)
                if response.status_code != 429 and response.status_code < 500:
                    raise MetabaseAPIError(f"{last_error} from {url}")
            if attempt < _RETRY_ATTEMPTS - 1 and self.backoff:
                time.sleep(self.backoff * 2**attempt)
        raise MetabaseAPIError(f"{last_error} from {url} after {_RETRY_ATTEMPTS} attempts")

    def _get(self, path: str, params: dict[str, str] | None = None) -> Any:
        return self._request("GET", path, params=params)

    def _extract_version(self, properties: Any) -> str:
        version = properties.get("version") if isinstance(properties, dict) else None
        tag = version.get("tag") if isinstance(version, dict) else version
        if not isinstance(tag, str) or not tag:
            raise MetabaseAPIError("could not read version tag from /api/session/properties")
        if _version_tuple(tag) < _version_tuple(self.min_version):
            raise MetabaseAPIError(
                f"Metabase {tag} is below the minimum supported version {self.min_version}"
                " (API-key auth requires 49+)"
            )
        return tag

    def assert_version(self) -> str:
        """Fetch the instance version and return it.

        Raises:
            MetabaseAPIError: version below min_version (API-key auth floor), with the
                detected and required versions in the message.
        """
        return self._extract_version(self._get("/api/session/properties"))

    def list_databases(self) -> list[dict[str, Any]]:
        """GET /api/database -- raw database payloads, used to locate warehouses by display name."""
        return _as_list(self._get("/api/database"), "/api/database")

    def database_metadata(self, db_id: int) -> dict[str, Any]:
        """GET /api/database/{db_id}/metadata?include_hidden=true -- the field_id map."""
        payload = self._get(f"/api/database/{db_id}/metadata", params={"include_hidden": "true"})
        if not isinstance(payload, dict):
            raise MetabaseAPIError(f"unexpected metadata shape for database {db_id}")
        return payload

    def list_cards(self) -> list[dict[str, Any]]:
        """GET /api/card -- all cards with dataset_query, collection_id, creator, archived."""
        return _as_list(self._get("/api/card"), "/api/card")

    def list_dashboards(self) -> list[dict[str, Any]]:
        """GET /api/dashboard -- dashboard listing (no dashcards; use get_dashboard)."""
        return _as_list(self._get("/api/dashboard"), "/api/dashboard")

    def get_dashboard(self, dash_id: int) -> dict[str, Any]:
        """GET /api/dashboard/{dash_id} -- full payload including dashcards -> card ids."""
        payload = self._get(f"/api/dashboard/{dash_id}")
        if not isinstance(payload, dict):
            raise MetabaseAPIError(f"unexpected dashboard shape for dashboard {dash_id}")
        return payload

    def list_collections(self) -> list[dict[str, Any]]:
        """GET /api/collection -- the collection tree, for exclude_collections filtering."""
        return _as_list(self._get("/api/collection"), "/api/collection")

    def list_snippets(self) -> list[dict[str, Any]]:
        """GET /api/native-query-snippet -- the SQL behind `{{snippet: name}}` tags.

        Returns [] instead of raising when the instance will not serve the endpoint (an
        API key without snippet permission answers 403, and snippet folders are an
        enterprise feature): a missing snippet degrades one native card's column
        resolution, which is not worth failing a whole build over.
        """
        try:
            return _as_list(self._get("/api/native-query-snippet"), "/api/native-query-snippet")
        except MetabaseAPIError:
            return []

    # ----------------------------------------------------------------------------------
    # `stitch mend` (issue #143): the read probes and the four writes it is allowed to make.
    #
    # Everything here is per-object and named, never bulk: mend writes one card at a time
    # so the summary can name every card it touched and the revert can undo exactly one
    # thing. All of it returns raw payloads, like the rest of this client -- deciding what a
    # response means is mend/apply.py's job.
    # ----------------------------------------------------------------------------------

    def current_user(self) -> dict[str, Any]:
        """GET /api/user/current -- who the API key is, for `doctor --write-access`."""
        payload = self._get("/api/user/current")
        if not isinstance(payload, dict):
            raise MetabaseAPIError("unexpected response shape from /api/user/current")
        return payload

    def get_card(self, card_id: int) -> dict[str, Any]:
        """GET /api/card/{card_id} -- the single-card payload, with `updated_at` and `can_write`.

        The staleness guard reads this immediately before writing: the listing payload the
        plan was built from is minutes old by then, and a card someone edited in between is
        a card mend must leave alone.
        """
        payload = self._get(f"/api/card/{card_id}")
        if not isinstance(payload, dict):
            raise MetabaseAPIError(f"unexpected response shape for card {card_id}")
        return payload

    def update_card(self, card_id: int, changes: dict[str, Any]) -> dict[str, Any]:
        """PUT /api/card/{card_id} -- a partial update: only the keys mend is changing."""
        payload = self._request("PUT", f"/api/card/{card_id}", json_body=changes)
        return payload if isinstance(payload, dict) else {}

    def run_card_query(self, card_id: int) -> dict[str, Any]:
        """POST /api/card/{card_id}/query -- re-execute a card to prove the rewrite runs.

        Returns the raw result envelope. Metabase reports a query failure INSIDE a 202
        response ({"status": "failed", "error": ...}) as readily as by status code, so both
        have to be read -- see mend/apply.py's query_error().
        """
        payload = self._request("POST", f"/api/card/{card_id}/query")
        return payload if isinstance(payload, dict) else {}

    def card_revisions(self, card_id: int) -> list[dict[str, Any]]:
        """GET /api/revision?entity=card&id={card_id} -- newest first, per Metabase.

        Returns [] rather than raising when the instance will not serve revisions: mend
        falls back to restoring the `before` query it captured, which is a worse audit
        trail but the same repair.
        """
        try:
            payload = self._get("/api/revision", params={"entity": "card", "id": str(card_id)})
        except MetabaseAPIError:
            return []
        return payload if isinstance(payload, list) else []

    def latest_card_revision(self, card_id: int) -> int | None:
        """The revision id to revert to -- the card's state as mend found it."""
        for revision in self.card_revisions(card_id):
            if isinstance(revision, dict) and isinstance(revision.get("id"), int):
                return revision["id"]
        return None

    def revert_card(self, card_id: int, revision_id: int) -> dict[str, Any]:
        """POST /api/revision/revert -- undo a write through Metabase's own history."""
        payload = self._request(
            "POST",
            "/api/revision/revert",
            json_body={"entity": "card", "id": card_id, "revision_id": revision_id},
        )
        return payload if isinstance(payload, dict) else {}

    def update_dashcards(self, dash_id: int, dashcards: list[dict[str, Any]]) -> dict[str, Any]:
        """PUT /api/dashboard/{dash_id} with the full dashcards array.

        Metabase 49+ takes dashcard edits on the dashboard itself; the whole array goes
        back, which is why mend reads the dashboard immediately before writing rather than
        trusting the copy in the plan.
        """
        payload = self._request(
            "PUT", f"/api/dashboard/{dash_id}", json_body={"dashcards": dashcards}
        )
        return payload if isinstance(payload, dict) else {}

    def fetch_all(self, database_names: list[str]) -> MetabasePayload:
        """Fetch everything resolve_metabase needs, in one bundle.

        database_names are Metabase display names (config metabase.databases[].metabase_name);
        only those databases' metadata is fetched. Calls assert_version first, snapshots
        raw payloads to cache_dir when set, and fills MetabasePayload.metabase_version.
        Snippets are best-effort (see list_snippets) -- everything else is required.

        Raises:
            MetabaseAPIError: any transport/auth/version failure. A configured database
                name that matches nothing is an error here, not a silent skip.
        """
        run_dir = self._new_run_dir()

        properties = self._get("/api/session/properties")
        self._snapshot(run_dir, "session_properties", properties)
        version = self._extract_version(properties)

        databases_raw = self._get("/api/database")
        self._snapshot(run_dir, "databases", databases_raw)
        databases = _as_list(databases_raw, "/api/database")
        by_name = {db.get("name"): db for db in databases if isinstance(db, dict)}
        missing = [name for name in database_names if name not in by_name]
        if missing:
            available = ", ".join(sorted(str(name) for name in by_name)) or "<none>"
            raise MetabaseAPIError(
                f"database(s) not found in Metabase: {', '.join(missing)} -- available: {available}"
            )
        matched = [by_name[name] for name in database_names]

        metadata: dict[int, dict[str, Any]] = {}
        for db in matched:
            db_id = db["id"]
            metadata[db_id] = self.database_metadata(db_id)
            self._snapshot(run_dir, f"database_metadata_{db_id}", metadata[db_id])

        cards_raw = self._get("/api/card")
        self._snapshot(run_dir, "cards", cards_raw)
        cards = _as_list(cards_raw, "/api/card")

        dashboards_raw = self._get("/api/dashboard")
        self._snapshot(run_dir, "dashboards", dashboards_raw)
        dashboards: list[dict[str, Any]] = []
        for summary in _as_list(dashboards_raw, "/api/dashboard"):
            dash_id = summary.get("id") if isinstance(summary, dict) else None
            if not isinstance(dash_id, int):
                continue
            detail = self.get_dashboard(dash_id)
            self._snapshot(run_dir, f"dashboard_{dash_id}", detail)
            dashboards.append(detail)

        collections_raw = self._get("/api/collection")
        self._snapshot(run_dir, "collections", collections_raw)
        collections = _as_list(collections_raw, "/api/collection")

        snippets = self.list_snippets()
        self._snapshot(run_dir, "snippets", snippets)

        payload = MetabasePayload(
            metabase_version=version,
            databases=matched,
            database_metadata=metadata,
            cards=cards,
            dashboards=dashboards,
            collections=collections,
            snippets=snippets,
        )
        self._snapshot(run_dir, "payload", payload.model_dump(mode="json"))
        self._prune_cache()
        return payload

    def _new_run_dir(self) -> Path | None:
        if self.cache_dir is None:
            return None
        run_dir = self.cache_dir / datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%S-%fZ")
        run_dir.mkdir(parents=True, exist_ok=True)
        return run_dir

    @staticmethod
    def _snapshot(run_dir: Path | None, name: str, payload: Any) -> None:
        if run_dir is None:
            return
        path = run_dir / f"{name}.json"
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def _prune_cache(self) -> None:
        if self.cache_dir is None or not self.cache_dir.is_dir():
            return
        runs = sorted((p for p in self.cache_dir.iterdir() if p.is_dir()), reverse=True)
        for stale in runs[max(self.retain, 0) :]:
            shutil.rmtree(stale, ignore_errors=True)
