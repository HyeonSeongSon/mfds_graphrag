"""스냅숏 폴더 → 그래프 하나(파싱만, Neo4j 없음). 순서: 법령 → 행정규칙 → DUR → 판례 · 해석례 · 심판 → 처분 → CITES.

refs 해석은 스냅숏의 법령 사전(`LawDict.from_snapshot`)과 조문 집합(`article_set`)으로 한다. 평가셋 생성기와 같은 입력이다.
"""

import json
import time
from collections import Counter
from pathlib import Path

from mfds.disposition import read_rows
from mfds.load import cites, parse_admrul, parse_decisions, parse_disposition, parse_dur, parse_law
from mfds.load.graph import Graph
from mfds.refs import LawDict, Resolver, article_set


def _xml(d: Path) -> list[Path]:
    return sorted(d.glob("*.xml"))


def build_graph(snapshot: Path) -> tuple[Graph, dict[str, float]]:
    """(끝점 없는 엣지를 뺀 그래프, 단계별 초). 뺀 엣지 수는 stats의 `prune.*`."""
    times: dict[str, float] = {}
    t = time.perf_counter()

    def lap(name: str) -> None:
        nonlocal t
        now = time.perf_counter()
        times[name] = now - t
        t = now

    law = snapshot / "law"
    meta = parse_law.law_meta(_xml(law / "list_mfds"))
    bodies = _xml(law / "body")
    g = Graph()
    for p in bodies:
        g.update(parse_law.parse_body(p, meta))
    g.update(parse_law.parse_hierarchy(_xml(law / "lsstmd"), set(meta)))
    name_to_id = {m["name"]: lid for lid, m in meta.items()}
    for p in _xml(law / "thdcmp"):
        g.update(parse_law.parse_delegation(p, name_to_id))
    lap("법령")

    adm_bodies = _xml(snapshot / "admrul" / "body")
    adm = Graph()
    for p in adm_bodies:
        adm.update(parse_admrul.parse_body(p))
    g.update(adm)
    resolver = Resolver(LawDict.from_snapshot(snapshot), article_set(bodies))
    known = {uid for uid, (label, _) in g.nodes.items() if label in ("Article", "Paragraph")}
    g.update(parse_admrul.based_on_law(adm, resolver, known))
    lap("행정규칙")

    dur_body = parse_dur.find_body(adm_bodies)
    if dur_body is None:
        g.stats["dur.병용금기 고시 없음"] += 1
    else:
        g.update(parse_dur.parse(dur_body))
    lap("DUR")

    dec = snapshot / "decisions"
    g.update(parse_decisions.parse_prec(_xml(dec / "prec" / "body"), resolver, known))
    g.update(parse_decisions.parse_expc(_xml(dec / "expc" / "body")))
    g.update(parse_decisions.parse_decc(_xml(dec / "decc" / "body")))
    lap("판례 · 해석례 · 심판")

    g.update(parse_disposition.parse(read_rows(snapshot), resolver, known))
    lap("처분")

    g.update(cites.build(g.cite_units, resolver, known))
    lap("CITES")

    for type_, n in g.prune().items():
        g.stats[f"prune.{type_} 끝점 없음(버림)"] += n
    return g, times


def load_exclusion(path: Path) -> set[str]:
    """`eval/exclusion_ids.json`의 `disposition`(DSPSDTLS_SEQ 또는 uid) · `case`(판례일련번호 또는 uid) → 뺄 노드 uid.

    다른 키(슬롯 조합 · 거절 세트 이름 등)는 적재기가 쓰지 않는다. 평가 그래프는 T1 · T3를 함께 빼므로 두 키 중 하나라도
    없으면 잘못된 파일로 보고 멈춘다(빈 목록은 허용).
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    missing = {"disposition", "case"} - set(data)
    if missing:
        raise SystemExit(f"{path}: {sorted(missing)} 키가 없다")
    disp = {x if str(x).startswith("disp:") else f"disp:food:{x}" for x in data.get("disposition", [])}
    case = {x if str(x).startswith("case:") else f"case:{x}" for x in data.get("case", [])}
    return disp | case


def report(g: Graph) -> dict[str, Counter]:
    return {"nodes": g.node_counts(), "edges": g.edge_counts(), "stats": g.stats}
