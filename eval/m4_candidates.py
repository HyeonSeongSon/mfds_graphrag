"""M4(인용 역추적) 정답 후보: 대상 조문을 인용할 수 있는 표현을 문자열로 넓게 찾는다(1주차 D4에 잠금, PREREG 4-2 M4 정답 후보).

`mfds/refs.py`를 쓰지 않는다. 이 후보를 사용자가 걸러 정답을 만들고, 그 정답으로 refs의 재현율을 재기 때문이다.
`mfds/load/`도 쓰지 않는다(정답 독립성). 입력은 `eval.sources`가 읽은 법령 · 조문 · 고시 조문을 호출자가 넘긴다.

- 대상: 조문 X = (법령 L, 조 N, 가지 B)
- 검색 범위: L 본문, L의 하위 법령(체계도) 본문, 고시 조문 전체(정답 템플릿 `m4_citing_articles`와 같은 범위)
- 표현: `제N조`(B가 있으면 `제N조의B`). 뒤에 `의 숫자`가 오면 다른 조문이라 버린다(`제5조의2`는 제5조가 아님).
  N이 안쪽에 드는 `제A조부터 제B조까지`도 받는다. 법령 이름을 찾을 때 바로 앞 조문 나열(`제11조, 제12조 및`)은 건너뛴다
  - L 본문: 대상 조문 자신과, 바로 앞이 다른 법령 이름(`「…」`)이거나 `같은 법`이 다른 법령을 가리키는 표현을 뺀 전부
  - 하위 법령 · 고시: 바로 앞 40자 안에 L의 이름 · 약칭이 있거나, 바로 앞이 `같은 법`이고 그 앞 마지막 `「…」`가 L이거나,
    40자 안에 낱말 `법`이 있고 그 문서에서 `법`이 L을 가리킬 때(하위 법령은 늘, 고시는 `「L」(이하 "법"…)` 정의가 있을 때)
- 결과: (출처 조문 uid(항 · 호는 조문으로 올림), 맞은 단위 uid, 앞뒤 80자). 넓게 잡고 사용자가 거른다
"""

import re
from dataclasses import dataclass

WINDOW = 40
SNIPPET = 80
_DOTS = "·・•‧ㆍ"
_QUOTED = re.compile(r"「([^「」]+)」")
_LAW_WORD = re.compile(r"(?<![가-힣])법(?![가-힣])")
_SAME_LAW = re.compile(r"같은\s*법\s*$")
_ALIAS = re.compile(r"「([^「」]+)」[^「」]{0,40}?이하\s*[\"“”'‘’]?\s*법\s*[\"“”'‘’]?\s*(?:이라|라)")


@dataclass(frozen=True)
class Candidate:
    source: str  # 조문 uid(`art:` · `admart:`)
    unit: str  # 맞은 단위 uid(조 · 항 · 호 · 고시 조문)
    snippet: str


def _key(name: str) -> str:
    """이름 비교 키: 공백 제거, 가운뎃점 통일."""
    return re.sub(r"\s+", "", re.sub(f"[{_DOTS}]", "ㆍ", name or ""))


def _name_pattern(names: list[str]) -> re.Pattern:
    def one(n: str) -> str:
        return "".join(r"\s*" if c == " " else f"[{_DOTS}]" if c in _DOTS else re.escape(c) for c in n)
    return re.compile("|".join(one(n) for n in sorted(set(names), key=len, reverse=True)))


def article_pattern(no: str) -> re.Pattern:
    """`23` · `23-2` → 그 조문만 맞는 정규식(`제23조의2`는 `23`에 맞지 않음, `제123조` · `제230조`도 아님)."""
    n, _, b = no.partition("-")
    body = rf"제\s*{n}\s*조" + (rf"\s*의\s*{b}(?!\d)" if b else "")
    return re.compile(r"(?<!\d)" + body + r"(?!\s*의\s*\d)")


_ART = r"제\s*(\d+)\s*조(?:\s*의\s*(\d+))?(?:\s*제\s*\d+\s*항)?"
_RANGE = re.compile(rf"{_ART}\s*부터\s*{_ART}\s*까지")
# 나열 꼬리: `제11조, 제12조제1항 및 ` 같은 앞선 조문 표기(맥락 법령 이름을 찾을 때 건너뜀)
_LIST_TAIL = re.compile(r"(?:제\s*\d+\s*조(?:\s*의\s*\d+)?(?:\s*제\s*\d+\s*[항호])*(?:\s*(?:부터|까지))?"
                        r"\s*(?:,|ㆍ|·|및|또는|와|과|부터)?\s*)+$")


