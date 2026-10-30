"""eval/grouping.py: 정규화, 묶음, 별 모양 근접 중복(연쇄 없음), 같은 업소 이웃, 50건 조건, 대표 조문, 상한, dev-test 충돌.

업소 정보는 가짜 값으로 만든 합성 자료만 쓴다.
"""

import json
import sys
from collections import Counter
from pathlib import Path

from lxml import etree

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "eval"))
import grouping as g  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"
BASE = "영업자가 지켜야 할 사항 중 유통기한이 경과된 원료를 보관한 사실이 있음 위반"


def test_normalize_violation_removes_dates_ordinals_punctuation() -> None:
    a = g.normalize_violation("(20250103)유통기한 경과 제품 보관(2차 위반)")
    b = g.normalize_violation("(20260411) 유통기한 경과 제품 보관 (3차위반)")
    c = g.normalize_violation("2025. 1. 3. 유통기한 경과 제품 보관, 2차 위반")
    d = g.normalize_violation("2025년 1월 3일 유통기한 경과 제품 보관 2차 위반")
    assert a == b == c == d == "유통기한경과제품보관위반"


def test_normalize_on_fixture_rows_drops_date_prefix() -> None:
    rows = json.loads((FIX / "food_disposition_I0470_5rows.json").read_text(encoding="utf-8"))["I0470"]["row"]
    for r in rows:
        assert not g.normalize_violation(r["VILTCN"]).startswith("(")
        assert not any(ch.isspace() for ch in g.normalize_violation(r["VILTCN"]))


def test_exact_groups_skip_empty_key() -> None:
    texts = {"1": "(20250101)위반 A", "2": "(20250202) 위반 A", "3": "위반 B", "4": "  "}
    assert g.exact_groups(texts, g.normalize_violation) == {"위반A": ["1", "2"], "위반B": ["3"]}


def test_case_key_and_t3_text_fallback() -> None:
    assert g.case_key("2025도 16747") == "2025도16747"
    assert g.t3_text({"판결요지": "<br/> 요지 ", "판시사항": "사항"}) == "요지"
    assert g.t3_text({"판결요지": "", "판시사항": "<br/>판시 사항"}) == "판시 사항"
    assert g.t3_text({"판결요지": None, "판시사항": None}) == ""


def test_near_dup_is_star_not_chain() -> None:
    a = BASE
    b = BASE + "가나다라마바"  # a와 0.82
    c = b + "사아자차카타"  # b와 0.85, a와 0.70
    near = g.NearDup({"a": a, "b": b, "c": c, "z": "전혀 다른 위반 내용인 위생교육 미이수"})
    ga, gb, gc = (g.grams(x) for x in (a, b, c))
    assert g.jaccard(ga, gb) >= 0.8 and g.jaccard(gb, gc) >= 0.8 and g.jaccard(ga, gc) < 0.8
    assert near.neighbors(["a"]) == {"b"}  # c는 b를 거쳐야만 이어지므로 들어오지 않는다
    assert near.neighbors(["a", "c"]) == {"b"}


def test_near_dup_matches_brute_force() -> None:
    texts = {str(i): BASE[: 20 + i] for i in range(30)} | {"x": "식품 등의 표시기준 위반", "y": "식품등의 표시 기준 위반함"}
    near = g.NearDup(texts)
    gs = {i: g.grams(t) for i, t in texts.items()}
    for i in texts:
        brute = {j for j in texts if j != i and g.jaccard(gs[i], gs[j]) >= 0.8}
        assert near.similar(i) == brute


def test_same_key_neighbors_ignore_spaces_and_blank_keys() -> None:
    same = g.SameKey({"1": "가짜식당 가짜시 1번길", "2": "가짜식당가짜시 1번길", "3": "", "4": "다른가게 주소"})
    assert same.neighbors(["1"]) == {"2"}
    assert same.neighbors(["3"]) == set()


