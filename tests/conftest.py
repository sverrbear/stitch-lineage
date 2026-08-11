import re

import pytest

from stitch_lineage.graph.schema import (
    Confidence,
    Coverage,
    Edge,
    EdgeType,
    Graph,
    Node,
    NodeType,
    column_node_id,
    mb_card_node_id,
    mb_field_node_id,
)

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def uncoloured(output: str) -> str:
    """Console output with Rich's colour taken out and its layout left alone.

    Rich treats `GITHUB_ACTIONS` as a tty, so CI renders styled even when nothing is
    piped anywhere -- and the highlighter wraps bare numbers and quoted strings in
    style codes mid-line. Use this where the *layout* is the assertion (a diff hunk's
    indentation, an aligned column, `lines[0] ==`), and pair it with `wide_console` so
    the width cannot wrap what the spacing is meant to prove.
    """
    return _ANSI.sub("", output)


def plain(output: str) -> str:
    """Console output with Rich's colour and line wrapping both taken back out.

    The default for asserting on a *message*: on top of `uncoloured`, every run of
    whitespace collapses to one space, so a sentence rewrapped at the runner's width
    still reads as one string. Reach for `uncoloured` instead when the whitespace is
    the thing being checked -- this deliberately destroys it.
    """
    return " ".join(uncoloured(output).split())


@pytest.fixture
def wide_console():
    """Pin the CLI console's width, for the tests that assert on layout.

    `plain()` rescues a *wrapped* line, but a Rich Table does not wrap -- it truncates
    cells to fit, and at 40 columns `fct_orders` renders as `fct_…`, which no amount of
    unwrapping brings back. Same for a diff hunk's indentation or `lines[0] ==`. A test
    about what a table (or a column, or a line) *says* has to fix the width it says it
    in rather than inherit the runner's.

    Restores `_width` rather than going through monkeypatch: `console.width` reads back
    as a computed int, so monkeypatch would "restore" a hard-coded 80 over rich's
    auto-detect and silently pin every later test in the session to that width -- which
    is precisely the environment-dependence this file exists to remove.
    """
    from stitch_lineage.cli import console

    original = console._width  # None means "ask the terminal at render time"
    console.width = 200
    try:
        yield
    finally:
        console._width = original


MODEL_ID = "model.smitten.fct_matches"
COLUMN_ID = column_node_id(MODEL_ID, "match_intensity")
FIELD_ID = mb_field_node_id(101)
CARD_ID = mb_card_node_id(412)


@pytest.fixture
def sample_graph() -> Graph:
    nodes = [
        Node(
            node_id=MODEL_ID,
            node_type=NodeType.MODEL,
            name="fct_matches",
            database="ANALYTICS",
            schema_="MARTS",
            table="FCT_MATCHES",
        ),
        Node(
            node_id=COLUMN_ID,
            node_type=NodeType.COLUMN,
            name="match_intensity",
            column="MATCH_INTENSITY",
            data_type="NUMBER",
        ),
        Node(node_id=FIELD_ID, node_type=NodeType.MB_FIELD, name="Match Intensity"),
        Node(
            node_id=CARD_ID,
            node_type=NodeType.MB_CARD,
            name="Match intensity by country",
            properties={"collection_id": 7, "creator": "sverrir"},
        ),
    ]
    edges = [
        Edge(
            from_=COLUMN_ID,
            to=FIELD_ID,
            edge_type=EdgeType.BINDS_TO,
            confidence=Confidence.EXACT,
        ),
        Edge(
            from_=FIELD_ID,
            to=CARD_ID,
            edge_type=EdgeType.CONSUMED_BY,
            confidence=Confidence.EXACT,
            evidence={"mbql": ["field", 101, None]},
        ),
    ]
    return Graph(
        generated_at="2026-08-06T00:00:00+00:00",
        dbt_invocation_id="abc-123",
        metabase_version="0.53.2",
        coverage=Coverage(models_bound=1, models_total=1, columns_traced=1, columns_total=1),
        nodes=nodes,
        edges=edges,
    )
