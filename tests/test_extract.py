import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import extract as E  # noqa: E402
from mfds.refs import LawDict  # noqa: E402

FOOD, FOOD_D, FOOD_R, DRUG, DRUG_D = "000001", "000002", "000003", "000004", "000005"


@pytest.fixture(scope="module")
def ctx():
    names = {"식품위생법": FOOD, "식품위생법 시행령": FOOD_D, "식품위생법 시행규칙": FOOD_R,
             "약사법": DRUG, "약사법 시행령": DRUG_D}
    kind = {FOOD: "법률", FOOD_D: "대통령령", FOOD_R: "총리령", DRUG: "법률", DRUG_D: "대통령령"}
    title = {v: k for k, v in names.items()}
    parent = {FOOD_D: FOOD, FOOD_R: FOOD_D, DRUG_D: DRUG}
    return E.Ctx(LawDict(names, kind, title, parent, {"특허법"}), None)


def item(text, law="", is_key=False):
    return {"text": text, "law": law, "is_key": is_key}


def arts(ctx, text, it, **kw):
    return E.resolve_item(ctx, text, it, **kw)


# ---- 입력 ----

def test_prec_input_never_has_reference_fields():
    f = {"판시사항": "가", "판결요지": "나", "참조조문": "식품위생법 제44조", "참조판례": "대법원 2000두1", "판례내용": "다"}
    parts = E.doc_fields("prec", f)
    assert parts == [("판시사항", "가"), ("판결요지", "나")]
    text = E.user_text("prec", parts)
    assert "제44조" not in text and "2000두1" not in text


def test_long_body_keeps_head_and_tail():
    body = "a" * 4000 + "b" * 1000 + "c" * 4000
    (k, v), = E.doc_fields("prec", {"판시사항": "", "판결요지": "", "참조조문": "x", "판례내용": body})
    assert k == "판례내용" and v == "a" * 4000 + E.CUT + "c" * 4000
    assert E.doc_fields("prec", {"판례내용": "x" * 8000}) == [("판례내용", "x" * 8000)]  # 8천 자 이하는 그대로
    keys = [k for k, _ in E.doc_fields("decc", {"주문": "a", "청구취지": "", "이유": "b" * 9000})]
    assert keys == ["주문", "이유"]
    assert [k for k, _ in E.doc_fields("expc", {"질의요지": "q", "회답": "a", "이유": "r"})] == ["질의요지", "회답", "이유"]


def test_empty_prec_body_is_no_input():
    assert E.doc_fields("prec", {"판시사항": "", "판결요지": "", "판례내용": ""}) == []


def test_sample_is_proportional_and_seeded():
    docs = [{"uid": f"{k}:{i}", "kind": k} for k, n in (("prec", 40), ("expc", 10), ("decc", 50)) for i in range(n)]
    a = E.sample_docs(docs, 10)
    assert [d["uid"] for d in a] == [d["uid"] for d in E.sample_docs(docs, 10)]
    kinds = [d["kind"] for d in a]
    assert (kinds.count("prec"), kinds.count("expc"), kinds.count("decc")) == (4, 1, 5)


# ---- 응답 · 표본 재사용 ----

def ok_record(uid, refs, config=E.CONFIG_HASH):
    content = json.dumps({"cited_refs": refs}, ensure_ascii=False)
    return {"custom_id": uid, "config": config, "response": {"status_code": 200, "body": {
        "model": "m", "usage": {"prompt_tokens": 100, "completion_tokens": 10},
        "choices": [{"finish_reason": "stop", "message": {"content": content}}]}}}


def test_parse_record_failures():
    assert E.parse_record(ok_record("x", [item("제1조")]))[1] == ""
    bad = ok_record("x", [{"text": "제1조", "law": ""}])
    assert E.parse_record(bad)[1] == "schema"
    cut = {"response": {"status_code": 200, "body": {"choices": [{"finish_reason": "length", "message": {}}]}}}
    assert E.parse_record(cut)[1] == "finish length"
    assert E.parse_record({"response": {"status_code": 429, "body": {}}})[1] == "http 429"
    assert E.parse_record({"response": None, "error": {"code": "x"}})[1] == "http None"


def test_clean_items_dedupes_by_text_and_law():
    out = {"cited_refs": [item(" 같은 법 제5조 ", "약사법"), item("같은 법 제5조", "약사법", True),
                          item("같은 법 제5조", "식품위생법"), item("", "약사법")]}
    got = E.clean_items(out, "decc")
    assert got == [item("같은 법 제5조", "약사법", True), item("같은 법 제5조", "식품위생법")]
    assert all(not x["is_key"] for x in E.clean_items(out, "prec"))


def test_stale_when_config_or_input_changes():
    doc = {"uid": "decc:1", "text": "가"}
    rec = {"config": E.CONFIG_HASH, "input": E.input_hash(doc), "response": {"status_code": 200}}
    assert not E.stale(rec, doc)
    assert E.stale({**rec, "config": "old"}, doc)
    assert E.stale(rec, {**doc, "text": "나"})
    assert E.stale({**rec, "response": {"status_code": 500}}, doc)
    assert E.stale(None, doc)


