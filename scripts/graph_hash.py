"""평가용 그래프의 정답 Cypher 결과 해시(PLAN 1주차 D5 4단계 dump · 복원 연습, 2주차 D4 동결, 3주차 D4 · 4주차 D3 확인).

dump 파일 자체의 해시는 메타데이터 때문에 달라질 수 있어 그래프 상태는 이 결과 해시로 비교한다(PLAN 변경 이력 3 반영).
건수 · 해시만 출력한다(문항 내용 · 처분 문구는 출력하지 않음).

    python scripts/graph_hash.py [--evalset eval/evalset/v1] [--content]

- 라벨별 노드 수 · 관계 유형별 엣지 수
- v1 평가셋에서 슬롯이 있는 문항(T1 · T3 · T7 제외, `check_templates.load_evalset`) 전부에 정답 템플릿(`schema/eval_cypher/`)을
  돌린 결과. 행은 키를 정렬한 JSON 문자열로 바꿔 정렬하고 중복 행은 그대로 둔다(PREREG 4-2 숨은 조건 검증과 같은 비교).
- 해시 = sha256(JSON{"counts": {...}, "results": {문항 id: [행, ...]}}, 키 정렬 · UTF-8). 의도별 해시도 함께 낸다.
- `--content`(dump · 복원 확인용): 노드 전부(라벨 · 속성)와 엣지 전부(양 끝 uid · 유형 · 속성)를 행마다 JSON으로 바꿔
  sha256한 값들을 정렬해 다시 sha256한 내용 해시. 정답 템플릿이 닿지 않는 속성까지 복원됐는지 본다.
"""

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from check_templates import CYPHER, load_evalset  # noqa: E402

from mfds.load import write  # noqa: E402


def canon(rows: list[dict]) -> list[str]:
    return sorted(json.dumps(r, ensure_ascii=False, sort_keys=True) for r in rows)


def digest(obj) -> str:
    return hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def result_hash(items: list[dict], templates: dict[str, str], run, counts: dict) -> dict:
    """run(query, params) -> list[dict]. 전체 해시 · 의도별 해시 · 문항 수."""
    results = {it["id"]: canon(run(templates[it["intent"]], it["params"])) for it in items}
    by_intent: dict[str, dict] = defaultdict(dict)
    for it in items:
        by_intent[it["intent"]][it["id"]] = results[it["id"]]
    return {"total": digest({"counts": counts, "results": results}), "counts": digest(counts),
            "intents": {k: digest(v) for k, v in sorted(by_intent.items())}, "n": len(items)}


NODE_Q = "MATCH (n) RETURN labels(n) AS l, properties(n) AS p"
EDGE_Q = "MATCH (a)-[r]->(b) RETURN a.uid AS a, type(r) AS t, b.uid AS b, properties(r) AS p"


def content_hash(rows_nodes, rows_edges) -> dict:
    """행 iterable(dict) → 노드 · 엣지 · 전체 내용 해시. 행 순서와 무관하고 중복 행은 그대로 센다."""
    def part(rows) -> tuple[str, int]:
        hs = sorted(hashlib.sha256(json.dumps(r, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8"))
                    .hexdigest() for r in rows)
        return hashlib.sha256("\n".join(hs).encode("utf-8")).hexdigest(), len(hs)
    (hn, nn), (he, ne) = part(rows_nodes), part(rows_edges)
    return {"nodes": hn, "edges": he, "n_nodes": nn, "n_edges": ne, "total": digest({"nodes": hn, "edges": he})}


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--evalset", default="eval/evalset/v1")
    ap.add_argument("--content", action="store_true", help="노드 · 엣지 전체 내용 해시도 낸다")
    args = ap.parse_args()
    items = load_evalset(ROOT / args.evalset)
    templates = {p.stem: p.read_text(encoding="utf-8") for p in CYPHER.glob("*.cypher")}
    with write.driver() as drv, drv.session() as s:
        drv.verify_connectivity()

        def read(q: str) -> list:
            return [tuple(r.values()) for r in s.run(q)]

        nodes, edges = write.counts(read)
        counts = {"nodes": dict(sorted(nodes.items())), "edges": dict(sorted(edges.items()))}
        h = result_hash(items, templates, lambda q, p: [r.data() for r in s.run(q, p)], counts)
        c = content_hash((r.data() for r in s.run(NODE_Q)), (r.data() for r in s.run(EDGE_Q))) if args.content else None
    print(f"# 그래프 노드 {sum(nodes.values()):,} · 엣지 {sum(edges.values()):,}, 정답 템플릿 슬롯 {h['n']}")
    print(f"  건수 해시\t{h['counts']}")
    for k, v in h["intents"].items():
        print(f"  {k}\t{v}")
    print(f"## 결과 해시 {h['total']}")
    if c:
        print(f"## 내용 해시 {c['total']} (노드 {c['n_nodes']:,} {c['nodes'][:12]} · 엣지 {c['n_edges']:,} {c['edges'][:12]})")


if __name__ == "__main__":
    main()
