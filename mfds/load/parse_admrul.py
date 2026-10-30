"""행정규칙 본문 XML → AdminRule · AdminArticle · Annex(owner `adm{행정규칙ID}`)와 `BASED_ON_LAW`.

- AdminArticle: `조문내용` 하나가 조 하나다. `제N조(의M)(제목)`로 시작하지 않는 단위(장 · 절 제목, 조문형식이 아닌 고시 본문)는
  노드로 만들지 않는다. 항 구조는 나누지 않는다(고시 본문은 항 요소가 없다)
- Annex: 별표구분 `별표`만(별지 · 서식 · 별도 · 붙임은 노드 아님)
- BASED_ON_LAW: 고시의 근거 조항은 제1조(목적 · 근거)에 적는다 → 제1조 본문을 refs로 읽어 조 · 항으로 올린다(호 → 항)

refs로 읽을 본문(`cite_units`)은 조문 본문에서 `제N조(제목)` 머리를 뗀 것. 맥락 법령이 없어 고시 안의 자기 조문
참조(`제3조에 따라`)는 refs에서 no_context로 떨어진다(행정규칙 → 행정규칙 엣지는 스키마 밖).
"""

import re
from pathlib import Path

from lxml import etree

from mfds.load.graph import Graph, article_or_paragraph
from mfds.load.parse_law import ARTICLE_HEAD, num
from mfds.refs import Resolver

ARTICLE = re.compile(r"^\s*제\s*(\d+)\s*조(?:\s*의\s*(\d+))?\s*(?:\(([^()]*)\))?")


def parse_body(path: Path) -> Graph:
    g = Graph()
    root = etree.parse(str(path)).getroot()
    info = root.find("행정규칙기본정보")
    aid = info.findtext("행정규칙ID")
    adm = f"adm:{aid}"
    g.node(adm, {"admrul_id": aid, "serial": info.findtext("행정규칙일련번호"), "name": info.findtext("행정규칙명"),
                 "kind": info.findtext("행정규칙종류"), "ministry": info.findtext("소관부처명"),
                 "issued": info.findtext("발령일자"), "effective": info.findtext("시행일자")})
    for e in root.findall("조문내용"):
        text = (e.text or "").strip()
        m = ARTICLE.match(text)
        if not m:
            g.stats["admrul.조문 아닌 단위(노드 아님)"] += 1
            continue
        n, b = int(m.group(1)), int(m.group(2) or 0)
        an = num(str(n), str(b))
        uid = f"admart:{aid}:{an}"
        if not g.node(uid, {"number": n, "branch": b, "title": m.group(3), "text": text}):
            g.stats["admrul.조문 uid 중복(뒤의 것 버림)"] += 1
            continue
        g.edge("HAS_ARTICLE", adm, uid)
        g.cite_units.append(([(uid, ARTICLE_HEAD.sub("", text, count=1))], None, None))
    for a in root.iter("별표단위"):
        kind = a.findtext("별표구분")
        if kind != "별표":
            g.stats[f"admrul.별표단위 {kind}(노드 아님)"] += 1
            continue
        no = num(a.findtext("별표번호"), a.findtext("별표가지번호"))
        uid = f"anx:adm{aid}:{no}"
        body = (a.findtext("별표내용") or "").strip()
        if g.node(uid, {"no": no, "title": (a.findtext("별표제목") or "").strip() or None, "text": body,
                        "owner": f"adm{aid}", "has_text": bool(body)}):
            g.edge("HAS_ANNEX", adm, uid)
        else:
            g.stats["admrul.별표 uid 중복"] += 1
    return g


def based_on_law(g: Graph, resolver: Resolver, known: set[str]) -> Graph:
    """행정규칙 그래프(여러 개 합친 것도 됨)의 제1조 → BASED_ON_LAW(AdminRule → Article · Paragraph)."""
    out = Graph()
    for uid, (label, props) in g.nodes.items():
        if label != "AdminArticle" or (props["number"], props["branch"]) != (1, 0):
            continue
        adm = "adm:" + uid.split(":")[1]
        n = 0
        for ref in resolver.resolve(ARTICLE_HEAD.sub("", props["text"], count=1)).refs:
            if ref.historical:  # 엣지에 historical 속성이 없어 구법 참조는 넣지 않는다
                out.stats["admrul.근거 조항 구법 참조(버림)"] += 1
                continue
            dst = article_or_paragraph(ref.uid, known)
            if dst:
                out.edge("BASED_ON_LAW", adm, dst)
                n += 1
        out.stats["admrul.근거 조항 있는 행정규칙" if n else "admrul.제1조에 근거 조항 없음"] += 1
    return out
