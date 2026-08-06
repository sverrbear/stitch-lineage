"""Metabase API client: HTTP + retry + payload caching (SPEC.md section 7.2).

This is the ONLY module in the codebase allowed to import requests (enforced by
import-linter). All methods return raw, untransformed API payloads; resolution
happens in resolve/metabase.py against the MetabasePayload bundle.
"""

from pathlib import Path
from typing import Any

from stitch_lineage.payloads import MetabasePayload


class MetabaseAPIError(Exception):
    """Metabase unreachable, auth rejected, version below minimum, or a non-2xx response."""


class MetabaseClient:
    """Talks to one Metabase instance via API-key auth (Metabase 49+).

    Auth is the x-api-key header. When cache_dir is set, every fetch_all run snapshots
    the raw responses to {cache_dir}/{timestamp}/ before parsing (caller gitignores it;
    retention is the caller's concern -- config output.retain_cache_runs, default 3).
    Cached payloads are the unit-test fixtures for resolve/metabase.py.
    """

    def __init__(
        self,
        url: str,
        api_key: str,
        cache_dir: Path | None = None,
        min_version: str = "0.49",
    ) -> None:
        self.url = url.rstrip("/")
        self.api_key = api_key
        self.cache_dir = cache_dir
        self.min_version = min_version

    def assert_version(self) -> str:
        """Fetch the instance version and return it.

        Raises:
            MetabaseAPIError: version below min_version (API-key auth floor), with the
                detected and required versions in the message.
        """
        raise NotImplementedError

    def list_databases(self) -> list[dict[str, Any]]:
        """GET /api/database -- raw database payloads, used to locate warehouses by display name."""
        raise NotImplementedError

    def database_metadata(self, db_id: int) -> dict[str, Any]:
        """GET /api/database/{db_id}/metadata?include_hidden=true -- the field_id map."""
        raise NotImplementedError

    def list_cards(self) -> list[dict[str, Any]]:
        """GET /api/card -- all cards with dataset_query, collection_id, creator, archived."""
        raise NotImplementedError

    def list_dashboards(self) -> list[dict[str, Any]]:
        """GET /api/dashboard -- dashboard listing (no dashcards; use get_dashboard)."""
        raise NotImplementedError

    def get_dashboard(self, dash_id: int) -> dict[str, Any]:
        """GET /api/dashboard/{dash_id} -- full payload including dashcards -> card ids."""
        raise NotImplementedError

    def list_collections(self) -> list[dict[str, Any]]:
        """GET /api/collection -- the collection tree, for exclude_collections filtering."""
        raise NotImplementedError

    def fetch_all(self, database_names: list[str]) -> MetabasePayload:
        """Fetch everything resolve_metabase needs, in one bundle.

        database_names are Metabase display names (config metabase.databases[].metabase_name);
        only those databases' metadata is fetched. Calls assert_version first, snapshots
        raw payloads to cache_dir when set, and fills MetabasePayload.metabase_version.

        Raises:
            MetabaseAPIError: any transport/auth/version failure. A configured database
                name that matches nothing is an error here, not a silent skip.
        """
        raise NotImplementedError
