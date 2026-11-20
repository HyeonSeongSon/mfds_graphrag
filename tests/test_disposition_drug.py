"""mfds/disposition.py 의약품 · 화장품 처분(T7): 업체 정보 · 인명 가림, 근거법령 분할, 위반 조문 정답. 값은 모두 가짜다."""

import json
from pathlib import Path

from mfds import disposition as D
from mfds.refs import LawDict, Resolver

YAK, RULE, COS, COS_R = "001783", "011794", "002015", "008741"
LD = LawDict({"약사법": YAK, "의약품 등의 안전에 관한 규칙": RULE, "화장품법": COS, "화장품법 시행규칙": COS_R},
             {YAK: "법률", RULE: "총리령", COS: "법률", COS_R: "총리령"},
             {YAK: "약사법", RULE: "의약품 등의 안전에 관한 규칙", COS: "화장품법", COS_R: "화장품법 시행규칙"},
             {COS_R: COS})
ARTS = {f"art:{YAK}:{n}" for n in (31, 37, 38, 62, 76)} | {f"art:{RULE}:{n}" for n in (48, 95)} | \
       {f"art:{COS}:{n}" for n in (13, 24)} | {f"art:{COS_R}:{n}" for n in (22, 29)}
R = Resolver(LD, ARTS)


def arts(text: str) -> dict:
    return D.basis_articles(text, R.resolve)


def test_labeled_lines_answer_is_violation_only() -> None:
    res = arts("○ 위반법령: 「약사법」 제31조제4항\n○ 처분근거법령: 「약사법」 제76조제2항 및 「의약품 등의 안전에 관한 규칙」 "
               "제95조 관련 [별표8] 행정처분의 기준")
    assert res["violation"] == [f"art:{YAK}:31"]
    assert set(res["disposition"]) == {f"art:{YAK}:76", f"art:{RULE}:95"}


def test_halfwidth_brackets_and_unlabeled_disposition_line() -> None:
    res = arts("○ ｢약사법｣ 제31조제4항\n○ ｢약사법｣ 제76조, ｢의약품 등의 안전에 관한 규칙｣ 제95조")
    assert res["violation"] == [f"art:{YAK}:31"] and f"art:{YAK}:76" in res["disposition"]


def test_disposition_article_mixed_in_violation_line_is_dropped() -> None:
    res = arts("○ 「약사법」 제62조, 제76조")
    assert res["violation"] == [f"art:{YAK}:62"] and res["dropped"] == 1


def test_only_disposition_articles_gives_empty_answer() -> None:
    assert arts("○「약사법」제76조제2항 및 「의약품 등의 안전에 관한 규칙」[별표8] Ⅰ. 일반기준 제11호")["violation"] == []


def test_violation_side_annex_kept_and_same_law_rule_resolved() -> None:
    """위반 쪽 별표(표시 · 광고 준수사항)는 처분 라벨 근거가 아니고, 같은 줄의 "같은 법 시행규칙"은 그 줄 법령으로 읽는다."""
    res = arts("○「화장품법」제13조 및 같은 법 시행규칙 제22조 관련 [별표 5] 제2호 사목\n"
               "○「화장품법」제24조제1항제10호 및 같은 법 시행규칙 제29조 관련 [별표 7] 행정처분의 기준")
    assert res["violation"] == [f"art:{COS}:13", f"art:{COS_R}:22"]
    assert set(res["disposition"]) == {f"art:{COS}:24", f"art:{COS_R}:29"}


def test_context_not_taken_from_other_label_line() -> None:
    """처분 줄 → 위반 줄 순서여도 위반 줄의 상대 표기가 처분 줄 법령으로 읽히지 않는다(같은 라벨 앞 줄만 문맥)."""
    res = arts("○ 처분근거법령: 「화장품법 시행규칙」 제29조\n○ 위반법령: 같은 법 제22조")
    assert f"art:{COS_R}:22" not in res["violation"]


def test_header_line_label_carries_to_sub_bullets() -> None:
    res = arts("○ 위반법령\n - 「약사법」제37조제1항, 제38조제1항\n○ 행정처분기준\n - 「약사법」 제76조제1항제3호")
    labels = [l for l, _ in res["lines"]]
    assert labels == ["violation", "violation", "disposition", "disposition"]
    assert res["violation"] == [f"art:{YAK}:37", f"art:{YAK}:38"]