def _num(main: str, branch: str | None) -> tuple[int, int]:
    return int(main), int(branch or 0)


def find(no: str, text: str) -> list[tuple[int, int]]:
    """본문에서 대상 조문을 가리키는 자리(시작, 끝): `제N조` 표기와, N이 안쪽에 드는 `제A조부터 제B조까지`."""
    n, _, b = no.partition("-")
    target = _num(n, b)
    spans = [m.span() for m in article_pattern(no).finditer(text)]
    spans += [m.span() for m in _RANGE.finditer(text)
              if _num(m.group(1), m.group(2)) < target < _num(m.group(3), m.group(4))]
    return sorted(set(spans))


def descendants(law_id: str, subordinate: dict[str, set[str]]) -> set[str]:
    """체계도(아래 → 위) → L 아래 모든 법령ID."""
    out: set[str] = set()
    frontier = {law_id}
    while frontier:
        frontier = {c for c, ups in subordinate.items() if ups & frontier} - out - {law_id}
        out |= frontier
    return out


def law_aliases(admin_articles: dict) -> dict[str, set[str]]:
    """고시 ID → `「X」(이하 "법"이라 한다)`로 정의한 X의 이름 키 집합."""
    out: dict[str, set[str]] = {}
    for a in admin_articles.values():
        for m in _ALIAS.finditer(a.text):
            out.setdefault(a.admrul_id, set()).add(_key(m.group(1)))
    return out


def _snippet(text: str, start: int, end: int) -> str:
    return re.sub(r"\s+", " ", text[max(0, start - SNIPPET):end + SNIPPET]).strip()


def candidates(target: str, laws: dict, articles: dict, admin_articles: dict,
               subordinate: dict[str, set[str]], aliases: dict[str, set[str]] | None = None) -> list[Candidate]:
    """대상 조문 uid(`art:{L}:{no}`) → 후보 목록(문서 순서, 같은 단위 · 같은 위치 중복 없음)."""
    _, lid, no = target.split(":")
    law = laws[lid]
    names = [n for n in (law.name, law.abbr) if n]
    own_keys = {_key(n) for n in names}
    name_re = _name_pattern(names)
    subs = descendants(lid, subordinate)
    aliases = law_aliases(admin_articles) if aliases is None else aliases
    out: list[Candidate] = []

    def mentions_law(before: str, law_word_means_l: bool) -> bool:
        before = _LIST_TAIL.sub("", before)
        window = before[-WINDOW:]
        if name_re.search(window):
            return True
        if _SAME_LAW.search(before):
            quoted = _QUOTED.findall(before)
            return bool(quoted) and _key(quoted[-1]) in own_keys
        return law_word_means_l and bool(_LAW_WORD.search(window))

    for a in articles.values():
        if a.law_id != lid and a.law_id not in subs:
            continue
        if a.uid == target:
            continue
        for unit, text in a.units:
            for start, end in find(no, text or ""):
                before = text[:start]
                if a.law_id == lid:
                    q = re.search(r"「([^「」]+)」\s*$", before)
                    quoted = _QUOTED.findall(before) if _SAME_LAW.search(before) else []
                    if (q and _key(q.group(1)) not in own_keys) or (quoted and _key(quoted[-1]) not in own_keys):
                        continue
                elif not mentions_law(before, True):
                    continue
                out.append(Candidate(a.uid, unit, _snippet(text, start, end)))
    for a in admin_articles.values():
        for start, end in find(no, a.text or ""):
            if mentions_law(a.text[:start], bool(aliases.get(a.admrul_id, set()) & own_keys)):
                out.append(Candidate(a.uid, a.uid, _snippet(a.text, start, end)))
    return out


def sources(cands: list[Candidate]) -> list[str]:
    """후보의 출처 조문 uid(처음 나온 순서, 중복 없음). 풀 조건 `1~200`은 이 수로 센다."""
    return list(dict.fromkeys(c.source for c in cands))
