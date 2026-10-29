"""국가법령정보: 식약처 소관 행정규칙(고시 · 예규 · 훈령) 목록 · 본문.

파일은 행정규칙ID(개정돼도 같음)로 저장한다. 본문은 행정규칙일련번호(현행 버전)로 요청하고, 응답의 일련번호가
목록과 같은지 확인한다. 병용금기 고시(DUR 원천)도 여기서 받고 `dur.py`가 파싱한다.

    python -m mfds.collect.admrul [--snapshot data/raw/YYYYMMDD]
"""

import argparse
from collections import Counter
from pathlib import Path

from mfds.collect.common import Collector, FetchError, drf_url, env, expect, fetch_pages, snapshot_dir

MFDS_ORG = "1471000"


def plan(rules: list[dict[str, str]]) -> list[tuple[str, str, dict]]:
    """행정규칙 목록 → (행정규칙ID, 저장 경로, 요청 인자) 목록. 같은 ID가 둘이면 파일이 덮이므로 멈춘다."""
    dup = [i for i, n in Counter(r["행정규칙ID"] for r in rules).items() if n > 1]
    if dup:
        raise FetchError(f"행정규칙ID 중복: {dup[:5]}")
    return [(r["행정규칙ID"], f"admrul/body/{r['행정규칙ID']}.xml", {"target": "admrul", "ID": r["행정규칙일련번호"]})
            for r in rules]


def collect(c: Collector, oc: str) -> dict[str, int]:
    rules = fetch_pages(c, "admrul", "admrul/list_mfds", oc, "admrul", "admrul", org=MFDS_ORG)
    failed = []
    for aid, rel, params in plan(rules):
        try:
            c.fetch("admrul", aid, rel, drf_url(oc, "lawService", **params),
                    check=expect("행정규칙기본정보/행정규칙일련번호", params["ID"]))
        except FetchError as e:  # 하나가 실패해도 나머지는 받는다. 다시 실행하면 빠진 것만 요청한다
            failed.append(f"{aid}: {e}")
    return {"행정규칙": len(rules), **Counter(r["행정규칙종류"] for r in rules), "실패": failed}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", type=Path, default=None)
    args = ap.parse_args()
    c = Collector(args.snapshot or snapshot_dir())
    summary = collect(c, env("LAW_OC"))
    failed = summary.pop("실패")
    print(f"admrul: {summary}, 실패 {len(failed)}, 이번 요청 {c.requests}회 → {c.snapshot}")
    for line in failed:
        print("  실패", line)
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
