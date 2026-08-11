"""Applying a mend plan, one reversible write at a time (SPEC.md section 14).

The safety of this feature is not an approval button -- it is the order of operations in
this file, per card:

  1. **Staleness guard.** Re-read the card. If its `updated_at` moved since the plan was
     built, a human edited it while we were deciding; their edit outranks ours and the card
     is skipped as `stale`. Same principle as `stitch apply`'s dirty-file refusal.
  2. **Snapshot.** Record the revision id to come back to.
  3. **Write.** One PUT, carrying only what changed.
  4. **Validate.** Re-execute the card. Metabase reports a broken query inside a 202 body as
     readily as by status code, so both are read.
  5. **Revert on failure.** Through the revisions API, or by restoring the `before` query
     the plan captured when the instance will not serve revisions.

What validation cannot catch is a `strip` that removed a clause the card needed to mean
what it said -- it runs, and quietly answers a different question. That is why strip is the
action teams can dial out of `mend.auto`, and why the summary names every strip first.
"""

from collections.abc import Callable, Sequence
from typing import Any, Protocol

from stitch_lineage.io.metabase_client import MetabaseAPIError
from stitch_lineage.mend.models import CardOutcome, CardPlan, MendAction, MendOutcome, MendPlan
from stitch_lineage.mend.render import format_diff

_OK_STATUSES = frozenset({"completed", "ok", "success"})


class CardWriter(Protocol):
    """The slice of the Metabase client the apply loop uses.

    A Protocol rather than the client itself so the loop is testable against a fake that
    records calls: the tests for revert-on-failure must never be one typo away from a real
    PUT at someone's BI estate.
    """

    def get_card(self, card_id: int) -> dict[str, Any]: ...

    def update_card(self, card_id: int, changes: dict[str, Any]) -> dict[str, Any]: ...

    def run_card_query(self, card_id: int) -> dict[str, Any]: ...

    def latest_card_revision(self, card_id: int) -> int | None: ...

    def revert_card(self, card_id: int, revision_id: int) -> dict[str, Any]: ...

    def get_dashboard(self, dash_id: int) -> dict[str, Any]: ...

    def update_dashcards(self, dash_id: int, dashcards: list[dict[str, Any]]) -> dict[str, Any]: ...


Log = Callable[[str], None]


def query_error(result: Any) -> str | None:
    """Metabase's complaint about a re-executed card, or None if it ran.

    A failed query can arrive three ways: a non-2xx (raised before this is called), a
    `status` that is not a success word, or an `error` key beside a perfectly happy status.
    Treating only the status code as authoritative is how a broken card gets recorded as
    repaired.
    """
    if not isinstance(result, dict):
        return "unreadable query result from Metabase"
    error = result.get("error")
    status = result.get("status")
    if isinstance(status, str) and status.casefold() not in _OK_STATUSES:
        detail = error if isinstance(error, str) and error else status
        return str(detail)
    if error:
        return str(error)
    return None


