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
    # run `dbt docs generate` automatically at the start of every `stitch build`
    # (overridable per invocation with --docs/--no-docs)
    auto_docs: bool = False
    # extra args appended to the dbt docs generate command, e.g. ["--target", "prod"]
    docs_args: list[str] = Field(default_factory=list)


class MetabaseDatabaseMapping(BaseModel):
    metabase_name: str
    dbt_database: str
    # prefix present on dbt physical table names but absent in the BI database --
    # stripped (case-insensitively, anchored at the start) before matching, so
    # dev-target artifacts (sis_fct_matches) bind to a prod-pointed Metabase
    # (fct_matches). Env-interpolable, e.g. table_prefix: ${USER_PREFIX}_
    table_prefix: str = ""


class MetabaseConfig(BaseModel):
    url: str
    api_key: str
    min_version: str = "0.49"
    databases: list[MetabaseDatabaseMapping]
    include_schemas: list[str] = Field(default_factory=list)
    exclude_collections: list[str] = Field(default_factory=list)
    missing_env: list[str] = Field(default_factory=list, exclude=True, repr=False)

    def require_env(self) -> None:
        """Raise unless every ${ENV_VAR} in the metabase section resolved at load time.

        Commands that call the Metabase API must call this up front, before doing any
        work; commands that never touch the API (build --no-metabase, impact, search,
        export, doctor --unbound/--untraced) work without the env vars set.

        The message is only the headline -- the CLI appends the why and the fix.
        """
        names = list(dict.fromkeys(self.missing_env))
        if names:
            plural = len(names) > 1
            raise StitchConfigError(
                f"environment variable{'s' if plural else ''} {', '.join(names)} "
                f"{'are' if plural else 'is'} referenced in stitch.yml but not set"
            )


class RelationshipsConfig(BaseModel):
    write_to: Literal["meta", "relationships_test", "contract_constraint"] = "meta"
    # exactly [target_table_key, target_field_key]; consumed by resolve_dbt
    fk_meta_keys: list[str] = Field(
        default_factory=lambda: ["metabase.fk_target_table", "metabase.fk_target_field"],
        min_length=2,
        max_length=2,
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

    Env interpolation of the metabase section is lazy/tolerant: a referenced-but-unset
    env var is not a load error; the raw reference is kept, the name recorded on
    cfg.metabase.missing_env, and MetabaseConfig.require_env() raises when a command
    that actually calls the API runs. All other sections interpolate strictly.
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

    missing: list[str] = []
    interpolated = {
        key: _interpolate_env(value, missing if key == "metabase" else None)
        for key, value in raw.items()
    }
    try:
        cfg = StitchConfig.model_validate(interpolated)
    except ValidationError as exc:
        raise StitchConfigError(f"invalid stitch.yml: {exc}") from exc
    cfg.metabase.missing_env = missing
    return cfg


def _interpolate_env(value: Any, missing: list[str] | None = None) -> Any:
    """Substitute ${ENV_VAR} references; strict when missing is None, tolerant otherwise.

    Tolerant mode records unset variable names in `missing` and leaves the raw
    reference in place instead of raising.
    """
    if isinstance(value, str):

        def _sub(match: re.Match[str]) -> str:
            name = match.group("name")
            if name not in os.environ:
                if missing is None:
                    raise StitchConfigError(
                        f"environment variable {name} is referenced in stitch.yml but not set"
                    )
                missing.append(name)
                return match.group(0)
            return os.environ[name]

        return _ENV_REF.sub(_sub, value)
    if isinstance(value, dict):
        return {key: _interpolate_env(item, missing) for key, item in value.items()}
    if isinstance(value, list):
        return [_interpolate_env(item, missing) for item in value]
    return value
