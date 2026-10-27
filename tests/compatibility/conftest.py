"""Opt-in real Metabase tests. No URL or production credential is accepted."""

from pathlib import Path

import pytest

from .live_metabase import VERSIONS, DisposableMetabase


def pytest_addoption(parser):
    parser.addoption("--metabase-version", choices=VERSIONS, default=None)
    parser.addoption("--metabase-artifacts", default=None)


@pytest.fixture(scope="session")
def live_metabase(request, tmp_path_factory):
    version = request.config.getoption("--metabase-version")
    if version is None:
        pytest.skip("opt in with --metabase-version; requires Docker")
    output = request.config.getoption("--metabase-artifacts")
    artifacts = Path(output) if output else tmp_path_factory.mktemp("metabase-compat")
    with DisposableMetabase(version, artifacts) as instance:
        yield instance
