"""Native SQL card resolution: template-tag substitution + sqlglot (SPEC.md section 7.4).

A native card's SQL is the *hard* parsing problem dbt's compiled_code is not: it is
written against the warehouse by hand and it is not valid SQL until Metabase has
substituted its template tags. So resolution runs in two passes -- substitute, then
parse -- and every step that cannot be completed degrades instead of raising.

Called from resolve/metabase.py, which owns the coverage counters and the edges. Pure
string/AST work: no filesystem, no network (SPEC.md section 4 seam).
"""

import re
from typing import Any, NamedTuple

import sqlglot
from pydantic import BaseModel, Field
from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.optimizer.qualify import qualify as _sqlglot_qualify
from sqlglot.optimizer.scope import traverse_scope
from sqlglot.schema import MappingSchema

_DIALECT = "snowflake"

# Metabase's template-tag syntax. `{{...}}` and `[[...]]` never nest in a valid card,
# so a non-greedy single pass over each is the whole grammar.
_TAG_RE = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")
_OPTIONAL_RE = re.compile(r"\[\[(.*?)\]\]", re.DOTALL)
_CARD_TAG_RE = re.compile(r"^#\s*(\d+)")
_SNIPPET_TAG_RE = re.compile(r"^snippet\s*:\s*(.+)$", re.IGNORECASE)
# table-level fallback when the SQL does not parse at all: only names that resolve to a
# real Metabase table survive, so a CTE or a typo never reaches the graph as a "table"
_FROM_RE = re.compile(
    r"\b(?:from|join)\s+([A-Za-z_][\w$]*(?:\s*\.\s*[A-Za-z_][\w$]*){0,2})", re.IGNORECASE
)

# One neutral literal per tag type, chosen so the substituted SQL parses -- nothing here
# is ever executed, so the only requirement is that sqlglot accepts it in the position
# the author used the tag in. A field-filter (`dimension`) tag stands in for a whole
# boolean condition, which is why it substitutes a predicate rather than a value.
_TAG_LITERALS = {
    "number": "1",
    "boolean": "TRUE",
    "date": "'1970-01-01'",
    "datetime": "'1970-01-01'",
    "temporal-unit": "'day'",
    "dimension": "1 = 1",
}
_DEFAULT_TAG_LITERAL = "'stitch'"
# a card reference is a subquery in Metabase; the placeholder keeps the SQL parseable and
# deliberately exposes no column of its own, so nothing physical is ever attributed to it
# (the referenced card's own fields arrive through the card-on-card machinery instead)
_CARD_PLACEHOLDER = "(select 1 as stitch_card_ref)"
_MAX_SNIPPET_DEPTH = 3
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _error_text(exc: Exception) -> str:
    """First line of a sqlglot error, without its ANSI highlighting or SQL echo.

    The reason string is stored in graph.json and printed by `stitch doctor`: escape codes
    would corrupt both, and the echoed fragment would put a card's SQL somewhere it is not
    expected to appear.
    """
    return _ANSI_RE.sub("", str(exc).splitlines()[0].strip())


class Snippets(NamedTuple):
    """Native-query snippets, indexed both ways a tag can name one."""

    by_name: dict[str, str]
    by_id: dict[int, str]


EMPTY_SNIPPETS = Snippets({}, {})


class TableIndex(NamedTuple):
    """One Metabase database's tables, indexed the way a SQL identifier arrives.

    by_qualified keys are (schema, table) casefolded; by_name maps a bare table name to
    every table id carrying it (a native query has no schema context, so a bare name is
    only usable when it is unique); location gives a table id's (schema, name) in
    Metabase's own casing; columns is the {table_id: {column: field_id}} map and
    field_ids its flattened values, for validating a field id a template tag names.
    """

    by_qualified: dict[tuple[str, str], int]
    by_name: dict[str, list[int]]
    location: dict[int, tuple[str, str]]
    columns: dict[int, dict[str, int]]
    field_ids: set[int]


class NativeField(NamedTuple):
    """One field a native card consumes. exact is True only for a field id read out of a
    field-filter template tag -- structured metadata, as certain as an MBQL ref. Anything
    sqlglot resolved out of the SQL text is `parsed`."""

    field_id: int
    exact: bool
    evidence: dict[str, Any]


