"""Local browser Mend orchestration, injected by the CLI across the app's I/O seam.

One expiring preview per server. Planning reads Metabase; applying consumes the stored
plan, never a client-supplied query. No background jobs, snapshot refresh or force path.
"""

import hashlib
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from stitch_lineage.config import StitchConfig, StitchConfigError, load_config
from stitch_lineage.graph.impact import column_display, diff_columns
from stitch_lineage.graph.schema import EdgeType, Graph, NodeType
from stitch_lineage.io.credentials import restore_api_key
from stitch_lineage.io.graph_store import previous_graph_path, read_graph
from stitch_lineage.io.metabase_client import MetabaseAPIError, MetabaseClient
from stitch_lineage.mend.apply import apply_plan
from stitch_lineage.mend.explain import explain_plan
from stitch_lineage.mend.models import CardOutcome, CardPlan, MendAction, MendPlan
from stitch_lineage.mend.plan import affected_card_ids, build_plan
from stitch_lineage.mend.render import format_diff
from stitch_lineage.mend_review import MendReviewError
from stitch_lineage.payloads import MetabasePayload
from stitch_lineage.resolve.metabase import collection_ids_matching


def _client(cfg: StitchConfig) -> MetabaseClient:
    return MetabaseClient(
        cfg.metabase.url, cfg.metabase.api_key, min_version=cfg.metabase.min_version
    )


@dataclass
class _Preview:
    id: str
    fingerprint: str
    deadline: float
    plan: MendPlan


