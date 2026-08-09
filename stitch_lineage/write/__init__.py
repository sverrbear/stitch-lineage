"""Repo writes. `yaml_writer` is the only module in the codebase that touches model YAML.

Seam (SPEC.md section 4): write/ never imports resolve/, app/ or the Metabase client, and
nothing but the CLI imports write/ -- `stitch serve` and the static export stay read-only,
so every repo mutation goes through `stitch apply`.
"""
