"""Own a fresh Docker network, warehouse and Metabase for one compatibility run.

All names and credentials are synthetic. The API URL comes only from the container
we create; environment variables cannot redirect this harness to another instance.
"""

import json
import subprocess
import time
import warnings
from pathlib import Path
from uuid import uuid4

import requests

from stitch_lineage.io.metabase_client import MetabaseClient

VERSIONS = ("v0.49.25.2", "v0.56.25.1", "v0.64.1")
WAREHOUSE_IMAGE = "postgres:16.11-alpine"
SQL = """
CREATE TABLE public.compat_orders (
    id integer PRIMARY KEY, amount integer, amount_usd integer, region text
);
INSERT INTO public.compat_orders VALUES (1, 10, 100, 'West'), (2, 20, 200, 'East');
"""


def docker(*args, input=None, check=True, include_stderr=False, timeout=180):
    result = subprocess.run(
        ["docker", *args], input=input, text=True, capture_output=True, timeout=timeout, check=False
    )
    if check and result.returncode:
        raise RuntimeError(f"docker {args[0]} failed: {result.stderr}")
    return (result.stdout + (result.stderr if include_stderr else "")).strip()


def wait_until(probe, *, timeout=420):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            value = probe()
            if value:
                return value
        except (requests.RequestException, RuntimeError) as exc:
            last = exc
        time.sleep(2)
    raise RuntimeError(f"Timed out waiting for disposable Metabase: {last}")


