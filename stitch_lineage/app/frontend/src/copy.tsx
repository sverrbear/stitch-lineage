// Every user-facing string in the app, in one place (#177).
//
// Grouped by the surface it appears on, in the order a reader meets it. Edit the
// words here and they change everywhere they are shown; nothing in a component
// spells out prose of its own, and `copy.enforce.test.ts` fails the build if that
// stops being true.
//
// Three shapes live here, and which one to use is decided by the sentence:
//
//   * a plain string, for a label that never varies;
//   * a function, where a number or a name lands inside the sentence — so the
//     grammar (`1 card` vs `2 cards`) stays next to the words rather than being
//     assembled at the call site;
//   * a function returning JSX, for the handful of sentences that carry inline
//     `<code>` — the markup is part of the sentence, so it belongs with it. This is
//     why the file is .tsx.
//
// It is deliberately NOT a flat map of keys. `copy.apply.confirm` reads as what it
// is at the call site, and a nested object is what makes the groups skimmable when
// the thing you want to change is "the wording on the apply dialog".

import type { ReactNode } from 'react'

/** `1 card` / `2 cards` — the plural rule every count in the app shares. */
export function plural(n: number, word: string): string {
  return `${n} ${word}${n === 1 ? '' : 's'}`
}

export const copy = {
  mend: {
    title: 'Mend',
    headline: 'Repair reports after a column change',
    intro: 'Declare a rename, review the affected reports, then approve the exact repairs.',
    readOnly: 'Repairs are available in the local app. Run stitch serve from your configured dbt project. This view is read-only.',
    loading: 'Reading the current and previous snapshots…',
    planning: 'Reading Metabase and preparing your review. No repairs are being applied…',
    applying: 'Applying the approved repairs and checking queries. Keep this page open…',
    baseline: 'Before', current: 'After', unknownTime: 'Build time unavailable',
    snapshots: 'Snapshots compared',
    freshness: 'These are saved snapshots, not continuous monitoring. Refresh dbt artifacts, sync Metabase, and run stitch build after a schema change. Every build replaces the previous snapshot.',
    coverage: 'Affected reports are limited to dependencies captured in the baseline graph and visible to your API key. No matches do not prove a change is safe.',
    missingTitle: 'Your first graph is the starting point',
    missing: 'There is no previous snapshot to compare yet. After the next schema change, refresh dbt artifacts, sync Metabase, and run stitch build. Mend can then compare before and after. Do not rebuild just to dismiss this message.',
    noRemoved: 'No removed columns in these snapshots',
    noRemovedDetail: 'A rename appears as one removed column and one replacement. Mend currently repairs removed-column references; type changes need manual review.',
    typeChanges: (n: number) => `${plural(n, 'type change')} also need manual review.`,
    changed: 'Removed column', replacement: 'Replacement column',
    replacementFor: (name: string) => `Replacement for ${name}`,
    replacementPlaceholder: 'Search current columns, or leave empty',
    replacementHint: 'Select an exact column from the current graph. Leave empty for a removal with no replacement.',
    invalidTarget: 'Choose an exact column from the suggestions before reviewing.',
    unsynced: 'Not bound to a Metabase field yet; this rename will remain manual until synced.',
    options: 'Other repair actions',
    repointOnly: 'Declared renames only by default. No clauses are removed and no reports archived unless you opt in below.',
    strip: 'Allow removing broken clauses for this review',
    stripWarning: 'Removing a filter, grouping, or aggregation can change the numbers and meaning of a report, even when its query runs.',
    archive: 'Allow archiving reports for this review',
    archiveWarning: 'Archiving hides reports whose essential references are gone. Review each affected report before approving.',
    disabledAction: 'Disabled by mend.auto in stitch.yml. Change the project policy outside this app to enable it.',
    repointDisabled: 'Repoint is disabled by mend.auto in stitch.yml; declared renames will be listed for manual review.',
    review: 'Review repairs', reload: 'Reload snapshots',
    changedInputs: 'Selections changed. Review repairs again to create a current preview.',
    previewTitle: (n: number) => `${plural(n, 'affected report')}`,
    ready: (n: number) => `${plural(n, 'repair')} ready to approve`,
    manual: (n: number) => `${plural(n, 'report')} for manual review`,
    nothing: 'No affected reports were found in this baseline. Check coverage before treating the change as safe.',
    previewNote: 'This preview expires after 15 minutes. Another review or a server restart replaces it. Planning has not changed Metabase.',
    queryDiff: 'Query before / after', dashboardDiff: 'Dashboard filter before / after',
    changes: 'What changes',
    namedDiff: 'Query diff with field names',
    namedDiffNote: 'Field references are labelled for review. The exact query below is what will be sent to Metabase.',
    rawDiff: 'Exact query diff',
    rawDashboard: 'Exact dashboard filter wiring',
    customExpression: 'This expression cannot be simplified safely. Its structure is shown with field names; inspect the detailed diff before approving.',
    queryBefore: 'Current query', dashboardEdit: (name: string) => `Update filter wiring on ${name}`,
    before: 'Before', after: 'After',
    openReport: 'Open in Metabase ↗',
    actions: { repoint: 'Update references', strip: 'Remove clauses', archive: 'Archive report', notify: 'Manual review' },
    confirm: (n: number) => `I have reviewed and approve ${plural(n, 'repair')}.`,
    manualUnchanged: (n: number) => `${plural(n, 'manual report')} below will not be changed.`,
    apply: (n: number) => `Apply ${plural(n, 'repair')}`,
    validationNote: 'Successful execution means the query ran. It does not prove that the report still answers the same business question. Failed query repairs trigger a rollback attempt; rollback can fail.',
    results: 'Repair results',
    resultSummary: (good: number, attention: number, manual: number) =>
      `${plural(good, 'completed change')} · ${attention} need attention · ${manual} manual`,
    resultRefresh: 'These results describe this approval only. The graph is not rebuilt automatically. Check the reports, then rebuild when your artifacts are ready.',
    interrupted: 'The apply response was interrupted. Check the result or Metabase before reviewing again; do not repeat the apply request.',
    checkResult: 'Check completed result',
    recoveryQuery: 'Original query for recovery',
    recoveryAfter: 'Attempted query',
    recoveryNote: 'Use this exact original dataset_query to restore the report in Metabase if needed. Check dashboard filter wiring separately. This recovery record lasts only until the next completed apply or server restart; save it before leaving.',
    recoveryDashboard: (name: string) => `Original filter wiring · ${name}`,
    reverted: 'Reverted after query failure', rollbackFailed: 'Rollback failed — action required', failed: 'Failed — check details',
    status: { applied: 'Updated · query ran', archived: 'Archived · not executed', notify: 'Manual review', stale: 'Stale · not changed', skipped: 'Skipped · not changed' },
    dashboardWrites: (n: number) => `${plural(n, 'dashboard filter update')} completed`,
  },
  /** The shell: loading, fatal errors, the dev banner. */
  app: {
    loadingView: 'Loading view…',
    invalidLinkTitle: 'Page not found',
    invalidLinkDetail: 'This link is invalid. Use search to find a model, column, card or dashboard.',
    loadingGraph: 'Loading graph…',
    devFixtureBanner: 'dev fixture graph (no API)',
    loadErrorTitle: 'Couldn’t load the lineage graph',
    /** Where the graph was looked for, and what to run instead. */
    loadErrorDetail: (): ReactNode => (
      <>
        Expected either <code>window.__STITCH_GRAPH__</code> (static export) or a local API at{' '}
        <code>api/graph</code> — run <code>stitch serve</code> from your dbt repo.
      </>
    ),
  },

  /** Top bar: nav, the two actions, and the build stamp's tooltip. */
  header: {
    home: 'Home',
    search: 'Search (Cmd/Ctrl+K)',
    themeToggle: (next: 'light' | 'dark') => `Switch to ${next} theme`,
    /** Hovering the build stamp says where the graph came from, and when it is old. */
    builtAt: (generatedAt: string | null, origin: string, stale: boolean) =>
      stale
        ? `${generatedAt} · data source: ${origin} — run \`stitch build\` to refresh`
        : `${generatedAt} · data source: ${origin}`,
  },

  /** The home page: one question and the field that answers it. */
  home: {
    title: 'Trace a column',
    searchPlaceholder: 'Model, column, card or dashboard',
    coverage: 'Coverage',
  },

  /** Views of the selected object. */
  workspace: {
    summary: 'Summary',
    lineage: 'Lineage',
    views: 'Object views',
    archived: 'archived',
  },

  /** Search, inline on the home page and inside the palette. */
  search: {
    placeholder: 'Search models, columns, cards, dashboards…',
    ariaLabel: 'Search the lineage graph',
    empty: 'No matches.',
    archived: 'Archived in Metabase',
    archivedTag: 'Archived',
    viaField: (field: string) => `via ${field}`,
  },

  /** ⌘K. */
  palette: {
    placeholder: 'Jump to anything…',
    hint: '↑↓ navigate · ↵ open · esc close',
  },

  /**
   * The manager marks (#187) — hover tooltip and accessible name, the same string in
   * both. They say who manages the table in WORDS, because 14px of brand color is not
   * something a reader should have to decode, and some readers cannot.
   */
  badges: {
    dbt: 'Managed by dbt. Produced by the dbt pipeline',
    snowflake: 'Source table. Landed in Snowflake, not managed by dbt',
    metabase: 'Metabase (BI)',
  },

  /** The same fact as a row in the detail panel, where there is room for a sentence. */
  managedBy: {
    label: 'Managed by',
    dbt: 'dbt - the pipeline builds this table',
    snowflake: 'Nothing in dbt — a source table, landed in Snowflake',
    metabase: 'Metabase',
  },

  /** Dependency and BI fan-out lists on a detail page. */
  fanout: {
    hops: 'Steps along the dependency chain',
    noDashboard: 'Not pinned to any dashboard',
    cardsOnNoDashboard: 'Cards on no dashboard',
    details: 'Details',
    openExternal: 'Open ↗',
    archived: 'archived',
  },

  /** The coverage lists behind the home page's gaps. */
  coverage: {
    notInGraph: 'Not in this graph',
    groupBy: 'Group by',
    backToOverview: '← Overview',
    openGroup: (label: string) => `Open ${label} →`,
    empty: 'Nothing here — this build has full coverage.',
  },

  /** The detail panels: column, model, source, card, dashboard, field. */
  node: {
    placeholder: 'Only referenced by an edge — this build never resolved a definition for it.',
    viewLineage: 'View lineage →',
    openInMetabase: 'Open in Metabase ↗',
    definedAs: 'Defined as',
    impact: 'Downstream impact',
    impactSummary: (cards: string, dashboards: string) => `Consumed by ${cards} on ${dashboards}.`,
    noImpact: 'Not consumed by any Metabase card in this graph.',
    impactScope: 'Dependencies found in this graph. Unresolved lineage may hide other consumers.',
    impactTraceGap: (reason: string) => `Upstream lineage is incomplete: ${reason}.`,
    impactModelGaps: (columns: string) => `${columns} with incomplete upstream lineage in this table.`,
    downstreamPaths: (models: string, fields: string) => `Downstream paths — ${models}, ${fields}`,
    objectDetails: 'Details',
    upstream: (columns: string, sources: string) => `Upstream — ${columns}, ${sources}`,
    listTruncated: 'Only part of the result is listed below; summary counts are complete.',
    upstreamListTruncated: 'Only part of the upstream result is listed; summary counts are complete.',
    downstreamListTruncated: 'Only part of the downstream result is listed; summary counts are complete.',
    noDisplayedEntries: 'No entries in this displayed portion.',
    declaredRelationships: 'Declared relationships',
    /** The two ways a `binds_to` hop goes missing, and the command that lists them. */
    unboundWhy: (): ReactNode => (
      <>
        Either the Metabase table is not bound to a dbt model in this build, or the column is absent
        from the dbt artifacts this graph was built from. <code>stitch doctor --unbound</code> lists
        the models with no bound Metabase table.
      </>
    ),
    gapFieldsUnbound: (fields: string, type: string, why: ReactNode): ReactNode => (
      <>
        No dbt column binds to the {fields} this {type} queries, so the chain stops at Metabase.{' '}
        {why}
      </>
    ),
    gapFieldUnbound: (why: ReactNode): ReactNode => (
      <>No dbt column binds to this field. {why}</>
    ),
    gapNativeUnresolved:
      'This card is native SQL, and native cards are not resolved into column lineage in this build — the chain is unknown, not empty. Coverage counts them separately.',
    gapQueryUnresolved: (): ReactNode => (
      <>
        No Metabase field reference resolved out of this card&rsquo;s query, so there is no chain to
        walk. <code>stitch doctor --unresolved-cards</code> lists the refs and why each one failed.
      </>
    ),
    gapDashboardUnresolved:
      'None of the cards on this dashboard resolved to a Metabase field, so there is no chain to walk down into dbt.',
    cardsOnDashboard: (cards: string) => `Cards on this dashboard — ${cards}`,
    appearsOn: (dashboards: string) => `Appears on ${dashboards}`,
    consumedBy: (cards: string) => `Consumed by ${cards}`,
    fieldsItQueries: (fields: string) => `${fields} it queries`,
    reverseView: (type: string): ReactNode => (
      <>
        The reverse view: every dbt column this {type} ultimately depends on, named the dbt way with
        the model it comes from. Non-exact chains carry their weakest hop — hover it for what that
        means.
      </>
    ),
    fieldsWithNoColumn: (fields: string) => `${fields} with no dbt column`,
    unboundFieldsNote: (why: ReactNode): ReactNode => (
      <>
        The chain stops at Metabase for these, so whatever they contribute is missing from the
        columns above. {why}
      </>
    ),
    dependencies: (upstream: string, downstream: string) =>
      `Dependencies — ${upstream} upstream, ${downstream} downstream`,
    columns: (count: number) => `Columns — ${count}`,
    nothingUpstreamSource: 'Nothing upstream.',
    nothingUpstream: 'Nothing upstream',
    nothingDownstream: 'Nothing downstream',
    references: 'References',
    referencedBy: 'Referenced by',
    validated: 'Validated by a dbt relationships test',
    notFound: 'Node not found',
    notFoundDetail: (): ReactNode => <> is not in the loaded graph. It may have been removed in a newer build.</>,
  },

  /** The lineage canvas. */
  diagram: {
    focus: 'Focus selection',
    overview: 'Show overview',
    collapseColumns: 'Collapse columns',
    allColumns: (n: number) => `Show all ${n} columns`,
  },
  lineage: {
    showing: (shown: number, total: number) => `Showing ${shown} of ${total} groups in this trace`,
    moreGroups: (direction: 'up' | 'down', n: number) => `+ ${n} ${direction === 'up' ? 'upstream' : 'downstream'}`,
    moreColumns: (n: number) => `+ ${n} more traced columns`,
    bindings: (n: number) => `${n} Metabase field binding${n === 1 ? '' : 's'}`,
    showBindings: 'Show field nodes',
    hideBindings: 'Hide field nodes',
    exploreHint: 'Expand a branch to follow it · totals include the full graph',
    unknownNode: (nodeId: string): ReactNode => <>Unknown node: {nodeId}</>,
    openInMetabase: (name: string) => `Open “${name}” in Metabase`,
    openDetail: (type: string, name: string) => `Open the ${type} page for ${name}`,
    downstreamCounts: (cards: number, dashboards: number) =>
      `Downstream: ${cards.toLocaleString()} card${cards === 1 ? '' : 's'} · ${dashboards.toLocaleString()} dashboard${dashboards === 1 ? '' : 's'}`,
    grainLabel: 'Lineage grain',
    grainColumn: 'Which columns feed which fields',
    grainTable: 'Which models feed which cards — the same subgraph rolled up',
    details: 'Details',
    truncated: 'drawing truncated; counts are exact',
    hint: 'click a node to re-trace from it · click its name to open it',
  },

  /** The apply dialog: plan, confirm, results. */
  apply: {
    title: 'Apply staged changes',
    titleDone: 'Applied',
    /**
     * What `relationships.write_to` means, in the words the reader would use (#134).
     * The raw config value told them the setting's name, not what lands in their repo.
     */
    writeForm: {
      relationships_test: (): ReactNode => (
        <>
          a dbt <code>relationships</code> test at <code>severity: warn</code>
        </>
      ),
      meta: (): ReactNode => (
        <>
          <code>metabase.fk_*</code> meta keys
        </>
      ),
      contract_constraint: (): ReactNode => <>a model contract foreign-key constraint</>,
    },
    /**
     * What is about to be written. The wording is carried over exactly as it was
     * written inline, plurals and all, so relocating it changed no rendered text.
     */
    lead: (
      total: number,
      relationships: number,
      descriptions: number,
      files: number,
      writeForm: ReactNode,
    ): ReactNode => (
      <>
        {total} staged change{total === 1 ? '' : 's'} ({relationships}{' '}
        relationship{relationships === 1 ? '' : 's'},{' '}
        {descriptions} description edit
        {descriptions === 1 ? '' : 's'}) →{' '}
        {files} file{files === 1 ? '' : 's'}. Relationships are
        written as {writeForm}.
      </>
    ),
    unappliable: 'Cannot be written',
    unchanged: 'Already true in the repo',
    couldNotWrite: 'Could not be written',
    note: 'This writes the files above in your dbt repo. A file with uncommitted changes is refused and reported — forcing stays in the CLI.',
    written: 'Written',
    refused: (n: number) => `Refused — ${n}`,
    /** A refused file is not lost: it is still staged, and this says how to land it. */
    refusedNote: (): ReactNode => (
      <>
        Those changes are still staged. Commit or stash the file and apply again — or run{' '}
        <code>stitch apply --force</code> in a terminal.
      </>
    ),
    graphPatched: (edges: number, descriptions: number): ReactNode => (
      <>
        Graph updated in place: {edges} relationship
        {edges === 1 ? '' : 's'}, {descriptions} description
        {descriptions === 1 ? '' : 's'} — the panels already show it.
      </>
    ),
    close: 'Close',
    cancel: 'Cancel',
    /**
     * The sentences lib/workspace assembles. They live here so the words are in one
     * place; the grammar that picks between them stays with the state it reads.
     */
    outcomeRefused: (changes: string, files: string, refused: string, stillStaged: number) =>
      `Wrote ${changes} to ${files}; ${refused} refused, ${stillStaged} still staged.`,
    outcomeNothingApplied: (changes: string) =>
      `Nothing to write — the repo already said all ${changes}; staged entries cleared.`,
    outcomeNothing: 'Nothing to write.',
    outcomeWrote: (changes: string, files: string, stillStaged: number) =>
      `Wrote ${changes} to ${files}. ${stillStaged} still staged.`,
    statusPlanning: 'Planning the writes…',
    statusApplying: (files: string) => `Writing ${files} in your dbt repo…`,
    statusRefreshing: 'Re-reading the graph…',
    buttonApplying: 'Applying…',
    buttonApply: 'Apply',
    buttonApplyFiles: (files: string) => `Apply ${files}`,
    nothingToWrite: 'No writes required',
    writeTitle: 'Write these files',
  },

  /** The staged workspace: everything waiting for `stitch apply`. */
  staged: {
    pageTitle: 'Staged changes',
    readOnly: 'This view is read-only. Open stitch serve to review staged changes.',
    panelLabel: 'Staged changes',
    title: (total: number) => `Staged changes (${total})`,
    hide: 'Hide staged changes',
    empty:
      'Nothing staged yet — edit a description on a table or column page.',
    relationships: 'Relationships',
    descriptions: 'Description edits',
    unresolved: 'Not in this graph',
    unresolvedTitle: 'Its model is not in this graph',
    editRelationshipTitle: 'Change cardinality',
    editRelationshipLabel: (model: string, column: string) =>
      `Edit staged relationship ${model}.${column}`,
    discardRelationshipTitle: 'Discard this staged relationship',
    discardRelationshipLabel: (model: string, column: string) =>
      `Discard staged relationship ${model}.${column}`,
    editDescriptionTitle: 'Open the table’s page to re-edit this description',
    discardDescriptionTitle: 'Discard this staged edit',
    discardDescriptionLabel: (label: string) => `Discard staged description ${label}`,
    edit: 'Edit',
    applyNothing: 'Nothing staged',
    applyTitle: 'Review the exact YAML changes, then write them',
    apply: 'Review & apply…',
    applyReassurance: 'nothing touches the repo until you confirm in the preview',
    /** No apply endpoint on this build: the CLI is the way through. */
    cliOnly: (): ReactNode => (
      <>
        run <code>stitch apply</code> to write these into the dbt repo
      </>
    ),
  },

  /** Reading and staging a description, on a model or column page. */
  description: {
    staging: 'Staging…',
    fieldLabel: 'Description',
    /** Where the edit goes, and the two keys that end it. */
    hint: (): ReactNode => (
      <>
        Staged in <code>.stitch/</code> only — <code>stitch apply</code> writes it into the model’s{' '}
        <code>_schema.yml</code>. ⌘/Ctrl-Enter saves, Esc cancels.
      </>
    ),
    stageEdit: 'Stage edit',
    cancel: 'Cancel',
    discardTitle: 'Drop the staged edit and reset state',
    discard: 'Discard staged edit',
    none: 'No description.',
    stagedBadge: 'Staged edit',
    stagedBadgeTitle: 'Not in the repo until `stitch apply`',
    workspaceLink: 'In the staged workspace →',
    whatRepoSays: 'What the repo says',
    repoHasNone: 'The repo has none yet',
    edit: 'Edit description',
    add: 'Add a description',
    notEditable: 'Not editable here',
  },

  /** Editing a previously staged relationship. */
  stage: {
    titleEdit: 'Edit this staged relationship',
    cardinality: 'Cardinality',
    /** Where the declaration goes, and what it takes to make it real. */
    note: (): ReactNode => (
      <>
        This stages the declaration in <code>.stitch/</code> only. Run <code>stitch apply</code> to
        write it into the model’s <code>_schema.yml</code> — nothing touches the repo before that.
      </>
    ),
    cancel: 'Cancel',
    saving: 'Saving…',
    saveEdit: 'Save edit',
  },

  /** The legend under each canvas: what a mark and what a line mean. */
  legend: {
    /** The three marks. The warehouse side splits by who manages it (#187). */
    dbt: 'managed by dbt',
    snowflake: 'source — landed in Snowflake, not dbt-managed',
    metabase: 'Metabase / BI',
    rollupThickness: 'thickness = contributing columns',
    exact: 'exact',
    inexact: 'parsed / inferred / name match — hover an edge for evidence',
  },

  /** The unified diff inside the apply dialog. */
  diff: {
    empty: 'No file changes — the repo already says all of this.',
  },
}
