"""원자료 읽기 대조: `eval/sources.py`(생성기가 정답을 만드는 원자료 읽기)와 적재한 그래프의 건수 · uid 집합을 비교한다.

잠그지 않는 확인 도구다(PREREG "sources.py와 적재기 결과 대조"). 두 코드는 정답 독립성 때문에 따로 구현했으므로,
같은 스냅숏에서 같은 uid가 나와야 한다. 다르면 둘 중 하나의 파서 버그다. 먼저 전체 그래프를 적재한다.

    python -m mfds.load --snapshot data/raw/20261105 --mode full --wipe
    python scripts/check_sources.py data/raw/20261105

대조: 법령 · 조 · 항 · 호 · 고시 조문 · 판례 노드, 위임(DELEGATES_TO) · 체계도(SUBORDINATE_OF) 엣지,
DurRule(uid · 종류) · INVOLVES 엣지. 불일치가 있으면 앞 5개 uid를 보이고 종료 코드 1로 끝난다. 업소 정보는 다루지 않는다.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from eval import sources  # noqa: E402
from mfds.load import write  # noqa: E402

SHOW = 5


def from_sources(S: Path) -> dict[str, set]:
    law_map = sources.laws(S)
    arts = sources.articles(S)
    units = {u for a in arts.values() for u, _ in a.units}
    rules = sources.dur_rules(S)
    return {
        "Law": {f"law:{lid}" for lid in law_map},
        "Article": {u for u in units if u.startswith("art:")},
        "Paragraph": {u for u in units if u.startswith("par:")},
        "Item": {u for u in units if u.startswith("item:")},
        "AdminArticle": set(sources.admin_articles(S)),
        "Case": {f"case:{pid}" for pid in sources.precedents(S)},
        "DurRule": {(r.uid, r.type) for r in rules},
        "DELEGATES_TO": {(s, d) for s, ds in sources.delegations(S, law_map, set(arts)).items() for d in ds},
        "SUBORDINATE_OF": {(f"law:{lo}", f"law:{up}") for lo, ups in sources.subordinate(S, set(law_map)).items()
                           for up in ups},
        "INVOLVES": {(r.uid, i.uid) for r in rules for i in r.ingredients},
    }


def from_graph(session) -> dict[str, set]:
    def col(q: str) -> list:
        return [tuple(r.values()) if len(r) > 1 else r[0] for r in session.run(q)]

    out = {lab: set(col(f"MATCH (n:{lab}) RETURN n.uid"))
           for lab in ("Law", "Article", "Paragraph", "Item", "AdminArticle", "Case")}
    out["DurRule"] = set(col("MATCH (n:DurRule) RETURN n.uid, n.type"))
    for t in ("DELEGATES_TO", "SUBORDINATE_OF", "INVOLVES"):
        out[t] = set(col(f"MATCH (a)-[:{t}]->(b) RETURN a.uid, b.uid"))
    return out


def main() -> None:
    S = Path(sys.argv[1])
    src = from_sources(S)
    with write.driver() as drv, drv.session() as session:
        db = from_graph(session)
    bad = 0
    print(f"# 원자료 읽기 대조 ({S.as_posix()})")
    for k in src:
        only_src, only_db = sorted(src[k] - db[k]), sorted(db[k] - src[k])
        ok = not only_src and not only_db
        bad += not ok
        print(f"- [{'OK' if ok else '불일치'}] {k}: sources {len(src[k]):,} · 그래프 {len(db[k]):,}"
              + ("" if ok else f" (sources만 {len(only_src)}: {only_src[:SHOW]}, 그래프만 {len(only_db)}: {only_db[:SHOW]})"))
    print(f"\n## 결과: 불일치 {bad}건")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
