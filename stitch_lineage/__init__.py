"""stitch: dbt <-> Metabase column lineage and interactive ERD."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("stitch-lineage")
except PackageNotFoundError:  # a checkout that was never pip-installed
    __version__ = "0.0.0+unknown"