# ---- 문자열 경계 · 공백 복구 ----

def test_find_spans_boundaries():
    t = "식품위생법 제5조의2에 따라 식품위생법 제5조를"
    spans, _ = E.find_spans(t, "식품위생법 제5조", short=False)
    assert spans == [(t.rfind("식품위생법 제5조"), t.rfind("식품위생법 제5조") + len("식품위생법 제5조"))]
    assert E.find_spans("식품위생법 제7조", "법 제7조", short=True)[0] == []
    glued = "경우는「식품위생법」 제45조"
    assert E.find_spans(glued, "「식품위생법」 제45조", short=False)[0]  # 명시 법령명에는 앞 경계를 걸지 않음
    t2 = "식품위생법  제44조 제1항"
    spans, recovered = E.find_spans(t2, "식품위생법 제44조제1항", short=False)
    assert recovered and t2[spans[0][0]:spans[0][1]] == "식품위생법  제44조 제1항"


# ---- 경로별 해석 ----

def test_explicit_is_resolved_alone_even_when_repeated(ctx):
    t = "식품위생법 제44조 위반, 약사법 제5조, 식품위생법 제44조"
    x = arts(ctx, t, item("식품위생법 제44조", "식품위생법"))
    assert x["path"] == "명시" and x["article_uids"] == [f"art:{FOOD}:44"]
    assert x["notes"]["명시 law 맞음"] == 1
    assert arts(ctx, t, item("약사법 제5조", "식품위생법"))["notes"]["명시 law 틀림"] == 1


def test_relative_uses_sentinel_per_position(ctx):
    t = "식품위생법 제5조, 약사법 제5조 및 같은 법 제5조"
    x = arts(ctx, t, item("같은 법 제5조", "약사법"))
    assert x["path"] == "같은 법" and x["article_uids"] == [f"art:{DRUG}:5"]
    assert E.b_agreement(ctx, [x])["B 일치"] == 1
    y = arts(ctx, "약사법 제31조 및 같은 법 시행령 제3조", item("같은 법 시행령 제3조", "약사법"))
    assert y["article_uids"] == [f"art:{DRUG_D}:3"]


def test_plain_same_law_after_rule_means_the_act(ctx):
    """refs.py는 "같은 법 시행규칙" 뒤의 "같은 법"을 시행규칙으로 읽는다. "같은 법"은 법률로 바로잡는다(표본 7건)."""
    t = "식품위생법 제44조, 같은 법 시행규칙 제57조에 따르면 같은 법 제75조"
    x = arts(ctx, t, item("같은 법 제75조", "식품위생법"))
    assert x["article_uids"] == [f"art:{FOOD}:75"] and x["notes"]["같은 법 → 기본 법률로 바로잡음"] == 1
    st = E.b_agreement(ctx, [x])  # 판정 B는 사전 기준(센티넬 그대로) → 불일치, 보정 기준은 기록만
    assert st["B 분모"] == 1 and st["B 일치"] == 0 and st["기록: 같은 법 → 법률 보정 기준 일치"] == 1
    y = arts(ctx, t, item("같은 법 시행규칙 제57조", "식품위생법"))
    assert y["article_uids"] == [f"art:{FOOD_R}:57"]


def test_relative_in_parentheses_follows_refs(ctx):
    t = "식품위생법 제44조를 위반하였다(약사법 제5조 참조). 같은 법 제9조"
    assert arts(ctx, t, item("같은 법 제9조", "약사법"))["article_uids"] == [f"art:{DRUG}:9"]


def test_cut_breaks_relative_context(ctx):
    t = "식품위생법 제44조 위반" + E.CUT + "같은 법 제7조"
    x = arts(ctx, t, item("같은 법 제7조"))
    assert x["article_uids"] == [] and x["notes"]["센티넬 no_context"] == 1
    assert arts(ctx, t, item("같은 법 제7조", "약사법"))["notes"]["law 보조 가드: 입력에 없음(버림)"] == 1
    y = arts(ctx, t, item("같은 법 제7조", "식품위생법"))
    assert y["article_uids"] == [f"art:{FOOD}:7"] and y["notes"]["law 보조 가드: 잘린 앞 조각만(통과)"] == 1


def test_sentinel_outside_mfds_is_dropped(ctx):
    x = arts(ctx, "특허법 제5조 및 같은 법 제7조", item("같은 법 제7조", "식품위생법"))
    assert x["article_uids"] == [] and x["notes"]["센티넬 not_mfds(버림)"] == 1


