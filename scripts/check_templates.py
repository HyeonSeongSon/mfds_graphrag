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

1주차 D5(평가용 그래프 `--mode eval` 적재 직후, PREREG 4-2 숨은 조건 · 그래프 천장 · M4, 2026-11-26)는 v1 평가셋을 바로 읽는다.
T6은 제외를 적용한 정답(`answer.n`)과 비교하고, M4는 정답이 비어 있으므로(`answer: null`) 대조 대신 정답 템플릿 결과가
비는지를 역할(test 풀 · dev · dev 예비)과 `pool_rank` 순으로 보고한다. 문항 내용(질문 · 처분 문구)은 출력하지 않는다.

    python scripts/check_templates.py --evalset eval/evalset/v1 [--m4-out eval/evalset/v1/m4_resolved_t2c.json]

`--m4-out`(PREREG 4-2 M4 대상 풀): Text2Cypher T4는 test 풀(`pool_rank` 1~36)에서 정답 템플릿 결과가 비지 않는 30개를
시드 순서(`pool_rank`)로, dev는 dev 6(37~42) 중 결과가 빈 조문을 dev 예비(43~48)로 순서대로 바꾼 6개를 고른다.
정답은 그 결과의 uid 집합(정렬, 중복 없음)이다. 30개를 못 채우면 채운 만큼만 쓰고 부족 수를 적는다(AMENDMENTS 기록 대상).
숨은 조건 불일치 · 템플릿 오류가 하나라도 있으면 파일을 쓰지 않는다.
"""

import argparse
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


def expected(item: dict, graph: str = "full"):
    """원자료 정답. full 그래프(D4)의 T6은 제외 미적용 값, eval 그래프(D5)는 제외 적용 값(`answer.n`). M4 정답 미정이면 None."""
    a = item["answer"]
    if a is None:
        return None
    if item["intent"] == "t2_pair_contraindication":
        return (a["uids"][0], a["uids"][1], a["verdict"], tuple(sorted(a["rule_ids"])))
    if item["intent"].startswith("t6_"):
        return item["meta"]["n_without_exclusion"] if graph == "full" else a["n"]
    return tuple(sorted(a["uids"]))


def load_evalset(d: Path) -> list[dict]:
    """v1 test · dev에서 슬롯이 있는 문항(T1 · T3 · T7 제외)을 대조 파일과 같은 모양으로 읽는다."""
    out = []
    for split in ("test", "dev"):
        for line in (d / f"{split}.jsonl").read_text(encoding="utf-8").splitlines():
            x = json.loads(line)
            if x["type"] in ("T1", "T3", "T7"):
                continue
            out.append({"id": x["id"], "split": split, "intent": x["intent"], "answer": x["answer"], "meta": x["meta"],
                        "params": {k: v["uid"] for k, v in x["slots"].items()}})
    return out


def actual(intent: str, rows: list[dict]):
    if intent == "t2_pair_contraindication":
        r = rows[0]
        return (r["ing_a"], r["ing_b"], r["verdict"], tuple(sorted(r["rule_ids"])))
    if intent.startswith("t6_"):
        return rows[0]["n"]
    return tuple(sorted(r["uid"] for r in rows))


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("compare", nargs="?", help="D4: build_evalset --compare-out 파일(full 그래프)")
    ap.add_argument("--evalset", help="D5: v1 평가셋 디렉터리(eval 그래프)")
    ap.add_argument("--m4-out", help="D5: Text2Cypher T4 30 · dev 6 정답 파일(m4_resolved_t2c.json)을 쓴다")
    args = ap.parse_args()
    if bool(args.compare) == bool(args.evalset):
        ap.error("대조 파일과 --evalset 중 하나만 준다")
    if args.m4_out and not args.evalset:
        ap.error("--m4-out은 --evalset과 함께 쓴다")
    graph = "eval" if args.evalset else "full"
    items = load_evalset(ROOT / args.evalset) if args.evalset else json.loads(Path(args.compare).read_text(encoding="utf-8"))
    q = {p.stem: p.read_text(encoding="utf-8") for p in CYPHER.glob("*.cypher")}
    qc = {p.stem: p.read_text(encoding="utf-8") for p in CHECK.glob("*.cypher")}
    bad: Counter = Counter()
    total: Counter = Counter()
    hidden: Counter = Counter()
    evidence: Counter = Counter()
    errors: list[str] = []
    m4: list[tuple] = []  # (split, role, pool_rank, 대상 uid, 정답 템플릿 결과 uid 수, 문항 id, 결과 uid 집합)
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
            want = expected(it, graph)
            if intent == "m4_citing_articles" and graph == "eval":
                m = it["meta"]
                m4.append((it["split"], m["role"], m["pool_rank"], it["params"]["article"], len(got), it["id"],
                           sorted(set(got))))
            if want is None:
                pass
            elif got != want:
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
    print(f"- 합계 불일치 {sum(bad.values())} / {sum(total.values())}"
          + (" (M4는 정답 미정이라 대조하지 않음)" if graph == "eval" else "") + f", {time.perf_counter() - t0:.1f}초")
    if m4:
        print("- M4 정답 템플릿 결과(역할 · pool_rank 순, 빈 결과 표시)")
        for role in ("test", "dev", "reserve"):
            rows = sorted(r for r in m4 if r[1] == role)
            empty = [r[2] for r in rows if r[4] == 0]
            print(f"  {role}: {len(rows)}개, 빈 결과 {len(empty)} {empty}")
        for r in sorted(m4, key=lambda r: (r[1] != "test", r[1], r[2])):
            print(f"  {r[1]}\t{r[2]}\t{r[3]}\t{r[4]}")
    if args.m4_out:
        hidden_bad = sum(v for (k, same), v in hidden.items() if not same)
        if errors or hidden_bad:
            raise SystemExit(f"템플릿 오류 {len(errors)} · 숨은 조건 불일치 {hidden_bad}: M4 정답 파일을 쓰지 않는다")
        write_m4(ROOT / args.m4_out, m4)


M4_T2C_N = 30
M4_DEV_N = 6


def resolve_m4(rows: list[tuple]) -> dict:
    """역할별 (pool_rank 순) 결과로 Text2Cypher T4 30 · dev 6을 고른다(PREREG 4-2 M4 대상 풀)."""
    by = {role: sorted((r for r in rows if r[1] == role), key=lambda r: r[2]) for role in ("test", "dev", "reserve")}
    entry = lambda r: {"id": r[5], "pool_rank": r[2], "article": r[3], "answer": r[6]}  # noqa: E731
    test = [entry(r) for r in by["test"] if r[4]][:M4_T2C_N]
    dev, spare = [], [r for r in by["reserve"] if r[4]]
    replaced = []
    for r in by["dev"]:
        if r[4]:
            dev.append(entry(r))
        elif spare:
            s = spare.pop(0)
            dev.append(entry(s))
            replaced.append([r[2], s[2]])
    used = {e["pool_rank"] for e in test}
    return {
        "rule": "PREREG 4-2 M4 대상 풀: test 풀(pool_rank 1~36)에서 정답 템플릿 결과가 비지 않는 30개를 pool_rank 순으로, "
                "dev 6(37~42)은 결과가 빈 조문을 dev 예비(43~48)로 순서대로 바꿈. 정답은 1주차 D5 평가용 그래프의 "
                "m4_citing_articles 결과 uid 집합",
        "test": test,
        "dev": dev,
        "report": {
            "test_empty_ranks": [r[2] for r in by["test"] if not r[4]],
            "test_unused_ranks": [r[2] for r in by["test"] if r[4] and r[2] not in used],
            "test_short": M4_T2C_N - len(test),
            "dev_empty_ranks": [r[2] for r in by["dev"] if not r[4]],
            "dev_replaced": replaced,
            "dev_short": M4_DEV_N - len(dev),
        },
    }


def write_m4(path: Path, rows: list[tuple]) -> None:
    data = resolve_m4(rows)
    path.write_bytes((json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True) + "\n").encode("utf-8"))
    rep = data["report"]
    shown = path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else path.as_posix()
    print(f"- M4 정답 파일 {shown}: test {len(data['test'])} · dev {len(data['dev'])}, {rep}")


if __name__ == "__main__":
    main()
