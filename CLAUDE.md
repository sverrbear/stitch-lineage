# CLAUDE.md

stitch: dbt ↔ Metabase column lineage + interactive ERD. Local-first; the dbt repo is the database. `SPEC.md` is the design truth (v0.5) — **Claude owns SPEC.md as Sverrir's master agent** and updates it via docs PRs whenever decisions diverge from it.

## Session bootstrap (after a context clear)

1. Read this file and `SPEC.md`, then `gh issue list` + `gh pr list` for live state.
2. Respawn the **delivery-manager** (Haiku agent, name `delivery-manager`) with the standing checklist below, and re-arm a ~20-minute session cron that pings it.
3. Coding work continues issue-by-issue with Opus worktree agents (below). The main session orchestrates and handles git only — it does not hand-write code (saves Fable quota).

## The workflow

- **GitHub issues are the unit of work.** User feedback → issue (concise, factual, labeled bug/enhancement + phase-N/backlog). The delivery-manager also derives issues from SPEC.md gaps.
- **Delivery-manager (Haiku)** runs reconciliation passes on a ~20-min ping: detect merged/closed PRs, close + re-label issues (merges to non-default branches don't fire "Fixes #N" — close manually), verify issue↔PR links, report anomalies only. It never merges anything.
- **Coding agents are Opus**, one per issue (or one per coherent group), each in its own git worktree: `git -C <repo> worktree add ~/Desktop/stitch-worktrees/<name> -b <branch> origin/main`, own `.venv` (`pip install -e ".[dev]"`), frontend work also `npm install` in `stitch_lineage/app/frontend`.
- **One individually mergeable PR per issue**, body starts `Fixes #N`. Anything touching the committed frontend `dist/` must be SEQUENTIAL/stacked (parallel dist rebuilds always conflict). Conventional commits ending `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- **Nobody merges except Sverrir.** Ever. Agents push + open PRs and stop.
- **Branch protection on main**: 1 review + green `test` check + **strict up-to-date-with-main** — every merge stales the remaining PRs. Re-sync survivors after each merge, or consolidate several green PRs into one release PR (precedent: #42) when the user wants one click.

## Quality gates (every PR)

- `pytest -q` full suite, `ruff check .`, `ruff format --check .`, `lint-imports` (architecture seams — SPEC §4) all green in the worktree.
- Frontend: `npm run typecheck`, `npm test`, `npm run build`, rebuilt `dist/` committed (git-install distribution — the wheel/repo must carry the built app).
- UI changes: **visual verification against the real graph** (`~/Desktop/data/.stitch/graph.json`, read-only) with before/after screenshots in the PR body.
- Public repo: **never commit real Smitten data** (card titles, column names) — synthetic shape-only fixtures; screenshots in PR bodies are fine.
- Real-world resolution claims get measured numbers in the PR body (e.g. cards resolved before/after against the cached payload).

## Using stitch on the Smitten data repo (`~/Desktop/data`)

- Config: `stitch.yml` at that repo's root (Metabase "Analytics" ↔ `DEV_DBT`, `table_prefix: ${USER_PREFIX}_`, `auto_docs: true`, `serve.erd_default_scope`). Env: `METABASE_API_KEY` in `.env`.
- `stitch build` runs `dbt docs generate` (dev target → **one MFA push to Sverrir's phone — warn him first**); `stitch build --no-docs` reuses artifacts with no MFA. `.stitch/` is fully local, never committed.
- `stitch serve` → http://127.0.0.1:8787.
