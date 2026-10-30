"""판례 · 법령해석례 · 행정심판례 본문 XML → Case · Interpretation · AdminAppeal, `REFERENCES {source:'field'}` · `CITES_CASE`.

- 본문 필드는 HTML 태그(`<br/>`)를 줄바꿈으로 바꾸고 양끝 공백만 정리한다
- Case.ref_text = 참조조문 필드를 태그 제거 · 공백 하나로 정리한 값(refs 입력과 같음, 평가셋 생성기의 T3 정답 입력과도 같음)
- REFERENCES: 참조조문을 맥락 없이 refs로 읽어 조 · 항으로 올린다(호 → 항). historical = refs의 구법 표시.
  해석례 · 심판에는 참조조문 필드가 없어 1주차에는 엣지가 없다(2주차 D2 source=extracted)
- CITES_CASE: 참조판례 필드의 사건번호(공백 제거)가 스냅숏 판례의 사건번호와 같으면 잇는다. 같은 사건번호 판례가 여럿이면
  인용 바로 앞의 법원명으로 좁히고, 그래도 여럿이면 법원 · 선고일이 모두 같을 때(같은 판결의 중복 문서)만 다 잇는다
"""

import re
from collections import defaultdict
from pathlib import Path

from lxml import etree

from mfds.load.graph import Graph, article_or_paragraph
from mfds.refs import Resolver

_TAG = re.compile(r"<[^>]+>")
_BR = re.compile(r"<br\s*/?>", re.I)
CASE_NO = re.compile(r"(?<![\d가-힣])(\d{2,4})\s?([가-힣]{1,3})\s?(\d+)(?!\d)")
COURT = re.compile(r"(대법원|[가-힣]+(?:법원|고법|지법|행법))\s*[\d.\s]*(?:자|선고)?\s*$")


def body_text(s: str | None) -> str:
    return _TAG.sub("", _BR.sub("\n", s or "")).strip()


def one_line(s: str | None) -> str:
    """eval/grouping.clean_html과 같은 정리: 태그를 공백으로, 공백을 하나로."""
    return re.sub(r"\s+", " ", _TAG.sub(" ", s or "")).strip()


def case_key(case_no: str) -> str:
    return re.sub(r"\s+", "", case_no or "")


def _fields(path: Path) -> dict[str, str | None]:
    return {c.tag: c.text for c in etree.parse(str(path)).getroot()}


def parse_prec(paths: list[Path], resolver: Resolver, known: set[str]) -> Graph:
    g = Graph()
    cases: dict[str, dict] = {}
    cited: dict[str, str] = {}
    for p in paths:
        f = _fields(p)
        pid = f.get("판례정보일련번호") or p.stem
        uid = f"case:{pid}"
        ref = one_line(f.get("참조조문"))
        props = {"prec_id": pid, "case_no": (f.get("사건번호") or "").strip(), "court": f.get("법원명"),
                 "date": f.get("선고일자"), "holding": body_text(f.get("판시사항")),
                 "summary": body_text(f.get("판결요지")), "ref_text": ref}
        g.node(uid, props)
        cases[uid] = props
        cited[uid] = one_line(f.get("참조판례"))
        if not ref:
            g.stats["prec.참조조문 없음"] += 1
            continue
        res = resolver.resolve(ref)
        for r in res.refs:
            dst = article_or_paragraph(r.uid, known)
            if dst:
                g.edge("REFERENCES", uid, dst, {"source": "field", "historical": r.historical})
            else:
                g.stats["prec.참조조문 별표 · 없는 항(엣지 아님)"] += 1
        for fail in res.failures:
            g.stats[f"prec.참조조문 해석 실패 {fail.reason}"] += 1
    by_no: dict[str, list[str]] = defaultdict(list)
    for uid, props in cases.items():
        by_no[case_key(props["case_no"])].append(uid)
    for uid, text in cited.items():
        for m in CASE_NO.finditer(text):
            cands = by_no.get("".join(m.groups()), [])
            if not cands:
                g.stats["prec.참조판례 스냅숏 밖"] += 1
                continue
            if len(cands) > 1:
                court = COURT.search(text[max(0, m.start() - 40):m.start()])
                if court:
                    cands = [c for c in cands if cases[c]["court"] == court.group(1)] or cands
            if len({(cases[c]["court"], cases[c]["date"]) for c in cands}) > 1:
                g.stats["prec.참조판례 사건번호 모호(버림)"] += 1
                continue
            for c in cands:
                if c != uid:
                    g.edge("CITES_CASE", uid, c)
    return g


def parse_expc(paths: list[Path]) -> Graph:
    g = Graph()
    for p in paths:
        f = _fields(p)
        eid = f.get("법령해석례일련번호") or p.stem
        g.node(f"expc:{eid}", {"expc_id": eid, "title": body_text(f.get("안건명")), "date": f.get("해석일자"),
                               "question": body_text(f.get("질의요지")), "answer": body_text(f.get("회답")),
                               "reason": body_text(f.get("이유"))})
    return g


def parse_decc(paths: list[Path]) -> Graph:
    g = Graph()
    for p in paths:
        f = _fields(p)
        did = f.get("행정심판례일련번호") or p.stem
        g.node(f"decc:{did}", {"decc_id": did, "title": body_text(f.get("사건명")), "date": f.get("의결일자"),
                               "order": body_text(f.get("주문")), "claim": body_text(f.get("청구취지")),
                               "reason": body_text(f.get("이유"))})
    return g