class MendRuntime:
    def __init__(
        self,
        config: Path,
        graph_path: Path,
        *,
        client_factory: Callable[[StitchConfig], Any] = _client,
        clock: Callable[[], float] = time.monotonic,
        ttl: int = 900,
    ):
        self.config = config
        self.graph_path = graph_path
        self.baseline_path = previous_graph_path(graph_path)
        self.client_factory = client_factory
        self.clock = clock
        self.ttl = ttl
        self._lock = threading.Lock()
        self._preview: _Preview | None = None
        self._result: dict[str, Any] | None = None

    def _inputs(self) -> tuple[StitchConfig, Graph, Graph, str]:
        if not self.baseline_path.is_file():
            raise MendReviewError(
                "No previous graph exists. The first build establishes a baseline. After a "
                "schema change, refresh dbt artifacts, sync Metabase, and run 'stitch build' "
                "to compare the next graph. Do not rebuild just to dismiss this message.",
                409,
            )
        try:
            # Hash the bytes actually parsed, not a second file read after parsing.
            baseline_bytes = self.baseline_path.read_bytes()
            current_bytes = self.graph_path.read_bytes()
            config_before = self.config.read_bytes()
            cfg = load_config(self.config)
            if config_before != self.config.read_bytes():
                raise MendReviewError("Configuration changed while reading it. Try again.")
            baseline = Graph.model_validate_json(baseline_bytes)
            current = Graph.model_validate_json(current_bytes)
            resolved = cfg.model_dump_json().encode()
            fingerprint = hashlib.sha256(
                b"\0".join([baseline_bytes, current_bytes, config_before, resolved])
            ).hexdigest()
        except (OSError, ValueError, StitchConfigError) as exc:
            raise MendReviewError(
                "Could not read Mend's snapshots or configuration. Check stitch.yml and run "
                "'stitch doctor' before reviewing repairs.",
                503,
            ) from exc
        return cfg, baseline, current, fingerprint

    def _connected(self, cfg: StitchConfig):
        restore_api_key(cfg)
        try:
            cfg.metabase.require_env()
        except StitchConfigError as exc:
            raise MendReviewError(
                "Metabase credentials are unavailable. Run 'stitch init' to reconnect, or set "
                "the API-key environment variable before starting 'stitch serve'.",
                503,
            ) from exc
        if not cfg.metabase.api_key:
            raise MendReviewError("Metabase API key is empty. Run 'stitch init' to reconnect.", 503)
        return self.client_factory(cfg)

    def context(self) -> dict[str, Any]:
        if not self.baseline_path.is_file():
            return {
                "state": "missing-baseline",
                "current": self._stamp(self.graph_path),
                "baseline": None,
                "removed": [],
                "replacements": [],
                "allowed_actions": [],
                "type_changes": 0,
            }
        cfg, baseline, current, _ = self._inputs()
        diff = diff_columns(baseline, current)
        old_nodes = {n.node_id: n for n in baseline.nodes}
        new_nodes = {n.node_id: n for n in current.nodes}
        bound = {edge.from_ for edge in current.edges if edge.edge_type is EdgeType.BINDS_TO}
        return {
            "state": "ready" if diff.removed else "no-removed-columns",
            "current": {"file": self.graph_path.name, "generated_at": current.generated_at},
            "baseline": {"file": self.baseline_path.name, "generated_at": baseline.generated_at},
            "removed": [
                {"id": nid, "label": column_display(old_nodes, nid)} for nid in diff.removed
            ],
            "replacements": sorted(
                [
                    {
                        "id": n.node_id,
                        "label": column_display(new_nodes, n.node_id),
                        "bound": n.node_id in bound,
                    }
                    for n in current.nodes
                    if n.node_type is NodeType.COLUMN
                ],
                key=lambda n: n["label"],
            ),
            "allowed_actions": cfg.mend.auto,
            "type_changes": len(diff.type_changed),
        }

    @staticmethod
    def _stamp(path: Path) -> dict[str, Any]:
        try:
            return {"file": path.name, "generated_at": read_graph(path).generated_at}
        except (OSError, ValueError):
            return {"file": path.name, "generated_at": None}

    def preview(self, renames: dict[str, str], actions: list[str]) -> dict[str, Any]:
        if not self._lock.acquire(blocking=False):
            raise MendReviewError("Another Mend operation is running. Wait for it to finish.")
        try:
            # A failed new review cannot leave an older approval live.
            self._preview = None
            cfg, baseline, current, fingerprint = self._inputs()
            diff = diff_columns(baseline, current)
            current_columns = {n.node_id for n in current.nodes if n.node_type is NodeType.COLUMN}
            if any(
                old not in diff.removed or new not in current_columns
                for old, new in renames.items()
            ):
                raise MendReviewError(
                    "Rename selections no longer match the snapshots. Reload Mend.", 422
                )
            requested = set(actions)
            if not requested <= {"repoint", "strip", "archive"}:
                raise MendReviewError("Unknown repair action.", 422)
            if not requested <= set(cfg.mend.auto):
                raise MendReviewError(
                    "An action is disabled by mend.auto in stitch.yml. Reload Mend.", 422
                )
            auto = [a for a in MendAction if a.value in requested]
            client = self._connected(cfg)
            payload = client.fetch_all([db.metabase_name for db in cfg.metabase.databases])
            # The graph's new field may have disappeared since the last build. Remove stale
            # bindings from the planning copy so the engine's unresolved-rename guard wins.
            current = _live_bindings(current, payload)
            unreadable: dict[int, str] = {}
            cards = {card["id"]: card for card in payload.cards}
            for card_id in affected_card_ids(baseline, diff):
                try:
                    cards[card_id] = client.get_card(card_id)
                except MetabaseAPIError:
                    unreadable[card_id] = (
                        "Cannot read this report with the current API key; review it in Metabase."
                    )
                    cards.pop(card_id, None)
            payload.cards = list(cards.values())
            plan = build_plan(
                baseline,
                current,
                diff,
                payload,
                renames=renames,
                auto=auto,
                notify_only_collections=cfg.mend.notify_only_collections,
            )
            for entry in plan.cards:
                if (
                    entry.downgraded_from is not None
                    and entry.downgraded_from.value in cfg.mend.auto
                    and entry.downgraded_from.value not in requested
                    and "is not in mend.auto" in entry.reason
                ):
                    entry.reason = entry.reason.replace(
                        "is not in mend.auto", "was not selected for this review"
                    )
            planned = {card.card_id for card in plan.cards}
            nodes = {n.node_id: n for n in baseline.nodes}
            for card_id in affected_card_ids(baseline, diff):
                card = cards.get(card_id)
                if card_id not in planned:
                    name = nodes.get(f"mb_card::{card_id}")
                    plan.cards.append(
                        CardPlan(
                            card_id=card_id,
                            name=name.name if name else f"Report {card_id}",
                            action=MendAction.NOTIFY,
                            reason=unreadable.get(
                                card_id, "Report is already archived; no changes planned."
                            ),
                        )
                    )
                elif card and card.get("can_write") is False:
                    entry = next(c for c in plan.cards if c.card_id == card_id)
                    entry.action = MendAction.NOTIFY
                    entry.reason = "The API key cannot edit this report; review it in Metabase."
                    entry.after = None
                    entry.dashcards = []
                    entry.archive = False
            if self._inputs()[3] != fingerprint:
                raise MendReviewError(
                    "Snapshots or configuration changed while planning. Review again."
                )
            response = {
                "preview_id": secrets.token_urlsafe(24),
                "expires_at": (datetime.now(UTC) + timedelta(seconds=self.ttl)).isoformat(),
                "plan": plan.model_dump(mode="json"),
                "writing_count": len(plan.writing),
                "reviews": explain_plan(plan, baseline, current),
                "diffs": {
                    str(card.card_id): format_diff(card.before, card.after, card.name)
                    for card in plan.cards
                    if card.after is not None
                },
            }
            self._preview = _Preview(
                response["preview_id"],
                fingerprint,
                self.clock() + self.ttl,
                plan,
            )
            return response
        except MetabaseAPIError as exc:
            raise MendReviewError(
                "Metabase could not be read. Check the connection and API-key permissions, then "
                "review again. No repairs were applied.",
                503,
            ) from exc
        finally:
            self._lock.release()

    def apply(self, preview_id: str) -> dict[str, Any]:
        if not self._lock.acquire(blocking=False):
            raise MendReviewError("Another Mend operation is running. Do not submit again.")
        try:
            preview = self._preview
            if (
                preview is None
                or not preview_id.isascii()
                or not secrets.compare_digest(preview.id, preview_id)
            ):
                raise MendReviewError(
                    "This preview was replaced, consumed, or lost on restart. Review again."
                )
            # Consume before any I/O; errors never turn approval into an automatic retry.
            self._preview = None
            if self.clock() >= preview.deadline:
                raise MendReviewError("This preview expired. Review repairs again.", 410)
            cfg, _, _, fingerprint = self._inputs()
            if fingerprint != preview.fingerprint:
                raise MendReviewError("Snapshots or configuration changed. Review repairs again.")
            client = self._connected(cfg)
            guarded = _ReviewedWriter(client, preview.plan, cfg.mend.notify_only_collections)
            outcome = apply_plan(
                preview.plan,
                guarded,
                auto=[MendAction(action) for action in cfg.mend.auto],
                notify_only_collections=cfg.mend.notify_only_collections,
            )
            # The wrapper turns stronger browser guards into precise per-card outcomes.
            outcome.cards = [guarded.refusals.get(c.card_id, c) for c in outcome.cards]
            failed = {card.card_id for card in outcome.failures}
            result = {
                "preview_id": preview_id,
                "outcome": outcome.model_dump(mode="json"),
                # A failed rollback needs the original query in the UI, not merely a red
                # status. Retain exact review data for partial dashboard failures too.
                "recovery": {
                    str(card.card_id): {
                        "before": card.before,
                        "after": card.after,
                        "dashcards": [edit.model_dump(mode="json") for edit in card.dashcards],
                    }
                    for card in preview.plan.cards
                    if card.card_id in failed
                },
            }
            self._result = result
            return result
        except MetabaseAPIError as exc:
            raise MendReviewError(
                "Metabase could not be checked. This approval is consumed; review again.", 503
            ) from exc
        finally:
            self._lock.release()

    def result(self, preview_id: str) -> dict[str, Any]:
        if (
            self._result
            and preview_id.isascii()
            and secrets.compare_digest(self._result["preview_id"], preview_id)
        ):
            return self._result
        raise MendReviewError(
            "No completed result is available for this preview. If a request was interrupted, "
            "check Metabase before reviewing again. Do not repeat the apply request."
        )


