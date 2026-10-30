"""조문 참조 정규화기. 고정 자료의 실제 문장(법령 본문 · 처분 근거 조항 · 판례 참조조문)과 실패해야 하는 사례."""

import shutil
from pathlib import Path

import pytest
from lxml import etree

from mfds.refs import LawDict, Resolver, article_set

FIX = Path(__file__).resolve().parent / "fixtures"
YAKSA, ENF, RULE, FOOD, NARCO, HFOOD, LABEL = "001783", "004138", "011794", "001805", "002025", "009353", "013094"


@pytest.fixture(scope="module")
def laws() -> LawDict:
    return LawDict.from_xml(FIX / "law_list_mfds.xml", [FIX / "lsstmd_yaksa.xml"], [FIX / "law_list_all_p1.xml"])


@pytest.fixture(scope="module")
def r(laws) -> Resolver:
    return Resolver(laws)


def body(name: str) -> tuple[str, set[str], list[tuple[str, str]]]:
    root = etree.parse(str(FIX / name)).getroot()
    lid = root.findtext("기본정보/법령ID")
    arts, texts = set(), []
    for u in root.find("조문").findall("조문단위"):
        if u.findtext("조문여부") != "조문":
            continue
        no, br = int(u.findtext("조문번호")), (u.findtext("조문가지번호") or "").strip()
        num = f"{no}-{int(br)}" if br else str(no)
        arts.add(f"art:{lid}:{num}")
        texts.append((num, " ".join(e.text or "" for e in u.iter() if e.tag in ("조문내용", "항내용", "호내용", "목내용"))))
    return lid, arts, texts


def uids(r: Resolver, text: str, law: str | None = None, art: str | None = None) -> list[str]:
    return r.resolve(text, law, art).uids


def reasons(r: Resolver, text: str, law: str | None = None) -> list[str]:
    return [f.reason for f in r.resolve(text, law).failures]


# 법령 사전

def test_lawdict_counts_and_hierarchy(laws) -> None:
    assert len(laws.title) == 87
    assert laws.names["약사법"] == YAKSA
    assert laws.parent[RULE] == ENF and laws.root(RULE) == YAKSA  # 「의약품 등의 안전에 관한 규칙」은 약사법 총리령
    assert laws.decree_of(RULE) == ENF
    assert "약사법 시행규칙" not in laws.names  # 보건복지부령


def test_abbreviation(r) -> None:
    assert uids(r, "건강기능식품법 제4조") == [f"art:{HFOOD}:4"]


# 처분 근거 조항 표기(식품안전나라)

def test_disposition_paragraph_without_je(r) -> None:
    assert uids(r, "식품위생법 제44조(영업자 등의 준수사항) 1항") == [f"par:{FOOD}:44:1"]


def test_disposition_multi_with_item(r) -> None:
    text = "건강기능식품에 관한 법률 제4조(영업의 종류 및 시설기준) 1항, 건강기능식품에 관한 법률 제32조(영업허가취소 등) 1항10호"
    assert uids(r, text) == [f"par:{HFOOD}:4:1", f"item:{HFOOD}:32:1:10"]


def test_middle_dot_variants(r) -> None:
    assert uids(r, "식품 등의 표시·광고에 관한 법률 제8조(부당한 표시 또는 광고행위의 금지) 1항") == [f"par:{LABEL}:8:1"]
    assert uids(r, "식품 등의 표시ㆍ광고에 관한 법률 제8조") == [f"art:{LABEL}:8"]


def test_law_name_only_gives_nothing(r) -> None:
    res = r.resolve("식품위생법")
    assert res.refs == [] and res.failures == []


def test_other_ministry_disposition_basis_fails(r) -> None:
    assert reasons(r, "지방세징수법 제7조(관허사업의 제한) 2항") == ["unknown_law"]


# 판례 참조조문

CASE = ("구 마약류 관리에 관한 법률(2024. 2. 6. 법률 제20214호로 개정되기 전의 것) 제2조 제3호, 제5호 (자)목, "
        "제4조 제1항, 제5조 제1항, 제61조 제1항 제7호, 의료법 제18조 제1항, 제5항, 약사법 제23조 제4항 제5호")


def test_case_refs_inherit_law_and_article(r) -> None:
    assert uids(r, CASE) == [f"item:{NARCO}:2:0:3", f"item:{NARCO}:2:0:5", f"par:{NARCO}:4:1", f"par:{NARCO}:5:1",
                             f"item:{NARCO}:61:1:7", f"item:{YAKSA}:23:4:5"]


