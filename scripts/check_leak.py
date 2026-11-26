"""적재 후 누수 검사(PLAN 1주차 D5 2단계 · "적재 후 누수 검사", PREREG 4-4). 건수만 출력한다(원문 · 문항 내용은 출력하지 않음).

    python scripts/check_leak.py            # 입력 해시 · 평가셋 원천(meta.members) ⊆ 제외 목록 (Neo4j 없이)
    python scripts/check_leak.py --graph    # + 평가용 그래프에 제외 노드 0건 (Neo4j, `.env` 접속 정보)

- 입력: 평가셋 jsonl(test · dev)과 제외 목록이 AMENDMENTS의 마지막 기록 해시와 같은지 먼저 본다(다른 버전 파일 방지).
- 제외 목록은 적재기와 같은 `load_exclusion`으로 읽는다(`disposition` · `case` · `drug_disposition` 세 키 필수).
- T1 · T3 · T7 문항의 `meta.members`(원천 번호)를 적재기 uid(`disp:food:` · `case:` · `disp:drug:`)로 바꿔 모두 제외 목록에
  있는지 본다. 원천 없는 T1 · T3 · T7 문항이 있어도 실패다(빈 목록 방지).
- `--graph`: 제외 uid를 세 종류로 나눠 평가용 그래프에서 센다. 모두 0이어야 한다. 노드가 없으면 그 노드에 닿는 엣지도 없다.
- 벡터 인덱스 누수 검사는 임베딩 · 인덱스를 만든 뒤 따로 한다(PLAN D5 7단계).
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "eval"))
from mfds.load.build import load_exclusion  # noqa: E402

import prereg  # noqa: E402

PREFIX = {"T1": "disp:food:", "T3": "case:", "T7": "disp:drug:"}
KINDS = {"disp:food:": "Disposition", "disp:drug:": "Disposition", "case:": "Case"}


def member_uid(kind: str, m: str) -> str:
    p = PREFIX[kind]
    return str(m) if str(m).startswith(p) else f"{p}{m}"


def kind_of(uid: str) -> str:
    return next(p for p in KINDS if uid.startswith(p))


def input_hashes(root: Path, paths: list[Path]) -> dict[str, str]:
    """입력 파일이 AMENDMENTS에 마지막으로 기록된 해시와 같은지. 경로 → '같음' · '다름' · '기록 없음'."""
    amended = {k: v for e in prereg.amendments_entries(root) for k, v in e.get("new_hashes", {}).items()}
    out = {}
    for p in paths:
        k = p.relative_to(root).as_posix()
        out[k] = "기록 없음" if k not in amended else ("같음" if prereg.sha256(p) == amended[k] else "다름")
    return out


def members_check(items: list[dict], excluded: set[str]) -> dict:
    """T1 · T3 · T7 문항 원천이 모두 제외 목록에 있는가. 유형별 문항 수 · 원천 수 · 빠진 원천 수 · 원천 없는 문항 수."""
    r: dict[str, Counter] = {k: Counter() for k in PREFIX}
    for x in items:
        k = x.get("type")
        if k not in PREFIX:
            continue
        ms = (x.get("meta") or {}).get("members") or []
        r[k]["문항"] += 1
        r[k]["원천 없는 문항"] += not ms
        for m in ms:
            r[k]["원천"] += 1
            r[k]["제외 목록에 없음"] += member_uid(k, m) not in excluded
    ok = all(c["문항"] and not c["원천 없는 문항"] and not c["제외 목록에 없음"] for c in r.values())
    return {"by_type": r, "ok": ok}


def graph_check(excluded: set[str], run) -> dict[str, int]:
    """제외 uid 종류별로 그래프에 남은 노드 수. `run(query, params) -> int`."""
    by: dict[str, list[str]] = {p: [] for p in KINDS}
    for u in excluded:
        by[kind_of(u)].append(u)
    return {p: run(f"MATCH (n:{KINDS[p]}) WHERE n.uid IN $ids RETURN count(n)", {"ids": ids}) for p, ids in by.items()}


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--evalset", default="eval/evalset/v1")
    ap.add_argument("--exclusion", default="eval/exclusion_ids.json")
    ap.add_argument("--graph", action="store_true", help="평가용 그래프(Neo4j)에서 제외 노드를 센다")
    args = ap.parse_args()
    ev, exf = ROOT / args.evalset, ROOT / args.exclusion
    files = [ev / "test.jsonl", ev / "dev.jsonl", exf]
    fail = []

    print("# 입력 해시(AMENDMENTS 마지막 기록과 대조)")
    for k, s in input_hashes(ROOT, files).items():
        print(f"  {s}\t{k}")
        fail += [k] if s != "같음" else []

    excluded = load_exclusion(exf)
    print(f"# 제외 목록: 합 {len(excluded):,}, " + ", ".join(f"{p} {n:,}" for p, n in
                                                         sorted(Counter(kind_of(u) for u in excluded).items())))

    items = [json.loads(line) for p in files[:2] for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]
    mc = members_check(items, excluded)
    print("# 평가셋 원천(meta.members) ⊆ 제외 목록")
    for k, c in mc["by_type"].items():
        print(f"  {k}\t문항 {c['문항']:,}\t원천 {c['원천']:,}\t원천 없는 문항 {c['원천 없는 문항']}\t제외 목록에 없음 {c['제외 목록에 없음']}")
    fail += [] if mc["ok"] else ["members"]

    if args.graph:
        from mfds.load import write
        with write.driver() as drv, drv.session() as s:
            drv.verify_connectivity()
            left = graph_check(excluded, lambda q, p: s.run(q, p).single()[0])
            total = s.run("MATCH (n) RETURN count(n)").single()[0]
        print(f"# 평가용 그래프(노드 {total:,})에 남은 제외 노드")
        for p, n in left.items():
            print(f"  {p}\t{n}")
        fail += [] if total and not any(left.values()) else ["graph"]

    print(f"## 결과: {'통과' if not fail else '실패 ' + ', '.join(fail)}")
    if fail:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
