"""collect/foodsafety.py: API 응답 검사, 쪽 모음, 복사 모드, 업소 정보 열 지우기. 업소 정보는 가짜 값으로 만든다."""

import csv
import gzip
import io
import json
import shutil
from pathlib import Path

import pytest

from mfds.collect import foodsafety
from mfds.collect.common import Collector, FetchError

FIX = Path(__file__).resolve().parent / "fixtures"
COLS = ["PRCSCITYPOINT_BSSHNM", "ADDR", "TELNO", "PRSDNT_NM", "LCNS_NO", "DSPSDTLS_SEQ", "VILTCN", "LAWORD_CD_NM"]


def page(total: int, seqs: list[str]) -> bytes:
    rows = [{"DSPSDTLS_SEQ": s, "VILTCN": "v"} for s in seqs]
    body = {"I0470": {"total_count": str(total), "row": rows, "RESULT": {"CODE": "INFO-000", "MSG": "ok"}}}
    return json.dumps(body).encode("utf-8")


class RangeSession:
    def __init__(self, pages: dict[str, bytes]) -> None:
        self.pages = pages
        self.urls: list[str] = []

    def get(self, url: str, timeout: int):
        self.urls.append(url)
        start = url.split("/json/")[1].split("/")[0]
        return type("R", (), {"status_code": 200, "content": self.pages[start]})()


def write_csv_gz(path: Path) -> None:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(COLS)
    w.writerow(["가짜업소", "가짜주소", "000", "가짜대표", "L1", "1", "위반", "식품위생법 제44조"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(("﻿" + buf.getvalue()).encode("utf-8")))


def test_check_json_fixture_and_error() -> None:
    foodsafety.check_json((FIX / "food_disposition_I0470_5rows.json").read_bytes())
    with pytest.raises(FetchError, match="INFO-300"):
        foodsafety.check_json(json.dumps({"RESULT": {"CODE": "INFO-300", "MSG": "인증키 오류"}}).encode())
    with pytest.raises(FetchError, match="JSON 아님"):
        foodsafety.check_json(b"<html>")


def test_collect_api_pages_and_count(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(foodsafety, "PAGE", 2)
    s = RangeSession({"1": page(3, ["1", "2"]), "3": page(3, ["3"])})
    c = Collector(tmp_path / "snap", session=s, sleep=lambda _: None)
    assert foodsafety.collect_api(c, "foodkey") == 3
    assert "foodkey" not in c.manifest.read_text(encoding="utf-8")
    assert [r["DSPSDTLS_SEQ"] for r in foodsafety.read_rows(c.snapshot)] == ["1", "2", "3"]


def test_collect_api_count_mismatch(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(foodsafety, "PAGE", 2)
    s = RangeSession({"1": page(3, ["1"]), "3": page(3, ["3"])})
    c = Collector(tmp_path / "snap", session=s, sleep=lambda _: None)
    with pytest.raises(FetchError, match="total_count 3"):
        foodsafety.collect_api(c, "foodkey")


def test_copy_mode_and_pii_removed_on_read(tmp_path: Path) -> None:
    src = tmp_path / "manual" / "I0470.csv.gz"
    write_csv_gz(src)
    c = Collector(tmp_path / "snap")
    assert foodsafety.collect_copy(c, src) == 1
    row = foodsafety.read_rows(c.snapshot)[0]
    assert not set(foodsafety.PII) & set(row) and row["VILTCN"] == "위반"
    assert foodsafety.read_rows(c.snapshot, keep_pii=True)[0]["ADDR"] == "가짜주소"
    manifest = c.manifest.read_text(encoding="utf-8")
    assert foodsafety.MANUAL_NOTE in manifest and "가짜" not in manifest and c.requests == 0


def test_read_rows_from_json_fixture(tmp_path: Path) -> None:
    d = tmp_path / "foodsafety" / "I0470"
    d.mkdir(parents=True)
    shutil.copy(FIX / "food_disposition_I0470_5rows.json", d / "000001.json")
    assert len(foodsafety.read_rows(tmp_path)) == 5


def test_scrub_masks_own_business_values_in_text() -> None:
    """위반 · 처분 내용 원문 안의 그 업소 정보는 가린다(가짜 값). 짧은 값 · 다른 열은 그대로."""
    from mfds.disposition import scrub
    row = {"PRCSCITYPOINT_BSSHNM": "가나다 식품", "LCNS_NO": "20190012345", "PRSDNT_NM": "", "ADDR": "", "TELNO": "",
           "VILTCN": "(20250101) 가나다식품에서 제조한 제품(품목번호 2019-0012345-001) 표시 위반",
           "DSPSCN": "가나다 식품 영업정지 7일", "INDUTY_CD_NM": "식품제조가공업"}
    out = scrub(row)
    assert out["VILTCN"] == "(20250101) <업소명>에서 제조한 제품(품목번호 <인허가번호>-001) 표시 위반"
    assert out["DSPSCN"] == "<업소명> 영업정지 7일"
    assert out["INDUTY_CD_NM"] == row["INDUTY_CD_NM"] and out["PRCSCITYPOINT_BSSHNM"] == row["PRCSCITYPOINT_BSSHNM"]
    short = scrub({**row, "PRCSCITYPOINT_BSSHNM": "가", "LCNS_NO": "123"})
    assert "가나다식품" in short["VILTCN"] and "<" not in short["VILTCN"]


def test_has_mask_marks_only_scrub_output() -> None:
    from mfds.disposition import has_mask
    assert has_mask("<업소명>에서 제조한 제품") and has_mask("품목번호 <인허가번호>-001")
    assert not has_mask("유통기한 <1개월> 경과") and not has_mask("") and not has_mask(None)
