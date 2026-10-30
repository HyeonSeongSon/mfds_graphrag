"""mfds/load: 파싱(고정 자료 · 합성 자료, Neo4j 없음)과 고정 자료 트랜잭션 적재 후 롤백(Neo4j 없으면 skip).

고정 자료를 스냅숏 배치로 복사해 쓴다(git 제외 `data/raw/`는 읽지 않는다). 처분 업소 정보는 고정 자료에 없다.
"""

import json
import os
import shutil
import sys
from pathlib import Path

import pytest
import yaml
from lxml import etree

from mfds import disposition
from mfds.collect import dur, foodsafety
from mfds.load import cites, parse_admrul, parse_decisions, parse_disposition, parse_dur, parse_law, write
from mfds.load.build import build_graph, load_exclusion
from mfds.load.graph import Graph, article_or_paragraph, label_of
from mfds.refs import LawDict, Resolver, article_set

ROOT = Path(__file__).resolve().parents[1]
FIX = Path(__file__).resolve().parent / "fixtures"
SCHEMA = yaml.safe_load((ROOT / "schema" / "schema.yaml").read_text(encoding="utf-8"))
YAKSA, ENF, RULE, FOOD = "001783", "004138", "011794", "001805"
BODIES = {YAKSA: "law_yaksa.xml", ENF: "law_yaksa_enf.xml", RULE: "law_yaksa_rule.xml", FOOD: "law_food.xml"}
LAYOUT = {
    "law/list_mfds/p001.xml": "law_list_mfds.xml", "law/list_all/p001.xml": "law_list_all_p1.xml",
    **{f"law/body/{lid}.xml": f for lid, f in BODIES.items()},
    f"law/lsstmd/{YAKSA}.xml": "lsstmd_yaksa.xml", f"law/thdcmp/{YAKSA}.xml": "thdcmp_yaksa_delegation.xml",
    "admrul/body/43553.xml": "admrul_dur.xml",
    "decisions/prec/body/618489.xml": "prec_with_ref.xml", "decisions/prec/body/612927.xml": "prec_no_ref.xml",
    "decisions/expc/body/314666.xml": "expc_one.xml", "decisions/decc/body/260117.xml": "decc_one.xml",
    "foodsafety/I0470/000001.json": "food_disposition_I0470_5rows.json",
}


