"""Load dbt artifacts from target/ (SPEC.md section 7.1)."""

import json
from pathlib import Path
from typing import Any


class StitchArtifactError(Exception):
    """A required dbt artifact is missing or unreadable; the message names the fix."""


def _load_artifact(target_path: Path, filename: str) -> dict[str, Any]:
    path = Path(target_path) / filename
    if not path.is_file():
        raise StitchArtifactError(
            f"{filename} not found in {target_path} -- run 'dbt docs generate'"
        )
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise StitchArtifactError(f"could not read {path}: {exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise StitchArtifactError(
            f"{path} is not valid JSON ({exc}) -- re-run 'dbt docs generate'"
        ) from exc
    if not isinstance(data, dict):
        raise StitchArtifactError(f"{path} is not a JSON object -- re-run 'dbt docs generate'")
    return data


def load_manifest(target_path: Path) -> dict[str, Any]:
    """Load and JSON-parse {target_path}/manifest.json.

    Raises:
        StitchArtifactError: file missing or unparseable, with the fix in the message,
            e.g. "manifest.json not found in target/ -- run 'dbt docs generate'".
            Never return a partial result.
    """
    return _load_artifact(target_path, "manifest.json")


def load_catalog(target_path: Path) -> dict[str, Any]:
    """Load and JSON-parse {target_path}/catalog.json.

    Raises:
        StitchArtifactError: file missing or unparseable, with the fix in the message,
            e.g. "catalog.json not found in target/ -- run 'dbt docs generate'".
            Never return a partial result.
    """
    return _load_artifact(target_path, "catalog.json")
