import json
import re

import pytest

from stitch_lineage.app import frontend_dist
from stitch_lineage.export.static_site import export_site
from stitch_lineage.graph.schema import Node, NodeType
from stitch_lineage.io.graph_store import write_graph

GLOBAL_LINE = re.compile(r"^\s*window\.__STITCH_(GRAPH|META)__ = (?P<json>.+);$", re.MULTILINE)


def _inlined(index_html: str) -> dict[str, dict]:
    """The injected globals. index.html documents the injection in an HTML comment that
    matches the same shape, so only parseable payloads count."""
    found = {}
    for match in GLOBAL_LINE.finditer(index_html):
        try:
            found[match.group(1)] = json.loads(match.group("json").replace("<\\/", "</"))
        except json.JSONDecodeError:
            continue
    return found


@pytest.fixture
def graph_path(tmp_path, sample_graph):
    path = tmp_path / ".stitch" / "graph.json"
    write_graph(sample_graph, path)
    return path


def test_marker_is_replaced_by_parseable_globals(tmp_path, graph_path, sample_graph):
    out = export_site(graph_path, tmp_path / "site", "https://mb.example.com")
    index_html = (out / "index.html").read_text()
    assert "__STITCH_INLINE_DATA__" not in index_html
    globals_ = _inlined(index_html)
    assert len(globals_["GRAPH"]["nodes"]) == len(sample_graph.nodes)
    assert len(globals_["GRAPH"]["edges"]) == len(sample_graph.edges)
    assert globals_["META"] == {
        "metabase_url": "https://mb.example.com",
        "generated_at": sample_graph.generated_at,
        "schema_version": sample_graph.schema_version,
        "erd_default_scope": None,
        "staging_enabled": False,
    }


def test_configured_erd_scope_is_inlined(tmp_path, graph_path):
    out = export_site(graph_path, tmp_path / "site", None, "schema:MARTS")
    meta = _inlined((out / "index.html").read_text())["META"]
    assert meta["erd_default_scope"] == "schema:MARTS"


def test_inlined_graph_matches_graph_json_exactly(tmp_path, graph_path):
    out = export_site(graph_path, tmp_path / "site", None)
    inlined = _inlined((out / "index.html").read_text())["GRAPH"]
    assert inlined == json.loads(graph_path.read_text())


def test_closing_script_tag_cannot_escape(tmp_path, sample_graph):
    hostile = sample_graph.model_copy(
        update={
            "nodes": [
                *sample_graph.nodes,
                Node(
                    node_id="model.demo.evil",
                    node_type=NodeType.MODEL,
                    name="evil",
                    description="</script><script>alert(1)</script>",
                ),
            ]
        }
    )
    graph_path = tmp_path / "graph.json"
    write_graph(hostile, graph_path)
    out = export_site(graph_path, tmp_path / "site", None)
    index_html = (out / "index.html").read_text()
    template = (frontend_dist() / "index.html").read_text()
    assert "<\\/script>" in index_html
    assert index_html.count("</script>") == template.count("</script>")
    # and it still parses back to the original text
    evil = next(
        node
        for node in _inlined(index_html)["GRAPH"]["nodes"]
        if node["node_id"] == "model.demo.evil"
    )
    assert evil["description"] == "</script><script>alert(1)</script>"


def test_assets_are_copied(tmp_path, graph_path):
    out = export_site(graph_path, tmp_path / "site", None)
    expected = {path.name for path in (frontend_dist() / "assets").iterdir()}
    assert {path.name for path in (out / "assets").iterdir()} == expected
    assert expected


def test_export_is_deterministic(tmp_path, graph_path):
    first = export_site(graph_path, tmp_path / "a", None)
    second = export_site(graph_path, tmp_path / "b", None)
    assert (first / "index.html").read_bytes() == (second / "index.html").read_bytes()


def test_repeated_export_into_the_same_dir_is_stable(tmp_path, graph_path):
    out = tmp_path / "site"
    export_site(graph_path, out, None)
    first = (out / "index.html").read_bytes()
    export_site(graph_path, out, None)
    assert (out / "index.html").read_bytes() == first


def test_unparseable_graph_raises(tmp_path):
    path = tmp_path / "graph.json"
    path.write_text("{not json")
    with pytest.raises(ValueError):
        export_site(path, tmp_path / "site", None)
