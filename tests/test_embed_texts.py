"""scripts/embed_texts.py: PREREG 4-5 임베딩 텍스트 규칙(법령명 접두, 항 번호, 별표 청크, 정답 출처 필드 제외)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import embed_texts as E  # noqa: E402


def test_article_and_admin_article_prefix_law_name() -> None:
    r = {"uid": "art:1:20-2", "law": "축산물 위생관리법", "text": "제20조의2(감시원)\n① 본문"}
    assert E.texts_for("Article", r) == [("art:1:20-2", "[축산물 위생관리법] 제20조의2(감시원)\n① 본문")]
    a = {"uid": "admart:9:8", "law": "어떤 고시", "text": "제8조(협의) 본문"}
    assert E.texts_for("AdminArticle", a) == [("admart:9:8", "[어떤 고시] 제8조(협의) 본문")]


def test_paragraph_number_and_branch() -> None:
    r = {"uid": "par:1:3-2:2", "law": "법", "n": 3, "b": 2, "no": 2, "text": "② 위원회는 심의한다."}
    assert E.texts_for("Paragraph", r) == [("par:1:3-2:2", "[법] 제3조의2 제2항 위원회는 심의한다.")]
    r0 = {**r, "b": 0, "text": "본문만"}
    assert E.texts_for("Paragraph", r0)[0][1] == "[법] 제3조 제2항 본문만"


def test_annex_chunks_1000_no_overlap() -> None:
    body = "가" * 2500
    got = E.texts_for("Annex", {"uid": "anx:1:7-2", "law": "법", "no": "7-2", "title": "기준", "text": body})
    assert [i for i, _ in got] == ["anx:1:7-2#0", "anx:1:7-2#1", "anx:1:7-2#2"]
    assert all(t.startswith("[법] 별표 7의2 기준 · ") for _, t in got)
    assert "".join(t.split(" · ", 1)[1] for _, t in got) == body
    assert E.texts_for("Annex", {"uid": "anx:1:1", "law": "법", "no": "1", "title": "t", "text": ""}) == []


def test_answer_source_fields_not_used() -> None:
    assert E.texts_for("Disposition", {"uid": "disp:food:1", "text": "위반 문구"}) == [("disp:food:1", "위반 문구")]
    assert "ref_text" not in E.Q["Case"] and "law_ref_text" not in E.Q["Disposition"]


def test_case_summary_then_holding_and_empty() -> None:
    assert E.texts_for("Case", {"uid": "case:1", "summary": " 요지 ", "holding": "사항"}) == [("case:1", "요지")]
    assert E.texts_for("Case", {"uid": "case:1", "summary": "", "holding": "사항"}) == [("case:1", "사항")]
    assert E.texts_for("Case", {"uid": "case:1", "summary": None, "holding": " "}) == []


def test_two_field_nodes_joined() -> None:
    assert E.texts_for("Interpretation", {"uid": "interp:1", "a": "질의", "b": "회답"}) == [("interp:1", "질의\n회답")]
    assert E.texts_for("AdminAppeal", {"uid": "decc:1", "a": "", "b": "청구"}) == [("decc:1", "청구")]
