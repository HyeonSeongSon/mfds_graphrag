import json
import sys
from collections import Counter
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


def test_shorter_items_inside_longer_citation_are_dropped(ctx):
    """같은 자리를 두 번 해석하지 않는다: "같은 법 제21조제1항" 안의 "법 제21조" · "제21조"(표본 B 확인 파일 5쌍)."""
    t = "식품위생법 제5조 및 같은 법 제21조제1항에 따라"
    items = [item("식품위생법 제5조", "식품위생법"), item("같은 법 제21조제1항", "식품위생법"),
             item("법 제21조", "식품위생법", True), item("제21조", "식품위생법")]
    res = E.resolve_doc(ctx, t, items)
    assert res[1]["article_uids"] == [f"art:{FOOD}:21"] and res[1]["is_key"]  # is_key를 옮겨 받음
    assert res[2]["path"] == res[3]["path"] == "더 긴 인용에 포함(버림)"
    assert res[2]["article_uids"] == res[3]["article_uids"] == []
    assert res[1]["notes"]["겹침으로 is_key 옮겨 받음"] == 1


def test_partial_overlap_keeps_the_earlier_span(ctx):
    """일부만 겹치면 같은 인용을 경계만 다르게 자른 것(표본 9쌍): 먼저 시작한 쪽만 남기고 is_key를 옮긴다."""
    t = "약사법 제5조 및 같은 법 제76조제1항제6호에 따라"
    res = E.resolve_doc(ctx, t, [item("같은 법 제76조", "약사법"), item("제76조제1항제6호", "약사법", True)])
    assert res[0]["article_uids"] == [f"art:{DRUG}:76"] and res[0]["is_key"]
    assert res[1]["path"] == "더 긴 인용에 포함(버림)" and res[1]["article_uids"] == []
    assert res[1]["notes"]["겹친 자리 버림(일부 겹침, 뒤에서 시작)"] == 1


def test_historical_is_refs_only_and_inference_is_recorded(ctx):
    """historical에는 refs.py가 표시한 구법만(PREREG 274행), refs.py 밖 추정은 historical_inferred로 따로 낸다.
    추출 엣지 적재 때 둘을 합쳐 쓰는 것(AMENDMENTS 4 근거 (1) · (3))은 적재기 몫이라 여기서는 분리만 본다."""
    old = "구 식품위생법(2013. 3. 23. 법률 제11690호로 개정되기 전의 것, 이하 '법'이라 한다) 제36조"
    t = old + ", 같은 법 제75조에 따라 처분한다. 법 제37조를 위반하였고 제38조도 위반하였다. 약사법 제5조"
    res = E.resolve_doc(ctx, t, [item(old, "식품위생법"), item("같은 법 제75조", "식품위생법"),
                                 item("법 제37조", "식품위생법"), item("제38조", "식품위생법"), item("약사법 제5조", "약사법")])
    assert res[0]["historical"] == [f"art:{FOOD}:36"] and res[0]["historical_inferred"] == []
    for x, n in zip(res[1:4], (75, 37, 38)):  # refs.py는 넘기지 않음 → 추정만
        assert x["historical"] == [] and x["historical_inferred"] == [f"art:{FOOD}:{n}"]
    assert res[4]["historical"] == res[4]["historical_inferred"] == []
    cont = E.resolve_doc(ctx, "구 식품위생법(2013. 3. 23. 법률 제11690호로 개정되기 전의 것) 제36조, 제37조",
                         [item("제37조", "식품위생법")])[0]
    assert cont["historical"] == [f"art:{FOOD}:37"]  # 쉼표로 이어진 조문은 refs.py(센티넬)가 표시
    cur = "구 식품위생법 제36조와 달리 현행 식품위생법 제40조 및 같은 법 제75조"
    x = E.resolve_doc(ctx, cur, [item("같은 법 제75조", "식품위생법")])[0]
    assert x["article_uids"] == [f"art:{FOOD}:75"] and x["historical"] == x["historical_inferred"] == []


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


# ---- 사람 확인 파일: 덮어쓰기 막기 · 채움 집계(PLAN B1 판정 기준 2026-11-27) ----

