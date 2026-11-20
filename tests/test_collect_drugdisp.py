"""collect/drugdisp.py: 키 가림, 응답 검사, 쪽 모음, 건수 · 번호 검사. 네트워크 없이 가짜 세션으로 시험한다."""

import json
from pathlib import Path

import pytest

from mfds.collect import drugdisp
from mfds.collect.common import Collector, FetchError, mask


def page(total: int, seqs: list[str], code: str = "00") -> bytes:
    items = [{"ADM_DISPS_SEQ": s, "EXPOSE_CONT": "v", "BEF_APPLY_LAW": "「약사법」 제31조"} for s in seqs]
    body = {"header": {"resultCode": code, "resultMsg": "NORMAL SERVICE."},
            "body": {"pageNo": 1, "totalCount": total, "numOfRows": 2, "items": items}}
    return json.dumps(body).encode("utf-8")


class PageSession:
    def __init__(self, pages: dict[str, bytes]) -> None:
        self.pages = pages
        self.urls: list[str] = []

    def get(self, url: str, timeout: int):
        self.urls.append(url)
        n = url.split("pageNo=")[1].split("&")[0]
        return type("R", (), {"status_code": 200, "content": self.pages[n]})()


def test_service_key_keeps_encoded_and_quotes_decoded() -> None:
    assert drugdisp.service_key("ab%2Bcd%3D%3D") == "ab%2Bcd%3D%3D"
    assert drugdisp.service_key("ab+cd/==") == "ab%2Bcd%2F%3D%3D"


def test_mask_hides_service_key() -> None:
    url = drugdisp.page_url("SECRET%2B", 1)
    assert "SECRET" not in mask(url) and "serviceKey=***" in mask(url)


def test_check_json_error() -> None:
    drugdisp.check_json(page(1, ["1"]))
    with pytest.raises(FetchError, match="오류 응답"):
        drugdisp.check_json(page(1, ["1"], code="30"))
    with pytest.raises(FetchError, match="오류 응답"):  # 키 오류 응답에는 header가 없다
        drugdisp.check_json(json.dumps({"OpenAPI_ServiceResponse": {"cmmMsgHeader": {}}}).encode())
    with pytest.raises(FetchError, match="JSON 아님"):
        drugdisp.check_json(b"<html>")


def test_collect_pages_and_count(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(drugdisp, "PAGE", 2)
    s = PageSession({"1": page(3, ["10", "11"]), "2": page(3, ["12"])})
    c = Collector(tmp_path / "snap", session=s, sleep=lambda _: None)
    assert drugdisp.collect(c, "SECRETKEY") == 3
    assert "SECRETKEY" not in c.manifest.read_text(encoding="utf-8")
    assert sorted(p.name for p in (c.snapshot / "drugdisp").iterdir()) == ["p001.json", "p002.json"]
    again = Collector(tmp_path / "snap", session=PageSession({}), sleep=lambda _: None)
    assert drugdisp.collect(again, "SECRETKEY") == 3 and again.requests == 0  # 이어받기


def test_collect_count_mismatch_and_duplicate(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(drugdisp, "PAGE", 2)
    s = PageSession({"1": page(3, ["10", "11"]), "2": page(3, [])})
    with pytest.raises(FetchError, match="totalCount 3"):
        drugdisp.collect(Collector(tmp_path / "a", session=s, sleep=lambda _: None), "k")
    s = PageSession({"1": page(3, ["10", "11"]), "2": page(3, ["10"])})
    with pytest.raises(FetchError, match="중복"):
        drugdisp.collect(Collector(tmp_path / "b", session=s, sleep=lambda _: None), "k")