def test_token_tiers_and_this_decree_trap(ctx):
    t = "식품위생법(이하 '법'이라 한다) 제31조를 위반하여 법 제75조, 영 제21조, 규칙 제3조, 이 영 제3조"
    assert arts(ctx, t, item("법 제75조", "식품위생법"))["article_uids"] == [f"art:{FOOD}:75"]
    assert arts(ctx, t, item("영 제21조", "식품위생법"))["article_uids"] == [f"art:{FOOD_D}:21"]
    assert arts(ctx, t, item("영 제21조", "식품위생법 시행령"))["article_uids"] == [f"art:{FOOD_D}:21"]
    assert arts(ctx, t, item("규칙 제3조", "식품위생법"))["article_uids"] == [f"art:{FOOD_R}:3"]
    assert arts(ctx, t, item("이 영 제3조", "식품위생법"))["article_uids"] == [f"art:{FOOD_D}:3"]  # 법 제3조가 아님


def test_bare_uses_law_only_where_regex_has_no_context(ctx):
    t = "약사법 제7조에 따라, 제7조"
    x = arts(ctx, t, item("제7조", "식품위생법"))
    assert x["article_uids"] == [f"art:{DRUG}:7"]  # 앞 자리는 약사법, 뒤 자리는 가드에서 버림
    assert x["notes"]["law 보조 가드: 입력에 없음(버림)"] == 1
    assert arts(ctx, "약사법 제7조, 제7조", item("제7조", "약사법"))["notes"]["센티넬로 해석"] == 2


def test_guard_needs_law_before_position(ctx):
    x = arts(ctx, "제3조를 위반하였다. 식품위생법", item("제3조", "식품위생법"))
    assert x["article_uids"] == [] and x["notes"]["law 보조 가드: 뒤에만 있음(버림)"] == 1
    y = arts(ctx, "식품위생법에 따르면 제3조를 위반", item("제3조", "식품위생법"))
    assert y["article_uids"] == [f"art:{FOOD}:3"] and y["notes"]["law 보조로 해석"] == 1
    z = arts(ctx, "식품위생법에 따르면 제3조를 위반", item("제3조", "식품위생법"), use_law=False)
    assert z["article_uids"] == [] and z["notes"]["law 보조 꺼짐"] == 1


def test_same_article_tokens_are_not_resolved(ctx):
    assert arts(ctx, "같은 조 제2항", item("같은 조 제2항"))["path"] == "같은 조(풀지 않음)"


def test_b_excludes_items_whose_sentinel_splits(ctx):
    t = "약사법 제5조 및 같은 법 제7조. 식품위생법 제1조 및 같은 법 제7조"
    x = arts(ctx, t, item("같은 법 제7조", "약사법"))
    st = E.b_agreement(ctx, [x])
    assert st["B 분모"] == 0 and st["B 제외: 자리마다 갈림"] == 1 and st["갈림 중 law가 하나와 맞음"] == 1


# ---- check(1판 버그: article_uids 호출로 늘 죽음) ----

def test_check_runs_and_reports_c_and_coverage(ctx):
    docs = {
        "case:1": {"uid": "case:1", "kind": "prec", "text": "식품위생법에 따르면 제44조를 위반", "ref_field": "식품위생법 제44조"},
        "decc:1": {"uid": "decc:1", "kind": "decc", "text": "식품위생법 제31조 및 같은 법 제75조", "ref_field": ""},
    }
    recs = [ok_record("case:1", [item("제44조", "식품위생법")]),
            ok_record("decc:1", [item("식품위생법 제31조", "식품위생법", True), item("같은 법 제75조", "식품위생법")]),
            ok_record("decc:1", [])]
    rep, res, d1 = E.check(recs, docs, expected={"case:1", "decc:1", "decc:9"}, n_total=2, ctx=ctx)
    c = rep["C(판례 참조조문 대비)"]
    assert c["LLM 재현율"] == 1.0 and c["정규식 재현율"] == 0.0 and c["닿을 수 있는 정답"] == 1 and c["그중 LLM 회수"] == 1
    assert rep["기록"]["커버리지: 중복 custom_id"] == 1 and rep["기록"]["커버리지: 결과 없는 대상"] == 1
    assert rep["B"]["분모"] == 1 and rep["B"]["일치"] == 1
    assert rep["is_key(경로별 있음 · 뺌)"]["decc · 명시 · 있음"] == 1
    assert {x["uid"] for x in res} == {"case:1", "decc:1"}


def test_batch_prepare_refuses_after_submit(tmp_path, monkeypatch):
    monkeypatch.setattr(E, "OUT", tmp_path)
    (tmp_path / "batch.json").write_text("{}", encoding="utf-8")
    with pytest.raises(SystemExit):
        E.cmd_batch_prepare(None)


def test_cost_uses_cached_price():
    u = [{"prompt_tokens": 1_000_000, "prompt_tokens_details": {"cached_tokens": 400_000}, "completion_tokens": 100_000}]
    assert round(E.cost(u, E.PRICE_BATCH), 4) == round(0.6 * 0.5 + 0.4 * 0.025 + 0.1 * 2.5, 4)
