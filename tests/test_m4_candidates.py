"""eval/m4_candidates.py: 조문 번호 경계, 범위 · 나열 표기, 하위 법령 · 고시의 법령 맥락, 대상 자신 제외, import 금지.

합성 법령 · 조문만 쓴다(`eval.sources`의 Law · Article · AdminArticle과 같은 모양).
"""

import ast
import sys
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "eval"))
import m4_candidates as m4  # noqa: E402

LAWS = {
    "L": NS(law_id="L", name="약사법", abbr=None, kind="법률"),
    "E": NS(law_id="E", name="약사법 시행령", abbr=None, kind="대통령령"),
    "R": NS(law_id="R", name="약사법 시행규칙", abbr=None, kind="총리령"),
    "F": NS(law_id="F", name="식품위생법", abbr=None, kind="법률"),
}
SUB = {"E": {"L"}, "R": {"E"}}


def _art(lid: str, no: str, *texts: str) -> NS:
    uid = f"art:{lid}:{no}"
    units = [(uid, texts[0])] + [(f"par:{lid}:{no}:{i}", t) for i, t in enumerate(texts[1:], 1)]
    return NS(uid=uid, law_id=lid, no=no, units=units, text=" ".join(texts))


def _adm(aid: str, n: int, text: str) -> NS:
    return NS(uid=f"admart:{aid}:{n}", admrul_id=aid, rule_name="고시", text=text)


def _sources(target: str, arts: list[NS], adms: list[NS] = ()) -> list[str]:
    a = {x.uid: x for x in arts}
    aa = {x.uid: x for x in adms}
    return m4.sources(m4.candidates(target, LAWS, a, aa, SUB))


def test_article_number_boundaries() -> None:
    p = m4.article_pattern("5")
    assert p.search("제5조에 따라") and p.search("제 5 조 제1항")
    assert not p.search("제5조의2에 따라") and not p.search("제15조") and not p.search("제50조")
    q = m4.article_pattern("5-2")
    assert q.search("제5조의2에 따라") and q.search("제5조 의 2")
    assert not q.search("제5조에") and not q.search("제5조의20") and not q.search("제5조의2의3")


def test_range_counts_inner_articles_only() -> None:
    text = "제3조부터 제7조까지의 규정"
    assert m4.find("5", text) and m4.find("5-2", text)
    assert not m4.find("8", text) and not m4.find("2", text)
    assert m4.find("3", text) == [(0, 3)]  # 끝점은 일반 표기로만 잡힌다


def test_same_law_excludes_target_and_other_law_quotes() -> None:
    arts = [
        _art("L", "5", "제5조제2항에 따른 허가"),  # 대상 자신
        _art("L", "9", "제5조에 따른 허가를 받은 자"),
        _art("L", "10", "「식품위생법」 제5조에 따른 영업"),
        _art("L", "11", "「식품위생법」 제3조 및 같은 법 제5조"),
    ]
    assert _sources("art:L:5", arts) == ["art:L:9"]


def test_subordinate_needs_law_context() -> None:
    arts = [
        _art("L", "5", "본문"),
        _art("E", "2", "법 제5조에 따른 허가"),  # 하위 법령의 `법` = 상위 법률
        _art("E", "3", "영 제5조에 따른 신고"),  # 시행령 자기 조문
        _art("R", "4", "제5조에 따른 서식"),  # 맥락 없음: 자기 조문
        _art("R", "6", "「약사법」 제3조, 제4조제1항 및 제5조에 따라", "같은 법 제5조"),  # 나열 꼬리 건너뜀
        _art("F", "7", "법 제5조에 따른"),  # 범위 밖 법령
    ]
    assert _sources("art:L:5", arts) == ["art:E:2", "art:R:6"]


def test_admin_rule_name_or_defined_alias() -> None:
    adms = [
        _adm("A1", 1, "「약사법」 제5조에 따라"),
        _adm("A2", 1, "법 제5조에 따라"),  # `법` 정의 없음
        _adm("A3", 1, "이 고시는 「약사법」(이하 \"법\"이라 한다)에 따른다"),
        _adm("A3", 2, "법 제5조에 따른 기준"),
        _adm("A4", 1, "「식품위생법」 제5조"),
    ]
    assert _sources("art:L:5", [_art("L", "5", "본문")], adms) == ["admart:A1:1", "admart:A3:2"]


def test_descendants_transitive() -> None:
    assert m4.descendants("L", SUB) == {"E", "R"}
    assert m4.descendants("E", SUB) == {"R"}


def test_no_refs_or_loader_import() -> None:
    tree = ast.parse(Path(m4.__file__).read_text(encoding="utf-8"))
    names = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    names |= {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert not any(x.startswith(("mfds", "sources", "eval")) for x in names), names
