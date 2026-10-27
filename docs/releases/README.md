# Preparing a release

Release notes live beside this file. A version bump and notes prepare a release; a `v*` tag
triggers the publishing workflow. Do not tag until the intended main commit has passed the
required review and CI checks. Follow the repository's merge and review policy; tagging
the validated release commit triggers publication.

Run the repository's Python, Ruff, import-linter and frontend gates in `CLAUDE.md`. Rebuild
the frontend with the Node version in `.nvmrc` and check that committed `dist/` matches the
source. If another frontend change merges, sync and rebuild rather than merging hashed
bundles by hand.

From a checkout with development dependencies installed:

```sh
python -m pip install build twine
python -m build
python -m twine check dist/*
```

Use a fresh environment for installed-artifact validation. Replace the wheel filename with
the version being prepared; do not install Stitch editable into this environment:

```sh
python -m venv /tmp/stitch-release-wheel
/tmp/stitch-release-wheel/bin/python -m pip install dist/stitch_lineage-0.5.0-py3-none-any.whl pytest httpx
env -u PYTHONPATH /tmp/stitch-release-wheel/bin/python scripts/release_smoke.py dist/stitch_lineage-0.5.0-py3-none-any.whl
```

The smoke runner verifies the installed version and every frontend file against both the
wheel and checkout. It starts the installed CLI's loopback server, checks the graph/meta
APIs and frontend HTTP assets, and uses synthetic graphs and in-memory Metabase responses
for rename impact, review, explicit one-use approval, repair, rollback and recovery data.
`pytest` and `httpx` support the existing synthetic fixture/TestClient; they are validation
dependencies, not additional runtime requirements. No real Metabase writes are performed.

Also run the [public demos](../../assets/demo/README.md) in a real browser. Confirm the
named calculation/filter changes, approval, successful repair and visible failed-rollback
recovery state. Fresh installation tests do not establish compatibility with real Metabase
versions; that evidence comes from the separate version matrix.

## v0.5.0 preparation evidence

Prepared on 2026-10-27 from main `c47667b` plus the release/docs/demo changes:

- Python 3.13.12: 1,100 tests passed; Ruff lint/format and all seven architecture contracts passed.
- Node 24.22.1: typecheck/build passed; 415 frontend tests passed, three optional tests skipped;
  `npm ci` reported zero vulnerabilities. The rebuilt frontend was byte-identical to main.
- Isolated sdist → wheel build and Twine checks passed. A fresh non-editable wheel installation
  served all eight frontend files, identical to the checkout and archive.
- Installed CLI impact found the two synthetic changed columns. Mend completed the success,
  query-failure with restore, and failed-restore cases, preserved exact recovery data, and
  rejected repeated approval. Browser checks confirmed one report and dashboard filter
  updated on success, and explicit recovery instructions after a failed rollback.
- The tests emitted one upstream Starlette warning about `httpx` TestClient deprecation;
  it did not affect validation.

Re-run against the final release commit after any additional feature merges. Update the
notes to include only features actually present on that commit, verify required approval
and live CI, then let the owner merge. Tag only after final validation. This preparation
has not published to PyPI.
