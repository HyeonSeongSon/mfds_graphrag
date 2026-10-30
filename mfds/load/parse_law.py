"""법령 본문 · 법령체계도 · 위임조문 3단비교 XML → Law · Article · Paragraph · Item · Annex 노드와 구조 엣지.

- Article: 조문여부 `조문`만(전문 = 장 · 절 제목은 노드 아님). number · branch 정수, text는 조 · 항 · 호 · 목 본문 전체,
  effective_from = 조문시행일자(effective_to는 현행이라 비움)
- Paragraph: 항번호 ①→1(`<16>` 같은 표기도 숫자로). 항번호 없는 항(호만 있는 조)은 노드를 만들지 않고 호를 조에 단다(항 자리 0)
- Item: 호번호 + 호가지번호(`1-2`), text는 호 본문 + 목 본문
- Annex: 별표구분 `별표`만(서식 · 별도는 번호가 별표와 겹쳐 노드로 만들지 않는다). 본문이 비면 has_text=false
- SUBORDINATE_OF: 법령체계도 법률 > 시행령 > 시행규칙에서 아래 → 위. 둘 다 식약처 법령일 때만
  (중간 법령이 식약처 소관이 아니면 그 위 식약처 법령에 잇는다, `refs.LawDict`와 같은 걷기)
- DELEGATES_TO: 3단비교 법률조문 → 시행령조문 · 시행규칙조문. 대상 법령명이 식약처 목록에 없으면 버린다

refs로 읽을 본문(`cite_units`)은 조문마다 하나: 단위별 자기 본문(조는 조문내용에서 `제N조(제목)` 머리를 뗀 것,
항은 항내용, 호는 호 + 목)을 문서 순서로 낸다. `cites.py`가 이어 붙여 한 번에 읽고 참조를 난 단위에 돌려준다.
"""

import re
import unicodedata
from pathlib import Path

from lxml import etree

from mfds.load.graph import Graph
from mfds.refs import norm_text

ARTICLE_HEAD = re.compile(r"^\s*제\s*\d+\s*조(?:\s*의\s*\d+)?\s*(?:\([^()]*\))?")


def num(main: str, branch: str | None) -> str:
    """조 · 별표 번호 문자열: 가지가 0이 아니면 `7-2`."""
    b = int(branch or 0)
    return f"{int(main)}-{b}" if b else str(int(main))


def paragraph_no(s: str) -> int:
    s = s.strip()
    digits = re.sub(r"\D", "", s)
    if digits:
        return int(digits)
    return int(unicodedata.numeric(s[0]))


def text_of(e, tags: tuple[str, ...]) -> str:
    return "\n".join(t for t in ((x.text or "").strip() for x in e.iter(*tags)) if t)


def law_meta(list_paths: list[Path]) -> dict[str, dict[str, str]]:
    """식약처 법령 목록 → 법령ID → {mst, name, kind, abbr}."""
    out = {}
    for p in list_paths:
        for law in etree.parse(str(p)).getroot().iter("law"):
            out[law.findtext("법령ID")] = {"mst": law.findtext("법령일련번호"), "name": norm_text(law.findtext("법령명한글")),
                                         "kind": law.findtext("법령구분명")}
    return out


