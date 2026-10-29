"""국가법령정보: 판례 · 법령해석례 · 행정심판례. 식약처 소관 법률 26개 이름으로 본문 검색(`search=2`) → ID 합치기 → 본문.

판례는 데이터출처가 법원(`대법원`)인 것만 받는다. 국세 · 지방세 · 산재 등 다른 출처는 받지 않고 출처별로 센다.
검색 범위는 `docs/data-sources.md`(행정심판도 26개 법률, 15차).

    python -m mfds.collect.decisions [--snapshot data/raw/YYYYMMDD]
"""

import argparse
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from mfds.collect.common import Collector, FetchError, drf_url, env, expect, fetch_pages, snapshot_dir

MFDS_ORG = "1471000"
COURT_SOURCE = "대법원"


@dataclass(frozen=True)
class Target:
    name: str  # DRF target
    item: str  # 검색 결과 항목 태그
    list_id: str  # 검색 결과의 ID 필드
    body_id: str  # 본문의 ID 경로


TARGETS = [
    Target("expc", "expc", "법령해석례일련번호", "법령해석례일련번호"),
    Target("prec", "prec", "판례일련번호", "판례정보일련번호"),
    Target("decc", "decc", "행정심판재결례일련번호", "행정심판례일련번호"),
]


def merge(t: Target, hits: dict[str, list[dict[str, str]]]) -> tuple[list[str], Counter, Counter]:
    """법률별 검색 결과 → (받을 ID 목록, 법률별 고유 건수, 제외한 출처별 건수). ID 순서는 처음 나온 순."""
    ids: dict[str, None] = {}
    excluded: Counter = Counter()
    per_law: Counter = Counter()
    seen_excluded: set[str] = set()
    for law, items in hits.items():
        for it in items:
            i = it[t.list_id]
            if t.name == "prec" and it.get("데이터출처명") != COURT_SOURCE:
                if i not in seen_excluded:
                    seen_excluded.add(i)
                    excluded[it.get("데이터출처명") or "(빈칸)"] += 1
                continue
            per_law[law] += 1
            ids.setdefault(i, None)
    return list(ids), per_law, excluded


def collect(c: Collector, oc: str) -> dict:
    laws = [l for l in fetch_pages(c, "law", "law/list_mfds", oc, "law", "law", org=MFDS_ORG)
            if l["법령구분명"] == "법률"]
    summary: dict = {"법률": len(laws)}
    failed: list[str] = []
    for t in TARGETS:
        hits = {}
        for l in laws:
            hits[l["법령명한글"]] = fetch_pages(c, t.name, f"decisions/{t.name}/search/{l['법령ID']}", oc, t.name,
                                             t.item, query=l["법령명한글"], search=2)
        ids, per_law, excluded = merge(t, hits)
        for i in ids:
            try:
                c.fetch(t.name, i, f"decisions/{t.name}/body/{i}.xml",
                        drf_url(oc, "lawService", target=t.name, ID=i), check=expect(t.body_id, i))
            except FetchError as e:  # 하나가 실패해도 나머지는 받는다. 다시 실행하면 빠진 것만 요청한다
                failed.append(f"{t.name} {i}: {e}")
        summary[t.name] = {"검색 합(중복 포함)": sum(len(v) for v in hits.values()), "고유": len(ids),
                           "제외 출처": dict(excluded), "법률별": dict(per_law.most_common())}
    summary["실패"] = failed
    return summary


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", type=Path, default=None)
    args = ap.parse_args()
    c = Collector(args.snapshot or snapshot_dir())
    summary = collect(c, env("LAW_OC"))
    failed = summary.pop("실패")
    for k, v in summary.items():
        print(f"{k}: {v}")
    print(f"실패 {len(failed)}, 이번 요청 {c.requests}회 → {c.snapshot}")
    for line in failed:
        print("  실패", line)
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
