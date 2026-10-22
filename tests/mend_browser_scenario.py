"""Synthetic browser Mend estate shared by integration tests and the local demo."""

from copy import deepcopy
from pathlib import Path

import mend_scenario as scenario
from test_mend_apply import FakeMetabase

from stitch_lineage.graph.schema import Confidence, Edge, EdgeType, Node, NodeType
from stitch_lineage.io.graph_store import previous_graph_path, write_graph
from stitch_lineage.io.metabase_client import MetabaseAPIError

CONFIG = """metabase:
  url: https://metabase.example.test
  api_key: ${STITCH_MEND_DEMO_KEY}
  databases:
    - metabase_name: Analytics
      dbt_database: ANALYTICS
mend:
  auto: [repoint, strip, archive]
  notify_only_collections: ['*Personal*']
"""


def baseline():
    graph = scenario.baseline_graph()
    for card_id, name in [
        (407, "Revenue SQL"),
        (408, "Read-only revenue"),
        (409, "Restricted revenue"),
    ]:
        graph.nodes.append(
            Node(node_id=f"mb_card::{card_id}", node_type=NodeType.MB_CARD, name=name)
        )
        graph.edges.append(
            Edge(
                from_=f"mb_field::{scenario.F_AMOUNT}",
                to=f"mb_card::{card_id}",
                edge_type=EdgeType.CONSUMED_BY,
                confidence=Confidence.EXACT,
            )
        )
    return graph


def project(directory: Path) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    config = directory / "stitch.yml"
    config.write_text(CONFIG)
    graph = directory / ".stitch/graph.json"
    write_graph(scenario.candidate_graph(), graph)
    write_graph(baseline(), previous_graph_path(graph))
    return config, graph


class BrowserMetabase(FakeMetabase):
    def __init__(self, **kwargs):
        self.payload = scenario.payload()
        self.payload.database_metadata = {
            1: {
                "tables": [
                    {
                        "id": 5,
                        "fields": [
                            {"id": scenario.F_AMOUNT_USD, "name": "AMOUNT_USD", "active": True},
                            {"id": scenario.F_CREATED, "name": "CREATED_AT", "active": True},
                            {"id": scenario.F_REGION, "name": "REGION", "active": True},
                        ],
                    }
                ]
            }
        }
        native = deepcopy(self.payload.cards[0])
        native.update(
            id=407,
            name="Revenue SQL",
            dataset_query={
                "type": "native",
                "database": 1,
                "native": {"query": "select sum(amount) from fct_orders"},
            },
        )
        readonly = deepcopy(self.payload.cards[0])
        readonly.update(id=408, name="Read-only revenue", can_write=False)
        self.payload.cards.extend([native, readonly])
        super().__init__(
            cards={c["id"]: deepcopy(c) for c in self.payload.cards},
            dashboards={d["id"]: deepcopy(d) for d in self.payload.dashboards},
            **kwargs,
        )
        self.fetches = 0
        self.on_fetch = None

    def fetch_all(self, _names):
        self.fetches += 1
        if self.on_fetch:
            self.on_fetch()
        self._maybe_fail("fetch_all")
        return self.payload.model_copy(deep=True)

    def list_collections(self):
        self._maybe_fail("list_collections")
        return deepcopy(self.payload.collections)

    def get_card(self, card_id):
        if card_id not in self.cards:
            raise MetabaseAPIError("HTTP 403", status_code=403)
        return deepcopy(super().get_card(card_id))

    def update_card(self, card_id, changes):
        result = super().update_card(card_id, changes)
        self.cards[card_id].update(deepcopy(changes))
        return result

    def update_dashcards(self, dash_id, dashcards):
        result = super().update_dashcards(dash_id, dashcards)
        self.dashboards[dash_id]["dashcards"] = deepcopy(dashcards)
        return result
