"""scripts/graph_hash.py: 행 순서 무시 · 중복 행 유지 · 건수 반영."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import graph_hash as G  # noqa: E402

ITEMS = [{"id": "a", "intent": "q", "params": {}}, {"id": "b", "intent": "q", "params": {}}]
COUNTS = {"nodes": {"Law": 1}, "edges": {"CITES": 2}}


def _h(rows_a, rows_b, counts=COUNTS):
    rows = {"a": rows_a, "b": rows_b}
    it = iter(["a", "b"])
    return G.result_hash(ITEMS, {"q": "RETURN 1"}, lambda q, p: rows[next(it)], counts)["total"]


def test_row_order_ignored() -> None:
    assert _h([{"uid": 1}, {"uid": 2}], []) == _h([{"uid": 2}, {"uid": 1}], [])


def test_duplicate_rows_kept() -> None:
    assert _h([{"uid": 1}], []) != _h([{"uid": 1}, {"uid": 1}], [])


def test_counts_and_results_change_hash() -> None:
    base = _h([{"uid": 1}], [{"n": 3}])
    assert base != _h([{"uid": 1}], [{"n": 4}])
    assert base != _h([{"uid": 1}], [{"n": 3}], {"nodes": {"Law": 2}, "edges": {"CITES": 2}})


def test_content_hash_order_free_and_sensitive() -> None:
    n = [{"l": ["Law"], "p": {"uid": "law:1", "name": "x"}}, {"l": ["Article"], "p": {"uid": "art:1"}}]
    e = [{"a": "law:1", "t": "HAS_ARTICLE", "b": "art:1", "p": {}}]
    h = G.content_hash(n, e)
    assert h == G.content_hash(list(reversed(n)), e) and h["n_nodes"] == 2 and h["n_edges"] == 1
    assert h["total"] != G.content_hash([n[0], {"l": ["Article"], "p": {"uid": "art:1", "text": "y"}}], e)["total"]
