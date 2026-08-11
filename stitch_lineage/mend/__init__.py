"""`stitch mend` -- impact-driven Metabase card remediation (SPEC.md section 14).

The seam runs through this package the same way it runs through the rest of the codebase:
`plan`, `rewrite`, `render` and `models` are pure (dicts and graphs in, a plan out) and
`apply` is the only module that touches the API, through io/. That is what makes the
taxonomy testable offline against synthetic cards -- which is the only responsible way to
develop something that writes to a live BI estate.
"""

from stitch_lineage.mend.models import (
    AUTO_ACTIONS,
    CardOutcome,
    CardPlan,
    DashcardEdit,
    DeadRef,
    MendAction,
    MendOutcome,
    MendPlan,
)

__all__ = [
    "AUTO_ACTIONS",
    "CardOutcome",
    "CardPlan",
    "DashcardEdit",
    "DeadRef",
    "MendAction",
    "MendOutcome",
    "MendPlan",
]
