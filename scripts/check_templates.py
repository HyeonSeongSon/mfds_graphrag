"""1주차 D4 템플릿 대조: 전체 그래프(`--mode full`)에서 정답 Cypher 템플릿을 생성기가 고른 슬롯마다 돌려 원자료 정답과 비교한다.

잠그지 않는 확인 도구다. 먼저 `python -m mfds.load --snapshot data/raw/20261105 --mode full --wipe`로 적재하고,
`python eval/build_evalset.py --dry-run --compare-out <scratchpad>/compare.json`으로 대조 파일을 만든다.

- 11개 템플릿이 오류 없이 실행되는지, 없는 성분 uid에서 T2 템플릿이 null을 내는지
- 문항 단위: 대조 파일의 T2 · T4 · T6 슬롯 전부에 템플릿을 돌려 생성기 정답과 같은지(T6은 제외 미적용 값과 비교,
  full 그래프에는 T1 · T3 제외 노드가 남아 있음. M4는 refs 기준 "범위 안 · 구법 아님" 인용 집합과 비교)
- 숨은 조건 판(`schema/eval_cypher_check/`) 2개가 고른 슬롯에서 정답 템플릿과 같은지(uid 집합으로 비교, 순서 무관)
- M3 검색 · 인용 정답(`answer.evidence`, pair 규칙 + 상대 성분의 preg · age 규칙)도 그래프에서 같은 집합인지
다르면 적재기나 생성기 버그다. 슬롯 값 · 정답 uid만 출력하고 업소 정보는 다루지 않는다.

    python scripts/check_templates.py <scratchpad>/compare.json
"""

import json
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mfds.load import write  # noqa: E402

CYPHER = ROOT / "schema" / "eval_cypher"
CHECK = ROOT / "schema" / "eval_cypher_check"
# M3 evidence 대조용(정답 템플릿은 성분 uid만 내므로 따로 둔다): x와 y 사이 pair 규칙, 그 y의 preg · age 규칙
M3_EVIDENCE = """
MATCH (x:Ingredient {uid: $ingredient})<-[:INVOLVES]-(p:DurRule {type: 'pair'})-[:INVOLVES]->(y:Ingredient)
WHERE y <> x
MATCH (y)<-[:INVOLVES]-(r:DurRule {type: $typ})
UNWIND [p.uid, r.uid] AS uid
RETURN DISTINCT uid
"""
M3_TYPE = {"m3_pair_and_pregnancy": "preg", "m3_pair_and_age": "age"}


def expected(item: dict):
    a = item["answer"]
    if item["intent"] == "t2_pair_contraindication":
        return (a["uids"][0], a["uids"][1], a["verdict"], tuple(sorted(a["rule_ids"])))
    if item["intent"].startswith("t6_"):
        return item["meta"]["n_without_exclusion"]
    return tuple(sorted(a["uids"]))


def actual(intent: str, rows: list[dict]):
    if intent == "t2_pair_contraindication":
        r = rows[0]
        return (r["ing_a"], r["ing_b"], r["verdict"], tuple(sorted(r["rule_ids"])))
    if intent.startswith("t6_"):
        return rows[0]["n"]
    return tuple(sorted(r["uid"] for r in rows))


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    items = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    q = {p.stem: p.read_text(encoding="utf-8") for p in CYPHER.glob("*.cypher")}
    qc = {p.stem: p.read_text(encoding="utf-8") for p in CHECK.glob("*.cypher")}
    bad: Counter = Counter()
    total: Counter = Counter()
    hidden: Counter = Counter()
    evidence: Counter = Counter()
    errors: list[str] = []
    t0 = time.perf_counter()
    with write.driver() as drv, drv.session() as s:
        def run(text: str, params: dict) -> list[dict]:
            return [r.data() for r in s.run(text, params)]

        print(f"# 템플릿 대조 ({len(items)} 슬롯, 그래프 노드 {run('MATCH (n) RETURN count(n) AS n', {})[0]['n']:,})")
        miss = run(q["t2_pair_contraindication"], {"ing_a": "ing:없는 성분", "ing_b": "ing:없는 성분 2"})
        print(f"- 없는 성분 uid: T2 결과 {miss} → {'null' if miss and miss[0]['ing_a'] is None else '확인 필요'}")
        for it in items:
            intent = it["intent"]
            total[intent] += 1
            try:
                got = actual(intent, run(q[intent], it["params"]))
            except Exception as e:  # noqa: BLE001 템플릿 오류는 세고 계속
                errors.append(f"{it['id']}: {type(e).__name__}")
                continue
            want = expected(it)
            if got != want:
                bad[intent] += 1
                if bad[intent] <= 3:
                    g, w = (set(got), set(want)) if isinstance(got, tuple) and intent != "t2_pair_contraindication" else (got, want)
                    diff = f"그래프만 {sorted(g - w)[:5]} · 원자료만 {sorted(w - g)[:5]}" if isinstance(g, set) else f"그래프 {g} · 원자료 {w}"
                    print(f"  ! {it['id']} {it['params']}: {diff}")
            if intent in qc:
                hidden[intent, actual(intent, run(qc[intent], it["params"])) == got] += 1
            if intent in M3_TYPE:
                ev = sorted(r["uid"] for r in run(M3_EVIDENCE, {**it["params"], "typ": M3_TYPE[intent]}))
                evidence[intent, ev == sorted(it["answer"]["evidence"])] += 1
    print(f"- 템플릿 오류 {len(errors)}건 {errors[:5]}")
    for intent in sorted(total):
        print(f"- {intent}: {total[intent]}개 중 불일치 {bad[intent]}")
    for intent in sorted(qc):
        print(f"- 숨은 조건 판 {intent}: 같음 {hidden[intent, True]} · 다름 {hidden[intent, False]}")
    for intent in sorted(M3_TYPE):
        print(f"- M3 evidence {intent}: 같음 {evidence[intent, True]} · 다름 {evidence[intent, False]}")
    print(f"- 합계 불일치 {sum(bad.values())} / {sum(total.values())}, {time.perf_counter() - t0:.1f}초")


if __name__ == "__main__":
    main()