def test_candidate_excluded_and_fifty_boundary() -> None:
    texts = {str(i): f"위반 사항 {i:03d}번 독립 문구 {i * 7919}" for i in range(60)}
    groups = {"big": [str(i) for i in range(50)], "over": [str(i) for i in range(51)]}
    near = g.NearDup(texts)
    cands = g.build_candidates(groups, {"0": ["art:000001:44"]}, near)
    by = {c.key: c for c in cands}
    assert by["big"].ok and not by["over"].ok


def test_business_neighbor_counts_toward_condition() -> None:
    texts = {"1": "위반 A 문구", "2": "완전히 다른 표현의 같은 사건 기록"}
    same = g.SameKey({"1": "가짜상호 가짜주소", "2": "가짜상호 가짜주소"})
    cand = g.build_candidates({"k": ["1"]}, {"1": ["art:000001:3"]}, g.NearDup(texts), same)[0]
    assert cand.excluded == {"1", "2"}


def test_candidates_without_answer_are_skipped_and_answers_merged() -> None:
    near = g.NearDup({"1": "가", "2": "나", "3": "다"})
    cands = g.build_candidates({"a": ["1", "2"], "b": ["3"]}, {"1": ["art:1:2"], "2": ["art:1:3", "art:1:2"]}, near)
    assert [c.key for c in cands] == ["a"] and cands[0].answer == ["art:1:2", "art:1:3"]


def test_representative_tie_uses_numeric_article_order() -> None:
    freq = Counter({"art:001783:9": 5, "art:001783:10": 5, "art:001783:2": 1})
    assert g.representative(["art:001783:10", "art:001783:9", "art:001783:2"], freq) == "art:001783:9"
    assert g.representative(["art:001783:24-3", "art:001783:24"], Counter()) == "art:001783:24"
    freq["art:001783:10"] += 1
    assert g.representative(["art:001783:9", "art:001783:10"], freq) == "art:001783:10"


def _cand(k: str, art: str, members: list[str] | None = None, excluded: set[str] | None = None) -> g.Candidate:
    m = members or [k]
    return g.Candidate(k, m, [art], excluded or set(m))


def test_select_caps_per_article_and_fillable() -> None:
    cands = [_cand(f"a{i}", "art:000001:44") for i in range(30)] + [_cand(f"b{i}", "art:000001:7") for i in range(5)]
    sel = g.select(cands, Counter(), n_test=22, n_dev=10, cap=20)
    assert sel.fillable == 25
    assert Counter(c.rep for c in sel.test + sel.dev) == {"art:000001:44": 20, "art:000001:7": 5}
    assert len(sel.test) == 22 and len(sel.dev) == 3


def test_select_drops_dev_conflicting_with_test() -> None:
    t = _cand("t", "art:1:1", ["t"], {"t", "d1"})
    d1 = _cand("d1", "art:1:2")  # test 문항의 제외 범위(근접 중복 · 같은 업소)에 들어 있음
    d2 = _cand("d2", "art:1:3")
    sel = g.select([t, d1, d2], Counter(), n_test=1, n_dev=1)
    assert [c.key for c in sel.test] == ["t"] and [c.key for c in sel.dev] == ["d2"] and sel.dev_dropped == 1


def test_select_skips_candidates_over_fifty() -> None:
    big = g.Candidate("big", ["x"], ["art:1:1"], {str(i) for i in range(51)})
    small = _cand("s", "art:1:1")
    sel = g.select([big, small], Counter(), n_test=2, n_dev=0)
    assert [c.key for c in sel.test] == ["s"] and sel.fillable == 1


def test_module_does_not_write_files_or_import_loader() -> None:
    src = (Path(g.__file__)).read_text(encoding="utf-8")
    assert "open(" not in src and "write_" not in src
    assert "mfds.load" not in src


def test_constants_match_prereg() -> None:
    """잠금 전에 묶음 상수가 PREREG CONSTANTS와 어긋나지 않게 한다."""
    import prereg
    c = prereg.read_constants()
    assert (g.N_GRAM, g.JACCARD, g.NEIGHBOR_MAX, g.CAP_PER_ARTICLE) == (
        c["NGRAM"], c["JACCARD"], c["NEIGHBOR_MAX"], c["CAP_PER_ARTICLE"])
    assert c["T3_INPUT_FIELDS"] == ["판결요지", "판시사항"]
    assert g.t3_text({"판결요지": "", "판시사항": "<b>사항</b>"}) == "사항"