@pytest.fixture(scope="module")
def snap(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("snap")
    for dst, src in LAYOUT.items():
        (root / dst).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(FIX / src, root / dst)
    return root


@pytest.fixture(scope="module")
def graph(snap) -> Graph:
    return build_graph(snap)[0]


@pytest.fixture(scope="module")
def resolver() -> Resolver:
    laws = LawDict.from_xml(FIX / "law_list_mfds.xml", [FIX / "lsstmd_yaksa.xml"], [FIX / "law_list_all_p1.xml"])
    return Resolver(laws, article_set([FIX / f for f in BODIES.values()]))


def fixture_rows() -> list[dict[str, str]]:
    rows = json.loads((FIX / "food_disposition_I0470_5rows.json").read_text(encoding="utf-8"))["I0470"]["row"]
    return [{k: v for k, v in r.items() if k not in disposition.PII} for r in rows]


# 공유 모듈 mfds/disposition.py

def test_violation_text_strips_only_leading_date() -> None:
    assert disposition.violation_text("(20241008)영업시설 전부철거") == "영업시설 전부철거"
    assert disposition.violation_text("  (20240429)1) 2차 위반(2024.1.2.) ") == "1) 2차 위반(2024.1.2.)"
    assert disposition.violation_text("위반 (20240429)") == "위반 (20240429)"
    assert disposition.violation_text(None) == ""


def test_foodsafety_reexports_shared_module() -> None:
    assert foodsafety.read_rows is disposition.read_rows and foodsafety.PII is disposition.PII
    sys.path.insert(0, str(ROOT / "eval"))
    import prereg
    assert "mfds/disposition.py" in prereg.LOCK_TARGETS


# 법령

def test_law_body_nodes_and_numbers() -> None:
    g = parse_law.parse_body(FIX / "law_yaksa.xml", parse_law.law_meta([FIX / "law_list_mfds.xml"]))
    law = g.nodes[f"law:{YAKSA}"][1]
    assert law["name"] == "약사법" and law["kind"] == "법률" and law["mst"]
    arts = {u for u, (l, _) in g.nodes.items() if l == "Article"}
    assert arts == article_set([FIX / "law_yaksa.xml"])  # 전문(장 · 절 제목)은 노드 아님
    a = g.nodes[f"art:{YAKSA}:24-3"][1]
    assert (a["number"], a["branch"]) == (24, 3) and isinstance(a["number"], int)
    assert g.nodes[f"art:{YAKSA}:2"][1]["branch"] == 0
    assert g.nodes[f"par:{YAKSA}:44:1"][1]["no"] == 1
    root = etree.parse(str(FIX / "law_yaksa.xml")).getroot()
    assert sum(1 for _ in root.iter("호")) == sum(1 for l, _ in g.nodes.values() if l == "Item")
    # 항번호 없는 항의 호는 조에 단다(항 자리 0)
    assert ("HAS_ITEM", f"art:{YAKSA}:2", f"item:{YAKSA}:2:0:1", "") in g.edges
    assert f"par:{YAKSA}:2:0" not in g.nodes


def test_paragraph_numbers() -> None:
    assert [parse_law.paragraph_no(s) for s in ("①", "⑮", "<16>", "㉑")] == [1, 15, 16, 21]


def test_annex_only_byeolpyo_kind() -> None:
    g = parse_law.parse_body(FIX / "law_yaksa_rule.xml")
    root = etree.parse(str(FIX / "law_yaksa_rule.xml")).getroot()
    want = sum(1 for a in root.iter("별표단위") if a.findtext("별표구분") == "별표")
    annex = {u: p for u, (l, p) in g.nodes.items() if l == "Annex"}
    assert len(annex) == want and all(p["owner"] == RULE for p in annex.values())
    assert g.stats["law.별표단위 서식(노드 아님)"] > 0


def test_hierarchy_mfds_only() -> None:
    g = parse_law.parse_hierarchy([FIX / "lsstmd_yaksa.xml"], {YAKSA, ENF, RULE})
    assert set(g.edges) == {("SUBORDINATE_OF", f"law:{ENF}", f"law:{YAKSA}", ""),
                            ("SUBORDINATE_OF", f"law:{RULE}", f"law:{ENF}", "")}


def test_delegation_targets_mfds_laws_only() -> None:
    meta = parse_law.law_meta([FIX / "law_list_mfds.xml"])
    g = parse_law.parse_delegation(FIX / "thdcmp_yaksa_delegation.xml", {m["name"]: k for k, m in meta.items()})
    targets = {k[2].split(":")[1] for k in g.edges}
    assert {ENF, RULE} <= targets and targets <= set(meta)
    assert all(k[1].startswith(f"art:{YAKSA}:") for k in g.edges)
    assert g.stats["law.위임 대상 식약처 밖(버림)"] > 0  # 「약사법 시행규칙」(보건복지부령) 등


# 행정규칙

def test_admrul_nodes_and_based_on_law(resolver) -> None:
    g = parse_admrul.parse_body(FIX / "admrul_dur.xml")
    assert g.nodes["adm:43553"][1]["kind"] == "고시"
    assert g.nodes["admart:43553:1"][1]["title"] == "목적"
    assert {u for u, (l, _) in g.nodes.items() if l == "Annex"} == {f"anx:adm43553:{i}" for i in (1, 2, 3)}
    known = {f"art:{YAKSA}:23-2", f"par:{YAKSA}:23-2:1", f"art:{YAKSA}:26", f"par:{YAKSA}:26:2"}
    b = parse_admrul.based_on_law(g, resolver, known)
    # 제1조: 「약사법」제23조의2제1항제2호 및 제26조제2항제3호 → 호는 항으로
    assert {k[2] for k in b.edges} == {f"par:{YAKSA}:23-2:1", f"par:{YAKSA}:26:2"}


def test_article_or_paragraph_lifting() -> None:
    known = {"art:1:5", "par:1:5:2"}
    assert article_or_paragraph("item:1:5:2:3", known) == "par:1:5:2"
    assert article_or_paragraph("item:1:5:0:3", known) == "art:1:5"
    assert article_or_paragraph("par:1:5:9", known) == "art:1:5"
    assert article_or_paragraph("anx:1:2", known) is None and article_or_paragraph("art:1:6", known) is None


# DUR

def test_dur_rules_and_edges() -> None:
    g = parse_dur.parse(FIX / "admrul_dur.xml")
    annex = dur.annexes_from_xml(FIX / "admrul_dur.xml")
    rules = {u: p for u, (l, p) in g.nodes.items() if l == "DurRule"}
    deleted = g.stats["dur.<삭제> 행(규칙 없음)"]
    assert len(rules) == sum(map(len, annex.values())) - deleted
    for t in ("pair", "age", "preg"):
        assert any(p["type"] == t and p["text"] for p in rules.values())
    pair = rules["dur:pair:acemetacin:methotrexate"]
    assert pair["annex"] == 1 and pair["condition_note"]
    assert g.nodes["ing:methotrexate"][1]["class_codes"] == ["421"]
    for u, p in rules.items():
        if p["type"] == "pair":
            _, _, a, b = u.split("#")[0].split(":")
            assert a <= b
            assert ("CONTRAINDICATED_WITH", f"ing:{a}", f"ing:{b}", u) in g.edges
        assert ("DEFINED_IN", u, f"anx:adm43553:{p['annex']}", "") in g.edges
    assert sum(1 for k in g.edges if k[0] == "CONTRAINDICATED_WITH") == sum(p["type"] == "pair" for p in rules.values())
    assert all(label_of(k[2]) == "Ingredient" for k in g.edges if k[0] == "INVOLVES")
    combo = next(u for u, (l, p) in g.nodes.items() if l == "Ingredient" and p["is_combination"])
    parts = {k[2] for k in g.edges if k[0] == "HAS_COMPONENT" and k[1] == combo}
    assert parts == {f"ing:{n}" for n in combo[4:].split(" + ")} and len(parts) >= 2
    assert any("#2" in u for u in rules)


def test_dur_suffix_by_row_hash() -> None:
    rules = [("dur:a", "h2", 1), ("dur:a", "h1", 2), ("dur:b", "h9", 3), ("dur:a", "h1", 4)]
    assert parse_dur.assign_suffixes(rules) == ["dur:a#3", "dur:a", "dur:b", "dur:a#2"]
    row = {"seq": 1, "ingredient": "x", "age": "18세 미만", "dosage_form": ""}
    assert parse_dur.row_hash(row, "2") == parse_dur.row_hash({**row, "seq": 99}, "2")  # 연번은 내용이 아님


# 판례 · 해석례 · 심판

def test_prec_references_field_and_historical(resolver) -> None:
    laws = resolver.laws
    free = Resolver(laws)  # 조문 집합 없이: 고정 자료에 마약류관리법 본문이 없다
    ref = parse_decisions.one_line(etree.parse(str(FIX / "prec_with_ref.xml")).getroot().findtext("참조조문"))
    res = free.resolve(ref)
    known = {r.article_uid for r in res.refs} | {r.uid for r in res.refs if r.uid.startswith("par:")}
    g = parse_decisions.parse_prec([FIX / "prec_with_ref.xml", FIX / "prec_no_ref.xml"], free, known)
    case = g.nodes["case:618489"][1]
    assert case["case_no"] == "2025도16747" and case["court"] == "대법원" and case["ref_text"] == ref
    assert "<br" not in case["summary"]
    edges = {k[2]: p for k, p in g.edges.items() if k[0] == "REFERENCES"}
    assert edges and all(p["source"] == "field" for p in edges.values())
    assert any(p["historical"] for p in edges.values())  # 구 마약류 관리에 관한 법률
    assert g.stats["prec.참조조문 없음"] == 1


def _prec(path: Path, pid: str, no: str, court: str, date: str, cited: str = "") -> Path:
    path.write_text(f"<PrecService><판례정보일련번호>{pid}</판례정보일련번호><사건번호>{no}</사건번호>"
                    f"<법원명>{court}</법원명><선고일자>{date}</선고일자><참조판례>{cited}</참조판례></PrecService>",
                    encoding="utf-8")
    return path


def test_cites_case_by_case_number(tmp_path: Path, resolver) -> None:
    ps = [_prec(tmp_path / "1.xml", "1", "2015두39996", "대법원", "20200604"),
          _prec(tmp_path / "2.xml", "2", "2019노100", "서울고등법원", "20190101"),
          _prec(tmp_path / "3.xml", "3", "2019노100", "대전고등법원", "20190202"),
          _prec(tmp_path / "4.xml", "4", "2021도1", "대법원", "20210101",
                "[1] 대법원 2020. 6. 4. 선고 2015두39996 판결(공2020하, 1367)&lt;br/&gt;, 2019노100 판결, "
                "대전고등법원 2019. 2. 2. 선고 2019노100 판결, 대법원 1986.10.14. 선고 86도1678 판결")]
    g = parse_decisions.parse_prec(ps, resolver, set())
    assert {k[2] for k in g.edges if k[0] == "CITES_CASE"} == {"case:1", "case:3"}
    assert g.stats["prec.참조판례 사건번호 모호(버림)"] == 1 and g.stats["prec.참조판례 스냅숏 밖"] == 1


def test_expc_decc_nodes() -> None:
    e = parse_decisions.parse_expc([FIX / "expc_one.xml"]).nodes["expc:314666"][1]
    assert e["date"] == "20140609" and e["question"] and e["answer"]
    d = parse_decisions.parse_decc([FIX / "decc_one.xml"]).nodes["decc:260117"][1]
    assert d["order"] == "청구인의 청구를 기각한다." and d["date"] == "20190422"


# 처분

def test_disposition_label_matches_shared_function(resolver) -> None:
    rows = fixture_rows()
    known = {f"art:{FOOD}:36"}
    g = parse_disposition.parse(rows, resolver, known)
    assert len(g.nodes) == 5
    for r in rows:
        p = g.nodes[f"disp:food:{r['DSPSDTLS_SEQ']}"][1]
        assert p["violation_text"] == disposition.violation_text(r["VILTCN"])
        assert not p["violation_text"].startswith("(")
        assert p["law_ref_text"] == r["LAWORD_CD_NM"] and p["source"] == "food"
        assert not set(p) & set(disposition.PII)
    assert {k[2] for k in g.edges} == {f"art:{FOOD}:36"}  # 축산물 위생관리법 2행은 고정 자료에 본문이 없다


def test_disposition_refuses_rows_with_business_info(resolver) -> None:
    with pytest.raises(ValueError, match="업소 정보"):
        parse_disposition.parse([{**fixture_rows()[0], "ADDR": "가짜주소"}], resolver, set())


# CITES

def test_cites_whole_article_context_and_recitation(resolver) -> None:
    known = {f"art:{FOOD}:7", f"art:{FOOD}:8", f"par:{FOOD}:7:1", "art:x:1", "par:x:1:1", "par:x:1:2", "par:x:1:3"}
    segs = [("art:x:1", ""), ("par:x:1:1", "① 「식품위생법」 제7조제1항에 따른 기준"),
            ("par:x:1:2", "② 같은 법 제8조에 따라"), ("par:x:1:3", "③ 「식품위생법」 제7조제1항을 준용한다")]
    g = cites.build([(segs, RULE, "1")], resolver, known)
    assert set((k[1], k[2]) for k in g.edges) == {("par:x:1:1", f"par:{FOOD}:7:1"), ("par:x:1:2", f"art:{FOOD}:8"),
                                                 ("par:x:1:3", f"par:{FOOD}:7:1")}
    assert all(p["historical"] is False and p["raw"] for p in g.edges.values())
    adm = cites.build([([("admart:1:2", "제3조에 따라")], None, None)], resolver, known)
    assert not adm.edges and adm.stats["cites.admrul 해석 실패 no_context"] == 1


# 전체 조립

def test_build_graph_matches_schema(graph) -> None:
    # 속성 이름은 모두 문자열이어야 한다(YAML 1.1은 따옴표 없는 `no`를 False로 읽는다)
    props = {label: set(spec["props"]) for label, spec in SCHEMA["nodes"].items()}
    assert all(isinstance(x, str) for v in props.values() for x in v)
    for uid, (label, p) in graph.nodes.items():
        assert label == label_of(uid) and set(p) <= props[label], (uid, set(p) - props[label])
    types = SCHEMA["edges"]
    for (t, s, d, _), p in graph.edges.items():
        assert s in graph.nodes and d in graph.nodes, (t, s, d)
        assert t in types and set(p) <= set(types[t].get("props", [])), (t, p)
        frm, to = types[t]["from"], types[t]["to"]
        assert graph.nodes[s][0] in (frm if isinstance(frm, list) else [frm]), (t, s)
        assert graph.nodes[d][0] in (to if isinstance(to, list) else [to]), (t, d)
    want = {"Law", "Article", "Paragraph", "Item", "Annex", "AdminRule", "AdminArticle", "Case", "Interpretation",
            "AdminAppeal", "Disposition", "Ingredient", "DurRule"}
    assert set(graph.node_counts()) == want


def test_exclusion_removes_nodes_and_edges(graph, tmp_path: Path) -> None:
    f = tmp_path / "ex.json"
    f.write_text(json.dumps({"disposition": ["4632752", "disp:food:4627314"], "case": ["618489"], "t2": []}),
                 encoding="utf-8")
    ex = load_exclusion(f)
    assert ex == {"disp:food:4632752", "disp:food:4627314", "case:618489"}
    g = Graph()
    g.update(graph)
    nodes, edges = g.exclude(ex)
    assert nodes == {"Disposition": 2, "Case": 1} and edges["BASED_ON"] == 2 and edges["REFERENCES"] >= 1
    assert not any(k[1] in ex or k[2] in ex for k in g.edges)
    for bad in ({"t2": []}, {"disposition": ["4632752"]}):  # 두 키 모두 있어야 한다
        f.write_text(json.dumps(bad), encoding="utf-8")
        with pytest.raises(SystemExit):
            load_exclusion(f)


def test_cli_dry_run_and_eval_needs_exclusion(snap, tmp_path: Path, monkeypatch, capsys) -> None:
    from mfds.load import __main__ as cli
    monkeypatch.setattr(sys, "argv", ["mfds.load", "--snapshot", str(snap), "--mode", "eval", "--dry-run"])
    with pytest.raises(SystemExit):
        cli.main()
    ex = tmp_path / "ex.json"
    ex.write_text(json.dumps({"disposition": ["4632752"], "case": []}), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["mfds.load", "--snapshot", str(snap), "--mode", "eval", "--exclusion", str(ex),
                                      "--dry-run"])
    cli.main()
    out = capsys.readouterr().out
    assert "Disposition\t4" in out and "dry-run" in out and "no_context" in out
    # 그래프에 없는 ID(형식이 어긋난 사건번호 등)가 있으면 멈춘다
    ex.write_text(json.dumps({"disposition": ["4632752"], "case": ["2020도1234"]}), encoding="utf-8")
    with pytest.raises(SystemExit, match="그래프에 없다"):
        cli.main()


def test_write_batches_cover_graph(graph) -> None:
    rows = {"node": 0, "edge": 0}
    for q, batch in write.batches(graph, size=1000):
        assert len(batch) <= 1000
        rows["node" if q.startswith("UNWIND $rows AS r MERGE (n:") else "edge"] += len(batch)
    assert rows == {"node": len(graph.nodes), "edge": len(graph.edges)}
    assert "{rule_id: r.key}" in write.edge_query("CONTRAINDICATED_WITH", "Ingredient", "Ingredient")
    assert len(write.constraint_statements()) == 18


# Neo4j: 고정 자료를 트랜잭션 안에 적재하고 건수를 본 뒤 롤백(DB에 남지 않음). test_eval_templates.py와 같은 skip

@pytest.fixture(scope="module")
def neo4j_session():
    neo4j = pytest.importorskip("neo4j")
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    driver = None
    try:
        driver = neo4j.GraphDatabase.driver(os.environ["NEO4J_URI"],
                                            auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"]))
        driver.verify_connectivity()
    except Exception as e:  # noqa: BLE001
        if driver is not None:
            driver.close()
        pytest.skip(f"Neo4j 없음: {e}")
    with driver.session() as s:
        yield s
    driver.close()


def test_fixture_load_counts_then_rollback(neo4j_session, graph) -> None:
    s = neo4j_session
    write.apply_constraints(s)  # 스키마만(IF NOT EXISTS). MERGE가 uid 인덱스를 쓰게
    uids = list(graph.nodes)
    present = "MATCH (n) WHERE n.uid IN $uids RETURN count(n) AS n"
    if s.run(present, uids=uids).single()["n"]:
        pytest.skip("DB에 고정 자료와 같은 uid 노드가 이미 있다(적재된 DB에서는 건수 비교가 안 됨)")
    tx = s.begin_transaction()
    try:
        def read(q: str) -> list:
            return [tuple(r.values()) for r in tx.run(q)]

        before_n, before_e = write.counts(read)
        write.write(lambda q, p: tx.run(q, p).consume(), graph, size=2000)
        after_n, after_e = write.counts(read)
        assert after_n - before_n == graph.node_counts()
        assert after_e - before_e == graph.edge_counts()
        for r in fixture_rows():
            got = tx.run("MATCH (d:Disposition {uid: $u}) RETURN d.violation_text AS v",
                         u=f"disp:food:{r['DSPSDTLS_SEQ']}").single()["v"]
            assert got == disposition.violation_text(r["VILTCN"])
        art = tx.run("MATCH (a:Article {uid: $u}) RETURN a.number AS n, a.branch AS b", u=f"art:{YAKSA}:24-3").single()
        assert (art["n"], art["b"]) == (24, 3)
    finally:
        tx.rollback()
    assert s.run(present, uids=uids).single()["n"] == 0
