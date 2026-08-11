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
  /** The shell: loading, fatal errors, the dev banner. */
  app: {
    loadingView: 'Loading view…',
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
    erd: 'ERD',
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
    lineage: 'Lineage',
    erd: 'ERD',
    coverage: 'Coverage',
  },

  /** Search, inline on the home page and inside the palette. */
  search: {
    placeholder: 'Search models, columns, cards, dashboards…',
    ariaLabel: 'Search the lineage graph',
    empty: 'No matches.',
    archived: 'archived in Metabase',
    archivedTag: 'archived',
    viaField: (field: string) => `via ${field}`,
  },

  /** ⌘K. */
  palette: {
    placeholder: 'Jump to anything…',
    hint: '↑↓ navigate · ↵ open · esc close',
  },

  /** The system marks, for anyone reading with a screen reader. */
  badges: {
    snowflake: 'Snowflake (dbt / warehouse)',
    metabase: 'Metabase (BI)',
  },

  /** Dependency and BI fan-out lists on a detail page. */
  fanout: {
    hops: 'steps along the dependency chain',
    noDashboard: 'not pinned to any dashboard',
    cardsOnNoDashboard: 'cards on no dashboard',
    details: 'details',
    openExternal: 'open ↗',
  },

  /** The coverage lists behind the home page's gaps. */
  coverage: {
    notInGraph: 'not in this graph',
    groupBy: 'Group by',
    backToOverview: '← Overview',
    openGroup: (label: string) => `Open ${label} →`,
    empty: 'Nothing here — this build has full coverage.',
  },

  /** The ERD canvas, its toolbar and the suggestions panel. */
  erd: {
    openHint: 'Open details · ⌘/Ctrl-click for lineage',
    focusHint: 'Show only this table and what it joins · click again for the whole scope',
    unfocusHint: 'Back to the whole scope',
    relateHint: 'Drag onto another column to declare a relationship',
    externalTag: 'other scope',
    physicalTable: (table: string) => `${table} — physical table in the warehouse`,
    phantomColumn: (openHint: string) =>
      `${openHint} · declared by a relationship, not in the catalog`,
    empty: 'No models in the graph — nothing to draw.',
    scope: 'Scope',
    externalCount: (n: number) =>
      `${n} model${n === 1 ? '' : 's'} from other scopes, drawn because a relationship in this one points at them`,
    internalScope: 'tooling schema — not part of the analytics model',
    configuredScopeTitle: 'serve.erd_default_scope in stitch.yml',
    configuredScopeMissing: (scope: ReactNode): ReactNode => (
      <>
        configured scope <code>{scope}</code> is not in this graph
      </>
    ),
    resetStale: 'the relationships changed — reset to lay the scope out again',
    reset: 'lay the scope out again and fit it to the window (drops dragged positions)',
    resetView: 'Reset view',
    resetMoved: (moved: number) => ` (${moved} moved)`,
    stagedTitle: 'everything waiting for `stitch apply` — relationships and description edits',
    stagedButton: (total: number) => `Staged changes (${total})`,
    suggestedButton: (n: number) => `Suggested (${n})`,
    /** The live focus filter, stated next to the way out of it (#163). */
    showingFocus: (name: string) => `Showing ${name} + relations — show all`,
    hintStaging:
      "click a table to see only what it joins · drag from a column's edge onto another column to declare a relationship · ↗ for details",
    hintReadOnly: 'click a table to see only what it joins · ↗ or a column for details',
    suggestPanelLabel: 'Suggested relationships',
    suggestTitle: (n: number) => `Suggested (${n})`,
    suggestCapTitle: 'the panel lists them all; the canvas draws the strongest',
    suggestNotDrawnCount: (n: number) => `${n} not drawn`,
    hideSuggestions: 'Hide suggestions',
    suggestReachLabel: 'Suggestion reach',
    suggestInScopeTitle: (scope: string) =>
      `candidates with both models in ${scope} — the ones this canvas draws`,
    suggestInScope: (scope: string, n: number) => `in ${scope} (${n})`,
    suggestAllScopesTitle:
      'every candidate in the graph, including pairs that join a model in another scope — accepting one does not need it drawn',
    suggestAllScopes: (n: number) => `all scopes (${n})`,
    suggestSourceLabel: 'Suggestion source',
    suggestEveryCandidate: 'every candidate',
    suggestAll: 'all',
    notDrawnTitle: 'listed here, but not drawn on this canvas',
    notDrawn: 'not drawn',
    elsewhereTitle: (scope: string) =>
      `this pair lives in ${scope} — accepting it stages the columns by name, no drawing needed`,
    accept: 'Accept…',
    dismissTitle: 'dismissed permanently — it will not be suggested again',
    dismiss: 'Dismiss',
  },

  /** The detail panels: column, model, source, card, dashboard, field. */
  node: {
    placeholder: 'Only referenced by an edge — this build never resolved a definition for it.',
    viewLineage: 'View lineage →',
    openInMetabase: 'Open in Metabase ↗',
    definedAs: 'Defined as',
    upstream: (columns: string, sources: string) => `Upstream — ${columns}, ${sources}`,
    downstream: (headline: string) => `Downstream — ${headline}`,
    fanoutTruncated: '(fan-out truncated for display)',
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
    relationships: (count: number) => `Relationships — ${count}`,
    dependencies: (upstream: string, downstream: string) =>
      `Dependencies — ${upstream} upstream, ${downstream} downstream`,
    biUsage: (cards: string, dashboards: string) => `BI usage — ${cards} on ${dashboards}`,
    columns: (count: number) => `Columns — ${count}`,
    nothingUpstreamSource: 'nothing upstream — this is where the data enters',
    nothingUpstream: 'nothing upstream in this graph',
    nothingDownstream: 'nothing downstream — this is a leaf',
    noBiUsage: 'no Metabase card reads this table in this graph',
    references: 'references',
    referencedBy: 'referenced by',
    validated: 'validated by a dbt relationships test',
    notFound: 'Node not found',
    notFoundDetail: (): ReactNode => <> is not in the loaded graph. It may have been removed in a newer build.</>,
  },

  /** The lineage canvas. */
  lineage: {
    unknownNode: (nodeId: string): ReactNode => <>Unknown node: {nodeId}</>,
    openInMetabase: (name: string) => `Open “${name}” in Metabase`,
    openDetail: (type: string, name: string) => `Open the ${type} page for ${name}`,
    truncatedCounts:
      'This walk hit its node cap, so these are lower bounds — the real counts are higher.',
    grainLabel: 'Lineage grain',
    grainColumn: 'Which columns feed which fields',
    grainTable: 'Which models feed which cards — the same subgraph rolled up',
    details: 'Details',
    truncated: 'large fan-out truncated',
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
        {descriptions === 1 ? '' : 's'} — the canvas and the panels
        already show it.
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
    nothingToWrite: 'nothing to write — the repo already says all of this',
    writeTitle: 'write these files in the dbt repo',
  },

  /** The staged workspace: everything waiting for `stitch apply`. */
  staged: {
    panelLabel: 'Staged changes',
    title: (total: number) => `Staged changes (${total})`,
    hide: 'Hide staged changes',
    empty:
      'Nothing staged yet — drag a column handle onto another, accept a suggestion, or edit a description on a table’s page.',
    relationships: 'Relationships',
    descriptions: 'Description edits',
    unresolved: 'not in this graph',
    unresolvedTitle: 'its model is not in this graph',
    editRelationshipTitle: 'change the columns or the cardinality',
    editRelationshipLabel: (model: string, column: string) =>
      `Edit staged relationship ${model}.${column}`,
    discardRelationshipTitle: 'discard this staged relationship',
    discardRelationshipLabel: (model: string, column: string) =>
      `Discard staged relationship ${model}.${column}`,
    editDescriptionTitle: 'open the table’s page to re-edit this description',
    discardDescriptionTitle: 'discard this staged edit',
    discardDescriptionLabel: (label: string) => `Discard staged description ${label}`,
    edit: 'edit',
    applyNothing: 'nothing staged',
    applyTitle: 'review the exact YAML changes, then write them',
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
    fieldLabel: 'Description',
    /** Where the edit goes, and the two keys that end it. */
    hint: (): ReactNode => (
      <>
        Staged in <code>.stitch/</code> only — <code>stitch apply</code> writes it into the model’s{' '}
        <code>_schema.yml</code>. ⌘/Ctrl-Enter saves, Esc cancels.
      </>
    ),
    staging: 'Staging…',
    stageEdit: 'Stage edit',
    cancel: 'Cancel',
    discardTitle: 'drop the staged edit and go back to what the repo says',
    discard: 'Discard staged edit',
    none: 'No description.',
    stagedBadge: 'staged edit',
    stagedBadgeTitle: 'not in the repo until `stitch apply`',
    workspaceLink: 'in the staged workspace →',
    whatRepoSays: 'what the repo says',
    repoHasNone: 'the repo has none yet',
    edit: 'Edit description',
    add: 'Add a description',
    notEditable: 'not editable here',
  },

  /** The mini relationship star on a model's page. */
  star: {
    openNode: (label: string) => `${label} — open`,
    hubNode: (label: string) => `${label} — you are here`,
    /** Nothing to draw yet, and the one place to go and draw it. */
    empty: (erdLink: string): ReactNode => (
      <>
        No relationships on this table yet — <a href={erdLink}>draw one in the ERD</a> and it shows
        up here.
      </>
    ),
    seeAll: 'see them all in the ERD →',
  },

  /** Staging a relationship a reader has just drawn, or accepting a suggestion. */
  stage: {
    titleEdit: 'Edit this staged relationship',
    titleAccept: 'Accept this relationship',
    titleStage: 'Stage a relationship',
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
    staging: 'Staging…',
    saveEdit: 'Save edit',
    stageRelationship: 'Stage relationship',
  },

  /** The legend under each canvas: what a line means. */
  legend: {
    dbt: 'dbt / warehouse',
    metabase: 'Metabase / BI',
    rollupThickness: 'thickness = contributing columns',
    declared: 'declared relationship — hover or click it to light the columns it joins',
    /** The heavier stroke and what proves it (#164) — the `<code>` is the test's name. */
    validated: (): ReactNode => (
      <>
        heavier line — validated by a dbt <code>relationships</code> test
      </>
    ),
    staged: (): ReactNode => (
      <>
        staged — not in the repo until <code>stitch apply</code>
      </>
    ),
    suggested: 'suggested — nobody has declared this yet',
    exact: 'exact',
    inexact: 'parsed / inferred / name match — hover an edge for evidence',
  },

  /** The unified diff inside the apply dialog. */
  diff: {
    empty: 'No file changes — the repo already says all of this.',
  },
}
