"""국가법령정보: 식약처 소관 법령 목록 · 본문 · 위임조문 3단비교 · 법령체계도, 전체 법령 목록.

파일은 법령ID(개정돼도 같음)로 저장한다. 본문 · 체계도는 MST(현행 버전)로 요청하고, 응답의 법령ID가 목록과
같은지 확인한다. 3단비교(`knd=2`)는 법률에만 요청한다(위임은 법률 조문 기준으로 내려온다).

    python -m mfds.collect.law [--snapshot data/raw/YYYYMMDD]
"""

import argparse
from pathlib import Path

from mfds.collect.common import Collector, FetchError, drf_url, env, expect, fetch_pages, snapshot_dir

MFDS_ORG = "1471000"


def plan(laws: list[dict[str, str]]) -> list[tuple[str, str, str, dict]]:
    """식약처 법령 목록 → (종류, 법령ID, 저장 경로, 요청 인자) 목록."""
    jobs = []
    for law in laws:
        lid, mst = law["법령ID"], law["법령일련번호"]
        jobs.append(("law", lid, f"law/body/{lid}.xml", {"target": "law", "MST": mst}))
        if law["법령구분명"] == "법률":
            jobs.append(("thdCmp", lid, f"law/thdcmp/{lid}.xml", {"target": "thdCmp", "MST": mst, "knd": 2}))
        jobs.append(("lsStmd", lid, f"law/lsstmd/{lid}.xml", {"target": "lsStmd", "MST": mst}))
    return jobs


def collect(c: Collector, oc: str) -> dict[str, int]:
    laws = fetch_pages(c, "law", "law/list_mfds", oc, "law", "law", org=MFDS_ORG)
    all_laws = fetch_pages(c, "law", "law/list_all", oc, "law", "law")
    jobs = plan(laws)
    failed, no_stmd = [], []
    for kind, lid, rel, params in jobs:
        try:
            c.fetch(kind, lid, rel, drf_url(oc, "lawService", **params), check=expect("기본정보/법령ID", lid))
        except FetchError as e:  # 하나가 실패해도 나머지는 받는다. 다시 실행하면 빠진 것만 요청한다
            # 상하위 법령이 없는 독립 총리령은 체계도가 빈 문서(법령ID 없음)로 온다. 저장하지 않고 따로 센다
            if kind == "lsStmd" and str(e).endswith("→ (없음)"):
                no_stmd.append(lid)
            else:
                failed.append(f"{kind} {lid}: {e}")
    return {"식약처 법령": len(laws), "법률": sum(l["법령구분명"] == "법률" for l in laws),
            "전체 법령 목록": len(all_laws), "본문 · 3단비교 · 체계도": len(jobs), "체계도 없음": no_stmd,
            "실패": failed}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", type=Path, default=None)
    args = ap.parse_args()
    c = Collector(args.snapshot or snapshot_dir())
    summary = collect(c, env("LAW_OC"))
    failed = summary.pop("실패")
    print(f"law: {summary}, 실패 {len(failed)}, 이번 요청 {c.requests}회 → {c.snapshot}")
    for line in failed:
        print("  실패", line)
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