class NativeResolution(BaseModel):
    """What resolve/metabase.py needs to turn one native card into graph edges.

    fields are the consumed mb_fields; upstream_cards the cards this one reads from
    (`{{#123-card}}` tags), for the existing transitive machinery; tables the physical
    tables the SQL names, for the table-level degrade; problems the itemized failures
    (same shape as the MBQL walk's, so `stitch doctor` renders them unchanged).
    """

    fields: list[NativeField] = Field(default_factory=list)
    upstream_cards: list[int] = Field(default_factory=list)
    tables: list[str] = Field(default_factory=list)
    problems: list[dict[str, Any]] = Field(default_factory=list)


def native_text(stage_or_query: Any) -> tuple[str, dict[str, Any]]:
    """(SQL text, template tags) out of either native shape.

    Legacy is `dataset_query.native` = {"query": sql, "template-tags": {...}}; MBQL 5 is
    a first stage whose `native` is the SQL string itself, with the tags beside it on the
    stage. Both spellings of the tag container are read, because instances in the wild
    emit the dict form inside a stage too.
    """
    if not isinstance(stage_or_query, dict):
        return "", {}
    native = stage_or_query.get("native")
    if isinstance(native, dict):
        sql = native.get("query")
        tags = native.get("template-tags")
    else:
        sql = native
        tags = stage_or_query.get("template-tags")
    return (
        sql if isinstance(sql, str) else "",
        tags if isinstance(tags, dict) else {},
    )


def resolve_native_sql(
    raw_sql: str,
    template_tags: dict[str, Any],
    snippets: Snippets,
    index: TableIndex | None,
) -> NativeResolution:
    """Resolve one native card's SQL to the mb_fields it consumes.

    Pass 1 -- substitution (`_substitute`), because the stored SQL is a template:
      * `{{variable}}` becomes a neutral literal chosen by the tag's declared type. The
        value is irrelevant to lineage; only the shape has to parse.
      * `{{field_filter}}` (a `dimension` tag) becomes `1 = 1`, and the tag's own field
        ref is recorded as a consumed field with confidence exact -- it names a field id
        outright, so parsing never enters into it.
      * `[[optional clauses]]` keep their contents and lose their brackets. The other
        reading -- drop the clause -- is the wrong kind of conservative here: a column
        only referenced inside an optional filter still breaks this card when it is
        renamed, and a blast radius that misses it is a false negative in the one report
        the tool exists to produce. Keeping the clause can over-report consumption; that
        is the direction to err in.
      * `{% snippet %}` / `{{snippet: name}}` inline the snippet's SQL when it was
        fetched, recursively (a snippet may use snippets), depth-capped. An unknown
        snippet substitutes empty and is recorded -- the card still resolves whatever
        the rest of its SQL supports.
      * `{{#123-card}}` records card 123 as upstream (the caller routes it through the
        same transitive resolution as MBQL card-on-card) and substitutes a column-less
        subquery placeholder, so nothing physical is attributed to it here.

    Pass 2 -- parse and resolve. Table references are rewritten to the canonical
    `schema.table` Metabase knows them by (a native query may name a table bare, or
    fully qualified with the warehouse database, which is not the Metabase display name
    and is therefore ignored: the card's database id already pins the connection).
    sqlglot then qualifies the query against a schema map built from those tables --
    which is what resolves unqualified columns and expands `select *` -- and every column
    that lands on a physical table becomes that table's field.

    Failure degrades, never raises: an unparseable card falls back to the table names its
    SQL mentions (`tables`), a table Metabase does not know, a column its metadata does
    not list, and a column no source claims are each recorded in `problems`. The caller
    counts a native card as resolved only when `problems` is empty.
    """
    result = NativeResolution()
    sql = _substitute(raw_sql, template_tags, snippets, index, result, depth=0)
    if not sql.strip():
        result.problems.append({"ref": None, "reason": "native SQL is empty"})
        return result
    if index is None:
        result.problems.append({"ref": None, "reason": "native card's database is not in scope"})
        return result
    try:
        parsed = sqlglot.parse_one(sql, dialect=_DIALECT)
    except SqlglotError as exc:
        result.problems.append(
            {"ref": None, "reason": f"native SQL parse failure: {_error_text(exc)}"}
        )
        result.tables = _tables_by_regex(sql, index)
        return result

    referenced = _canonicalize_tables(parsed, index, result)
    result.tables = sorted(f"{schema}.{table}" for schema, table in referenced.values())
    mapping: dict[str, Any] = {}
    for table_id, (schema, table) in referenced.items():
        mapping.setdefault(schema, {})[table] = dict.fromkeys(
            index.columns.get(table_id, {}), "TEXT"
        )
    try:
        qualified = _sqlglot_qualify(
            parsed,
            schema=MappingSchema(mapping, dialect=_DIALECT),
            dialect=_DIALECT,
            validate_qualify_columns=False,
            # a hand-written card outliving a Metabase metadata sync is normal; without
            # this, one column the field map is missing raises and costs the whole card
            allow_partial_qualification=True,
        )
    except SqlglotError as exc:
        result.problems.append(
            {"ref": None, "reason": f"native SQL parse failure: {_error_text(exc)}"}
        )
        return result
    _collect_columns(qualified, index, result)
    # a field can arrive twice (a field filter on a column the SQL also names); the exact
    # ref wins, and sorting keeps the caller's edge order independent of walk order
    deduped: dict[int, NativeField] = {}
    for field in result.fields:
        previous = deduped.get(field.field_id)
        if previous is None or (field.exact and not previous.exact):
            deduped[field.field_id] = field
    result.fields = [deduped[field_id] for field_id in sorted(deduped)]
    # a card that only reads another card resolves no column of its own, and legitimately:
    # its fields arrive from the referenced card, not from this SQL
    if not result.fields and not result.problems and not result.upstream_cards:
        result.problems.append({"ref": None, "reason": "native SQL references no known column"})
    return result


