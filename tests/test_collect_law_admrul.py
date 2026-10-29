"""collect/law.py · admrul.py: 고정 자료로 목록 파싱, 쪽 나누기, 요청 계획, 응답 ID 확인."""

from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from mfds.collect import admrul, law
from mfds.collect.common import Collector, FetchError, expect, fetch_pages, list_items

FIX = Path(__file__).resolve().parent / "fixtures"


class PageSession:
    """page 인자에 따라 미리 만든 검색 결과를 돌려준다."""

    def __init__(self, pages: dict[str, bytes]) -> None:
        self.pages = pages
        self.urls: list[str] = []

    def get(self, url: str, timeout: int):
        self.urls.append(url)
        content = self.pages[parse_qs(urlparse(url).query)["page"][0]]
        return type("R", (), {"status_code": 200, "content": content})()


def search_xml(total: int, ids: list[str]) -> bytes:
    rows = "".join(f'<law id="{i}"><법령ID>{i}</법령ID></law>' for i in ids)
    return f'<?xml version="1.0" encoding="UTF-8"?><LawSearch><totalCnt>{total}</totalCnt>{rows}</LawSearch>'.encode()


def test_list_items_mfds_laws() -> None:
    total, laws = list_items((FIX / "law_list_mfds.xml").read_bytes(), "law")
    assert total == 87 and len(laws) == 87
    assert sum(l["법령구분명"] == "법률" for l in laws) == 26
    assert len({l["법령ID"] for l in laws}) == 87


def test_law_plan_counts() -> None:
    _, laws = list_items((FIX / "law_list_mfds.xml").read_bytes(), "law")
    kinds = [k for k, *_ in law.plan(laws)]
    assert (kinds.count("law"), kinds.count("thdCmp"), kinds.count("lsStmd")) == (87, 26, 87)


def test_fetch_pages_walks_all_pages(tmp_path: Path) -> None:
    s = PageSession({"1": search_xml(3, ["a", "b"]), "2": search_xml(3, ["c"])})
    c = Collector(tmp_path / "snap", session=s, sleep=lambda _: None)
    items = fetch_pages(c, "law", "law/list_all", "k", "law", "law", display=2)
    assert [i["법령ID"] for i in items] == ["a", "b", "c"]
    assert (tmp_path / "snap" / "law" / "list_all" / "p002.xml").exists()


def test_fetch_pages_count_mismatch(tmp_path: Path) -> None:
    s = PageSession({"1": search_xml(3, ["a", "b"]), "2": search_xml(3, [])})
    c = Collector(tmp_path / "snap", session=s, sleep=lambda _: None)
    with pytest.raises(FetchError, match="totalCnt 3"):
        fetch_pages(c, "law", "law/list_all", "k", "law", "law", display=2)


def test_expect_law_id() -> None:
    body = (FIX / "law_yaksa.xml").read_bytes()
    expect("기본정보/법령ID", "001783")(body)
    with pytest.raises(FetchError, match="다른 응답"):
        expect("기본정보/법령ID", "000287")(body)
    expect("기본정보/법령ID", "001783")((FIX / "lsstmd_yaksa.xml").read_bytes())
    expect("기본정보/법령ID", "001783")((FIX / "thdcmp_yaksa_delegation.xml").read_bytes())


def test_admrul_plan_and_expect() -> None:
    total, rules = list_items((FIX / "admrul_list_mfds_p1.xml").read_bytes(), "admrul")
    assert total == 513 and len(rules) == 100
    jobs = admrul.plan(rules)
    assert len(jobs) == 100 and jobs[0][1] == f"admrul/body/{rules[0]['행정규칙ID']}.xml"
    expect("행정규칙기본정보/행정규칙일련번호", "2100000282826")((FIX / "admrul_dur.xml").read_bytes())


def test_admrul_duplicate_id_stops() -> None:
    rule = {"행정규칙ID": "1", "행정규칙일련번호": "10"}
    with pytest.raises(FetchError, match="중복"):
        admrul.plan([rule, {**rule, "행정규칙일련번호": "11"}])