def test_old_law_flag_and_version_paren_not_parsed(r) -> None:
    res = r.resolve(CASE)
    assert res.refs[0].historical and not res.refs[-1].historical
    assert not any(":20214" in u for u in res.uids)  # "법률 제20214호"는 호가 아니다


def test_unknown_law_continuation_also_fails(r) -> None:
    assert reasons(r, CASE) == ["unknown_law", "unknown_law"]  # 의료법 제18조 제1항, 제5항


# 맥락 해석(시행령 · 시행규칙 안의 법 · 영 · 규칙)

def test_beop_in_rule_is_parent_act(r) -> None:
    assert uids(r, "법 제31조제2항에 따라", RULE) == [f"par:{YAKSA}:31:2"]


def test_yeong_in_rule_is_decree(r) -> None:
    assert uids(r, "영 제3조제1항에 따른", RULE) == [f"par:{ENF}:3:1"]


def test_this_rule(r) -> None:
    assert uids(r, "이 규칙 제4조제1항제6호가목", RULE) == [f"item:{RULE}:4:1:6"]


def test_bare_article_uses_context_law(r) -> None:
    assert uids(r, "제7조의2제1항제3호", YAKSA) == [f"item:{YAKSA}:7-2:1:3"]


def test_no_context_fails(r) -> None:
    assert reasons(r, "제5조") == ["no_context"]


def test_particle_i_is_not_this_law(r) -> None:
    assert uids(r, "임상시험실시기관이 법 제34조의2제2항 단서에", RULE) == [f"par:{YAKSA}:34-2:2"]


# 범위 · 이어짐

def test_paragraph_range(r) -> None:
    assert uids(r, "법 제31조제2항부터 제4항까지의 규정에 따라", RULE) == [f"par:{YAKSA}:31:{n}" for n in (2, 3, 4)]


def test_item_range_without_paragraph(r) -> None:
    assert uids(r, "제9조제1호부터 제3호까지", RULE) == [f"item:{RULE}:9:0:{n}" for n in (1, 2, 3)]


def test_article_range(r) -> None:
    assert uids(r, "제3조부터 제5조까지", YAKSA) == [f"art:{YAKSA}:{n}" for n in (3, 4, 5)]


def test_quoted_law_and_conjunction(r) -> None:
    assert uids(r, "「약사법」제23조의2제1항제2호 및 제26조제2항제3호의 규정에 따라") == [
        f"item:{YAKSA}:23-2:1:2", f"item:{YAKSA}:26:2:3"]


def test_same_law_decree(r) -> None:
    assert uids(r, "약사법 제2조 및 동법 시행령 제5조") == [f"art:{YAKSA}:2", f"art:{ENF}:5"]


def test_same_law_after_unresolved_law(r) -> None:
    text = "「특허법」 제162조에 따른 심결 또는 같은 법 제189조에 따른 판결"
    res = r.resolve(text, YAKSA)
    assert res.refs == [] and [f.reason for f in res.failures] == ["unknown_law", "unknown_law"]


def test_same_law_without_antecedent(r) -> None:
    assert reasons(r, "같은 법 제3조제2항") == ["no_context"]


def test_same_article_keeps_law(r) -> None:
    text = "법 제61조의2제5항 각 호 외의 부분 전단에서 정하는 조치란 같은 조 제3항 전단에 따른 조치를 말한다"
    assert uids(r, text, ENF) == [f"par:{YAKSA}:61-2:5", f"par:{YAKSA}:61-2:3"]


def test_this_article_and_bare_paragraph_use_context_article(r) -> None:
    assert uids(r, "이 조 제2항에 따라", YAKSA, "44") == [f"par:{YAKSA}:44:2"]
    assert uids(r, "제1항에 따른 신고", YAKSA, "44") == [f"par:{YAKSA}:44:1"]
    assert uids(r, "제1항에 따른 신고", YAKSA) == []  # 조 맥락이 없으면 내지 않는다


def test_parenthetical_does_not_change_outer_context(r) -> None:
    text = "법 제31조제2항, 제31조의5제3항(법 제42조제5항에서 준용하는 경우를 포함한다) 및 제42조제1항에 따른"
    assert uids(r, text, ENF) == [f"par:{YAKSA}:31:2", f"par:{YAKSA}:31-5:3", f"par:{YAKSA}:42:5", f"par:{YAKSA}:42:1"]