def _substitute(
    sql: str,
    template_tags: dict[str, Any],
    snippets: Snippets,
    index: TableIndex | None,
    result: NativeResolution,
    depth: int,
) -> str:
    """Metabase's own substitution pass, done for lineage instead of execution."""
    without_optional = _OPTIONAL_RE.sub(lambda match: match.group(1), sql)
    return _TAG_RE.sub(
        lambda match: _substitute_tag(
            match.group(1).strip(), template_tags, snippets, index, result, depth
        ),
        without_optional,
    )


def _substitute_tag(
    inner: str,
    template_tags: dict[str, Any],
    snippets: Snippets,
    index: TableIndex | None,
    result: NativeResolution,
    depth: int,
) -> str:
    card_match = _CARD_TAG_RE.match(inner)
    if card_match:
        result.upstream_cards.append(int(card_match.group(1)))
        return _CARD_PLACEHOLDER
    snippet_match = _SNIPPET_TAG_RE.match(inner)
    tag = template_tags.get(inner) if isinstance(template_tags.get(inner), dict) else {}
    tag_type = str(tag.get("type") or "")
    if snippet_match or tag_type == "snippet":
        name = snippet_match.group(1).strip() if snippet_match else str(tag.get("snippet-name") or "")
        return _substitute_snippet(name, tag, template_tags, snippets, index, result, depth)
    if tag_type == "card":
        card_id = tag.get("card-id")
        if isinstance(card_id, int):
            result.upstream_cards.append(card_id)
        else:
            result.problems.append({"ref": inner, "reason": "card template tag has no card id"})
        return _CARD_PLACEHOLDER
    if tag_type == "dimension":
        _record_dimension_tag(inner, tag, index, result)
    # an undeclared tag is still a value the author wrote somewhere; a string literal is
    # the substitution most positions accept, and nothing here is executed
    return _TAG_LITERALS.get(tag_type, _DEFAULT_TAG_LITERAL)


def _substitute_snippet(
    name: str,
    tag: dict[str, Any],
    template_tags: dict[str, Any],
    snippets: Snippets,
    index: TableIndex | None,
    result: NativeResolution,
    depth: int,
) -> str:
    snippet_id = tag.get("snippet-id")
    content = snippets.by_name.get(name.casefold())
    if content is None and isinstance(snippet_id, int):
        content = snippets.by_id.get(snippet_id)
    if content is None:
        result.problems.append(
            {"ref": f"snippet: {name}" if name else "snippet", "reason": "snippet not available"}
        )
        return ""
    if depth >= _MAX_SNIPPET_DEPTH:
        result.problems.append(
            {"ref": f"snippet: {name}", "reason": "snippet nesting too deep to inline"}
        )
        return ""
    return _substitute(content, template_tags, snippets, index, result, depth + 1)


def _record_dimension_tag(
    inner: str, tag: dict[str, Any], index: TableIndex | None, result: NativeResolution
) -> None:
    """A field-filter tag names its field outright -- the one exact ref a native card has."""
    dimension = tag.get("dimension")
    if not isinstance(dimension, list) or len(dimension) < 2 or dimension[0] != "field":
        result.problems.append({"ref": inner, "reason": "field filter tag has no field ref"})
        return
    # MBQL 5 moved the options map into the middle of a ref: ["field", {opts}, id]
    target = dimension[2] if len(dimension) > 2 and isinstance(dimension[1], dict) else dimension[1]
    if not isinstance(target, int) or index is None or target not in index.field_ids:
        result.problems.append({"ref": inner, "reason": "field filter tag names an unknown field"})
        return
    result.fields.append(
        NativeField(target, True, {"source": "template_tag", "clauses": ["template-tag.dimension"]})
    )


