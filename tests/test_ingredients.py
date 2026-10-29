"""DUR 별표 파서와 성분 정규화기. 고정 자료(병용금기 고시 2026-07-21 시행본)의 실제 표기로 시험한다."""

from pathlib import Path

import pytest

from mfds.collect.dur import annexes_from_xml, check_seq, parse_annex
from mfds.ingredients import normalize

FIX = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(scope="module")
def annexes() -> dict:
    return annexes_from_xml(FIX / "admrul_dur.xml")


@pytest.fixture(scope="module")
def raw_names(annexes) -> set[str]:
    names = set()
    for r in annexes["1"]:
        names |= {r["ingredient_a"], r["ingredient_b"]}
    for k in ("2", "3"):
        names |= {r["ingredient"] for r in annexes[k]}
    return names


def uid(raw: str) -> str:
    return normalize(raw).uid


# 별표 파서

def test_annex_row_counts_and_no_missing_seq(annexes) -> None:
    assert {k: len(v) for k, v in annexes.items()} == {"1": 1680, "2": 211, "3": 1240}
    assert all(check_seq(v) == [] for v in annexes.values())


def test_multiline_cells_join(annexes) -> None:
    assert annexes["1"][1]["note"] == "methotrexate 1주에 15mg 이상 투여시"
    assert "황체호르몬부족으로 인한" in annexes["3"][0]["grade"]  # 한글은 단어 중간 줄바꿈이라 붙인다


def test_multiline_english_keeps_word_boundary(raw_names) -> None:
    assert any("Clove Powder" in n for n in raw_names)
    assert not any("ClovePowder" in n for n in raw_names)


def test_check_seq_detects_gap() -> None:
    rows = parse_annex("│1   │a│b│ │\n│3   │c│d│ │\n│3   │e│f│ │\n", "1")
    assert check_seq(rows) == [2, 3]  # 2 누락, 3 중복


# 성분 정규화

def test_case_variants_merge() -> None:
    assert uid("Acemetacin") == uid("acemetacin") == "ing:acemetacin"


def test_form_paren_is_condition_not_name() -> None:
    a, b = normalize("Rilpivirine (경구제)"), normalize("Rilpivirine(경구제)")
    assert a.uid == b.uid == "ing:rilpivirine"
    assert a.form == "경구제"
    assert normalize("Rilpivirine(주사제)").uid == "ing:rilpivirine"


def test_class_code_removed_and_kept_as_property() -> None:
    a, b = normalize("methotrexate (분류번호 : 421)"), normalize("methotrexate  (분류번호 : 421)")
    assert a.uid == b.uid == "ing:methotrexate"
    assert a.class_codes == ["421"]
    assert normalize("erythromycin (분류번호 : 614, 615)").class_codes == ["614", "615"]


def test_synonym_paren_becomes_alias() -> None:
    for raw in ("rifampicin (rifampin)", "Rifampicin(Rifampin)"):
        n = normalize(raw)
        assert n.uid == "ing:rifampicin" and n.aliases == ["rifampin"]
    assert normalize("valproic acid(valproate)").aliases == ["valproate"]


def test_equals_notation() -> None:
    n = normalize("Norethisterone(EP)= Norethindrone(USP)")
    assert n.uid == "ing:norethisterone" and n.aliases == ["norethindrone"]


def test_leading_qualifier_alias() -> None:
    n = normalize("(Micronized)Progesterone")
    assert n.uid == "ing:(micronized)progesterone" and "progesterone" in n.aliases


def test_contains_suffix() -> None:
    a, b = normalize("Cobicistat 함유제제"), normalize("cobicistat 함유제제")
    assert a.uid == b.uid == "ing:cobicistat" and a.contains
    n = normalize("ketoprofen 함유 외용제")
    assert n.uid == "ing:ketoprofen" and n.contains and n.form == "외용제"


def test_quote_variants_merge() -> None:
    assert uid("St. John's wort 함유제제") == uid("St. john’s wort 함유제제") == "ing:st. john's wort"


def test_combination_keeps_components() -> None:
    n = normalize("Atorvastatin + Amlodipine +Candesartan")
    assert n.uid == "ing:atorvastatin + amlodipine + candesartan"
    assert [c.uid for c in n.components] == ["ing:atorvastatin", "ing:amlodipine", "ing:candesartan"]
    n = normalize("desogestrel + ethinyl estradiol(정제)")
    assert n.uid == "ing:desogestrel + ethinyl estradiol" and n.form == "정제"


def test_isotope_stays_in_name() -> None:
    assert uid("Florbetaben(18F)") == "ing:florbetaben(18f)"
    assert uid("sodium iodide(131I)") != uid("sodium iodide(123I)")


def test_other_salt_stays_separate_node_with_same_base() -> None:
    a, b = normalize("morphine"), normalize("morphine sulfate")
    assert a.uid != b.uid
    assert b.base == "morphine"
    assert normalize("barium sulfate").base is None  # 양이온만 남으면 염 제거형을 만들지 않는다
    assert normalize("potassium citrate").base is None


def test_hyphen_spacing_from_line_wrap() -> None:
    assert uid("6β-iodomethyl-19-norcholest-5(10)- en-3β-ol(131I)") == uid("6β-iodomethyl-19-norcholest-5(10)-en -3β-ol(131I)")


def test_colon_replaced_for_rule_id() -> None:
    assert ":" not in normalize("foo:bar").name


def test_deleted_row() -> None:
    assert normalize("<삭제>") is None


def test_all_fixture_names_normalize_cleanly(raw_names) -> None:
    out = [normalize(r) for r in raw_names]
    ok = [n for n in out if n]
    assert len(out) - len(ok) == 2  # <삭제> 표기
    assert all("  " not in n.uid and n.uid == n.uid.strip() for n in ok)
    assert all(n.uid == n.uid.lower() for n in ok)
    assert len({n.uid for n in ok}) < len(ok)  # 대소문자 · 공백 변형이 합쳐진다
