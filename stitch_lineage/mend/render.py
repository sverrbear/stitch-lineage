"""Rendering a mend plan and its outcome (SPEC.md section 14).

Three renderings of one gathering, exactly as graph/impact.py does it: Slack mrkdwn for the
notice, GitHub markdown for a PR comment, plain text for a terminal and a CI log. The
gathering decides what is said and in what order; the renderers only do string layout, so a
notice and a PR comment can never disagree about what mend is going to do.

Ordering is a safety feature, not a style choice. In the plan, **strip comes first**: it is
the one action whose wrongness is silent -- the card runs, and answers a different question
under the same title -- so it is read before anything else. In the summary, **failed comes
first** when there is any, because it is an alarm and it sets the exit code; strip follows
for the same reason it led the plan.
"""

import difflib
import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from stitch_lineage.mend.models import CardPlan, MendAction, MendOutcome, MendPlan

_PLAN_ORDER = (MendAction.STRIP, MendAction.ARCHIVE, MendAction.REPOINT, MendAction.NOTIFY)

_ACTION_BLURB = {
    MendAction.STRIP: "a clause is deleted -- the card runs, under the same title, showing "
    "different numbers. Re-execution cannot catch that; read these.",
    MendAction.ARCHIVE: "the card's substance is gone -- archived, never deleted",
    MendAction.REPOINT: "a declared rename, followed to the new field",
    MendAction.NOTIFY: "no write -- listed for a human",
}

_STATUS_ORDER = ("failed", "stale", "applied", "archived", "skipped", "notify")
_STATUS_LABEL = {
    "failed": "FAILED",
    "stale": "STALE (skipped -- edited since the plan)",
    "applied": "APPLIED",
    "archived": "ARCHIVED",
    "skipped": "SKIPPED",
    "notify": "NOTIFY (no write)",
}

_NOTHING_TO_MEND = "✅ nothing to mend: no card references a removed column"


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


@dataclass
class _Entry:
    """One card as a headline and the lines underneath it."""

    head: str
    detail: list[str] = field(default_factory=list)


@dataclass
class _Block:
    """One action's worth of cards, with the sentence that says why it matters."""

    action: MendAction
    blurb: str
    entries: list[_Entry] = field(default_factory=list)


def _attribution(card: CardPlan) -> str:
    parts = [*card.dashboards]
    if card.collection:
        parts.append(card.collection)
    if card.owner:
        parts.append(card.owner)
    return ", ".join(parts)


def _head(card: CardPlan) -> str:
    attribution = _attribution(card)
    line = f"#{card.card_id} {card.name}"
    return f"{line}  ({attribution})" if attribution else line


def _detail(card: CardPlan) -> list[str]:
    """What was done to this card, most consequential line first."""
    lines = [f"removed {clause}" for clause in card.removed_clauses]
    lines.extend(f"repointed {change}" for change in card.repointed)
    for edit in card.dashcards:
        dropped = len(edit.before) - len(edit.after)
        change = f"{_plural(dropped, 'filter mapping')} dropped" if dropped else "filter repointed"
        lines.append(f"dashboard '{edit.dashboard_name}': {change}")
    if card.action is MendAction.NOTIFY or not lines:
        lines.append(card.reason)
    if card.downgraded_from is not None:
        lines.append(f"would have been {card.downgraded_from.value}")
    return lines


def plan_blocks(plan: MendPlan) -> list[_Block]:
    """The plan grouped for reading: strip first, then archive, repoint, notify."""
    blocks: list[_Block] = []
    for action in _PLAN_ORDER:
        cards = plan.by_action(action)
        if not cards:
            continue
        blocks.append(
            _Block(
                action=action,
                blurb=_ACTION_BLURB[action],
                entries=[_Entry(head=_head(card), detail=_detail(card)) for card in cards],
            )
        )
    return blocks


def plan_headline(plan: MendPlan) -> str:
    counts = [
        f"{len(plan.by_action(action))} {action.value}"
        for action in _PLAN_ORDER
        if plan.by_action(action)
    ]
    return f"stitch mend: {_plural(len(plan.cards), 'card')} affected -- {', '.join(counts)}"


def _preamble(plan: MendPlan) -> Iterator[str]:
    if plan.removed_columns:
        yield f"columns gone: {', '.join(plan.removed_columns)}"
    if plan.renames:
        declared = ", ".join(f"{old} -> {new}" for old, new in plan.renames.items())
        yield f"declared renames: {declared}"
    for complaint in plan.unresolved_renames:
        yield f"rename NOT applied -- {complaint}"


