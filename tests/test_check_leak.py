"""scripts/check_leak.py: 원천 uid 변환, 원천 ⊆ 제외 목록, 원천 없는 문항, 그래프 검사의 종류별 질의."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import check_leak as L  # noqa: E402

EX = {"disp:food:11", "disp:food:12", "case:21", "disp:drug:2026000001"}


def _item(kind: str, members) -> dict:
    return {"type": kind, "meta": {"members": members}}


def test_member_uid_prefix() -> None:
    assert L.member_uid("T1", "11") == "disp:food:11"
    assert L.member_uid("T3", 21) == "case:21"
    assert L.member_uid("T7", "2026000001") == "disp:drug:2026000001"
    assert L.member_uid("T7", "disp:drug:2026000001") == "disp:drug:2026000001"


def test_members_all_excluded_passes() -> None:
    items = [_item("T1", ["11", "12"]), _item("T3", ["21"]), _item("T7", ["2026000001"]), {"type": "T2", "meta": {}}]
    r = L.members_check(items, EX)
    assert r["ok"] and r["by_type"]["T1"]["원천"] == 2


def test_member_missing_from_exclusion_fails() -> None:
    items = [_item("T1", ["11", "99"]), _item("T3", ["21"]), _item("T7", ["2026000001"])]
    r = L.members_check(items, EX)
    assert not r["ok"] and r["by_type"]["T1"]["제외 목록에 없음"] == 1


def test_food_number_is_not_drug() -> None:
    """같은 숫자라도 유형마다 다른 uid다(식품 · 의약품 처분 번호는 겹칠 수 있음)."""
    items = [_item("T1", ["11"]), _item("T3", ["21"]), _item("T7", ["11"])]
    assert not L.members_check(items, EX)["ok"]


def test_empty_members_or_missing_type_fails() -> None:
    assert not L.members_check([_item("T1", []), _item("T3", ["21"]), _item("T7", ["2026000001"])], EX)["ok"]
    assert not L.members_check([_item("T1", ["11"]), _item("T3", ["21"])], EX)["ok"]  # T7 문항 없음


def test_graph_check_queries_each_kind() -> None:
    seen = []

    def run(q: str, p: dict) -> int:
        seen.append((q.split("(n:")[1].split(")")[0], sorted(p["ids"])))
        return 0

    left = L.graph_check(EX, run)
    assert left == {"disp:food:": 0, "disp:drug:": 0, "case:": 0}
    assert ("Disposition", ["disp:food:11", "disp:food:12"]) in seen and ("Case", ["case:21"]) in seen
