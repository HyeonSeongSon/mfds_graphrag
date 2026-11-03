"""평가셋 생성기용 원자료 읽기 (1주차 D4에 잠금). 적재기(`mfds/load/`) 파서와 따로 구현한다(PREREG 원칙 정답 독립성).

같은 코드를 쓰면 D4 템플릿 대조와 그래프 천장 검사(정답 Cypher vs 원자료)가 늘 일치해 적재 오류를 잡지 못한다.
uid는 `schema/schema.yaml` 규칙대로 여기서 따로 만든다. 예외(잠금 대상 공유 모듈): `mfds.refs`(법령명 정규화),
`mfds.ingredients`(성분 정규화), `mfds.disposition`(처분 행 읽기 · 업소 정보 가림), `mfds.collect.dur`(별표 표 파서).

- `laws`: 식약처 법령 목록 → 법령ID · 이름 · 약칭 · 종류. `subordinate`: 법령체계도 아래 → 위(식약처 법령끼리).
  `other_laws`: 타 부처 법률 이름(거절 세트)
- `articles`: 법령 본문 → 조문(조 · 항 · 호 단위 본문, 삭제 여부). `admin_articles`: 고시 조문
- `delegations`: 3단비교 → (법률 조문, 하위 법령 조문). 대상 법령이 식약처 밖이거나 조문이 본문에 없으면 버린다
- `dur_rules`: 병용금기 고시 별표 1~3 → DurRule(uid · 종류 · 성분)
- `precedents`: 법원 판례 필드
"""

import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

from mfds.collect import dur
from mfds.ingredients import Ingredient, normalize
from mfds.refs import norm_text

ARTICLE_HEAD = re.compile(r"^\s*제\s*\d+\s*조(?:\s*의\s*\d+)?\s*(?:\([^()]*\))?")
ADMIN_ARTICLE = re.compile(r"^\s*제\s*(\d+)\s*조(?:\s*의\s*(\d+))?")
DELETED = re.compile(r"^\s*제\s*\d+\s*조(?:\s*의\s*\d+)?\s*(?:\([^()]*\))?\s*삭제")
DUR_RULE_NAME = "의약품 병용금기 성분 등의 지정에 관한 규정"
DUR_TYPES = {"1": "pair", "2": "age", "3": "preg"}


def _xml(d: Path) -> list[Path]:
    return sorted(d.glob("*.xml"))


def art_no(main: str | int, branch: str | int | None) -> str:
    """조 번호 문자열(`schema.yaml` uid): 가지가 0이 아니면 `7-2`."""
    b = int(str(branch or 0).strip() or 0)
    return f"{int(main)}-{b}" if b else str(int(main))


@dataclass
class Law:
    law_id: str
    name: str
    kind: str  # 법률 | 대통령령 | 총리령 | 부령
    abbr: str | None


def laws(S: Path) -> dict[str, Law]:
    out = {}
    for p in _xml(S / "law" / "list_mfds"):
        for e in etree.parse(str(p)).getroot().iter("law"):
            lid = e.findtext("법령ID")
            out[lid] = Law(lid, norm_text(e.findtext("법령명한글")), e.findtext("법령구분명"),
                           norm_text(e.findtext("법령약칭명") or "") or None)
    return out


def other_laws(S: Path) -> list[str]:
    """전체 법령 목록 → 소관부처가 식약처가 아닌 법률 이름(거절 세트 `other_ministry_law`, 이름 순)."""
    out = set()
    for p in _xml(S / "law" / "list_all"):
        for e in etree.parse(str(p)).getroot().iter("law"):
            if e.findtext("소관부처명") != "식품의약품안전처" and e.findtext("법령구분명") == "법률":
                out.add(norm_text(e.findtext("법령명한글")))
    return sorted(out)