MARK = "표시(본문 · 구법 · 본문+구법)"


def rrow(order, verdict="", mark="", group=None, doc="d"):
    r = {"순서": order, "문서": doc, "판정": verdict, MARK: mark}
    if group:
        r["묶음"] = group
    return r


def test_review_file_is_not_overwritten_once_judged(tmp_path):
    p = tmp_path / "b_review.csv"
    E.write_review(p, [rrow("본")])
    E.write_review(p, [rrow("본")])  # 비어 있으면 다시 만들 수 있음
    E.write_review(p, [rrow("본", "맞음")])
    with pytest.raises(SystemExit):
        E.write_review(p, [rrow("본")])


def test_b_tally_fills_holds_from_spares():
    rows = [rrow("본", "맞음")] * 23 + [rrow("본", "틀림")] * 5 + [rrow("본", "보류", "구법")] * 2
    rows += [rrow("예비", "보류", "구법"), rrow("예비", "맞음"), rrow("예비", "틀림")] + [rrow("예비")] * 7
    t = E.tally_b(rows)
    assert (t["맞음"], t["판정 수"], t["보류"], t["판정한 예비"], t["결과"]) == (24, 30, 3, 3, "사용")


def test_b_tally_counts_exhausted_holds_as_not_right():
    rows = [rrow("본", "맞음")] * 24 + [rrow("본", "보류", "구법")] * 6 + [rrow("예비", "보류", "구법")] * 10
    t = E.tally_b(rows)
    assert t["모자란 수(맞음 아님으로 셈)"] == 6 and t["맞음"] == 24 and t["결과"] == "사용"
    rows = [rrow("본", "맞음")] * 23 + [rrow("본", "보류", "구법")] * 7 + [rrow("예비", "보류", "구법")] * 10
    assert E.tally_b(rows)["결과"] == "끔"


def test_b_tally_rejects_missing_or_invalid_values():
    with pytest.raises(SystemExit):
        E.tally_b([rrow("본", "맞음")] * 29 + [rrow("본")])
    with pytest.raises(SystemExit):
        E.tally_b([rrow("본", "맞음")] * 29 + [rrow("본", "보류")])  # 보류는 구법 표시가 있어야 함
    with pytest.raises(SystemExit):
        E.tally_b([rrow("본", "맞음")] * 29 + [rrow("본", "맞음", "본문, 구법")])


def test_d1_tally_counts_regex_wrong_and_reports_body_free_value():
    llm = [rrow("본", "맞음", group="LLM만 냄")] * 7 + [rrow("본", "맞음", "본문", "LLM만 냄")] * 2 + \
          [rrow("본", "틀림", group="LLM만 냄")]
    rgx = [rrow("본", "의도된 제외", "본문", "정규식만 냄")] * 3 + [rrow("본", "정규식 틀림", group="정규식만 냄")] * 2 + \
          [rrow("본", "놓침", group="정규식만 냄")] * 5
    t = E.tally_d1(llm + rgx)
    assert (t["LLM 맞음"], t["판정 수"], t["비율"]) == (14, 20, 0.7) and t["결과(B가 사용일 때)"] == "정규식 결과"
    assert t["본문 뺀 값(기록)"] == {"LLM 맞음": 9, "판정 수": 15, "비율": 0.6} and t["정규식 틀림"] == 2


# ---- D1 최종안(2026-11-27): 처음 낸 자리 · 같은 자리 · 판박이 키 · 구분 · 이중 판정 · 생략 ----

