"""그래프 → Neo4j. 노드는 라벨별, 엣지는 (유형, 시작 라벨, 끝 라벨)별로 `UNWIND … MERGE` 배치.

`run(query, params)`를 받는다. 실제 적재는 배치마다 쓰기 트랜잭션(`session_runner`), 통합 테스트는 열린 트랜잭션 하나의
`tx.run`을 넘기고 끝에 롤백한다. 노드를 먼저 다 쓰고 엣지를 쓴다(엣지는 MATCH로 끝점을 찾는다).
라벨 · 엣지 유형은 코드 상수(`graph.PREFIX_LABEL`, 파서의 유형 이름)라 쿼리 문자열에 그대로 넣는다.
"""

import os
from collections import Counter, defaultdict
from collections.abc import Callable, Iterator
from pathlib import Path

from mfds.load.graph import EDGE_KEY, Graph, label_of

ROOT = Path(__file__).resolve().parents[2]
CONSTRAINTS = ROOT / "schema" / "constraints.cypher"
BATCH = 5000
Run = Callable[[str, dict], object]


def node_query(label: str) -> str:
    return f"UNWIND $rows AS r MERGE (n:{label} {{uid: r.uid}}) SET n += r.props"


def edge_query(type_: str, src: str, dst: str) -> str:
    key = f" {{{EDGE_KEY[type_]}: r.key}}" if type_ in EDGE_KEY else ""
    return (f"UNWIND $rows AS r MATCH (a:{src} {{uid: r.src}}) MATCH (b:{dst} {{uid: r.dst}}) "
            f"MERGE (a)-[e:{type_}{key}]->(b) SET e += r.props")


def batches(g: Graph, size: int = BATCH) -> Iterator[tuple[str, list[dict]]]:
    by_label: dict[str, list[dict]] = defaultdict(list)
    for uid, (label, props) in g.nodes.items():
        by_label[label].append({"uid": uid, "props": {"uid": uid, **props}})
    by_edge: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for (t, s, d, key), props in g.edges.items():
        by_edge[(t, label_of(s), label_of(d))].append({"src": s, "dst": d, "key": key, "props": props})
    for label in sorted(by_label):
        rows = by_label[label]
        for i in range(0, len(rows), size):
            yield node_query(label), rows[i:i + size]
    for k in sorted(by_edge):
        rows = by_edge[k]
        for i in range(0, len(rows), size):
            yield edge_query(*k), rows[i:i + size]


def write(run: Run, g: Graph, size: int = BATCH) -> int:
    n = 0
    for query, rows in batches(g, size):
        run(query, {"rows": rows})
        n += 1
    return n


def constraint_statements() -> list[str]:
    text = "\n".join(l for l in CONSTRAINTS.read_text(encoding="utf-8").splitlines() if not l.lstrip().startswith("//"))
    return [s.strip() for s in text.split(";") if s.strip()]


def counts(run_read: Callable[[str], list]) -> tuple[Counter, Counter]:
    """DB의 라벨별 노드 수 · 유형별 엣지 수. `run_read(query)`는 (키, 수) 레코드 목록을 돌려준다."""
    nodes = Counter({r[0]: r[1] for r in run_read("MATCH (n) UNWIND labels(n) AS l RETURN l, count(*)")})
    edges = Counter({r[0]: r[1] for r in run_read("MATCH ()-[r]->() RETURN type(r), count(*)")})
    return nodes, edges


def driver():
    from dotenv import load_dotenv
    from neo4j import GraphDatabase

    load_dotenv(ROOT / ".env")
    return GraphDatabase.driver(os.environ["NEO4J_URI"], auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"]))


def session_runner(session) -> Run:
    def run(query: str, params: dict) -> None:
        session.execute_write(lambda tx: tx.run(query, params).consume())
    return run


def wipe(session) -> None:
    session.run("MATCH (n) CALL { WITH n DETACH DELETE n } IN TRANSACTIONS OF 10000 ROWS").consume()


def apply_constraints(session) -> None:
    for s in constraint_statements():
        session.run(s).consume()