def test_word_containing_dongjo_is_not_reference(r) -> None:
    assert uids(r, "협동조합 제3조", YAKSA) == [f"art:{YAKSA}:3"]


def test_annex(r) -> None:
    assert uids(r, "별표 1의2에 따른 기준", RULE) == [f"anx:{RULE}:1-2"]


def test_mok_is_ignored(r) -> None:
    assert uids(r, "제4조제1항제6호가목, 같은 호 다목부터 마목까지", RULE) == [f"item:{RULE}:4:1:6"]


def test_duplicates_removed(r) -> None:
    assert uids(r, "제3조 및 제3조", YAKSA) == [f"art:{YAKSA}:3"]


# 실패해야 하는 사례

def test_other_ministry_law_is_not_mfds(r) -> None:
    assert reasons(r, "「112신고의 운영 및 처리에 관한 법률」 제3조") == ["not_mfds"]


def test_law_names_without_spaces(r) -> None:
    """옛 판례 참조조문은 법령명을 붙여 쓴다. 띄어쓰기만 다르면 같은 법령이다."""
    assert uids(r, "식품위생법시행령 제20조, 제26조, 식품위생법시행규칙 제19조") == [
        "art:004097:20", "art:004097:26", "art:007634:19"]
    assert uids(r, "약사법시행령 제27조") == ["art:004138:27"]
    assert uids(r, "마약류관리에관한법률 제2조 제3호") == [f"item:{NARCO}:2:0:3"]
    assert reasons(r, "「112신고의운영및처리에관한법률」 제3조") == ["not_mfds"]


def test_two_letter_law_name_is_not_context_law(r) -> None:
    """「」 없는 두 글자 법령명(`형법` · `민법`) 뒤 조문을 지금 법령의 조문으로 읽지 않는다."""
    assert uids(r, "형법 제30조에 따라", YAKSA, "3") == []
    assert uids(r, "약사법 제23조, 민법 제5조", YAKSA, "3") == [f"art:{YAKSA}:23"]
    assert reasons(r, "형법 제12조") != ["no_context"]


def test_unknown_quoted_law(r) -> None:
    assert reasons(r, "「전자정부법」 제36조제1항", RULE) == ["unknown_law"]


def test_missing_article_with_article_set(laws) -> None:
    _, arts, _ = body("law_yaksa.xml")
    strict = Resolver(laws, arts)
    assert strict.resolve("약사법 제2조").uids == [f"art:{YAKSA}:2"]
    assert [f.reason for f in strict.resolve("약사법 제999조").failures] == ["no_article"]


# 통합: 고정 자료 법령 4개 전문

def test_full_text_refs_point_to_existing_articles(laws) -> None:
    bodies = [body(f) for f in ("law_yaksa.xml", "law_yaksa_enf.xml", "law_yaksa_rule.xml", "law_food.xml")]
    known = set().union(*(a for _, a, _ in bodies))
    covered = {lid for lid, _, _ in bodies}
    r = Resolver(laws)
    total = missing = 0
    for lid, _, texts in bodies:
        for num, text in texts:
            for ref in r.resolve(text, lid, num).refs:
                if ref.law_id in covered and ref.uid.startswith(("art:", "par:", "item:")):
                    total += 1
                    missing += ref.article_uid not in known
    assert total > 3000
    assert missing == 0


def test_from_snapshot_matches_from_xml(tmp_path: Path, laws: LawDict) -> None:
    """수집 스냅숏 배치(law/list_mfds · lsstmd · list_all)에서 만든 사전이 고정 자료로 만든 것과 같다."""
    for src, dst in [("law_list_mfds.xml", "law/list_mfds/p001.xml"), ("lsstmd_yaksa.xml", f"law/lsstmd/{YAKSA}.xml"),
                     ("law_list_all_p1.xml", "law/list_all/p001.xml")]:
        (tmp_path / dst).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(FIX / src, tmp_path / dst)
    snap = LawDict.from_snapshot(tmp_path)
    assert (snap.names, snap.kind, snap.parent, snap.other_names) == (laws.names, laws.kind, laws.parent,
                                                                     laws.other_names)


def test_article_set_matches_body_articles() -> None:
    for name in ("law_yaksa.xml", "law_food.xml"):
        assert article_set([FIX / name]) == body(name)[1]
