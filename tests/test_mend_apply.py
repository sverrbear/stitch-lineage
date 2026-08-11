"""The apply loop: staleness guard, snapshot, validate, revert (issue #143).

Every test here runs against FakeMetabase. Nothing in this file may reach a network, and
nothing in this file may construct a real MetabaseClient: the behaviour under test is what
happens when a write goes wrong, and proving that must never be one typo away from writing
to somebody's BI estate.
"""

import mend_scenario as scenario
import pytest

from stitch_lineage.io.metabase_client import MetabaseAPIError
from stitch_lineage.mend.apply import apply_plan, query_error
from stitch_lineage.mend.models import (
    CardPlan,
    DashcardEdit,
    MendAction,
    MendPlan,
)


class FakeMetabase:
    """A Metabase that records what it was asked to do and can be told to misbehave."""

    def __init__(
        self,
        *,
        cards: dict[int, dict] | None = None,
        query_results: dict[int, dict] | None = None,
        revisions: dict[int, int | None] | None = None,
        dashboards: dict[int, dict] | None = None,
        fail_on: tuple[str, ...] = (),
    ) -> None:
        self.cards = cards or {}
        self.query_results = query_results or {}
        self.revisions = revisions or {}
        self.dashboards = dashboards or {}
        self.fail_on = fail_on
        self.writes: list[tuple[int, dict]] = []
        self.reverts: list[tuple[int, int]] = []
        self.dashboard_writes: list[tuple[int, list[dict]]] = []
        self.queries_run: list[int] = []

    def _maybe_fail(self, what: str) -> None:
        if what in self.fail_on:
            raise MetabaseAPIError(f"HTTP 500: {what} exploded")

    def get_card(self, card_id: int) -> dict:
        self._maybe_fail("get_card")
        return self.cards.get(card_id, {"id": card_id, "updated_at": scenario.UPDATED_AT})

    def update_card(self, card_id: int, changes: dict) -> dict:
        self._maybe_fail("update_card")
        restoring = any(existing == card_id for existing, _ in self.writes)
        if "revert" in self.fail_on and restoring and changes.get("dataset_query"):
            # a second dataset_query write to the same card is the restore path
            raise MetabaseAPIError("HTTP 500: restore exploded")
        self.writes.append((card_id, changes))
        return {"id": card_id}

    def run_card_query(self, card_id: int) -> dict:
        self._maybe_fail("run_card_query")
        self.queries_run.append(card_id)
        return self.query_results.get(card_id, {"status": "completed", "data": {"rows": []}})

    def latest_card_revision(self, card_id: int) -> int | None:
        self._maybe_fail("latest_card_revision")
        return self.revisions.get(card_id)

    def revert_card(self, card_id: int, revision_id: int) -> dict:
        self._maybe_fail("revert_card")
        self.reverts.append((card_id, revision_id))
        return {"id": card_id}

    def get_dashboard(self, dash_id: int) -> dict:
        self._maybe_fail("get_dashboard")
        return self.dashboards.get(dash_id, {"id": dash_id, "dashcards": []})

    def update_dashcards(self, dash_id: int, dashcards: list[dict]) -> dict:
        self._maybe_fail("update_dashcards")
        self.dashboard_writes.append((dash_id, dashcards))
        return {"id": dash_id}


def strip_card(**overrides) -> CardPlan:
    defaults = {
        "card_id": 402,
        "name": "Orders, promo cohort",
        "action": MendAction.STRIP,
        "reason": "promo_code removed from a filter",
        "updated_at": scenario.UPDATED_AT,
        "revision_id": 9402,
        "before": {"type": "query", "query": {"source-table": 5, "filter": ["=", 1, 2]}},
        "after": {"type": "query", "query": {"source-table": 5}},
        "removed_clauses": ["filter -> fct_orders.promo_code"],
    }
    return CardPlan(**{**defaults, **overrides})


def plan_of(*cards: CardPlan) -> MendPlan:
    auto = [MendAction.REPOINT, MendAction.STRIP, MendAction.ARCHIVE]
    return MendPlan(auto=auto, cards=list(cards))


# --------------------------------------------------------------------------------------
# query_error: a broken card must never be recorded as repaired
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "result",
    [
        {"status": "failed", "error": "Column PROMO_CODE not found"},
        {"status": "failed"},
        {"status": "completed", "error": "something still went wrong"},
        {"error": "no status at all"},
        "not even a dict",
        None,
    ],
)
def test_query_error_catches_every_way_metabase_reports_a_failure(result):
    assert query_error(result) is not None


@pytest.mark.parametrize(
    "result",
    [{"status": "completed", "data": {}}, {"status": "ok"}, {"data": {"rows": []}}, {}],
)
def test_query_error_is_silent_on_success(result):
    assert query_error(result) is None


# --------------------------------------------------------------------------------------
# the happy path
# --------------------------------------------------------------------------------------