class DisposableMetabase:
    def __init__(self, version: str, artifacts: Path):
        if version not in VERSIONS:
            raise ValueError("Use a reviewed, pinned version from VERSIONS")
        self.version = version
        self.artifacts = artifacts
        self.name = f"stitch-compat-{uuid4().hex[:12]}"
        self.warehouse = f"{self.name}-warehouse"
        self.url = ""
        self.session = requests.Session()
        self.session.trust_env = False

    def __enter__(self):
        self.artifacts.mkdir(parents=True, exist_ok=True)
        try:
            docker("network", "create", self.name)
            docker(
                "run",
                "--detach",
                "--name",
                self.warehouse,
                "--network",
                self.name,
                "--env",
                "POSTGRES_PASSWORD=synthetic-compat-only",
                "--env",
                "POSTGRES_DB=compat",
                WAREHOUSE_IMAGE,
            )
            wait_until(
                lambda: (
                    "accepting connections"
                    in docker("exec", self.warehouse, "pg_isready", "-U", "postgres", check=False)
                ),
                timeout=90,
            )
            docker(
                "exec",
                "-i",
                self.warehouse,
                "psql",
                "-U",
                "postgres",
                "-d",
                "compat",
                "-v",
                "ON_ERROR_STOP=1",
                input=SQL,
            )
            docker(
                "run",
                "--detach",
                "--name",
                self.name,
                "--network",
                self.name,
                "--publish",
                "127.0.0.1::3000",
                "--env",
                "MB_ANON_TRACKING_ENABLED=false",
                "--env",
                "MB_CHECK_FOR_UPDATES=false",
                "--env",
                "MB_LOAD_SAMPLE_CONTENT=false",
                "--env",
                "JAVA_TOOL_OPTIONS=-Xmx1536m",
                f"metabase/metabase:{self.version}",
            )
            port = docker("port", self.name, "3000/tcp").rsplit(":", 1)[1]
            self.url = f"http://127.0.0.1:{int(port)}"
            wait_until(lambda: self.request("GET", "/api/health").get("status") == "ok")
            self._setup()
            return self
        except BaseException as exc:
            self.__exit__(type(exc), exc, exc.__traceback__)
            raise

    def __exit__(self, exc_type, *_):
        # Only our unique names are removed. Never prune or touch user containers.
        errors = []
        try:
            logs = docker("logs", self.name, check=False, include_stderr=True, timeout=30)
            (self.artifacts / "metabase.log").write_text(logs, encoding="utf-8")
        except Exception as exc:
            errors.append(str(exc))
        finally:
            for args in (
                ("rm", "--force", self.name, self.warehouse),
                ("network", "rm", self.name),
            ):
                try:
                    docker(*args, timeout=30)
                except Exception as exc:
                    errors.append(str(exc))
            self.session.close()
            for name in ("client", "reader"):
                if hasattr(self, name):
                    getattr(self, name)._session.close()
        if errors:
            message = f"Compatibility cleanup for {self.name}: {'; '.join(errors)}"
            if exc_type is None:
                raise RuntimeError(message)
            warnings.warn(message, RuntimeWarning, stacklevel=2)

    def request(self, method, path, body=None):
        response = self.session.request(method, self.url + path, json=body, timeout=60)
        if not response.ok:
            # Do not print setup/session/API-key response bodies containing credentials.
            raise RuntimeError(
                f"{method} {path}: HTTP {response.status_code}: "
                f"{response.text if path.startswith('/api/card') else ''}"
            )
        return response.json() if response.content else None

    def _setup(self):
        token = self.request("GET", "/api/session/properties")["setup-token"]
        if not token:
            raise RuntimeError("Refusing to seed an already configured Metabase")
        session = self.request(
            "POST",
            "/api/setup",
            {
                "token": token,
                "user": {
                    "email": "stitch-compat@example.invalid",
                    "password": "Synthetic1234!",
                    "first_name": "Stitch",
                    "last_name": "Compatibility",
                },
                "prefs": {"site_name": "Stitch compatibility", "allow_tracking": False},
            },
        )
        self.session.headers["X-Metabase-Session"] = session["id"]
        groups = self.request("GET", "/api/permissions/group")
        admin = next(g["id"] for g in groups if g["name"] == "Administrators")
        everyone = next(g["id"] for g in groups if g["name"] == "All Users")
        self.client = self._client(admin, "Stitch write test")
        self.reader = self._client(everyone, "Stitch read test")
        database = self.request(
            "POST",
            "/api/database",
            {
                "name": "Stitch synthetic warehouse",
                "engine": "postgres",
                "details": {
                    "host": self.warehouse,
                    "port": 5432,
                    "dbname": "compat",
                    "user": "postgres",
                    "password": "synthetic-compat-only",
                    "ssl": False,
                },
                "is_full_sync": True,
            },
        )
        self.database_id = database["id"]
        self.request("POST", f"/api/database/{self.database_id}/sync_schema")

        def table_ready():
            metadata = self.client.database_metadata(self.database_id)
            return next(
                (
                    t
                    for t in metadata.get("tables", [])
                    if t["name"] == "compat_orders" and len(t.get("fields", [])) == 4
                ),
                None,
            )

        self.table = wait_until(table_ready, timeout=180)
        self.fields = {field["name"]: field["id"] for field in self.table["fields"]}
        self.collection = self.request(
            "POST",
            "/api/collection",
            {
                "name": "Synthetic compatibility",
                "color": "#509EE3",
            },
        )["id"]
        permissions = self.request("GET", "/api/collection/graph")
        permissions["groups"][str(everyone)][str(self.collection)] = "read"
        self.request("PUT", "/api/collection/graph", permissions)
        self.evidence = {"version": self.client.assert_version(), "images": {}}
        if self.evidence["version"] != self.version:
            raise RuntimeError("The observed Metabase version does not match its pinned image tag")
        for image in (f"metabase/metabase:{self.version}", WAREHOUSE_IMAGE):
            self.evidence["images"][image] = json.loads(
                docker("image", "inspect", image, "--format", "{{json .RepoDigests}}")
            )
        self.save_evidence()

    def _client(self, group, name):
        key = self.request("POST", "/api/api-key", {"name": name, "group_id": group})
        client = MetabaseClient(self.url, key["unmasked_key"], timeout=60, backoff=0)
        client._session.trust_env = False
        return client

    def save_evidence(self):
        (self.artifacts / "evidence.json").write_text(
            json.dumps(self.evidence, indent=2) + "\n", encoding="utf-8"
        )

    def card(self, name, query):
        return self.request(
            "POST",
            "/api/card",
            {
                "name": name,
                "display": "table",
                "collection_id": self.collection,
                "dataset_query": query,
                "visualization_settings": {},
            },
        )

    def query(self, field="amount"):
        return {
            "database": self.database_id,
            "type": "query",
            "query": {
                "source-table": self.table["id"],
                "fields": [["field", self.fields[field], None]],
            },
        }

    def native(self, sql):
        return {
            "database": self.database_id,
            "type": "native",
            "native": {"query": sql, "template-tags": {}},
        }
