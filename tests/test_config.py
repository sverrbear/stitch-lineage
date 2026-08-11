import pytest

from stitch_lineage.config import StitchConfigError, load_config

VALID_CONFIG = """
dbt:
  project_dir: .
  target_path: target/
metabase:
  url: ${STITCH_METABASE_URL}
  api_key: ${STITCH_METABASE_API_KEY}
  databases:
    - metabase_name: Analytics
      dbt_database: ANALYTICS
  include_schemas: [MARTS, DIMS]
output:
  dir: .stitch/
"""


def _write(tmp_path, content):
    path = tmp_path / "stitch.yml"
    path.write_text(content)
    return path


def test_load_valid_config(tmp_path, monkeypatch):
    monkeypatch.setenv("STITCH_METABASE_URL", "https://mb.example.com")
    monkeypatch.setenv("STITCH_METABASE_API_KEY", "mb_test_key")
    cfg = load_config(_write(tmp_path, VALID_CONFIG))

    assert cfg.metabase.url == "https://mb.example.com"
    assert cfg.metabase.api_key == "mb_test_key"
    assert cfg.metabase.min_version == "0.49"
    assert cfg.metabase.databases[0].metabase_name == "Analytics"
    assert cfg.metabase.databases[0].dbt_database == "ANALYTICS"
    assert cfg.metabase.databases[0].table_prefix == ""
    assert cfg.metabase.include_schemas == ["MARTS", "DIMS"]
    assert cfg.metabase.exclude_collections == []
    assert cfg.metabase.exclude_packages == []
    assert cfg.metabase.exclude_models == []
    assert cfg.dbt.project_dir == "."
    assert cfg.dbt.auto_docs is False
    assert cfg.dbt.docs_args == []
    # #134: a drawn relationship is a dbt relationships test unless asked otherwise
    assert cfg.relationships.write_to == "relationships_test"
    assert cfg.relationships.validated_test_severity == "warn"
    assert cfg.relationships.fk_meta_keys == [
        "metabase.fk_target_table",
        "metabase.fk_target_field",
    ]
    assert cfg.output.dir == ".stitch/"
    assert cfg.output.retain_cache_runs == 3


def test_literal_api_key_is_an_error(tmp_path, monkeypatch):
    monkeypatch.setenv("STITCH_METABASE_URL", "https://mb.example.com")
    literal = VALID_CONFIG.replace("${STITCH_METABASE_API_KEY}", "mb_live_secret_key")
    with pytest.raises(StitchConfigError, match="literal key"):
        load_config(_write(tmp_path, literal))


def test_partial_env_ref_api_key_is_an_error(tmp_path, monkeypatch):
    monkeypatch.setenv("STITCH_METABASE_URL", "https://mb.example.com")
    monkeypatch.setenv("STITCH_METABASE_API_KEY", "mb_test_key")
    partial = VALID_CONFIG.replace(
        "${STITCH_METABASE_API_KEY}", "prefix-${STITCH_METABASE_API_KEY}"
    )
    with pytest.raises(StitchConfigError, match="environment variable reference"):
        load_config(_write(tmp_path, partial))


def test_missing_metabase_env_var_is_lazy(tmp_path, monkeypatch):
    monkeypatch.setenv("STITCH_METABASE_URL", "https://mb.example.com")
    monkeypatch.delenv("STITCH_METABASE_API_KEY", raising=False)
    cfg = load_config(_write(tmp_path, VALID_CONFIG))
    assert cfg.metabase.missing_env == ["STITCH_METABASE_API_KEY"]
    assert cfg.metabase.api_key == "${STITCH_METABASE_API_KEY}"
    with pytest.raises(StitchConfigError, match="STITCH_METABASE_API_KEY"):
        cfg.metabase.require_env()


def test_env_ready_metabase_config_passes_require_env(tmp_path, monkeypatch):
    monkeypatch.setenv("STITCH_METABASE_URL", "https://mb.example.com")
    monkeypatch.setenv("STITCH_METABASE_API_KEY", "mb_test_key")
    cfg = load_config(_write(tmp_path, VALID_CONFIG))
    assert cfg.metabase.missing_env == []
    cfg.metabase.require_env()


def test_missing_databases_points_at_doctor(tmp_path, monkeypatch):
    monkeypatch.setenv("STITCH_METABASE_URL", "https://mb.example.com")
    monkeypatch.setenv("STITCH_METABASE_API_KEY", "mb_test_key")
    without_databases = "\n".join(
        line
        for line in VALID_CONFIG.splitlines()
        if "databases:" not in line and "metabase_name" not in line and "dbt_database" not in line
    )
    with pytest.raises(StitchConfigError, match="stitch doctor --list-databases"):
        load_config(_write(tmp_path, without_databases))