def apply_plan(
    plan: MendPlan,
    client: CardWriter,
    *,
    force: bool = False,
    log: Log | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> MendOutcome:
    """Apply every writing entry in `plan`, in the order the plan lists them.

    The plan's order is upstream-first, so a card sourcing another is repaired after the
    card it reads and validation is never asked to pass through an unrepaired query.

    `force` overrides the staleness guard. It exists for the operator who knows the drift
    is theirs; it is not something CI should pass.

    on_progress, when given, is called as on_progress(done, total) after each card, the
    same shape the resolvers use. It counts every entry in the plan rather than only the
    writing ones, so the total matches the plan the operator was shown.
    """
    emit: Log = log or (lambda _line: None)
    outcome = MendOutcome(forced=force)
    total = len(plan.cards)
    for done, card in enumerate(plan.cards, start=1):
        if not card.writes:
            outcome.cards.append(
                CardOutcome(
                    card_id=card.card_id,
                    name=card.name,
                    action=card.action,
                    status="notify",
                    detail=card.reason,
                )
            )
        else:
            outcome.cards.append(_apply_card(card, client, force=force, emit=emit))
        if on_progress is not None:
            on_progress(done, total)
    return outcome


def _apply_card(card: CardPlan, client: CardWriter, *, force: bool, emit: Log) -> CardOutcome:
    def result(status: str, detail: str | None = None, **extra: Any) -> CardOutcome:
        return CardOutcome(
            card_id=card.card_id,
            name=card.name,
            action=card.action,
            status=status,  # type: ignore[arg-type]
            detail=detail,
            **extra,
        )

    try:
        current = client.get_card(card.card_id)
    except MetabaseAPIError as exc:
        return result("failed", f"could not re-read the card before writing: {exc}")

    if current.get("can_write") is False:
        return result("skipped", "the API key does not have write access to this card")

    observed = current.get("updated_at")
    if not force and card.updated_at and isinstance(observed, str) and observed != card.updated_at:
        return result(
            "stale",
            f"edited since the plan was built ({card.updated_at} -> {observed}) "
            "-- a human's edit outranks ours; re-plan to include it",
        )

    revision_id = card.revision_id
    if revision_id is None:
        try:
            revision_id = client.latest_card_revision(card.card_id)
        except MetabaseAPIError:
            revision_id = None

    if card.action is MendAction.ARCHIVE:
        emit(f"#{card.card_id} {card.name}: archived: false -> true")
        try:
            client.update_card(card.card_id, {"archived": True})
        except MetabaseAPIError as exc:
            return result("failed", f"archive failed: {exc}")
        # no re-execution: archiving does not change the query, so there is nothing a run
        # could prove, and Metabase's archive is already reversible from the UI
        return result("archived")

    if card.after is None:
        return result("skipped", "plan carries no rewritten query")

    emit(format_diff(card.before, card.after, f"#{card.card_id} {card.name}"))
    try:
        client.update_card(card.card_id, {"dataset_query": card.after})
    except MetabaseAPIError as exc:
        return result("failed", f"write failed: {exc}")

    try:
        failure = query_error(client.run_card_query(card.card_id))
    except MetabaseAPIError as exc:
        failure = str(exc)
    if failure is not None:
        reverted, revert_detail = _revert(card, client, revision_id, emit)
        detail = f"validation failed: {failure}"
        if revert_detail:
            detail = f"{detail}; {revert_detail}"
        return result("failed", detail, reverted=reverted)

    written, problems = _write_dashcards(card, client, emit)
    if problems:
        # the card itself is repaired and proven to run; only its filter wiring is not.
        # Reverting a validated repair because a dashboard write failed helps nobody, so
        # the card stays fixed and the dashboard is named in the summary instead.
        return result("failed", "; ".join(problems), dashcards_written=written)
    return result("applied", dashcards_written=written)


def _revert(
    card: CardPlan, client: CardWriter, revision_id: int | None, emit: Log
) -> tuple[bool, str | None]:
    """Undo the write. Revisions API first, captured `before` query second.

    Both paths are recorded honestly: a revert that itself failed is the one outcome a
    human must act on immediately, so it is never swallowed.
    """
    if revision_id is not None:
        try:
            client.revert_card(card.card_id, revision_id)
        except MetabaseAPIError as exc:
            emit(f"#{card.card_id}: revert to revision {revision_id} failed: {exc}")
        else:
            emit(f"#{card.card_id}: reverted to revision {revision_id}")
            return True, f"reverted to revision {revision_id}"
    if card.before is None:
        return False, "COULD NOT REVERT: no revision id and no captured query"
    try:
        client.update_card(card.card_id, {"dataset_query": card.before})
    except MetabaseAPIError as exc:
        return False, f"COULD NOT REVERT: {exc}"
    emit(f"#{card.card_id}: restored the query captured at plan time")
    return True, "restored the query captured at plan time"


def _write_dashcards(card: CardPlan, client: CardWriter, emit: Log) -> tuple[int, list[str]]:
    """Rewrite `parameter_mappings` on the dashcards this card's repair touched.

    The dashboard is re-read immediately before writing and only the named dashcards are
    changed, because PUT /api/dashboard takes the whole dashcards array -- sending back the
    copy the plan captured would silently revert anything else that moved on that dashboard
    since.
    """
    if not card.dashcards:
        return 0, []
    written = 0
    problems: list[str] = []
    by_dashboard: dict[int, list[Any]] = {}
    for edit in card.dashcards:
        by_dashboard.setdefault(edit.dashboard_id, []).append(edit)
    for dash_id, edits in sorted(by_dashboard.items()):
        try:
            dashboard = client.get_dashboard(dash_id)
        except MetabaseAPIError as exc:
            problems.append(f"dashboard {dash_id} could not be read: {exc}")
            continue
        dashcards = dashboard.get("dashcards")
        if not isinstance(dashcards, list):
            dashcards = dashboard.get("ordered_cards")
        if not isinstance(dashcards, list):
            problems.append(f"dashboard {dash_id} returned no dashcards to update")
            continue
        wanted = {edit.dashcard_id: edit for edit in edits}
        touched = 0
        for dashcard in dashcards:
            if not isinstance(dashcard, dict):
                continue
            edit = wanted.get(dashcard.get("id"))
            if edit is None:
                continue
            dashcard["parameter_mappings"] = edit.after
            touched += 1
            emit(
                f"#{card.card_id} on '{edit.dashboard_name}': dashcard {edit.dashcard_id} "
                f"parameter_mappings {len(edit.before)} -> {len(edit.after)}"
            )
        if not touched:
            problems.append(
                f"dashboard {dash_id}: dashcard(s) {sorted(wanted)} are no longer on it"
            )
            continue
        try:
            client.update_dashcards(dash_id, dashcards)
        except MetabaseAPIError as exc:
            problems.append(f"dashboard {dash_id} write failed: {exc}")
            continue
        written += touched
    return written, problems


def failed_count(outcome: MendOutcome) -> int:
    """How many cards ended `failed` -- what the CLI turns into a non-zero exit."""
    return len(outcome.failures)


def stale_cards(outcome: MendOutcome) -> Sequence[CardOutcome]:
    return outcome.with_status("stale")
