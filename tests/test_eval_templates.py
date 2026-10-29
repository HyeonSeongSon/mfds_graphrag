"""의도 목록 · 평가용 정답 Cypher 템플릿 · 평가 문장 템플릿의 짝 맞춤. Neo4j가 떠 있으면 EXPLAIN으로 문법도 본다."""

import os
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "schema"
INTENTS = yaml.safe_load((SCHEMA / "intents.yaml").read_text(encoding="utf-8"))["intents"]
EVAL = [i for i in INTENTS if i["use"] == "eval"]
QUESTIONS = {p.stem: yaml.safe_load(p.read_text(encoding="utf-8")) for p in (SCHEMA / "eval_questions").glob("*.yaml")}


def params(intent: dict) -> set[str]:
    text = (SCHEMA / "eval_cypher" / f"{intent['name']}.cypher").read_text(encoding="utf-8")
    return set(re.findall(r"\$(\w+)", text))


def test_intent_count_and_unique_names() -> None:
    names = [i["name"] for i in INTENTS]
    assert 25 <= len(names) <= 30 and len(set(names)) == len(names)
    assert 10 <= len(EVAL) <= 12


def test_every_eval_intent_has_template_with_matching_slots() -> None:
    files = {p.stem for p in (SCHEMA / "eval_cypher").glob("*.cypher")}
    assert files == {i["name"] for i in EVAL}
    for i in EVAL:
        assert params(i) == set(i["slots"]), i["name"]


def test_list_templates_are_capped_at_200() -> None:
    for i in EVAL:
        text = (SCHEMA / "eval_cypher" / f"{i['name']}.cypher").read_text(encoding="utf-8")
        if "uid 목록" in i["returns"]:
            assert "LIMIT 200" in text, i["name"]


def test_every_eval_intent_has_question_templates() -> None:
    by_intent = {q["intent"]: q for q in QUESTIONS["t2_t4_t6"]["intents"]}
    assert set(by_intent) == {i["name"] for i in EVAL}
    for i in EVAL:
        templates = by_intent[i["name"]]["templates"]
        assert len(templates) >= 2
        for t in templates:
            used = set(re.findall(r"\{(\w+?)(?:_label)?\}", t))
            assert used <= set(i["slots"]), (i["name"], t)


def test_refusal_and_source_frames_exist() -> None:
    assert {"t1_t3", "refusal"} <= set(QUESTIONS)
    assert {"other_ministry_law", "missing_article", "out_of_scope"} == set(QUESTIONS["refusal"]["kinds"])