def _canonicalize_tables(
    parsed: exp.Expression, index: TableIndex, result: NativeResolution
) -> dict[int, tuple[str, str]]:
    """Rewrite every resolvable table reference to `schema.table`, and report the rest.

    A native query names tables however the author felt like: bare, schema-qualified, or
    fully qualified with the warehouse database. The catalog part is dropped rather than
    checked -- Metabase's display name is not the warehouse database name (that is what
    the config `databases` map exists for), and the card's own database id has already
    pinned which connection this is. Rewriting to one canonical depth is what lets the
    schema map be built at one depth, which is what sqlglot requires.
    """
    cte_names = {cte.alias_or_name.casefold() for cte in parsed.find_all(exp.CTE)}
    referenced: dict[int, tuple[str, str]] = {}
    for table in parsed.find_all(exp.Table):
        if not table.name or (not table.db and table.name.casefold() in cte_names):
            continue
        table_id = _lookup_table(table.db, table.name, index)
        if table_id is None:
            written = ".".join(part for part in (table.catalog, table.db, table.name) if part)
            result.problems.append(
                {"ref": written, "reason": "table not found in Metabase metadata"}
            )
            continue
        schema, name = index.location[table_id]
        referenced[table_id] = (schema, name)
        table.set("catalog", None)
        table.set("db", exp.to_identifier(schema))
        table.set("this", exp.to_identifier(name))
    return referenced


def _lookup_table(db: str, name: str, index: TableIndex) -> int | None:
    """Table id for a written reference; a bare name only when it is unambiguous."""
    if db:
        return index.by_qualified.get((db.casefold(), name.casefold()))
    candidates = index.by_name.get(name.casefold(), [])
    return candidates[0] if len(candidates) == 1 else None


def _collect_columns(
    qualified: exp.Expression, index: TableIndex, result: NativeResolution
) -> None:
    """Every column of the qualified query that lands on a physical table.

    sqlglot's scopes carry the alias -> source map, so a column's table alias resolves to
    the exp.Table it came from (or to a CTE/subquery scope, which is not a consumption --
    the columns it selects were already collected in its own scope).
    """
    seen: set[int] = set()
    for scope in traverse_scope(qualified):
        outputs = {name.casefold() for name in scope.expression.named_selects}
        for column in scope.columns:
            source = scope.sources.get(column.table)
            if not isinstance(source, exp.Table):
                if not column.table and column.name.casefold() not in outputs:
                    result.problems.append(
                        {"ref": column.sql(dialect=_DIALECT), "reason": "column matches no source"}
                    )
                # an unqualified column that IS an output name is an order-by/having
                # reference to a select alias, not a table column: nothing to resolve
                continue
            table_id = index.by_qualified.get((source.db.casefold(), source.name.casefold()))
            if table_id is None:
                continue  # its table was already reported unknown
            # sqlglot upper-cases identifiers for Snowflake, so the AST is no place to read
            # a table's name from -- evidence carries Metabase's own spelling
            schema, table = index.location[table_id]
            field_id = index.columns.get(table_id, {}).get(column.name.casefold())
            if field_id is None:
                result.problems.append(
                    {
                        "ref": f"{schema}.{table}.{column.name}",
                        "reason": "column not listed in Metabase metadata",
                    }
                )
                continue
            if field_id in seen:
                continue
            seen.add(field_id)
            result.fields.append(
                NativeField(field_id, False, {"source": "native_sql", "table": f"{schema}.{table}"})
            )


def _tables_by_regex(sql: str, index: TableIndex) -> list[str]:
    """Table-level degrade for SQL that would not parse: the FROM/JOIN targets that are
    real Metabase tables. Anything else the regex catches -- a CTE, a table function, a
    typo -- fails the metadata lookup and never reaches the graph."""
    tables: set[str] = set()
    for written in _FROM_RE.findall(sql):
        parts = [part.strip() for part in written.split(".")]
        table_id = _lookup_table(parts[-2] if len(parts) > 1 else "", parts[-1], index)
        if table_id is not None:
            schema, name = index.location[table_id]
            tables.add(f"{schema}.{name}")
    return sorted(tables)