def test_missing_api_key(tmp_path):
    without_key = "\n".join(line for line in VALID_CONFIG.splitlines() if "api_key" not in line)
    with pytest.raises(StitchConfigError, match="api_key is required"):
        load_config(_write(tmp_path, without_key))


def test_missing_url(tmp_path):
    without_url = "\n".join(line for line in VALID_CONFIG.splitlines() if "url:" not in line)
    with pytest.raises(StitchConfigError, match="url is required"):
        load_config(_write(tmp_path, without_url))


def test_missing_file(tmp_path):
    with pytest.raises(StitchConfigError, match="stitch init"):
        load_config(tmp_path / "stitch.yml")


def test_dbt_auto_docs_and_docs_args(tmp_path, monkeypatch):
    monkeypatch.setenv("STITCH_METABASE_URL", "https://mb.example.com")
    monkeypatch.setenv("STITCH_METABASE_API_KEY", "mb_test_key")
    config = VALID_CONFIG.replace(
        "dbt:\n  project_dir: .",
        'dbt:\n  project_dir: .\n  auto_docs: true\n  docs_args: ["--target", "prod"]',
    )
    cfg = load_config(_write(tmp_path, config))
    assert cfg.dbt.auto_docs is True
    assert cfg.dbt.docs_args == ["--target", "prod"]


def test_table_prefix_env_interpolation(tmp_path, monkeypatch):
    monkeypatch.setenv("STITCH_METABASE_URL", "https://mb.example.com")
    monkeypatch.setenv("STITCH_METABASE_API_KEY", "mb_test_key")
    monkeypatch.setenv("USER_PREFIX", "sis")
    config = VALID_CONFIG.replace(
        "dbt_database: ANALYTICS",
        "dbt_database: ANALYTICS\n      table_prefix: ${USER_PREFIX}_",
    )
    cfg = load_config(_write(tmp_path, config))
    assert cfg.metabase.databases[0].table_prefix == "sis_"


def test_env_interpolation_inside_longer_strings(tmp_path, monkeypatch):
    monkeypatch.setenv("MB_HOST", "mb.example.com")
    monkeypatch.setenv("STITCH_METABASE_API_KEY", "mb_test_key")
    config = VALID_CONFIG.replace("${STITCH_METABASE_URL}", "https://${MB_HOST}/api")
    cfg = load_config(_write(tmp_path, config))
    assert cfg.metabase.url == "https://mb.example.com/api"


def _serve_config(scope_line: str) -> str:
    return VALID_CONFIG + f"serve:\n  erd_default_scope: {scope_line}\n"


def test_serve_section_is_optional(tmp_path, monkeypatch):
    monkeypatch.setenv("STITCH_METABASE_URL", "https://mb.example.com")
    monkeypatch.setenv("STITCH_METABASE_API_KEY", "mb_test_key")
    cfg = load_config(_write(tmp_path, VALID_CONFIG))
    assert cfg.serve.erd_default_scope is None


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        ('"schema:models"', "schema:models"),
        ('"tag:core"', "tag:core"),
        ('"  schema:MARTS  "', "schema:MARTS"),
        ('"tag:core:extra"', "tag:core:extra"),
        ('""', None),
    ],
)
def test_erd_default_scope_accepted_values(tmp_path, monkeypatch, configured, expected):
    monkeypatch.setenv("STITCH_METABASE_URL", "https://mb.example.com")
    monkeypatch.setenv("STITCH_METABASE_API_KEY", "mb_test_key")
    cfg = load_config(_write(tmp_path, _serve_config(configured)))
    assert cfg.serve.erd_default_scope == expected


@pytest.mark.parametrize("configured", ['"models"', '"schemas:models"', '"schema:"', '"tag: "'])
def test_erd_default_scope_rejects_an_unprefixed_value(tmp_path, monkeypatch, configured):
    monkeypatch.setenv("STITCH_METABASE_URL", "https://mb.example.com")
    monkeypatch.setenv("STITCH_METABASE_API_KEY", "mb_test_key")
    with pytest.raises(StitchConfigError, match="erd_default_scope"):
        load_config(_write(tmp_path, _serve_config(configured)))


def test_bind_denominator_exclusions_load(tmp_path, monkeypatch):
    """metabase.exclude_packages / exclude_models -- the bind denominator (#119)."""
    monkeypatch.setenv("STITCH_METABASE_URL", "https://mb.example.com")
    monkeypatch.setenv("STITCH_METABASE_API_KEY", "mb_test_key")
    content = VALID_CONFIG.replace(
        "  include_schemas: [MARTS, DIMS]",
        "  include_schemas: [MARTS, DIMS]\n"
        "  exclude_packages: [elementary, dbt_artifacts]\n"
        "  exclude_models: ['stg_*']",
    )
    cfg = load_config(_write(tmp_path, content))
    assert cfg.metabase.exclude_packages == ["elementary", "dbt_artifacts"]
    assert cfg.metabase.exclude_models == ["stg_*"]
