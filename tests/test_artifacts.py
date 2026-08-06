from pathlib import Path

import pytest

from stitch_lineage.io.artifacts import StitchArtifactError, load_catalog, load_manifest

FIXTURES = Path(__file__).parent / "fixtures" / "dbt"


def test_load_manifest_happy_path():
    manifest = load_manifest(FIXTURES)
    assert manifest["metadata"]["project_name"] == "demo"
    assert "model.demo.fct_orders" in manifest["nodes"]


def test_load_catalog_happy_path():
    catalog = load_catalog(FIXTURES)
    assert "model.demo.fct_orders" in catalog["nodes"]
    assert "source.demo.app.raw_users" in catalog["sources"]


def test_missing_manifest_names_the_fix(tmp_path):
    with pytest.raises(StitchArtifactError, match="dbt docs generate") as excinfo:
        load_manifest(tmp_path)
    assert "manifest.json" in str(excinfo.value)


def test_missing_catalog_names_the_fix(tmp_path):
    with pytest.raises(StitchArtifactError, match="dbt docs generate") as excinfo:
        load_catalog(tmp_path)
    assert "catalog.json" in str(excinfo.value)


def test_malformed_manifest_reports_path(tmp_path):
    (tmp_path / "manifest.json").write_text("{not json")
    with pytest.raises(StitchArtifactError, match="not valid JSON") as excinfo:
        load_manifest(tmp_path)
    assert str(tmp_path / "manifest.json") in str(excinfo.value)


def test_malformed_catalog_reports_path(tmp_path):
    (tmp_path / "catalog.json").write_text("[1, 2")
    with pytest.raises(StitchArtifactError, match="not valid JSON") as excinfo:
        load_catalog(tmp_path)
    assert str(tmp_path / "catalog.json") in str(excinfo.value)


def test_non_object_artifact_rejected(tmp_path):
    (tmp_path / "manifest.json").write_text("[]")
    with pytest.raises(StitchArtifactError, match="not a JSON object"):
        load_manifest(tmp_path)