def _drug_row(**kw) -> dict:
    row = {"ADM_DISPS_SEQ": "1", "ENTP_NAME": "(주)가짜바이오제약", "ADDR": "가짜도 가짜시 가짜구", "BIZRNO": "1234567890",
           "ENTP_NO": "20190001", "EXPOSE_CONT": "", "ADM_DISPS_NAME": "경고", "BEF_APPLY_LAW": "「약사법」 제31조"}
    row.update(kw)
    return row


def test_scrub_drug_full_and_core_name_and_food_unchanged() -> None:
    out = D.scrub(_drug_row(EXPOSE_CONT="가짜바이오제약이 허가 없이 제조, 주식회사가짜바이오제약 명의 사업자 1234567890"),
                  D._DRUG_MASKS, D.DRUG_TEXT_FIELDS)
    assert "가짜바이오" not in out["EXPOSE_CONT"] and "1234567890" not in out["EXPOSE_CONT"]
    assert out["EXPOSE_CONT"].startswith("<업체명>이 허가 없이") and D.has_mask(out["EXPOSE_CONT"])
    food = {"PRCSCITYPOINT_BSSHNM": "가나다 식품", "VILTCN": "가나다식품에서 위반", "DSPSCN": ""}
    assert D.scrub(food)["VILTCN"] == "<업소명>에서 위반"  # 식품 기본값 그대로


def test_short_core_name_not_masked_inside_words() -> None:
    out = D.scrub(_drug_row(ENTP_NAME="동아(주)", EXPOSE_CONT="동아시아 지역 수출용 제품"), D._DRUG_MASKS, D.DRUG_TEXT_FIELDS)
    assert out["EXPOSE_CONT"] == "동아시아 지역 수출용 제품"
    out = D.scrub(_drug_row(ENTP_NAME="주식회사 가짜제약", EXPOSE_CONT="한가짜제약 제품"), D._DRUG_MASKS, D.DRUG_TEXT_FIELDS)
    assert out["EXPOSE_CONT"] == "한가짜제약 제품"  # 핵심 이름 앞이 한글이면 가리지 않는다


def test_person_rules_mask_names_and_keep_common_words() -> None:
    st: dict = {}
    assert D.mask_person_names("약사 홍길동이 조제함", st) == f"약사 {D.PERSON}이 조제함"
    assert D.mask_person_names("홍길동 약사가 판매", st) == f"{D.PERSON} 약사가 판매"
    for masked in ("김O영으로부터", "김○영으로부터", "김*영으로부터"):
        assert D.mask_person_names(masked, st).startswith(D.PERSON)
    assert set(st) == {"이름+직함", "직함+이름", "부분 가림"}
    for keep in ("관리약사 부재", "무자격약사 조제", "대표자 변경 미신고", "약사 이외의 자가 조제", "대표 이사 변경",
                 "약사 조제 기록", "약사 이하 직원", "고용 약사가 판매", "공동 대표가 서명", "소속 약사의 근무",
                 "관리사 선생님이 권유"):
        assert D.mask_person_names(keep) == keep, keep


def _write_drug(tmp_path: Path, rows: list[dict]) -> Path:
    d = tmp_path / D.DRUG_SOURCE
    d.mkdir(parents=True)
    (d / "p001.json").write_text(json.dumps({"body": {"totalCount": len(rows), "items": rows}}, ensure_ascii=False),
                                 encoding="utf-8")
    return tmp_path


def test_read_drug_rows_masks_regardless_of_keep_pii(tmp_path: Path) -> None:
    S = _write_drug(tmp_path, [_drug_row(EXPOSE_CONT="대표 김철수가 가짜바이오제약 명의로 판매")])
    for keep in (False, True):
        r = D.read_drug_rows(S, keep_pii=keep)[0]
        assert "김철수" not in r["EXPOSE_CONT"] and "가짜바이오" not in r["EXPOSE_CONT"]
        assert D.has_mask(r["EXPOSE_CONT"]) and (("ENTP_NAME" in r) == keep)


def test_read_drug_rows_missing_folder_is_empty(tmp_path: Path) -> None:
    assert D.read_drug_rows(tmp_path) == []


def test_drug_violation_text_collapses_whitespace_and_bullet() -> None:
    assert D.drug_violation_text(" ○ 제조업무정지 기간 중\n  제조한 사실이 있음 ") == "제조업무정지 기간 중 제조한 사실이 있음"