def test_mask_article_refs_chains_titles_and_bare_forms() -> None:
    m = g.mask_article_refs
    assert m("구 식품위생법 제44조 제1항에 따른 준수사항") == "구 식품위생법 해당 조항에 따른 준수사항"
    assert m("「식품위생법」제37조(영업허가 등) 제4항 위반") == "「식품위생법」해당 조항 위반"  # 제목 괄호까지
    assert m("표시·광고에 관한 법률 제8조 1항 1호 및 2호 위반") == "표시·광고에 관한 법률 해당 조항 위반"
    assert m("식품위생법 23조 3항에 위배") == "식품위생법 해당 조항에 위배"  # `제` 없는 표기
    assert m("제75조(허가취소등)제1항제7의2호-폐업") == "해당 조항-폐업"
    assert m("시행규칙 제 89 조 [별표23] 위반") == "시행규칙 해당 조항 위반"
    assert m("고시 제1-2-3조 단서") == "고시 해당 조항 단서"
    assert m("제24조제2항에 따라 등록한 자로서 같은 조 제3항을 위반") == "해당 조항에 따라 등록한 자로서 해당 조항을 위반"
    assert m("전항의 행정처분, 동조 및 각 호") == "해당 조항의 행정처분, 해당 조항"
    assert m("같은 조항 위반죄, 이 조항에 따라") == "해당 조항 위반죄, 해당 조항에 따라"  # `조항`을 통째로
    assert m("시행규칙 [별표]9 II. 개별기준") == "시행규칙 해당 조항 II. 개별기준"
    assert m("시행규칙 제4조 [별표] '행정처분기준'") == "시행규칙 해당 조항 '행정처분기준'"
    assert m("전항으로 정한") == "해당 조항으로 정한"


def test_mask_article_refs_keeps_promulgation_numbers_and_words() -> None:
    m = g.mask_article_refs
    s = "구 약사법(1973.3.13 법률 제2611호로 개정되기 전의 것), 보건사회부고시 제91-80호, 령 제228호"
    assert m(s) == s and not g.has_article_ref(s)
    for s in ("자가품질검사 전항목 미실시", "같은 조례에 따라", "연식품협동조합이", "범행에 동조하여",
              "청색 1호 및 황색 4호", "3조 원 규모", "甲이 조기에 적절한 치료를", "주장과 같은 항소이유가",
              "동호회 활동", "이 조치에 따라", "등과 같은 조난사고로", "이와 같은 호객행위", ""):
        assert m(s) == s, s
    assert g.has_article_ref("식품위생법 제44조 위반")


def test_masked_text_leaves_no_refs_for_resolver() -> None:
    """가린 문장을 refs.py로 다시 읽어도 조문이 나오지 않는다(생성기 assert와 같은 검사)."""
    from mfds.refs import LawDict, Resolver
    laws = LawDict.from_xml(FIX / "law_list_mfds.xml", [FIX / "lsstmd_yaksa.xml"], [FIX / "law_list_all_p1.xml"])
    r = Resolver(laws)
    texts = ["약사법 제23조 제1항 및 같은 법 시행령 제3조의2에 따르면", "식품위생법 제44조(영업자 등의 준수사항) 1항 위반",
             "같은 법 제31조, 제33조 내지 제35조를 위반", "약사법 23조 3항, 동조 4항"]
    for name in ("prec_with_ref.xml", "prec_no_ref.xml"):
        root = etree.parse(str(FIX / name)).getroot()
        texts.append(g.t3_text({c.tag: c.text for c in root}))
    texts += [row["VILTCN"] for row in json.loads((FIX / "food_disposition_I0470_5rows.json").read_bytes())["I0470"]["row"]]
    assert any(r.resolve(t).article_uids for t in texts)
    for t in texts:
        assert not r.resolve(g.mask_article_refs(t)).article_uids, t
