"""병용금기 고시(「의약품 병용금기 성분 등의 지정에 관한 규정」) 별표 1~3 파서.

별표 본문은 상자 그림 문자 표다. 행 경계는 `├`(마지막은 `└`), 칸은 `│`, 한 칸이 여러 줄이면 이어 붙인다.
연번이 있는 줄에서 새 행이 시작한다. 연번은 1부터 빠짐없이 이어져야 한다(누락 0이 파싱 검증 기준).
"""

import re
from pathlib import Path

from lxml import etree

ANNEX_COLUMNS = {
    "1": ("seq", "ingredient_a", "ingredient_b", "note"),
    "2": ("seq", "ingredient", "age", "dosage_form"),
    "3": ("seq", "ingredient", "grade", "note"),
}
_HANGUL = re.compile(r"[가-힣]")


def _cells(line: str) -> list[str] | None:
    s = line.strip()
    if not (s.startswith("│") and s.endswith("│")):
        return None
    return [c.replace("　", " ") for c in s[1:-1].split("│")]


def _join(prev_raw: str, acc: str, nxt: str) -> str:
    """여러 줄 칸 잇기. 앞 줄이 칸을 꽉 채웠으면(뒤 공백 없음) 단어 중간 줄바꿈이라 붙이고, 아니면 공백으로 잇는다.
    한글은 폭이 두 칸이라 꽉 차도 뒤 공백이 남으므로, 경계 양쪽이 한글이면 붙인다."""
    if not nxt:
        return acc
    if not acc:
        return nxt
    if prev_raw == prev_raw.rstrip() or (_HANGUL.match(acc[-1]) and _HANGUL.match(nxt[0])):
        return acc + nxt
    return acc + " " + nxt


def parse_annex(text: str, annex_no: str) -> list[dict[str, str]]:
    """별표 본문 텍스트 → 행 dict 목록."""
    cols = ANNEX_COLUMNS[annex_no]
    rows: list[list[str]] = []
    last_raw: list[str] = []
    for line in text.splitlines():
        raw = _cells(line)
        if raw is None or len(raw) != len(cols):
            continue
        cells = [c.strip() for c in raw]
        if cells[0] == "연번":
            continue
        if cells[0]:
            rows.append(cells)
        elif rows:
            rows[-1] = [_join(p, a, b) for p, a, b in zip(last_raw, rows[-1], cells)]
        last_raw = raw
    out = [dict(zip(cols, r)) for r in rows]
    for r in out:
        r["seq"] = int(r["seq"])
    return out


def check_seq(rows: list[dict]) -> list[int]:
    """연번 누락 · 중복 목록(비어 있으면 통과)."""
    seqs = [r["seq"] for r in rows]
    seen = set(seqs)
    missing = [s for s in range(1, max(seqs, default=0) + 1) if s not in seen]
    return missing + sorted({s for s in seqs if seqs.count(s) > 1})


def annexes_from_xml(path: Path) -> dict[str, list[dict[str, str]]]:
    """행정규칙 본문 XML에서 별표 1~3을 파싱한다."""
    root = etree.parse(str(path)).getroot()
    out = {}
    for u in root.find("별표").findall("별표단위"):
        no = str(int(u.findtext("별표번호")))
        if no in ANNEX_COLUMNS and u.findtext("별표구분") == "별표":
            out[no] = parse_annex(u.findtext("별표내용") or "", no)
    return out
