"""Data contracts for `stitch mend` -- the plan file and the apply outcome (SPEC.md section 14).

The plan is a file a human reads in Slack and a machine applies minutes later, so every
field here is either something the notice renders or something the apply loop needs to
write safely. Nothing volatile lives in it: no generated_at, no wall clock, no ordering
that depends on dict iteration -- the same graphs and the same rename map must produce a
byte-identical plan, because that is what makes "the plan IS the dry run" true.
"""

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


class MendAction(StrEnum):
    """What mend will do to one card. Exactly one action per card.

    A card whose repair needs both a repoint and a clause removal is labelled by the
    most consequential of the two (`strip`), and its diff carries both: leaving the dead
    reference behind to keep the label pure would ship a query that does not run.
    """

    REPOINT = "repoint"
    STRIP = "strip"
    ARCHIVE = "archive"
    NOTIFY = "notify"


#: The actions `mend.auto` may name. `notify` is the absence of a write, not an autonomy
#: level, so it is not configurable -- see config.MendConfig.
AUTO_ACTIONS = (MendAction.REPOINT, MendAction.STRIP, MendAction.ARCHIVE)


class DeadRef(BaseModel):
    """One reference, inside one card, to a column the warehouse no longer has.

    `field_id` is the Metabase field id the card referenced (None for a by-name ref that
    only ever named a column). `clauses` are the shape-independent clause labels the walk
    found it under ("filter", "stage1.breakout", "joins.condition"), which is what the
    essentialness rule is applied to. `rename_to`/`new_field_id` are filled only for a
    declared rename whose target resolved to a live field.
    """

    column: str
    node_id: str
    field_id: int | None = None
    clauses: list[str] = Field(default_factory=list)
    essential: bool = False
    rename_to: str | None = None
    new_field_id: int | None = None


class DashcardEdit(BaseModel):
    """A dashcard whose `parameter_mappings` point at a dead or renamed field.

    Carried separately from the card query because it is a different write (PUT
    /api/dashboard/:id) against a different object -- a filter widget wired to a column
    that no longer exists breaks the dashboard even when the card behind it is repaired.
    """

    dashboard_id: int
    dashboard_name: str
    dashcard_id: int
    before: list[dict[str, Any]] = Field(default_factory=list)
    after: list[dict[str, Any]] = Field(default_factory=list)


class CardPlan(BaseModel):
    """The planned repair of one card: what changes, why, and what it was when we looked.

    `updated_at` and `revision_id` are the staleness guard's evidence -- observed at plan
    time, compared again at apply time. `before`/`after` are whole `dataset_query`
    documents rather than a diff: the apply loop PUTs `after`, and `before` is what it
    restores when the revisions API cannot.

    `downgraded_from` records an action that mend was capable of but is not allowed to
    take (dialed out of `mend.auto`, or the card lives in a notify-only collection); the
    action is then `notify` and nothing is written.
    """

    card_id: int
    name: str
    action: MendAction
    reason: str
    collection: str | None = None
    owner: str | None = None
    dashboards: list[str] = Field(default_factory=list)
    updated_at: str | None = None
    revision_id: int | None = None
    dead_refs: list[DeadRef] = Field(default_factory=list)
    repointed: list[str] = Field(default_factory=list)
    removed_clauses: list[str] = Field(default_factory=list)
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    archive: bool = False
    dashcards: list[DashcardEdit] = Field(default_factory=list)
    downgraded_from: MendAction | None = None
    depends_on: list[int] = Field(default_factory=list)

    @property
    def writes(self) -> bool:
        """Whether applying this entry sends anything to Metabase."""
        return self.action is not MendAction.NOTIFY and (
            self.archive or self.after is not None or bool(self.dashcards)
        )


class MendPlan(BaseModel):
    """A whole remediation plan -- the file, the Slack notice and the dry run, all one thing.

    `cards` is ordered upstream-first (card-on-card dependency depth, then card id): a
    card sourcing another is repaired after the card it reads, so re-execution validation
    is not asked to pass through a query that has not been repaired yet. Both keys are
    derived from the graph, so the order is stable across runs.
    """

    plan_version: int = 1
    renames: dict[str, str] = Field(default_factory=dict)
    auto: list[MendAction] = Field(default_factory=list)
    notify_only_collections: list[str] = Field(default_factory=list)
    removed_columns: list[str] = Field(default_factory=list)
    unresolved_renames: list[str] = Field(default_factory=list)
    cards: list[CardPlan] = Field(default_factory=list)

    def by_action(self, action: MendAction) -> list[CardPlan]:
        return [card for card in self.cards if card.action is action]

    @property
    def writing(self) -> list[CardPlan]:
        return [card for card in self.cards if card.writes]


CardStatus = Literal["applied", "archived", "stale", "failed", "notify", "skipped"]


class CardOutcome(BaseModel):
    """What actually happened to one card when the plan was applied.

    `reverted` says the write was undone (validation rejected the repaired query);
    `detail` carries the API's own words, because a Metabase error message is the most
    useful thing a summary can hand a human.
    """

    card_id: int
    name: str
    action: MendAction
    status: CardStatus
    detail: str | None = None
    reverted: bool = False
    dashcards_written: int = 0

    @property
    def failed(self) -> bool:
        return self.status == "failed"


class MendOutcome(BaseModel):
    """Every card's outcome, in the order the plan applied them."""

    cards: list[CardOutcome] = Field(default_factory=list)
    forced: bool = False

    def with_status(self, status: CardStatus) -> list[CardOutcome]:
        return [card for card in self.cards if card.status == status]

    @property
    def failures(self) -> list[CardOutcome]:
        return self.with_status("failed")
