"""stitch.yml -> validated StitchConfig (SPEC.md section 6.1)."""

import os
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError
from ruamel.yaml import YAML

_ENV_REF = re.compile(r"\$\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)\}")


class StitchConfigError(Exception):
    """Unusable stitch.yml: parse failure, missing values, missing env vars, literal secrets."""


class DbtConfig(BaseModel):
    project_dir: str = "."
    target_path: str = "target/"


class MetabaseDatabaseMapping(BaseModel):
    metabase_name: str
    dbt_database: str


class MetabaseConfig(BaseModel):
    url: str
    api_key: str
    min_version: str = "0.49"
    databases: list[MetabaseDatabaseMapping]
    include_schemas: list[str] = Field(default_factory=list)
    exclude_collections: list[str] = Field(default_factory=list)


class RelationshipsConfig(BaseModel):
    write_to: Literal["meta", "relationships_test", "contract_constraint"] = "meta"
    fk_meta_keys: list[str] = Field(
        default_factory=lambda: ["metabase.fk_target_table", "metabase.fk_target_field"]
    )
    cardinality_meta_key: str = "relationship_type"
    validated_test_severity: str = "warn"


class OutputConfig(BaseModel):
    dir: str = ".stitch/"
    retain_cache_runs: int = 3


class StitchConfig(BaseModel):
    dbt: DbtConfig = Field(default_factory=DbtConfig)
    metabase: MetabaseConfig
    relationships: RelationshipsConfig = Field(default_factory=RelationshipsConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)


def load_config(path: Path) -> StitchConfig:
    """Parse and validate stitch.yml, interpolating ${ENV_VAR} references.

    Hard rule: metabase.api_key must be a whole-value env reference in the file --
    a literal key in stitch.yml is a startup error, not a warning.
    """
    if not path.is_file():
        raise StitchConfigError(f"config file not found: {path} -- run 'stitch init' to create one")
    yaml = YAML(typ="safe")
    try:
        raw = yaml.load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise StitchConfigError(f"could not parse {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise StitchConfigError(f"{path} must contain a YAML mapping")

    metabase = raw.get("metabase")
    if not isinstance(metabase, dict):
        raise StitchConfigError("metabase section is required -- see SPEC.md section 6.1")
    api_key = metabase.get("api_key")
    if api_key is None:
        raise StitchConfigError(
            "metabase.api_key is required -- set it to an environment variable reference "
            "like ${STITCH_METABASE_API_KEY}"
        )
    if not isinstance(api_key, str) or _ENV_REF.fullmatch(api_key) is None:
        raise StitchConfigError(
            "metabase.api_key must be an environment variable reference like "
            "${STITCH_METABASE_API_KEY} -- a literal key in stitch.yml is an error"
        )
    if not metabase.get("url"):
        raise StitchConfigError("metabase.url is required")
    if not metabase.get("databases"):
        raise StitchConfigError(
            "metabase.databases is required -- run 'stitch doctor --list-databases'"
        )

    interpolated = _interpolate_env(raw)
    try:
        return StitchConfig.model_validate(interpolated)
    except ValidationError as exc:
        raise StitchConfigError(f"invalid stitch.yml: {exc}") from exc


def _interpolate_env(value: Any) -> Any:
    if isinstance(value, str):

        def _sub(match: re.Match[str]) -> str:
            name = match.group("name")
            if name not in os.environ:
                raise StitchConfigError(
                    f"environment variable {name} is referenced in stitch.yml but not set"
                )
            return os.environ[name]

        return _ENV_REF.sub(_sub, value)
    if isinstance(value, dict):
        return {key: _interpolate_env(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_interpolate_env(item) for item in value]
    return value