def test_a_strip_writes_the_query_then_validates_it():
    card = strip_card()
    client = FakeMetabase()
    outcome = apply_plan(plan_of(card), client)
    assert [entry.status for entry in outcome.cards] == ["applied"]
    assert client.writes == [(402, {"dataset_query": card.after})]
    assert client.queries_run == [402]
    assert client.reverts == []


def test_the_written_diff_is_logged():
    lines: list[str] = []
    apply_plan(plan_of(strip_card()), FakeMetabase(), log=lines.append)
    body = "\n".join(lines)
    assert "#402 Orders, promo cohort (before)" in body
    assert '-      "filter"' in body or '"filter"' in body


def test_an_archive_sets_the_flag_and_does_not_re_execute():
    card = strip_card(action=MendAction.ARCHIVE, archive=True, after=None)
    client = FakeMetabase()
    outcome = apply_plan(plan_of(card), client)
    assert [entry.status for entry in outcome.cards] == ["archived"]
    assert client.writes == [(402, {"archived": True})]
    assert client.queries_run == []


def test_notify_entries_are_reported_and_never_written():
    card = CardPlan(
        card_id=405,
        name="Scratch revenue check",
        action=MendAction.NOTIFY,
        reason="collection is notify-only",
    )
    client = FakeMetabase()
    outcome = apply_plan(plan_of(card), client)
    assert [entry.status for entry in outcome.cards] == ["notify"]
    assert outcome.cards[0].detail == "collection is notify-only"
    assert client.writes == []
    assert client.queries_run == []


# --------------------------------------------------------------------------------------
# the staleness guard
# --------------------------------------------------------------------------------------


def test_a_card_edited_since_the_plan_is_skipped_as_stale():
    client = FakeMetabase(cards={402: {"id": 402, "updated_at": "2026-08-11T12:00:00Z"}})
    outcome = apply_plan(plan_of(strip_card()), client)
    assert [entry.status for entry in outcome.cards] == ["stale"]
    assert "outranks ours" in outcome.cards[0].detail
    assert client.writes == []


def test_force_overrides_the_staleness_guard():
    client = FakeMetabase(cards={402: {"id": 402, "updated_at": "2026-08-11T12:00:00Z"}})
    outcome = apply_plan(plan_of(strip_card()), client, force=True)
    assert [entry.status for entry in outcome.cards] == ["applied"]
    assert outcome.forced is True
    assert client.writes


def test_a_card_with_no_observed_updated_at_is_not_treated_as_stale():
    client = FakeMetabase(cards={402: {"id": 402}})
    outcome = apply_plan(plan_of(strip_card()), client)
    assert [entry.status for entry in outcome.cards] == ["applied"]


def test_a_card_the_key_cannot_write_is_skipped_not_failed():
    client = FakeMetabase(
        cards={402: {"id": 402, "updated_at": scenario.UPDATED_AT, "can_write": False}}
    )
    outcome = apply_plan(plan_of(strip_card()), client)
    assert [entry.status for entry in outcome.cards] == ["skipped"]
    assert client.writes == []
    assert outcome.failures == []


def test_a_card_that_cannot_be_re_read_fails_before_writing_anything():
    client = FakeMetabase(fail_on=("get_card",))
    outcome = apply_plan(plan_of(strip_card()), client)
    assert [entry.status for entry in outcome.cards] == ["failed"]
    assert client.writes == []


# --------------------------------------------------------------------------------------
# revert on validation failure
# --------------------------------------------------------------------------------------


BROKEN = {"status": "failed", "error": "Column PROMO_CODE does not exist"}


def test_a_query_that_does_not_run_is_reverted_through_the_revisions_api():
    client = FakeMetabase(query_results={402: BROKEN})
    outcome = apply_plan(plan_of(strip_card()), client)
    entry = outcome.cards[0]
    assert entry.status == "failed"
    assert entry.reverted is True
    assert client.reverts == [(402, 9402)]
    assert "Column PROMO_CODE does not exist" in entry.detail
    assert "reverted to revision 9402" in entry.detail


def test_without_a_revision_id_the_captured_query_is_restored_instead():
    card = strip_card(revision_id=None)
    client = FakeMetabase(query_results={402: BROKEN})
    outcome = apply_plan(plan_of(card), client)
    assert outcome.cards[0].reverted is True
    assert client.reverts == []
    assert client.writes[-1] == (402, {"dataset_query": card.before})


def test_a_revision_id_missing_from_the_plan_is_fetched_before_the_write():
    card = strip_card(revision_id=None)
    client = FakeMetabase(revisions={402: 7777}, query_results={402: BROKEN})
    apply_plan(plan_of(card), client)
    assert client.reverts == [(402, 7777)]


