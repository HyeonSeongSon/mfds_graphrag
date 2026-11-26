"""scripts/check_templates.py: D5 M4 선택 규칙(PREREG 4-2 M4 대상 풀)과 eval 그래프의 T6 기대값."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import check_templates as C  # noqa: E402


def _row(role: str, rank: int, n: int) -> tuple:
    split = "test" if role == "test" else "dev"
    uids = [f"art:x:{rank}:{i}" for i in range(n)]
    return (split, role, rank, f"art:t:{rank}", n, f"{role}-{rank}", uids)


def _rows(test_empty=(), dev_empty=(), reserve_empty=()) -> list[tuple]:
    rows = [_row("test", r, 0 if r in test_empty else 2) for r in range(1, 37)]
    rows += [_row("dev", r, 0 if r in dev_empty else 1) for r in range(37, 43)]
    rows += [_row("reserve", r, 0 if r in reserve_empty else 1) for r in range(43, 49)]
    return rows


def test_first_30_nonempty_in_pool_rank_order() -> None:
    d = C.resolve_m4(list(reversed(_rows(test_empty=(13, 15, 22)))))  # 입력 순서와 무관
    ranks = [e["pool_rank"] for e in d["test"]]
    assert ranks == [r for r in range(1, 34) if r not in (13, 15, 22)]
    assert d["report"]["test_empty_ranks"] == [13, 15, 22]
    assert d["report"]["test_unused_ranks"] == [34, 35, 36] and d["report"]["test_short"] == 0


def test_empty_dev_replaced_by_reserve_in_order() -> None:
    d = C.resolve_m4(_rows(dev_empty=(39, 41), reserve_empty=(43,)))
    assert [e["pool_rank"] for e in d["dev"]] == [37, 38, 44, 40, 45, 42]
    assert d["report"]["dev_replaced"] == [[39, 44], [41, 45]] and d["report"]["dev_short"] == 0


def test_short_when_pool_runs_out() -> None:
    d = C.resolve_m4(_rows(test_empty=tuple(range(1, 10)), dev_empty=(37,), reserve_empty=tuple(range(43, 49))))
    assert len(d["test"]) == 27 and d["report"]["test_short"] == 3
    assert len(d["dev"]) == 5 and d["report"]["dev_short"] == 1


def test_answer_is_sorted_uid_set() -> None:
    d = C.resolve_m4(_rows())
    e = d["test"][0]
    assert e == {"id": "test-1", "pool_rank": 1, "article": "art:t:1", "answer": ["art:x:1:0", "art:x:1:1"]}


def test_t6_expected_depends_on_graph() -> None:
    it = {"intent": "t6_delegated_count", "answer": {"n": 3}, "meta": {"n_without_exclusion": 5}}
    assert C.expected(it, "full") == 5 and C.expected(it, "eval") == 3
    assert C.expected({"intent": "m4_citing_articles", "answer": None, "meta": {}}, "eval") is None