def format_plan_text(plan: MendPlan) -> str:
    """The terminal rendering: the same content as the notice, no markup."""
    if not plan.cards:
        return _NOTHING_TO_MEND
    lines = [f"⚠ {plan_headline(plan)}", ""]
    lines.extend(_preamble(plan))
    lines.append("")
    for block in plan_blocks(plan):
        lines.append(f"{block.action.value.upper()} ({len(block.entries)}) -- {block.blurb}")
        for entry in block.entries:
            lines.append(f"  {entry.head}")
            lines.extend(f"      {detail}" for detail in entry.detail)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def format_plan_github(plan: MendPlan) -> str:
    """Markdown for a PR comment or a job summary."""
    if not plan.cards:
        return _NOTHING_TO_MEND
    lines = [f"### ⚠ {plan_headline(plan)}", ""]
    lines.extend(f"- {line}" for line in _preamble(plan))
    lines.append("")
    for block in plan_blocks(plan):
        lines.append(f"**{block.action.value.upper()}** ({len(block.entries)}) — {block.blurb}")
        lines.append("")
        for entry in block.entries:
            lines.append(f"- `{entry.head}`")
            lines.extend(f"  - {detail}" for detail in entry.detail)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def format_plan_slack(plan: MendPlan) -> str:
    """Slack mrkdwn: *bold* headings, bullet lists, no tree glyphs."""
    if not plan.cards:
        return _NOTHING_TO_MEND
    lines = [f"*⚠ {plan_headline(plan)}*", ""]
    lines.extend(f"• {line}" for line in _preamble(plan))
    lines.append("")
    for block in plan_blocks(plan):
        lines.append(f"*{block.action.value.upper()}* ({len(block.entries)}) — {block.blurb}")
        for entry in block.entries:
            lines.append(f"• {entry.head}")
            lines.extend(f"        ◦ {detail}" for detail in entry.detail)
        lines.append("")
    return "\n".join(lines).rstrip()


def format_plan(plan: MendPlan, output_format: str) -> str:
    renderers = {
        "text": format_plan_text,
        "github-comment": format_plan_github,
        "slack": format_plan_slack,
    }
    return renderers[output_format](plan)


# --------------------------------------------------------------------------------------
# the summary, after the writes
# --------------------------------------------------------------------------------------


def _outcome_groups(outcome: MendOutcome) -> list[tuple[str, list[Any]]]:
    """Statuses in reading order, strips floated to the front of `applied`."""
    groups = []
    for status in _STATUS_ORDER:
        cards = outcome.with_status(status)
        if not cards:
            continue
        cards = sorted(
            cards, key=lambda card: (0 if card.action is MendAction.STRIP else 1, card.card_id)
        )
        groups.append((status, cards))
    return groups


def summary_headline(outcome: MendOutcome) -> str:
    counts = [
        f"{len(outcome.with_status(status))} {status}"
        for status in _STATUS_ORDER
        if outcome.with_status(status)
    ]
    verdict = "✅" if not outcome.failures else "❌"
    forced = " (--force: staleness guard off)" if outcome.forced else ""
    return f"{verdict} stitch mend applied: {', '.join(counts) or 'nothing to do'}{forced}"


def format_summary_text(outcome: MendOutcome) -> str:
    lines = [summary_headline(outcome), ""]
    for status, cards in _outcome_groups(outcome):
        lines.append(f"{_STATUS_LABEL[status]} ({len(cards)})")
        for card in cards:
            suffix = " [reverted]" if card.reverted else ""
            lines.append(f"  #{card.card_id} {card.name} [{card.action.value}]{suffix}")
            if card.detail:
                lines.append(f"      {card.detail}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def format_summary_slack(outcome: MendOutcome) -> str:
    lines = [f"*{summary_headline(outcome)}*", ""]
    for status, cards in _outcome_groups(outcome):
        lines.append(f"*{_STATUS_LABEL[status]}* ({len(cards)})")
        for card in cards:
            suffix = " _[reverted]_" if card.reverted else ""
            lines.append(f"• #{card.card_id} {card.name} `{card.action.value}`{suffix}")
            if card.detail:
                lines.append(f"        ◦ {card.detail}")
        lines.append("")
    return "\n".join(lines).rstrip()


# --------------------------------------------------------------------------------------
# diffs
# --------------------------------------------------------------------------------------


def format_diff(before: Any, after: Any, label: str) -> str:
    """A unified diff of two `dataset_query` documents, for the apply log.

    The plan is the dry run; this is what apply prints as it writes, so a CI log answers
    "what exactly changed in card #412" without anyone opening Metabase. Both sides are
    pretty-printed with sorted keys so the diff shows semantics rather than key order.
    """
    left = json.dumps(before, indent=2, sort_keys=True).splitlines() if before is not None else []
    right = json.dumps(after, indent=2, sort_keys=True).splitlines() if after is not None else []
    diff = difflib.unified_diff(
        left, right, fromfile=f"{label} (before)", tofile=f"{label} (after)", lineterm=""
    )
    body = "\n".join(diff)
    return body or f"{label}: no change"