@pytest.fixture(scope="module")
def neo4j_session():
    neo4j = pytest.importorskip("neo4j")
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    try:
        driver = neo4j.GraphDatabase.driver(os.environ["NEO4J_URI"],
                                            auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"]))
        driver.verify_connectivity()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Neo4j 없음: {e}")
    with driver.session() as s:
        yield s
    driver.close()


@pytest.mark.parametrize("intent", EVAL, ids=lambda i: i["name"])
def test_template_explains(neo4j_session, intent) -> None:
    text = (SCHEMA / "eval_cypher" / f"{intent['name']}.cypher").read_text(encoding="utf-8")
    neo4j_session.run("EXPLAIN " + text, {k: "x" for k in params(intent)}).consume()


# 11차 검토 반영 규칙

def test_per_intent_counts_match_prereg_totals() -> None:
    test = {i["name"]: i["n"]["test"] for i in EVAL}
    dev = {i["name"]: i["n"]["dev"] for i in EVAL}
    assert sum(test.values()) == 450 and sum(dev.values()) == 70
    assert sum(v for k, v in test.items() if k[:2] in ("m1", "m2", "m3", "m4")) == 200
    assert sum(v for k, v in test.items() if k.startswith("t6")) == 50


def test_heldout_units_group_siblings_and_all_outcomes_reachable() -> None:
    from itertools import permutations
    doc = yaml.safe_load((SCHEMA / "intents.yaml").read_text(encoding="utf-8"))
    n = {i["name"]: i["n"]["test"] for i in EVAL}
    units = {u: sum(n[i] for i in names) for u, names in doc["heldout_units"].items()}
    flat = [i for names in doc["heldout_units"].values() for i in names]
    assert set(flat) == {i for i in n if i[:2] in ("m1", "m2", "m3", "m4")}  # T4 의도 전부, t2 · T6 없음
    assert units == {"m1": 60, "m2": 60, "m3": 50, "m4": 30}
    outcomes = set()
    for order in permutations(units):
        total, pick = 0, []
        for u in order:
            if total + units[u] > doc["heldout_cap"]:
                continue
            total += units[u]
            pick.append(u)
            if total >= doc["heldout_min"]:
                break
        assert doc["heldout_min"] <= total <= doc["heldout_cap"], order
        outcomes.add(frozenset(pick))
    assert len(outcomes) == 5  # 상한 100이면 m4가 항상 뽑혀 3가지뿐


def test_refusal_scoring_rules() -> None:
    kinds = QUESTIONS["refusal"]["kinds"]
    assert kinds["out_of_scope"]["t2c_correct"] == "no_query"
    assert kinds["other_ministry_law"]["t2c_correct"] == kinds["missing_article"]["t2c_correct"] == "no_query_or_empty"
    assert kinds["other_ministry_law"]["empty_query_must_contain"] == ["law_name"]
    assert kinds["missing_article"]["empty_query_must_contain"] == ["law_name", "article_no"]


def test_historical_edges_excluded_from_templates() -> None:
    for name in ("m2_case_ref_delegated", "m4_citing_articles"):
        text = (SCHEMA / "eval_cypher" / f"{name}.cypher").read_text(encoding="utf-8")
        assert "historical, false) = false" in text, name


def test_schema_has_contains_and_lookup_properties() -> None:
    schema = yaml.safe_load((SCHEMA / "schema.yaml").read_text(encoding="utf-8"))
    assert "contains" in schema["nodes"]["Ingredient"]["props"]
    assert "historical" in schema["edges"]["REFERENCES"]["props"] and "historical" in schema["edges"]["CITES"]["props"]
    assert {"Ingredient", "Article", "Case", "Disposition"} <= set(schema["text2cypher_lookup"])


def test_check_templates_drop_only_hidden_conditions() -> None:
    check = {p.stem: p.read_text(encoding="utf-8") for p in (SCHEMA / "eval_cypher_check").glob("*.cypher")}
    assert set(check) == {"m2_case_ref_delegated", "m4_citing_articles"}
    for name, text in check.items():
        query = "\n".join(line for line in text.splitlines() if not line.startswith("//"))
        assert "historical" not in query and "SUBORDINATE_OF" not in query, name
        assert set(re.findall(r"\$(\w+)", text)) == params(next(i for i in EVAL if i["name"] == name))


@pytest.mark.parametrize("name", ["m2_case_ref_delegated", "m4_citing_articles"])
def test_check_template_explains(neo4j_session, name) -> None:
    text = (SCHEMA / "eval_cypher_check" / f"{name}.cypher").read_text(encoding="utf-8")
    neo4j_session.run("EXPLAIN " + text, {k: "x" for k in set(re.findall(r"\$(\w+)", text))}).consume()


# 숨은 조건 동작: 합성 그래프를 트랜잭션 안에 만들고 롤백한다(DB에 남지 않음)
SYNTHETIC = """
CREATE (act:Law {uid: 'test:law:act', kind: '법률', name: '시험법'}),
       (dec:Law {uid: 'test:law:dec', kind: '대통령령', name: '시험법 시행령'}),
       (other:Law {uid: 'test:law:other', kind: '법률', name: '다른법'}),
       (dec)-[:SUBORDINATE_OF]->(act),
       (t:Article {uid: 'test:art:act:5'}), (act)-[:HAS_ARTICLE]->(t),
       (inscope:Article {uid: 'test:art:dec:1'}), (dec)-[:HAS_ARTICLE]->(inscope),
       (outscope:Article {uid: 'test:art:other:9'}), (other)-[:HAS_ARTICLE]->(outscope),
       (old:Article {uid: 'test:art:act:7'}), (act)-[:HAS_ARTICLE]->(old),
       (inscope)-[:CITES]->(t),
       (outscope)-[:CITES]->(t),
       (old)-[:CITES {historical: true}]->(t),
       (x:Article {uid: 'test:art:act:2'}), (act)-[:HAS_ARTICLE]->(x),
       (y:Article {uid: 'test:art:act:3'}), (act)-[:HAS_ARTICLE]->(y),
       (d1:Article {uid: 'test:art:dec:2'}), (dec)-[:HAS_ARTICLE]->(d1),
       (d2:Article {uid: 'test:art:dec:3'}), (dec)-[:HAS_ARTICLE]->(d2),
       (x)-[:DELEGATES_TO]->(d1), (y)-[:DELEGATES_TO]->(d2),
       (c:Case {uid: 'test:case:1'}),
       (c)-[:REFERENCES {source: 'field'}]->(x),
       (c)-[:REFERENCES {source: 'field', historical: true}]->(y)
"""
HIDDEN = [
    ("m4_citing_articles", {"article": "test:art:act:5"}, {"test:art:dec:1"},
     {"test:art:dec:1", "test:art:other:9", "test:art:act:7"}),
    ("m2_case_ref_delegated", {"case": "test:case:1"}, {"test:art:dec:2"}, {"test:art:dec:2", "test:art:dec:3"}),
]


@pytest.mark.parametrize("name,params_,gold,check", HIDDEN, ids=[h[0] for h in HIDDEN])
def test_hidden_conditions_change_results_on_synthetic_graph(neo4j_session, name, params_, gold, check) -> None:
    gold_q = (SCHEMA / "eval_cypher" / f"{name}.cypher").read_text(encoding="utf-8")
    check_q = (SCHEMA / "eval_cypher_check" / f"{name}.cypher").read_text(encoding="utf-8")
    tx = neo4j_session.begin_transaction()
    try:
        tx.run(SYNTHETIC).consume()
        assert {r["uid"] for r in tx.run(gold_q, params_)} == gold
        assert {r["uid"] for r in tx.run(check_q, params_)} == check
    finally:
        tx.rollback()