def parse_body(path: Path, meta: dict[str, dict[str, str]] | None = None) -> Graph:
    g = Graph()
    root = etree.parse(str(path)).getroot()
    info = root.find("기본정보")
    lid = info.findtext("법령ID")
    law = f"law:{lid}"
    g.node(law, {"law_id": lid, "mst": (meta or {}).get(lid, {}).get("mst"),
                 "name": (info.findtext("법령명_한글") or "").strip(),
                 "abbr": (info.findtext("법령명약칭") or "").strip() or None,
                 "kind": info.findtext("법종구분"), "ministry": info.findtext("소관부처"),
                 "effective": info.findtext("시행일자")})
    for u in root.iter("조문단위"):
        if u.findtext("조문여부") != "조문":
            g.stats["law.전문 단위(노드 아님)"] += 1
            continue
        n, b = int(u.findtext("조문번호")), int((u.findtext("조문가지번호") or "").strip() or 0)
        an = num(str(n), str(b))
        art = f"art:{lid}:{an}"
        if not g.node(art, {"number": n, "branch": b, "title": (u.findtext("조문제목") or "").strip() or None,
                            "text": text_of(u, ("조문내용", "항내용", "호내용", "목내용")),
                            "effective_from": u.findtext("조문시행일자")}):
            g.stats["law.조문 uid 중복"] += 1
            continue
        g.edge("HAS_ARTICLE", law, art)
        segments = [(art, ARTICLE_HEAD.sub("", u.findtext("조문내용") or "", count=1))]
        g.cite_units.append((segments, lid, an))
        for h in u.findall("항"):
            hn = (h.findtext("항번호") or "").strip()
            pno = paragraph_no(hn) if hn else 0
            parent = art
            if pno:
                par = f"par:{lid}:{an}:{pno}"
                if g.node(par, {"no": pno, "text": text_of(h, ("항내용", "호내용", "목내용"))}):
                    g.edge("HAS_PARAGRAPH", art, par)
                    segments.append((par, h.findtext("항내용") or ""))
                else:
                    g.stats["law.항 uid 중복"] += 1
                parent = par
            for i in h.findall("호"):
                m = re.match(r"\s*(\d+)", i.findtext("호번호") or "")
                if not m:
                    g.stats["law.호번호 없음(버림)"] += 1
                    continue
                ino = num(m.group(1), (i.findtext("호가지번호") or "").strip())
                item = f"item:{lid}:{an}:{pno}:{ino}"
                body = text_of(i, ("호내용", "목내용"))
                if not g.node(item, {"no": ino, "text": body}):
                    g.stats["law.호 uid 중복"] += 1
                    continue
                g.edge("HAS_ITEM", parent, item)
                segments.append((item, body))
    for a in root.iter("별표단위"):
        kind = a.findtext("별표구분")
        if kind != "별표":
            g.stats[f"law.별표단위 {kind}(노드 아님)"] += 1
            continue
        no = num(a.findtext("별표번호"), a.findtext("별표가지번호"))
        anx = f"anx:{lid}:{no}"
        body = (a.findtext("별표내용") or "").strip()
        if g.node(anx, {"no": no, "title": (a.findtext("별표제목") or "").strip() or None, "text": body,
                        "owner": lid, "has_text": bool(body)}):
            g.edge("HAS_ANNEX", law, anx)
        else:
            g.stats["law.별표 uid 중복"] += 1
    return g


def parse_hierarchy(paths: list[Path], law_ids: set[str]) -> Graph:
    """법령체계도 → SUBORDINATE_OF(아래 → 위). 같은 연결이 여러 체계도에 나와도 하나."""
    g = Graph()

    def walk(node, up: str | None) -> None:
        for c in node:
            if c.tag in ("법률", "시행령", "시행규칙"):
                lid = c.findtext("기본정보/법령ID")
                if up and lid in law_ids and lid != up:
                    g.edge("SUBORDINATE_OF", f"law:{lid}", f"law:{up}")
                walk(c, lid if lid in law_ids else up)

    for p in paths:
        top = etree.parse(str(p)).getroot().find("상하위법")
        if top is not None:
            walk(top, None)
    return g


def parse_delegation(path: Path, name_to_id: dict[str, str]) -> Graph:
    """3단비교(knd=2) → DELEGATES_TO(법률 조문 → 시행령 · 시행규칙 조문). 대상 법령이 식약처 목록에 없으면 버린다."""
    g = Graph()
    root = etree.parse(str(path)).getroot()
    lid = root.findtext("기본정보/법령ID")
    for e in root.iter("법률조문"):
        src = f"art:{lid}:{num(e.findtext('조번호'), e.findtext('조가지번호'))}"
        for t in e:
            if t.tag not in ("시행령조문", "시행규칙조문"):
                continue
            target = name_to_id.get(norm_text(t.findtext("법령명") or ""))
            if target is None:
                g.stats["law.위임 대상 식약처 밖(버림)"] += 1
                continue
            g.edge("DELEGATES_TO", src, f"art:{target}:{num(t.findtext('조번호'), t.findtext('조가지번호'))}")
    return g