def test_a_failed_revert_says_so_loudly():
    card = strip_card(revision_id=None)
    client = FakeMetabase(query_results={402: BROKEN}, fail_on=("revert",))
    outcome = apply_plan(plan_of(card), client)
    entry = outcome.cards[0]
    assert entry.status == "failed"
    assert entry.reverted is False
    assert "COULD NOT REVERT" in entry.detail


def test_a_revisions_api_revert_that_fails_falls_back_to_the_captured_query():
    card = strip_card()
    client = FakeMetabase(query_results={402: BROKEN}, fail_on=("revert_card",))
    outcome = apply_plan(plan_of(card), client)
    assert outcome.cards[0].reverted is True
    assert client.writes[-1] == (402, {"dataset_query": card.before})


def test_a_validation_call_that_errors_is_treated_as_a_failure():
    client = FakeMetabase(fail_on=("run_card_query",))
    outcome = apply_plan(plan_of(strip_card()), client)
    assert outcome.cards[0].status == "failed"
    assert client.reverts == [(402, 9402)]


def test_a_write_that_fails_does_not_get_validated_or_reverted():
    client = FakeMetabase(fail_on=("update_card",))
    outcome = apply_plan(plan_of(strip_card()), client)
    assert outcome.cards[0].status == "failed"
    assert client.queries_run == []
    assert client.reverts == []


# --------------------------------------------------------------------------------------
# dashcards
# --------------------------------------------------------------------------------------


def _dashcard_plan() -> CardPlan:
    return strip_card(
        dashcards=[
            DashcardEdit(
                dashboard_id=10,
                dashboard_name="Order operations",
                dashcard_id=52,
                before=[{"parameter_id": "p-promo"}, {"parameter_id": "p-created"}],
                after=[{"parameter_id": "p-created"}],
            )
        ]
    )


def _dashboard() -> dict:
    return {
        "id": 10,
        "dashcards": [
            {"id": 51, "card_id": 401, "parameter_mappings": [{"parameter_id": "keep-me"}]},
            {"id": 52, "card_id": 402, "parameter_mappings": [{"parameter_id": "p-promo"}]},
        ],
    }


def test_dashcard_mappings_are_written_with_the_rest_of_the_dashboard_intact():
    client = FakeMetabase(dashboards={10: _dashboard()})
    outcome = apply_plan(plan_of(_dashcard_plan()), client)
    assert outcome.cards[0].status == "applied"
    assert outcome.cards[0].dashcards_written == 1
    dash_id, written = client.dashboard_writes[0]
    assert dash_id == 10
    by_id = {dashcard["id"]: dashcard for dashcard in written}
    assert by_id[52]["parameter_mappings"] == [{"parameter_id": "p-created"}]
    # the other dashcard was read back from Metabase and passed through untouched
    assert by_id[51]["parameter_mappings"] == [{"parameter_id": "keep-me"}]


def test_a_dashcard_that_moved_off_the_dashboard_is_reported_without_reverting_the_card():
    client = FakeMetabase(dashboards={10: {"id": 10, "dashcards": []}})
    outcome = apply_plan(plan_of(_dashcard_plan()), client)
    entry = outcome.cards[0]
    assert entry.status == "failed"
    assert "no longer on it" in entry.detail
    # the card itself is repaired and proved it runs; reverting that helps nobody
    assert client.reverts == []
    assert client.writes == [(402, {"dataset_query": _dashcard_plan().after})]


def test_a_dashboard_write_failure_is_reported_on_the_card():
    client = FakeMetabase(dashboards={10: _dashboard()}, fail_on=("update_dashcards",))
    outcome = apply_plan(plan_of(_dashcard_plan()), client)
    assert outcome.cards[0].status == "failed"
    assert "write failed" in outcome.cards[0].detail


# --------------------------------------------------------------------------------------
# the run as a whole
# --------------------------------------------------------------------------------------


def test_cards_are_applied_in_the_order_the_plan_lists_them():
    first = strip_card(card_id=401, name="upstream")
    second = strip_card(card_id=406, name="downstream", depends_on=[401])
    client = FakeMetabase()
    apply_plan(plan_of(first, second), client)
    assert [card_id for card_id, _ in client.writes] == [401, 406]


def test_one_failure_does_not_stop_the_rest_of_the_plan():
    client = FakeMetabase(query_results={402: BROKEN})
    outcome = apply_plan(plan_of(strip_card(), strip_card(card_id=404, name="other")), client)
    assert [entry.status for entry in outcome.cards] == ["failed", "applied"]
    assert len(outcome.failures) == 1


def test_a_plan_entry_with_nothing_to_write_is_skipped():
    card = strip_card(after=None, action=MendAction.STRIP)
    # writes is False, so the loop reports it as notify rather than inventing a write
    outcome = apply_plan(plan_of(card), FakeMetabase())
    assert outcome.cards[0].status == "notify"


def test_an_empty_plan_produces_an_empty_outcome():
    outcome = apply_plan(MendPlan(), FakeMetabase())
    assert outcome.cards == []
    assert outcome.failures == []
