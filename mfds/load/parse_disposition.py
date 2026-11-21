"""행정처분 행 → Disposition · `BASED_ON`.

식품(`mfds.disposition.read_rows`, 업소 정보 없음):
- uid `disp:food:{DSPSDTLS_SEQ}`, `source='food'`. violation_text는 `mfds.disposition.violation_text`(생성기와 같은 함수)
- 업소명 · 주소 · 전화 · 대표자 · 인허가번호는 넣지 않는다. 행에 그 열이 남아 있으면 멈춘다
- BASED_ON: 근거 조항(LAWORD_CD_NM) 원문을 맥락 없이 refs로 읽어 조 · 항으로 올린다(호 → 항)

의약품 · 화장품(`mfds.disposition.read_drug_rows`, 업체 정보 · 인명 가림):
- uid `disp:drug:{ADM_DISPS_SEQ}`, `source='drug'`. violation_text는 `drug_violation_text`(생성기와 같은 함수)
- BASED_ON: 근거법령(BEF_APPLY_LAW)의 위반 부분 조문만(`basis_articles`, T7 정답과 같은 정의). 처분 조문 · 별표는 넣지 않는다
"""

from mfds.disposition import DRUG_PII, PII, basis_articles, drug_violation_text, violation_text
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


def parse_drug(rows: list[dict[str, str]], resolver: Resolver, known: set[str]) -> Graph:
    """의약품 · 화장품 처분. 행이 없으면(스냅숏에 drugdisp 폴더 없음) 빈 그래프와 `disp.drug 0` 표시."""
    g = Graph()
    if not rows:
        g.stats["disp.drug 처분 0(스냅숏에 없음)"] += 1
        return g
    seqs = set()
    for r in rows:
        if set(DRUG_PII) & set(r):
            raise ValueError("의약품 처분 행에 업체 정보 열이 있다(read_drug_rows 기본값으로 읽는다)")
        seq = r["ADM_DISPS_SEQ"]
        seqs.add(seq)
        uid = f"disp:drug:{seq}"
        law_ref = r.get("BEF_APPLY_LAW") or ""
        if not g.node(uid, {"seq": seq, "source": "drug", "industry": None,
                            "violation_text": drug_violation_text(r.get("EXPOSE_CONT")), "type": None,
                            "content": r.get("ADM_DISPS_NAME"), "decided_at": r.get("LAST_SETTLE_DATE"),
                            "law_ref_text": law_ref}):
            g.stats["disp.drug 처분 ID 중복"] += 1
            continue
        basis = basis_articles(law_ref, resolver.resolve)
        if basis["glued_violation"]:  # 항 · 호 뒤 조가 붙은 표기(오타 의심). 엣지는 그대로 두고 건수만 남긴다(T7 원천에서는 빠짐)
            g.stats["disp.drug 위반 쪽 붙은 조 표기(오타 의심)"] += 1
        n = 0
        for ref in basis["violation_refs"]:  # 식품처럼 항이 있으면 항으로(호 → 항)
            dst = article_or_paragraph(ref, known)
            if dst:
                g.edge("BASED_ON", uid, dst)
                n += 1
        if not n:
            g.stats["disp.drug 위반 조문 없음(처분 조문만 · 해석 실패)"] += 1
    nodes = sum(1 for u in g.nodes if u.startswith("disp:drug:"))
    if nodes != len(seqs):  # 적재 무결성: 노드 수 = 고유 처분 번호 수
        raise ValueError(f"의약품 처분 노드 {nodes} ≠ 고유 번호 {len(seqs)}")
    g.stats["disp.drug 처분 노드"] += nodes
    return g