def subordinate(S: Path, law_ids: set[str]) -> dict[str, set[str]]:
    """법령체계도 → 법령ID → 바로 위 식약처 법령ID 집합(중간 법령이 식약처 밖이면 그 위 식약처 법령)."""
    up: dict[str, set[str]] = defaultdict(set)

    def walk(node, parent: str | None) -> None:
        for c in node:
            if c.tag in ("법률", "시행령", "시행규칙"):
                lid = c.findtext("기본정보/법령ID")
                if parent and lid in law_ids and lid != parent:
                    up[lid].add(parent)
                walk(c, lid if lid in law_ids else parent)

    for p in _xml(S / "law" / "lsstmd"):
        top = etree.parse(str(p)).getroot().find("상하위법")
        if top is not None:
            walk(top, None)
    return dict(up)


@dataclass
class Article:
    uid: str
    law_id: str
    no: str  # `23` · `23-2`
    title: str | None
    deleted: bool
    units: list[tuple[str, str]] = field(default_factory=list)  # (조 · 항 · 호 uid, 그 단위 자기 본문), 문서 순서

    @property
    def text(self) -> str:
        return " ".join(norm_text(t) for _, t in self.units if norm_text(t))


def _paragraph_no(s: str) -> int:
    digits = re.sub(r"\D", "", s)
    if digits:
        return int(digits)
    import unicodedata
    return int(unicodedata.numeric(s.strip()[0]))


def _join(e, tags: tuple[str, ...]) -> str:
    return "\n".join(t for t in ((x.text or "").strip() for x in e.iter(*tags)) if t)


def articles(S: Path) -> dict[str, Article]:
    """법령 본문 → 조문(조문여부 `조문`만). 단위 본문은 조는 `제N조(제목)` 머리를 뗀 조문내용, 항은 항내용, 호는 호 + 목."""
    out = {}
    for p in _xml(S / "law" / "body"):
        root = etree.parse(str(p)).getroot()
        lid = root.findtext("기본정보/법령ID")
        for u in root.iter("조문단위"):
            if u.findtext("조문여부") != "조문":
                continue
            no = art_no(u.findtext("조문번호"), u.findtext("조문가지번호"))
            uid = f"art:{lid}:{no}"
            if uid in out:
                continue
            head = u.findtext("조문내용") or ""
            a = Article(uid, lid, no, (u.findtext("조문제목") or "").strip() or None, bool(DELETED.match(head)),
                        [(uid, ARTICLE_HEAD.sub("", head, count=1))])
            for h in u.findall("항"):
                hn = (h.findtext("항번호") or "").strip()
                pno = _paragraph_no(hn) if hn else 0
                if pno:
                    a.units.append((f"par:{lid}:{no}:{pno}", h.findtext("항내용") or ""))
                for i in h.findall("호"):
                    m = re.match(r"\s*(\d+)", i.findtext("호번호") or "")
                    if m:
                        a.units.append((f"item:{lid}:{no}:{pno}:{art_no(m.group(1), i.findtext('호가지번호'))}",
                                        _join(i, ("호내용", "목내용"))))
            out[uid] = a
    return out


@dataclass
class AdminArticle:
    uid: str
    admrul_id: str
    rule_name: str
    text: str  # `제N조(제목)` 머리를 뗀 본문


def admin_articles(S: Path) -> dict[str, AdminArticle]:
    """고시 본문 → 조문(`조문내용` 하나가 조 하나, `제N조`로 시작하지 않으면 조문 아님, uid가 겹치면 앞의 것)."""
    out = {}
    for p in _xml(S / "admrul" / "body"):
        root = etree.parse(str(p)).getroot()
        aid = root.findtext("행정규칙기본정보/행정규칙ID")
        name = root.findtext("행정규칙기본정보/행정규칙명") or ""
        for e in root.findall("조문내용"):
            text = (e.text or "").strip()
            m = ADMIN_ARTICLE.match(text)
            if m:
                uid = f"admart:{aid}:{art_no(m.group(1), m.group(2))}"
                out.setdefault(uid, AdminArticle(uid, aid, name, ARTICLE_HEAD.sub("", text, count=1)))
    return out


