"""Load dbt artifacts from target/ (SPEC.md section 7.1)."""

from pathlib import Path
from typing import Any


class StitchArtifactError(Exception):
    """A required dbt artifact is missing or unreadable; the message names the fix."""


def load_manifest(target_path: Path) -> dict[str, Any]:
    """Load and JSON-parse {target_path}/manifest.json.

    Raises:
        StitchArtifactError: file missing or unparseable, with the fix in the message,
            e.g. "manifest.json not found in target/ -- run 'dbt docs generate'".
            Never return a partial result.
    """
    raise NotImplementedError


def load_catalog(target_path: Path) -> dict[str, Any]:
    """Load and JSON-parse {target_path}/catalog.json.

    Raises:
        StitchArtifactError: file missing or unparseable, with the fix in the message,
            e.g. "catalog.json not found in target/ -- run 'dbt docs generate'".
            Never return a partial result.
    """
    raise NotImplementedError
