"""식품 행정처분 행(`mfds.disposition.read_rows`, 업소 정보 없음) → Disposition · `BASED_ON`.

- uid `disp:food:{DSPSDTLS_SEQ}`. violation_text는 `mfds.disposition.violation_text`(평가셋 생성기와 같은 함수)
- 업소명 · 주소 · 전화 · 대표자 · 인허가번호는 넣지 않는다. 행에 그 열이 남아 있으면 멈춘다
- BASED_ON: 근거 조항(LAWORD_CD_NM) 원문을 맥락 없이 refs로 읽어 조 · 항으로 올린다(호 → 항)
"""

from mfds.disposition import PII, violation_text
from mfds.load.graph import Graph, article_or_paragraph
from mfds.refs import Resolver


def parse(rows: list[dict[str, str]], resolver: Resolver, known: set[str]) -> Graph:
    g = Graph()
    for r in rows:
        if set(PII) & set(r):
            raise ValueError("처분 행에 업소 정보 열이 있다(read_rows 기본값으로 읽는다)")
        seq = r["DSPSDTLS_SEQ"]
        uid = f"disp:food:{seq}"
        law_ref = r.get("LAWORD_CD_NM") or ""
        if not g.node(uid, {"seq": seq, "source": "food", "industry": r.get("INDUTY_CD_NM"),
                            "violation_text": violation_text(r.get("VILTCN")), "type": r.get("DSPS_TYPECD_NM"),
                            "content": r.get("DSPSCN"), "decided_at": r.get("DSPS_DCSNDT"),
                            "law_ref_text": law_ref}):
            g.stats["disp.처분 ID 중복"] += 1
            continue
        res = resolver.resolve(law_ref)
        n = 0
        for ref in res.refs:
            dst = article_or_paragraph(ref.uid, known)
            if dst:
                g.edge("BASED_ON", uid, dst)
                n += 1
        if not n:
            g.stats["disp.근거 조항 해석 없음"] += 1
    return g