def test_regex_spot_is_where_regex_first_emits(ctx):
    t = "약사법 제5조 위반. 식품위생법 제44조 및 제5조를 위반하였고, 식품위생법 제44조"
    spot, n = E.regex_spot(ctx, t, f"art:{FOOD}:5", "제5조")  # 첫 "제5조"는 약사법 자리라 건너뜀
    assert n == 2 and spot == (t.rfind("제5조"), t.rfind("제5조") + 3)
    spot, n = E.regex_spot(ctx, t, f"art:{FOOD}:44", "식품위생법 제44조")  # 반복 인용은 처음 낸 자리
    assert n == 2 and spot == (t.find("식품위생법 제44조"), t.find("식품위생법 제44조") + len("식품위생법 제44조"))
    t2 = "식품위생법 제44조 제1항을 위반하여 같은 법 제44조"  # 같은 조문을 여러 표기로, 조 → 항 구체화
    raw = [r.raw for r in ctx.r.resolve(t2).refs if r.article_uid == f"art:{FOOD}:44"][0]
    spot, _ = E.regex_spot(ctx, t2, f"art:{FOOD}:44", raw)
    assert spot is not None and spot[0] == 0
    assert E.regex_spot(ctx, t2, f"art:{FOOD}:99", "제99조") == (None, 0)


def test_refs_reads_front_to_back_without_look_ahead(ctx):
    """처음 낸 자리 방법의 가정: 접두를 잘라 해석한 결과가 전체 결과의 앞부분과 같다(마지막 ref의 조 → 항 구체화만 예외)."""
    for t in ["식품위생법 제3조부터 제5조까지 및 같은 법 제7조", "식품위생법 제44조 제1항, 제2항 및 같은 법 시행령 제21조",
              "「식품위생법」(이하 '법'이라 한다) 제31조, 약사법 제5조 및 같은 법 제7조"]:
        full = [r.article_uid for r in ctx.r.resolve(t).refs]
        for k in range(1, len(t) + 1):
            pre = [r.article_uid for r in ctx.r.resolve(t[:k]).refs]
            assert pre == full[:len(pre)], (t[:k], pre, full)


def test_same_spot_shows_longest_item_only(ctx):
    spans = [(10, 25, "같은 법 제21조제1항", [f"art:{FOOD}:21"]), (14, 21, "법 제21조", [f"art:{FOOD}:21"]),
             (16, 21, "제21조", [f"art:{FOOD}:21"]), (40, 45, "제9조", [f"art:{FOOD}:9"])]
    assert E.same_spot(ctx, spans, (12, 25)) == "같은 법 제21조제1항 → 식품위생법 제21조 외 2개"
    assert E.same_spot(ctx, spans, (30, 35)) == "없음"
    assert E.same_spot(ctx, [(0, 5, "제9조", [])], (0, 5)) == "제9조 → 해석 안 됨"


def test_anchor_key_matches_between_text_and_context():
    t = "이 사건 【별표 23】에 따르면 식품위생법 제44조를 위반하였다.\n[이유] 끝"
    a = t.find("식품위생법 제44조"); b = a + len("식품위생법 제44조")
    k = E.anchor_key(t[max(0, a - 150):a], t[a:b], t[b:b + 60])
    assert E.anchor_from_context(E.context(t, a, b, 150, 40), "식품위생법 제44조") == k  # 원문 속 【】는 건너뜀
    assert E.anchor_from_context(E.context(t, a, b, 250, 60), "식품위생법제44조") == k  # 문맥 길이 · 공백이 달라도 같은 키


def drow(group, order, verdict, gubun="", mark="", doc="d", ctxt="앞【제1조】뒤", cite="제1조", art="art:1:1"):
    return {"순서": order, "묶음": group, "문서": doc, "판정": verdict, MARK: mark, E.GUBUN_COL: gubun,
            "문맥": ctxt, "인용": cite, "조문": art}


def test_d1_gubun_rules_and_counts():
    llm = [drow("LLM만 냄", "본", "맞음")] * 10
    rgx = [drow("정규식만 냄", "본", "정규식 틀림")] * 3 + [drow("정규식만 냄", "본", "놓침", "정규식도 틀림")] * 2 + \
          [drow("정규식만 냄", "본", "놓침", "제외 위반+정규식도 틀림")] + [drow("정규식만 냄", "본", "놓침", "제외 위반")] + \
          [drow("정규식만 냄", "본", "의도된 제외", mark="본문")] * 3
    t = E.tally_d1(llm + rgx)
    assert (t["LLM 맞음"], t["판정 수"]) == (16, 20)
    assert t["정규식 오류 수(정규식 틀림 + 구분 정규식도 틀림)"] == 6 and t["제외 위반"] == 2
    for bad in ([drow("LLM만 냄", "본", "맞음", "제외 위반")], [drow("정규식만 냄", "본", "정규식 틀림", "정규식도 틀림")],
                [drow("정규식만 냄", "본", "놓침", "정규식 틀림")]):
        rows = (bad + llm[1:]) + rgx if bad[0]["묶음"] == "LLM만 냄" else llm + bad + rgx[1:]
        with pytest.raises(SystemExit):
            E.tally_d1(rows)


