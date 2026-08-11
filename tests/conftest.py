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


def plain(output: str) -> str:
    """Console output with Rich's colour and line wrapping taken back out.

    Rich treats `GITHUB_ACTIONS` as a tty, so CI renders styled and wrapped to the
    runner's width: a substring assertion over raw stdout pins the terminal the test
    ran in rather than the message it was meant to check. Strip the escapes, collapse
    every run of whitespace, and what is left is the text.
    """
    return " ".join(_ANSI.sub("", output).split())


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