def _live_bindings(graph: Graph, payload: MetabasePayload) -> Graph:
    live = {
        f"mb_field::{field['id']}": field
        for db in payload.database_metadata.values()
        for table in db.get("tables", [])
        for field in table.get("fields", [])
        if isinstance(field.get("id"), int) and field.get("active") is not False
    }
    nodes = {n.node_id: n for n in graph.nodes}
    return graph.model_copy(
        update={
            "edges": [
                e
                for e in graph.edges
                if e.edge_type is not EdgeType.BINDS_TO
                or (
                    e.to in live
                    and e.to in nodes
                    and str(live[e.to].get("name", "")).casefold()
                    == str(nodes[e.to].column or "").casefold()
                )
            ]
        }
    )


class _ReviewedWriter:
    """Reuse the apply engine, adding exact-query and current-collection browser guards.

    Collection permissions are fetched immediately before applying. Current card reads
    still happen inside the engine, so moves into protected/unknown collections fail shut.
    """

    def __init__(self, client: Any, plan: MendPlan, patterns: list[str]):
        self.client = client
        self.cards = {c.card_id: c for c in plan.cards}
        self.refusals: dict[int, CardOutcome] = {}
        self.patterns = patterns
        collections = client.list_collections() if patterns and plan.writing else []
        self.protected = collection_ids_matching(collections, patterns)
        self.known_collections = {c.get("id") for c in collections}

    def __getattr__(self, name: str):
        return getattr(self.client, name)

    def get_card(self, card_id: int) -> dict[str, Any]:
        current = self.client.get_card(card_id)
        reviewed = self.cards[card_id]
        collection = current.get("collection_id")
        detail = None
        status = "skipped"
        valid_collection = (
            collection is None
            or collection == "root"
            or (isinstance(collection, int) and not isinstance(collection, bool))
        )
        if self.patterns and (
            "collection_id" not in current
            or not valid_collection
            or collection in self.protected
            or (collection not in (None, "root") and collection not in self.known_collections)
        ):
            detail = "The report is now in a protected or unknown collection; no write attempted."
        elif current.get("archived"):
            detail = "The report was archived after review; no write attempted."
        elif current.get("dataset_query") != reviewed.before:
            status = "stale"
            detail = "The report query changed after review; review repairs again."
        if detail:
            self.refusals[card_id] = CardOutcome(
                card_id=card_id,
                name=reviewed.name,
                action=reviewed.action,
                status=status,
                detail=detail,
            )
            return {**current, "can_write": False}
        return current
