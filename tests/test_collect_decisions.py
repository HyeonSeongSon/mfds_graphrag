"""collect/decisions.py: 고정 자료로 ID 합치기, 판례 출처 제외, 본문 ID 확인."""

from pathlib import Path

import pytest

from mfds.collect import decisions
from mfds.collect.common import FetchError, expect, list_items

FIX = Path(__file__).resolve().parent / "fixtures"
T = {t.name: t for t in decisions.TARGETS}


def test_prec_merge_keeps_court_source_only() -> None:
    _, items = list_items((FIX / "prec_search_yaksa.xml").read_bytes(), "prec")
    ids, per_law, excluded = decisions.merge(T["prec"], {"약사법": items})
    assert excluded == {"국세법령정보시스템": 7, "지방세법령정보시스템": 1}
    assert len(ids) == per_law["약사법"] == 92


def test_merge_dedupes_across_laws_in_first_seen_order() -> None:
    t = T["decc"]
    a = [{t.list_id: "1"}, {t.list_id: "2"}]
    b = [{t.list_id: "2"}, {t.list_id: "3"}]
    ids, per_law, excluded = decisions.merge(t, {"식품위생법": a, "약사법": b})
    assert ids == ["1", "2", "3"]
    assert per_law == {"식품위생법": 2, "약사법": 2} and not excluded


def test_excluded_prec_counted_once() -> None:
    t = T["prec"]
    tax = {t.list_id: "9", "데이터출처명": "국세법령정보시스템"}
    _, _, excluded = decisions.merge(t, {"약사법": [tax], "식품위생법": [tax]})
    assert excluded == {"국세법령정보시스템": 1}


def test_search_fixtures_have_list_id_fields() -> None:
    for name, f in [("prec", "prec_search_yaksa.xml"), ("expc", "expc_search_yaksa.xml"),
                    ("decc", "decc_search_food.xml")]:
        _, items = list_items((FIX / f).read_bytes(), T[name].item)
        assert items and all(it[T[name].list_id] for it in items)


@pytest.mark.parametrize("name,f,i", [("prec", "prec_with_ref.xml", "618489"), ("expc", "expc_one.xml", "314666"),
                                      ("decc", "decc_one.xml", "260117")])
def test_body_id_check(name: str, f: str, i: str) -> None:
    body = (FIX / f).read_bytes()
    expect(T[name].body_id, i)(body)
    with pytest.raises(FetchError, match="다른 응답"):
        expect(T[name].body_id, "1")(body)
