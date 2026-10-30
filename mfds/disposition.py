"""식품 행정처분(I0470) 행 읽기 · 위반 문구 라벨 정규화 (1주차 D4에 잠금). 적재기와 평가셋 생성기가 같은 함수를 쓴다.

- `read_rows`: 스냅숏의 I0470 행(수동 수집 CSV 또는 API JSON). 기본으로 업소 정보 열을 지운다.
  업소 정보가 필요한 곳은 평가셋 생성기의 같은 업소 이웃 조건뿐이고, 메모리에서만 쓴다(`keep_pii=True`)
- `scrub`: 위반 내용 · 처분 내용 원문 안의 그 업소 업소명 · 인허가번호 등을 `<업소명>` · `<인허가번호>`로 가린다.
  `read_rows`가 `keep_pii`와 상관없이 늘 적용하므로 그래프 속성과 평가 문항 텍스트가 같다
- `violation_text`: `Disposition.violation_text` 값이자 `m2_disposition` 문항 라벨. 원문 앞 `(YYYYMMDD)`만 떼고
  양끝 공백을 정리한다(그 밖의 날짜 · 차수는 남긴다. T1 묶음 키 정규화는 `eval/grouping.normalize_violation`)

수집 코드(`mfds/collect/foodsafety.py`)는 잠그지 않으므로, 잠금 대상인 읽기 · 라벨 함수만 이 모듈에 둔다.
"""

import csv
import gzip
import io
import json
import re
from pathlib import Path

SERVICE = "I0470"
# 업소명 · 주소 · 전화 · 대표자 · 인허가번호
PII = ("PRCSCITYPOINT_BSSHNM", "ADDR", "TELNO", "PRSDNT_NM", "LCNS_NO")
# 자유 텍스트 열(위반 내용 · 처분 내용). 원문에 그 업소의 업소명 · 인허가번호가 들어 있는 처분이 있다
# (스냅숏 20261029 기준 처분 14건: 위반 내용 14, 그중 1건은 처분 내용에도)
TEXT_FIELDS = ("VILTCN", "DSPSCN")
# (열, 가림 표시, 최소 길이(공백 · 하이픈 뺀 글자 수)). 짧은 값은 흔한 낱말과 겹쳐 가리지 않는다
_MASKS = (("PRCSCITYPOINT_BSSHNM", "<업소명>", 2), ("PRSDNT_NM", "<대표자>", 2), ("ADDR", "<주소>", 10),
          ("LCNS_NO", "<인허가번호>", 6), ("TELNO", "<전화>", 8))
_DATE_PREFIX = re.compile(r"^\s*\(\d{8}\)")
_MASK = re.compile("|".join(re.escape(mark) for _, mark, _ in _MASKS))


def scrub(row: dict[str, str]) -> dict[str, str]:
    """자유 텍스트 열에서 그 행 업소의 업소 정보 값을 가림 표시로 바꾼다(글자 사이 공백 · 하이픈 무시).
    다른 업소 이름은 알 수 없어 가리지 못한다(한계)."""
    pats = []
    for col, mark, min_len in _MASKS:
        chars = [c for c in row.get(col) or "" if not c.isspace() and c != "-"]
        if len(chars) >= min_len:
            pats.append((re.compile(r"[\s\-]*".join(map(re.escape, chars))), mark))
    out = dict(row)
    for f in TEXT_FIELDS:
        text = out.get(f)
        if text:
            for p, mark in pats:
                text = p.sub(mark, text)
            out[f] = text
    return out


def has_mask(text: str | None) -> bool:
    """가림 표시가 든 텍스트인가. 위반 문구를 질문에 넣는 문항(T1 · m2 처분)은 이런 처분을 원천으로 뽑지 않는다(PREREG 4-2)."""
    return bool(_MASK.search(text or ""))


def read_rows(snapshot: Path, keep_pii: bool = False) -> list[dict[str, str]]:
    """스냅숏의 I0470 행(수동 수집 CSV 또는 API JSON). 자유 텍스트 안의 업소 정보는 늘 가리고(`scrub`),
    기본으로 업소 정보 열도 지운다."""
    path = snapshot / "foodsafety" / f"{SERVICE}.csv.gz"
    if path.exists():
        with gzip.open(path, "rb") as f:
            rows = list(csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig")))
    else:
        pages = sorted((snapshot / "foodsafety" / SERVICE).glob("*.json"))
        if not pages:
            raise FileNotFoundError(f"{snapshot}에 {SERVICE}가 없다")
        rows = [r for p in pages for r in json.loads(p.read_bytes())[SERVICE].get("row", [])]
    rows = [scrub(r) for r in rows]
    if not keep_pii:
        rows = [{k: v for k, v in r.items() if k not in PII} for r in rows]
    return rows


def violation_text(raw: str | None) -> str:
    """위반 내용 원문 → 라벨. 앞 `(YYYYMMDD)` 날짜만 떼고 양끝 공백을 정리한다."""
    return _DATE_PREFIX.sub("", raw or "").strip()
