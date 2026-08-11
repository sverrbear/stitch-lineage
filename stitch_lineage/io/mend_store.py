"""Reading and writing `.stitch/mend_plan.json` (SPEC.md section 14).

The plan is written by one process and applied by another minutes later, so the file is the
contract between them. It is written with sorted keys and a stable indent for the same
reason `graph.json` is: a plan that is byte-identical for the same inputs is a plan a human
can diff, and the plan IS the dry run.
"""

import json
from pathlib import Path

from stitch_lineage.mend.models import MendPlan

MEND_PLAN_FILENAME = "mend_plan.json"


class MendStoreError(Exception):
    """The plan file is missing, unreadable, or not a plan this version understands."""


def plan_path(output_dir: Path) -> Path:
    return output_dir / MEND_PLAN_FILENAME


def write_plan(plan: MendPlan, path: Path) -> Path:
    """Serialise the plan deterministically, creating the output directory if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(plan.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
    path.write_text(payload, encoding="utf-8")
    return path


def read_plan(path: Path) -> MendPlan:
    """Load a plan file.

    Raises:
        MendStoreError: the file is absent or does not parse as a plan. The message names
            the fix, because the only way to get a usable plan back is to re-run
            `stitch mend --plan` -- hand-editing this file is not a supported workflow.
    """
    if not path.is_file():
        raise MendStoreError(f"no mend plan at {path} -- run 'stitch mend --plan' first")
    try:
        return MendPlan.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise MendStoreError(
            f"{path} is not a usable mend plan ({exc}) -- re-run 'stitch mend --plan'"
        ) from exc