def delegations(S: Path, law_map: dict[str, Law], article_uids: set[str]) -> dict[str, list[str]]:
    """3단비교 → 법률 조문 uid → 위임받은 하위 법령 조문 uid(순서 유지, 중복 없음)."""
    by_name = {l.name: lid for lid, l in law_map.items()}
    out: dict[str, list[str]] = defaultdict(list)
    for p in _xml(S / "law" / "thdcmp"):
        root = etree.parse(str(p)).getroot()
        lid = root.findtext("기본정보/법령ID")
        for e in root.iter("법률조문"):
            src = f"art:{lid}:{art_no(e.findtext('조번호'), e.findtext('조가지번호'))}"
            for t in e:
                if t.tag not in ("시행령조문", "시행규칙조문"):
                    continue
                target = by_name.get(norm_text(t.findtext("법령명") or ""))
                dst = target and f"art:{target}:{art_no(t.findtext('조번호'), t.findtext('조가지번호'))}"
                if dst and dst in article_uids and src in article_uids and dst not in out[src]:
                    out[src].append(dst)
    return dict(out)


@dataclass
class DurRule:
    uid: str
    type: str  # pair | age | preg
    ingredients: tuple[Ingredient, ...]  # INVOLVES 대상(복합제는 복합제 하나)


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def dur_rules(S: Path) -> list[DurRule]:
    """병용금기 고시 별표 1~3 → DurRule. uid는 `schema.yaml`: `dur:pair:{정렬 앞}:{뒤}` · `dur:age:{성분}:{연령}` ·
    `dur:preg:{성분}`, 같은 uid가 여러 행이면 행 내용 sha256(연번 뺀 칸을 탭으로 이음) 순서로 2번째부터 `#2`."""
    path = next((p for p in _xml(S / "admrul" / "body")
                 if etree.parse(str(p)).getroot().findtext("행정규칙기본정보/행정규칙명") == DUR_RULE_NAME), None)
    if path is None:
        raise SystemExit("병용금기 고시가 스냅숏에 없다")
    rows = []  # (기본 uid, 행 해시, 연번, 종류, 성분)
    for annex_no, annex_rows in sorted(dur.annexes_from_xml(path).items()):
        cols = [c for c in dur.ANNEX_COLUMNS[annex_no] if c != "seq"]
        for r in annex_rows:
            h = hashlib.sha256("\t".join(str(r[c]) for c in cols).encode("utf-8")).hexdigest()
            if annex_no == "1":
                a, b = normalize(r["ingredient_a"]), normalize(r["ingredient_b"])
                if a is None or b is None:
                    continue
                ings = tuple(sorted((a, b), key=lambda i: i.name))
                base = f"dur:pair:{ings[0].name}:{ings[1].name}"
            else:
                i = normalize(r["ingredient"])
                if i is None:
                    continue
                ings = (i,)
                base = (f"dur:age:{i.name}:{_clean(r['age']).replace(':', '_')}" if annex_no == "2"
                        else f"dur:preg:{i.name}")
            rows.append((base, h, r["seq"], DUR_TYPES[annex_no], ings))
    rank: dict[int, int] = {}
    by_base: dict[str, list[int]] = defaultdict(list)
    for k, row in enumerate(rows):
        by_base[row[0]].append(k)
    for idx in by_base.values():
        for n, k in enumerate(sorted(idx, key=lambda k: (rows[k][1], rows[k][2]))):
            rank[k] = n
    return [DurRule(base if not rank[k] else f"{base}#{rank[k] + 1}", t, ings)
            for k, (base, _, _, t, ings) in enumerate(rows)]


def precedents(S: Path) -> dict[str, dict[str, str | None]]:
    """법원 판례 본문 → 판례정보일련번호 → 필드(사건번호 · 법원명 · 판결요지 · 판시사항 · 참조조문 등 원문)."""
    out = {}
    for p in _xml(S / "decisions" / "prec" / "body"):
        f = {c.tag: c.text for c in etree.parse(str(p)).getroot()}
        out[f.get("판례정보일련번호") or p.stem] = f
    return out
