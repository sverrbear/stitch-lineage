# Metabase compatibility tracker

Stitch's API-key authentication floor is Metabase 49. This is a minimum protocol
requirement, not a claim that every later release, driver, permission arrangement,
or query feature has been verified. The matrix below tests specific Community
Edition releases against a synthetic PostgreSQL warehouse. Enterprise-only
permissions, other warehouse drivers and upgrades of an existing application
database are outside this suite.

| Pinned release | Reason for inclusion | Expected API query representation |
| --- | --- | --- |
| `v0.49.25.2` | Patch release of the advertised minimum major | Legacy MBQL |
| `v0.56.25.1` | Last major before the default serialization change | Legacy MBQL |
| `v0.64.1` | Current stable release checked on 2026-10-27 | Staged MBQL 5 |

Metabase's [API changelog](https://www.metabase.com/docs/latest/developers-guide/api-changelog)
identifies v0.57 as the change to MBQL 5 in REST responses. The legacy and modern
lanes assert the actual returned representation. Both seed through the API and
test what the server stores; they do not substitute hand-authored response mocks.
The pins were verified against [Metabase releases](https://github.com/metabase/metabase/releases).

## Run locally

Docker must be running. From an installed development checkout:

```bash
python -m pip install -e '.[dev]'
docker pull metabase/metabase:v0.64.1
docker pull postgres:16.11-alpine
pytest tests/compatibility -v --metabase-version v0.64.1 \
  --metabase-artifacts /tmp/stitch-metabase-v0.64.1 \
  --junitxml=/tmp/stitch-metabase-v0.64.1/junit.xml
```

Repeat with either other pinned version. The ordinary `pytest -q` suite skips
these tests, so offline development does not require Docker. Selecting a version
makes missing Docker, startup failures and API failures fail the run; there is no
fallback to mocked results. Image pulls happen on demand if not already cached.

The harness creates a uniquely named Docker network and two containers, binds
Metabase to a random loopback-only port, and destroys only its own resources in
teardown. PostgreSQL has no published host port. It creates a fresh administrator,
API keys and a two-row warehouse; it accepts no instance URL or existing
credentials. It refuses setup if the instance has already been configured.
Allow roughly 2 GB of free Docker memory plus image storage per lane. CI runs
lanes on separate runners. A hard-killed local pytest process can leave its own
`stitch-compat-*` containers/network behind; remove those specific resources
after inspecting them, rather than pruning unrelated Docker resources.

## Contracts exercised

- API-key authentication, version detection, database metadata, active and archived
  card/dashboard listing responses, and raw payload caching.
- Exact field lineage from a query-builder card, a saved question that reads that
  card, and native PostgreSQL SQL; all three execute against the real warehouse.
- The release's returned legacy or staged query representation, used unchanged as
  input to Stitch's resolver and Mend rewriter.
- A declared `amount` → `amount_usd` repoint using Stitch's rewriter and apply
  engine, followed by real query execution and assertions on returned values.
- A dashboard parameter mapping repaired with the card, real filtered dashboard
  execution returning the expected row, and dashboard lineage.
- An API key with read-only collection access: `can_write=false`, Mend skips the
  write, a direct server write is rejected, and the saved card remains untouched.
- A concurrent edit rejected by Mend's timestamp guard.
- An accepted query update that fails at execution, followed by a real Metabase
  revision revert and verification that the original query and results return.

The recovery test deliberately supplies an invalid native query to the apply
engine to exercise server-side failure and rollback. It does not claim Mend
automatically rewrites native SQL or that rollback is guaranteed for every failure.

## CI and evidence

The `Metabase compatibility` workflow runs on pull requests, main pushes, manual
dispatch and weekly. It preserves the existing required `test` job. Each lane has
a time limit and runs independently, so one broken release does not hide another.
The workflow retains JUnit results, synthetic raw payload snapshots, container
logs and `evidence.json` with observed version, shape and pulled image digests for
seven days. No production data or credentials are used. API-key and setup responses
are not written into artifacts.

Use the matching successful workflow run as compatibility evidence for a commit.
A configured matrix alone is not passing evidence. At release preparation, check
all three lanes for the release commit, in addition to normal tests and fresh
installation checks. This suite does not establish support for untested versions.

### Initial measured results (2026-10-27)

All five live contract tests passed on each release, including filtered dashboard
execution and recovery through the real revision API:

| Version | Passed | Observed query shape | Local run time |
| --- | --- | --- | --- |
| `v0.49.25.2` | 5 / 5 | Legacy MBQL | 350.28 s |
| `v0.56.25.1` | 5 / 5 | Legacy MBQL | 82.68 s |
| `v0.64.1` | 5 / 5 | Staged MBQL 5 | 109.38 s |

These runs used Docker Desktop on an ARM64 Mac; v49's AMD64 image ran under
emulation. Timing includes startup and teardown and is not a performance target.
The v49 archived-dashboard endpoint returned `404` with `error_code: archived`;
Stitch retained its listing summary and left unavailable membership unresolved.
The v56 and v64 endpoints returned archived dashboard details. No production code
change was required for these tested contracts. Future compatibility claims should
use the workflow evidence for that commit, rather than this dated snapshot.

## Updating the matrix

1. Read Metabase's API changelog and verify the new stable release tag.
2. Update the reviewed allowlist in `tests/compatibility/live_metabase.py`, the
   workflow matrix and this table together. Keep a minimum-version and a
   pre-MBQL-5 lane while Stitch advertises that floor.
3. Run the new release against real containers. Inspect returned payloads when a
   contract changes; do not weaken assertions just to accept a new shape.
4. Add a small synthetic regression fixture if a production code fix is needed,
   then rerun the full matrix and ordinary quality gates.
5. Record any narrower support bounds here before changing public claims.

Upstream references: [Docker installation](https://www.metabase.com/docs/latest/installation-and-operation/running-metabase-on-docker),
[API keys](https://www.metabase.com/docs/latest/people-and-groups/api-keys), and
[API changelog](https://www.metabase.com/docs/latest/developers-guide/api-changelog).
