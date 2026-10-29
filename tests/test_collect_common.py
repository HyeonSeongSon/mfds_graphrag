"""collect/common.py: 요청 간격, 이어받기, 오류 문서 거부, 재시도, 키 가림, manifest 복구, 복사 모드.

네트워크 없이 가짜 세션으로 시험한다.
"""

from pathlib import Path

import pytest
import requests

from mfds.collect import common
from mfds.collect.common import Collector, FetchError

OK = b'<?xml version="1.0" encoding="UTF-8"?><LawSearch><totalCnt>1</totalCnt></LawSearch>'
ERR = ('<?xml version="1.0" encoding="UTF-8"?><Response><result>사용자 정보 검증에 실패하였습니다.</result>'
       '<msg>IP 등록</msg></Response>').encode("utf-8")
URL = "https://www.law.go.kr/DRF/lawSearch.do?OC=secretkey&type=XML&target=law"


class FakeResponse:
    def __init__(self, status: int, content: bytes) -> None:
        self.status_code = status
        self.content = content


class FakeSession:
    def __init__(self, replies: list) -> None:
        self.replies = list(replies)
        self.urls: list[str] = []

    def get(self, url: str, timeout: int) -> FakeResponse:
        self.urls.append(url)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        self.now += s


def make(tmp_path: Path, replies: list) -> tuple[Collector, FakeSession, FakeClock]:
    session, clock = FakeSession(replies), FakeClock()
    c = Collector(tmp_path / "snap", session=session, sleep=clock.sleep, clock=clock.clock)
    return c, session, clock


def manifest_rows(c: Collector) -> list[list[str]]:
    lines = c.manifest.read_text(encoding="utf-8").splitlines()
    assert lines[0].split("\t") == list(common.MANIFEST_COLUMNS)
    return [line.split("\t") for line in lines[1:]]


def test_requests_are_spaced_one_second(tmp_path: Path) -> None:
    c, _, clock = make(tmp_path, [FakeResponse(200, OK), FakeResponse(200, OK)])
    c.fetch("law", "a", "law/a.xml", URL)
    clock.now += 0.3
    c.fetch("law", "b", "law/b.xml", URL)
    assert clock.sleeps == [pytest.approx(0.7)]


def test_resume_skips_saved_file(tmp_path: Path) -> None:
    c, session, _ = make(tmp_path, [FakeResponse(200, OK)])
    c.fetch("law", "a", "law/a.xml", URL)
    again = Collector(tmp_path / "snap", session=session)
    assert again.fetch("law", "a", "law/a.xml", URL) == OK
    assert len(session.urls) == 1 and again.requests == 0
    assert len(manifest_rows(again)) == 1


def test_error_document_is_rejected_and_not_saved(tmp_path: Path) -> None:
    c, _, _ = make(tmp_path, [FakeResponse(200, ERR)])
    with pytest.raises(FetchError, match="사용자 정보 검증"):
        c.fetch("law", "a", "law/a.xml", URL)
    assert not (tmp_path / "snap" / "law" / "a.xml").exists()
    assert not list((tmp_path / "snap").rglob("*.part"))
    assert manifest_rows(c) == []


def test_missing_id_document_is_rejected(tmp_path: Path) -> None:
    body = '<?xml version="1.0" encoding="utf-8"?><Law>일치하는 판례가 없습니다.  판례명을 확인하여 주십시오.</Law>'
    c, _, _ = make(tmp_path, [FakeResponse(200, body.encode("utf-8"))])
    with pytest.raises(FetchError, match="없는 ID"):
        c.fetch("prec", "1", "prec/1.xml", URL)


def test_non_xml_is_rejected(tmp_path: Path) -> None:
    c, _, _ = make(tmp_path, [FakeResponse(200, b"<html>maintenance")])
    with pytest.raises(FetchError, match="XML 아님"):
        c.fetch("law", "a", "law/a.xml", URL)


def test_retries_network_error_and_5xx(tmp_path: Path) -> None:
    replies = [requests.ConnectionError(), FakeResponse(503, b""), FakeResponse(404, b""), FakeResponse(200, OK)]
    c, session, clock = make(tmp_path, replies)
    assert c.fetch("law", "a", "law/a.xml", URL) == OK
    assert len(session.urls) == 4
    assert [5.0, 10.0, 20.0] == [s for s in clock.sleeps if s >= 5]


def test_retries_exhausted(tmp_path: Path) -> None:
    c, _, _ = make(tmp_path, [FakeResponse(500, b"")] * 4)
    with pytest.raises(FetchError, match="재시도 소진") as e:
        c.fetch("law", "a", "law/a.xml", URL)
    assert "secretkey" not in str(e.value)


def test_client_error_is_not_retried(tmp_path: Path) -> None:
    c, session, _ = make(tmp_path, [FakeResponse(403, b"")])
    with pytest.raises(FetchError, match="HTTP 403"):
        c.fetch("law", "a", "law/a.xml", URL)
    assert len(session.urls) == 1


def test_manifest_masks_keys(tmp_path: Path) -> None:
    food = "http://openapi.foodsafetykorea.go.kr/api/foodkey123/I0470/json/1/1000"
    c, _, _ = make(tmp_path, [FakeResponse(200, OK), FakeResponse(200, b"{}")])
    c.fetch("law", "a", "law/a.xml", URL)
    c.fetch("foodsafety", "I0470", "foodsafety/p1.json", food, check=None)
    text = c.manifest.read_text(encoding="utf-8")
    assert "secretkey" not in text and "foodkey123" not in text
    assert "OC=***" in text and "/api/***/I0470" in text


def test_manifest_recovered_when_file_saved_without_row(tmp_path: Path) -> None:
    c, session, _ = make(tmp_path, [])
    (tmp_path / "snap" / "law").mkdir()
    (tmp_path / "snap" / "law" / "a.xml").write_bytes(OK)
    assert c.fetch("law", "a", "law/a.xml", URL) == OK
    rows = manifest_rows(c)
    assert session.urls == [] and len(rows) == 1 and rows[0][-1] == "manifest 복구"


def test_copy_in_keeps_source_and_records_hash(tmp_path: Path) -> None:
    src = tmp_path / "orig" / "I0470.csv.gz"
    src.parent.mkdir()
    src.write_bytes(b"gzdata")
    c, _, _ = make(tmp_path, [])
    c.copy_in("foodsafety", "I0470", "foodsafety/I0470.csv.gz", src, "사용자 수동 수집, 2026-10-28")
    c.copy_in("foodsafety", "I0470", "foodsafety/I0470.csv.gz", src, "사용자 수동 수집, 2026-10-28")
    rows = manifest_rows(c)
    assert src.read_bytes() == b"gzdata"
    assert len(rows) == 1 and rows[0][-1] == "사용자 수동 수집, 2026-10-28"
    assert rows[0][3] == "I0470.csv.gz"  # 저장소 밖 경로는 파일 이름만


def test_mask_patterns() -> None:
    assert common.mask(URL) == "https://www.law.go.kr/DRF/lawSearch.do?OC=***&type=XML&target=law"
    assert common.mask("x?type=XML&OC=k") == "x?type=XML&OC=***"
