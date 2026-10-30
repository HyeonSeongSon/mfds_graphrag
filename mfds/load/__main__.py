"""스냅숏 → Neo4j 적재.

    python -m mfds.load --snapshot data/raw/YYYYMMDD --mode eval|full [--exclusion eval/exclusion_ids.json] [--wipe] [--dry-run]

- full: 스냅숏 전부. eval: `--exclusion`의 처분 · 판례 노드와 그 노드에 닿는 엣지를 뺀다(제외 목록 필수)
- eval 모드는 비어 있지 않은 DB에 적재하지 않는다(남아 있는 제외 노드가 새지 않게, `--wipe`로 비우고 적재)
- `--dry-run`: 파싱과 건수 보고만 하고 Neo4j에 접속하지 않는다
- 접속은 `.env`의 `NEO4J_URI` · `NEO4J_USER` · `NEO4J_PASSWORD`. 끝에 유형별 노드 · 엣지 수와 적재 시간을 출력한다
"""

import argparse
import sys
import time
from pathlib import Path

from mfds.load import write
from mfds.load.build import build_graph, load_exclusion


def show(title: str, counts: dict) -> None:
    print(f"{title}: 합 {sum(counts.values()):,}")
    for k, v in sorted(counts.items()):
        print(f"  {k}\t{v:,}")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(prog="python -m mfds.load")
    ap.add_argument("--snapshot", type=Path, required=True)
    ap.add_argument("--mode", choices=("eval", "full"), required=True)
    ap.add_argument("--exclusion", type=Path, default=None)
    ap.add_argument("--wipe", action="store_true", help="적재 전에 DB의 노드 · 엣지를 모두 지운다")
    ap.add_argument("--dry-run", action="store_true", help="파싱 · 건수만, Neo4j 접속 없음")
    ap.add_argument("--batch", type=int, default=write.BATCH)
    args = ap.parse_args()
    if args.mode == "eval" and args.exclusion is None:
        ap.error("--mode eval에는 --exclusion이 필요하다")
    if not args.snapshot.is_dir():
        ap.error(f"스냅숏 폴더가 없다: {args.snapshot}")

    t0 = time.perf_counter()
    g, times = build_graph(args.snapshot)
    print(f"# 적재 {'(dry-run) ' if args.dry_run else ''}{args.snapshot.as_posix()} --mode {args.mode}")
    if args.mode == "eval":
        ex = load_exclusion(args.exclusion)
        absent = sorted(ex - g.nodes.keys())
        if absent:  # ID 형식이 어긋나면 아무것도 빠지지 않아 누수가 조용히 생긴다
            raise SystemExit(f"제외 목록 {len(absent):,}개가 스냅숏 그래프에 없다(예: {absent[:3]}). 생성기와 ID 형식을 맞춘다")
        nodes, edges = g.exclude(ex)
        print(f"제외 목록 {args.exclusion.as_posix()}: 노드 {dict(nodes)}, 엣지 {dict(edges)}")
    show("노드", g.node_counts())
    show("엣지", g.edge_counts())
    show("파싱 보고(버림 · 해석 실패 · 참고 수)", g.stats)
    no_ctx = g.stats.get("cites.admrul 해석 실패 no_context", 0)
    print(f"참고: 행정규칙 조문 안 자기 조문 참조(no_context) {no_ctx:,}건은 행정규칙 → 행정규칙 엣지가 스키마 밖이라 넣지 않는다(한계). "
          "D3 보고 수치는 조문 머리(`제N조(제목)`)와 조문 아닌 단위까지 읽은 값이라 이보다 크다")
    print("파싱 시간: " + ", ".join(f"{k} {v:.1f}초" for k, v in times.items()))
    if args.dry_run:
        print(f"전체 {time.perf_counter() - t0:.1f}초 (dry-run, 적재 안 함)")
        return

    t1 = time.perf_counter()
    with write.driver() as drv, drv.session() as s:
        drv.verify_connectivity()

        def read(q: str) -> list:
            return [tuple(r.values()) for r in s.run(q)]

        if args.wipe:
            write.wipe(s)
        elif args.mode == "eval" and read("MATCH (n) RETURN count(n)")[0][0]:
            raise SystemExit("eval 모드: DB가 비어 있지 않다. --wipe로 비우고 적재한다")
        write.apply_constraints(s)
        n = write.write(write.session_runner(s), g, args.batch)
        db_nodes, db_edges = write.counts(read)
    print(f"적재 배치 {n:,}개, {time.perf_counter() - t1:.1f}초 (전체 {time.perf_counter() - t0:.1f}초)")
    same = db_nodes == g.node_counts() and db_edges == g.edge_counts()
    print(f"DB 건수 = 파싱 건수: {'같음' if same else '다름'}")
    if not same:
        show("DB 노드", db_nodes)
        show("DB 엣지", db_edges)


if __name__ == "__main__":
    main()
