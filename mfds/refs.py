"""조문 참조 정규화기 (1주차 D4에 잠금). 그래프 엣지와 평가 정답 매칭이 같은 함수를 쓴다.

입력: 법령 · 고시 본문, 처분 근거 조항, 판례 참조조문 같은 문자열 + 맥락(지금 문서의 법령).
출력: 정규 ID(`art:` · `par:` · `item:` · `anx:`) 목록과 해석 실패 목록(사유 포함).

규칙
- 법령명: 식약처 87개 이름 + 약칭(가장 긴 이름 우선), 「」 유무 무관, 가운뎃점(· ㆍ ・) 통일
- `구 X법(… 개정되기 전의 것)`: 현행 법령으로 해석하고 historical=True
- 맥락 해석: 시행령 · 시행규칙 안의 `법`은 상위 법률, `영`은 시행령, `규칙`은 시행규칙. `이 법` · `이 영` · `이 규칙`은 지금 법령
- `같은 법` · `동법`은 바로 앞에 나온 법령, `같은 법 시행령`은 그 법령의 시행령. `같은 조` · `같은 항`은 바로 앞 조 · 항
- 법령명 없는 조문: 쉼표 · `및` · `또는` · `부터 … 까지`로 이어지면 앞 참조의 법령, 아니면 지금 문서의 법령
- 법령명 없는 항 · 호도 이어지면 앞 참조의 조(· 항)를 물려받는다(`제18조 제1항, 제5항`)
- `제N항부터 제M항까지`는 사이 번호를 모두 낸다
- 처분 표기 `제44조(제목) 1항10호`처럼 `제` 없는 항 · 호도 받는다. 목(가목 · (자)목)은 호까지만 본다
- 실패 사유: not_mfds(타 부처 법령), unknown_law(모르는 법령), no_context(맥락 없음), no_article(없는 조문)
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

DOTS = str.maketrans({"·": "ㆍ", "・": "ㆍ", "•": "ㆍ", "‧": "ㆍ"})



def norm_text(s: str) -> str:
    return re.sub(r"\s+", " ", s.translate(DOTS)).strip()


def _key(name: str) -> str:
    """법령명 비교 키: 공백을 뺀다."""
    return name.replace(" ", "")


def _num(main: str, branch: str | None) -> str:
    return f"{int(main)}-{int(branch)}" if branch else str(int(main))


@dataclass
class LawDict:
    """법령명 · 약칭 → 법령ID, 종류, 상위 법령."""
    names: dict[str, str]                      # 정규화한 이름 · 약칭 → law_id
    kind: dict[str, str]                       # law_id → 법률 | 대통령령 | 총리령 | 부령
    title: dict[str, str]                      # law_id → 정식 이름
    parent: dict[str, str] = field(default_factory=dict)   # law_id → 바로 위 법령 law_id
    other_names: set[str] = field(default_factory=set)     # 타 부처 법령명(정규화)

    def root(self, law_id: str) -> str:
        while law_id in self.parent:
            law_id = self.parent[law_id]
        return law_id

    def decree_of(self, law_id: str) -> str | None:
        """맥락 법령에서 `영`이 가리키는 시행령(자신이 시행령이면 자신)."""
        cur: str | None = law_id
        while cur:
            if self.kind.get(cur) == "대통령령":
                return cur
            cur = self.parent.get(cur)
        root = self.root(law_id)
        return self.names.get(norm_text(self.title[root] + " 시행령"))

    def child(self, law_id: str, suffix: str) -> str | None:
        """`같은 법 시행령` → 그 법령 이름 + ' 시행령'."""
        return self.names.get(norm_text(self.title[law_id] + " " + suffix))

    @classmethod
    def from_snapshot(cls, snapshot: Path) -> "LawDict":
        """수집 스냅숏(`mfds.collect.law`)에서: 식약처 법령 목록 · 법령체계도 전부 · 전체 법령 목록."""
        law = snapshot / "law"
        return cls.from_xml(sorted((law / "list_mfds").glob("*.xml")), sorted((law / "lsstmd").glob("*.xml")),
                            sorted((law / "list_all").glob("*.xml")))

    @classmethod
    def from_xml(cls, mfds_list: Path | list[Path], hierarchy: list[Path] = (),
                 other_lists: list[Path] = ()) -> "LawDict":
        names, kind, title, parent = {}, {}, {}, {}
        pages = [mfds_list] if isinstance(mfds_list, Path) else mfds_list
        for law in (l for p in pages for l in etree.parse(str(p)).getroot().iter("law")):
            lid, name = law.findtext("법령ID"), norm_text(law.findtext("법령명한글"))
            names[name] = lid
            title[lid] = name
            kind[lid] = law.findtext("법령구분명")
            abbr = norm_text(law.findtext("법령약칭명") or "")
            if abbr:
                names.setdefault(abbr, lid)
        for path in hierarchy:  # 법령체계도: 법률 > 시행령 > 시행규칙
            def walk(node, up: str | None) -> None:
                for c in node:
                    if c.tag in ("법률", "시행령", "시행규칙"):
                        lid = c.findtext("기본정보/법령ID")
                        if up and lid in title:
                            parent.setdefault(lid, up)
                        walk(c, lid if lid in title else up)
            walk(etree.parse(str(path)).getroot().find("상하위법"), None)
        for lid, name in title.items():  # 체계도가 없으면 이름으로: X 시행규칙 → X 시행령 → X
            if lid in parent:
                continue
            for suf, up in ((" 시행규칙", " 시행령"), (" 시행규칙", ""), (" 시행령", "")):
                if name.endswith(suf) and (name[: -len(suf)] + up) in names:
                    parent[lid] = names[name[: -len(suf)] + up]
                    break
        other = set()
        for path in other_lists:
            for law in etree.parse(str(path)).getroot().iter("law"):
                if law.findtext("소관부처명") != "식품의약품안전처":
                    other.add(norm_text(law.findtext("법령명한글")))
                    if (law.findtext("법령약칭명") or "").strip():
                        other.add(norm_text(law.findtext("법령약칭명")))
        return cls(names, kind, title, parent, other - set(names))


def article_set(bodies: list[Path]) -> set[str]:
    """법령 본문 XML → 조문 uid 집합(`Resolver(articles=…)`의 없는 조문 거르기용)."""
    out = set()
    for path in bodies:
        root = etree.parse(str(path)).getroot()
        lid = root.findtext("기본정보/법령ID")
        for u in root.iter("조문단위"):
            if u.findtext("조문여부") == "조문":
                out.add(f"art:{lid}:{_num(u.findtext('조문번호'), (u.findtext('조문가지번호') or '').strip() or None)}")
    return out


@dataclass
class Ref:
    uid: str
    article_uid: str
    law_id: str
    raw: str
    historical: bool = False


@dataclass
class Failure:
    raw: str
    reason: str


@dataclass
class Result:
    refs: list[Ref]
    failures: list[Failure]

    @property
    def uids(self) -> list[str]:
        return [r.uid for r in self.refs]

    @property
    def article_uids(self) -> list[str]:
        return list(dict.fromkeys(r.article_uid for r in self.refs))


LEVELS = {"art": 0, "par": 1, "item": 2}
_JOIN_GAP = re.compile(r"[\s,ㆍ]*(?:(?:및|또는|과|와|부터|까지)[\s,ㆍ]*)*")


class Resolver:
    def __init__(self, laws: LawDict, articles: set[str] | None = None) -> None:
        self.laws = laws
        self.articles = articles  # 있으면 없는 조문을 no_article로 거른다
        # 법령명은 띄어쓰기 없이도 받는다(옛 판례: `식품위생법시행령`, `마약류관리에관한법률`). 비교는 공백을 뺀 이름으로
        self.by_key = {_key(n): lid for n, lid in laws.names.items()}
        self.other_keys = {_key(n) for n in laws.other_names}
        law_alt = "|".join(r"\s?".join(map(re.escape, n.split(" "))) for n in sorted(laws.names, key=len, reverse=True))
        version = r"(?:\s?\([^()]*(?:개정|전의 것|시행|제정)[^()]*\))?"
        self.pattern = re.compile("|".join([
            rf"(?P<old>구\s)?「?(?P<law>{law_alt})」?{version}",
            r"「(?P<qlaw>[^」]{2,60})」",
            r"(?<![가-힣])(?P<rel>(?:같은|동)\s?법\s?시행령|(?:같은|동)\s?법\s?시행규칙|같은\s?법|동법|이\s?법|이\s?영|이\s?규칙)",
            r"(?<![가-힣])(?P<same>같은\s?조|같은\s?항|같은\s?호|이\s?조|동조|동항|동호)",
            r"(?<![가-힣])(?P<ctx>법|영|규칙)(?=\s?제\s?\d+\s?조)",
            # 두 글자 법령명(`형법` · `민법` · `상법` · `헌법`)도 받는다. 못 받으면 뒤 조문이 지금 법령 조문으로 읽힌다
            r"(?P<unk>[가-힣ㆍ]{1,40}(?:법|법률|령|규칙|규정))(?=\s?제\s?\d+\s?조)",
            r"제\s?(?P<art>\d+)\s?조(?:\s?의\s?(?P<artb>\d+))?(?:\s?\([^()]{1,60}\))?",
            r"(?P<parj>제\s?)?(?<![\d.])(?P<par>\d+)\s?항",
            r"(?P<itemj>제\s?)?(?<![\d.])(?P<item>\d+)\s?호(?:\s?의\s?(?P<itemb>\d+))?",
            r"별표\s?(?P<anx>\d+)(?:\s?의\s?(?P<anxb>\d+))?",
            r"(?P<mok>\(?[가-하]\)?\s?목|[가-하]\s?\d+\))",
        ]))

    def resolve(self, text: str, context_law: str | None = None, context_article: str | None = None) -> Result:
        """context_article: 지금 조문 번호('44', '7-2'). 있으면 조 없이 나온 `제1항` · `이 조 제2항`을 이 조로 본다."""
        text = norm_text(text)
        out: list[Ref | Failure] = []   # 낸 순서대로(조 → 항으로 구체화할 때 마지막 것을 바꾼다)
        cur = {"law": None, "art": None, "par": None, "item": None, "hist": False, "start": 0}
        pending: tuple[str | None, bool] | None = None   # 바로 앞 법령 토큰(법령, 구법 여부)
        last_law: str | None = None     # 마지막에 나온 법령(해석 실패 표시 포함) — `같은 법`이 가리킨다
        prev_level: str | None = None
        prev_end = 0
        stack: list[tuple[dict, str | None]] = []   # 괄호 안 참조가 바깥 맥락을 바꾸지 않게

        def emit(end: int) -> None:
            law, raw = cur["law"], text[cur["start"]:end]
            if law is None:
                out.append(Failure(raw, "no_context"))
                return
            if law.startswith("fail:"):
                out.append(Failure(raw, law[5:]))
                return
            a_uid = f"art:{law}:{cur['art']}"
            if self.articles is not None and a_uid not in self.articles:
                out.append(Failure(raw, "no_article"))
                return
            if cur["item"] is not None:
                uid = f"item:{law}:{cur['art']}:{cur['par'] or 0}:{cur['item']}"
            elif cur["par"] is not None:
                uid = f"par:{law}:{cur['art']}:{cur['par']}"
            else:
                uid = a_uid
            out.append(Ref(uid, a_uid, law, raw, cur["hist"]))

        for m in self.pattern.finditer(text):
            gap = text[prev_end:m.start()]
            for ch in re.findall(r"[()]", gap):
                if ch == "(":
                    stack.append((dict(cur), prev_level))
                elif stack:
                    saved, prev_level = stack.pop()
                    cur.update(saved)
            if ")" in gap:
                gap = gap[gap.rfind(")") + 1:]
            elif "(" in gap:
                prev_level = None  # 괄호 안 첫 참조는 바깥 참조에 이어 붙이지 않는다
            tight = not gap.strip()
            joined = _JOIN_GAP.fullmatch(gap) is not None
            ranged = "부터" in gap and joined
            kind = next(k for k in ("law", "qlaw", "rel", "same", "ctx", "unk", "art", "par", "item", "anx", "mok")
                        if m.group(k) is not None)
            if kind == "same":
                kind = {"조": "art", "항": "par", "호": "item"}[m.group("same")[-1]]
                same = True
            else:
                same = False

            if kind == "mok":
                prev_end = m.end()
                continue
            if kind in ("law", "qlaw", "rel", "ctx", "unk"):
                pending = (self._law_token(kind, m, context_law, last_law), bool(m.group("old")))
                cur["start"] = m.start()
                prev_level = None
                prev_end = m.end()
                continue
            if kind == "anx":
                owner = pending[0] if pending else (cur["law"] if joined and prev_level else context_law)
                pending = None
                if owner and not owner.startswith("fail:"):
                    uid = f"anx:{owner}:{_num(m.group('anx'), m.group('anxb'))}"
                    out.append(Ref(uid, uid, owner, m.group(0)))
                prev_level = None
                prev_end = m.end()
                continue

            if kind == "art":
                if same and m.group("same").startswith("이"):  # 이 조: 지금 조문
                    if not (context_article and context_law):
                        pending, prev_level, prev_end = None, None, m.end()
                        continue
                    new = context_article
                    cur.update(law=context_law, hist=False, start=m.start())
                elif same:  # 같은 조: 앞 참조의 법령 · 조 그대로
                    new = cur["art"]
                    if new is None:
                        prev_end = m.end()
                        continue
                    cur["start"] = m.start()
                else:
                    new = _num(m.group("art"), m.group("artb"))
                    if pending is not None and tight:
                        cur["law"], cur["hist"] = pending
                    elif not (joined and prev_level is not None):
                        cur["law"], cur["hist"] = context_law, False
                        cur["start"] = m.start()
                    else:
                        cur["start"] = m.start()
                pending = None
                if ranged and prev_level == "art" and "-" not in str(cur["art"]) and "-" not in new:
                    for n in range(int(cur["art"]) + 1, int(new)):
                        cur.update(art=str(n), par=None, item=None)
                        emit(m.end())
                cur.update(art=new, par=None, item=None)
                if cur["law"]:
                    last_law = cur["law"]
            else:  # par · item
                bare = not same and m.group(kind + "j") is None
                continues = cur["art"] is not None and prev_level is not None and (joined or tight)
                refine = continues and tight and LEVELS[prev_level] < LEVELS[kind]
                if not continues and not bare and context_article and context_law:
                    # 조 없이 나온 `제1항`: 지금 조문의 항
                    cur.update(law=context_law, art=context_article, par=None, item=None, hist=False)
                    prev_level = None
                elif not continues or (bare and not joined):
                    pending = None
                    prev_end = m.end()
                    continue
                if kind == "par":
                    new = cur["par"] if same else int(m.group("par"))
                else:
                    new = cur["item"] if same else _num(m.group("item"), m.group("itemb"))
                if refine:
                    out.pop()  # 제4조제1항: 방금 낸 조를 항으로 구체화
                else:
                    cur["start"] = m.start()
                if ranged and prev_level == kind and "-" not in str(cur[kind]) and "-" not in str(new):
                    for n in range(int(cur[kind]) + 1, int(new)):
                        cur[kind] = n if kind == "par" else str(n)
                        if kind == "par":
                            cur["item"] = None
                        emit(m.end())
                cur[kind] = new
                if kind == "par":
                    cur["item"] = None
            prev_level = kind
            emit(m.end())
            prev_end = m.end()

        refs, seen = [], set()
        for r in out:
            if isinstance(r, Ref) and r.uid not in seen:
                seen.add(r.uid)
                refs.append(r)
        return Result(refs, [f for f in out if isinstance(f, Failure)])

    def _law_token(self, kind: str, m: re.Match, context_law: str | None, last_law: str | None) -> str | None:
        laws = self.laws
        if kind == "law":
            return self.by_key[_key(m.group("law"))]
        if kind in ("qlaw", "unk"):
            key = _key(norm_text(m.group(kind)))
            if key in self.by_key:
                return self.by_key[key]
            return "fail:not_mfds" if key in self.other_keys else "fail:unknown_law"
        if kind == "rel":
            rel = m.group("rel").replace(" ", "")
            if rel in ("이법", "이영", "이규칙"):
                return context_law
            if last_law is None:
                return None
            if last_law.startswith("fail:"):  # 같은 법이 해석 못 한 법령(예: 「특허법」)을 가리킴
                return last_law
            for suf in ("시행령", "시행규칙"):
                if rel.endswith(suf):
                    return laws.child(last_law, suf) or "fail:unknown_law"
            return last_law
        if context_law is None:  # ctx: 법 · 영 · 규칙
            return None
        word = m.group("ctx")
        if word == "법":
            return laws.root(context_law)
        if word == "영":
            return laws.decree_of(context_law) or "fail:unknown_law"
        if laws.kind.get(context_law) in ("총리령", "부령"):
            return context_law
        return laws.child(laws.root(context_law), "시행규칙") or "fail:unknown_law"