def test_dual_agreement_counts_only_rows_judged_in_both():
    b = [{"순서": "본", "문서": "d", "인용": "제1조", "조문": "art:1:1", "문맥": "앞【제1조】뒤", "판정": "맞음"},
         {"순서": "예비", "문서": "d", "인용": "제2조", "조문": "art:1:2", "문맥": "앞【제2조】뒤", "판정": ""}]
    d = [drow("LLM만 냄", "본", "틀림"), drow("LLM만 냄", "본", "맞음", ctxt="앞【제2조】뒤", cite="제2조", art="art:1:2"),
         drow("정규식만 냄", "본", "놓침")]
    assert E.dual_agreement(b, d) == {"둘 다 판정한 같은 자리": 1, "일치": 0, "일치율": 0.0}


def test_d1_skipped_only_when_b_off_and_d1_blank():
    blank = [drow("LLM만 냄", "본", "")]
    assert E.d1_skipped({"결과": "끔"}, blank)
    assert not E.d1_skipped({"결과": "사용"}, blank)
    assert not E.d1_skipped({"결과": "끔"}, [drow("LLM만 냄", "본", "맞음")])
    assert not E.d1_skipped("사람 확인 파일 없음", blank)


def test_export_keeps_gubun_and_same_spot_but_not_context(tmp_path):
    rows = [{**drow("정규식만 냄", "본", "놓침", "제외 위반"), "같은 자리 LLM 결과": "없음", "메모": ""}]
    E.export_judgments(rows, tmp_path / "d1.csv")
    head = (tmp_path / "d1.csv").read_text(encoding="utf-8-sig").splitlines()[0]
    assert E.GUBUN_COL in head and "같은 자리 LLM 결과" in head and "문맥" not in head


def test_review_rows_skip_twin_sentences_and_cross_group_spot(ctx):
    """문서 간 판박이 문장과, LLM만 냄 · 정규식만 냄에 같은 자리가 들어가는 경우는 한 번만 뽑는다."""
    body = "처분 사유는 구 식품위생법 시행령 제5조에 따라 같은 법 제25조 제1항을 위반한 것이다"
    docs = {u: {"text": body} for u in ("decc:1", "decc:2")}
    a = body.find("같은 법 제25조 제1항"); b = a + len("같은 법 제25조 제1항")
    d1 = {"llm_only": [("decc:1", f"art:{FOOD}:25", "같은 법", "같은 법 제25조 제1항", (a, b), ""),
                       ("decc:2", f"art:{FOOD}:25", "같은 법", "같은 법 제25조 제1항", (a, b), "")],
          "regex_only": [("decc:1", f"art:{FOOD_D}:25", "정규식", "같은 법 제25조 제1항", (a, b), "없음")]}
    sk = Counter()
    rows = E.review_rows(d1, docs, per_side=1, spare=1, skipped=sk, ctx=ctx)
    assert [r["묶음"] for r in rows] == ["LLM만 냄"]
    assert sk == {"LLM만 냄: 같은 묶음 판박이": 1, "정규식만 냄: LLM만 냄과 같은 자리": 1}


def test_cost_uses_cached_price():
    u = [{"prompt_tokens": 1_000_000, "prompt_tokens_details": {"cached_tokens": 400_000}, "completion_tokens": 100_000}]
    assert round(E.cost(u, E.PRICE_BATCH), 4) == round(0.6 * 0.5 + 0.4 * 0.025 + 0.1 * 2.5, 4)
