"""Pure resolvers: parsed dicts in -> nodes/edges out.

Seam rule (SPEC.md section 4), enforced by import-linter: no requests, no
stitch_lineage.io, no filesystem modules (os/io/pathlib). Everything here is
unit-testable offline against cached payloads.
"""
