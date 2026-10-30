"""평가 원천 묶음 · 근접 중복 · 표본 조건 · 조항별 상한 (PREREG 4-2 · 4-4, 1주차 D4에 잠금).

- 묶음: T1은 정규화한 위반 문구가 정확히 같은 처분끼리, T3는 사건번호가 같은 판례끼리. Jaccard로 묶음을 잇지 않는다
- 근접 중복(별 모양): 문항 묶음의 구성원 하나와 문자 5-gram Jaccard 0.8 이상인 다른 문서. 문항을 중심으로 한 번만 보고 연쇄하지 않는다
- 같은 업소 이웃(T1): 업소 키가 같은 다른 처분. 키는 호출자가 git 제외 원본에서 메모리로만 만들어 넘긴다
- 표본 조건: 묶음 + 이웃이 50건 이하. 조항별 상한: 대표 조문당 dev + test 20 이하
- 질문 텍스트 조문 표기 가림(T1 · T3): 질문에 적힌 조문 번호로 정답을 바로 찾지 못하게 `해당 조항`으로 바꾼다.
  질문 텍스트에만 쓰고, 묶음 · 근접 중복 · 제외 목록은 원문으로 한다. 남은 참조가 없는지는 생성기가 `refs.py`로 확인한다

순수 함수만 둔다. 파일을 쓰지 않고 업소 키를 돌려주지 않는다(`SameKey.neighbors`는 문서 ID만 낸다).
"""

import math
import re
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

N_GRAM = 5
JACCARD = 0.8
NEIGHBOR_MAX = 50
CAP_PER_ARTICLE = 20

_DATE_PREFIX = re.compile(r"\(\d{8}\)")
_DATE = re.compile(r"\d{4}\s*[.\-/년]\s*\d{1,2}\s*[.\-/월]\s*\d{1,2}\s*[.일]?")
_ORDINAL = re.compile(r"\d+\s*차")
_NOISE = re.compile(r"[\s\W_]+")
_TAG = re.compile(r"<[^>]+>")


def normalize_violation(text: str) -> str:
    """T1 묶음 키: 앞 `(YYYYMMDD)` · 그 밖의 날짜 · 차수(`2차`) · 공백 · 문장부호를 지운다."""
    s = _DATE_PREFIX.sub("", text or "")
    s = _DATE.sub("", s)
    s = _ORDINAL.sub("", s)
    return _NOISE.sub("", s)


def case_key(case_no: str) -> str:
    """T3 묶음 키: 사건번호에서 공백을 지운다."""
    return re.sub(r"\s+", "", case_no or "")


def clean_html(s: str | None) -> str:
    return re.sub(r"\s+", " ", _TAG.sub(" ", s or "")).strip()


def t3_text(fields: dict[str, str | None]) -> str:
    """T3 문항 입력 · 근접 중복 비교 대상: 판결요지, 없으면 판시사항(2026-10-30 결정, t1_t3.yaml과 같음)."""
    return clean_html(fields.get("판결요지")) or clean_html(fields.get("판시사항"))


# 조문 표기 가림(PREREG 4-2). 법령 공포번호(`법률 제9432호`, `고시 제91-80호`)는 조문 단서가 아니라 남긴다
ARTICLE_MASK = "해당 조항"
_NUM = r"\d+(?:\s*-\s*\d+)*"
_BRANCH = r"(?:\s*의\s*\d+)?"
_PROMULGATION = rf"(?:법률|대통령령|총리령|부령|령|규칙|고시|훈령|예규|공고)\s*제\s*{_NUM}\s*호|제\s*\d+(?:\s*-\s*\d+)+\s*호"
# 조(`제` 없는 `N조` · 고시식 `제1-2-3조` 포함, 금액 `N조 원` 제외) + 바로 뒤 괄호(조문 제목 등)
_ART = rf"(?:제\s*)?{_NUM}\s*조{_BRANCH}(?!\s*(?:원|억|천|만))(?:\s*\([^()]{{1,40}}\))?"
# `[별표 9]` · `별표 9`와 번호가 괄호 밖에 있거나 없는 `[별표]9` · `[별표]`
_ANNEX = rf"(?:\[\s*)?별표\s*\d+{_BRANCH}(?:\s*\])?|\[\s*별표\s*\](?:\s*\d+{_BRANCH})?"
# 상대 표기. 앞에 한글 · 한자가 붙으면(`협동조합`, `甲이 조기에`) 보지 않고, 뒤에 공백 · 조사 · `제` · 숫자 · 문장부호가
# 올 때만 받는다(`같은 조례` · `같은 항소` · `전항목` · `동조하여` 같은 낱말은 구조적으로 빠짐). `같은 조항` · `이 조항`은 통째로
_RELATIVE = (r"(?<![가-힣一-鿿])(?:같은\s*(?:조항?|항|호)|동(?:조|항|호)|본조|본항|이\s(?:조항?|항)|전조|전항|각\s*호)"
             r"(?=$|[\s제\d,.·)」』’”]|[은는이가을를의에도만와과로으])")
_REF_START = rf"(?:{_ART}|{_ANNEX}|{_RELATIVE}|제\s*\d+\s*항|제\s*\d+{_BRANCH}\s*호{_BRANCH})"
# 이어지는 표기는 `제` 없는 `1항` · `3호` · `①` · `가목`도 받는다(앞 표기에 붙어 있을 때만. `청색 1호` 같은 낱말은 남김)
_REF_NEXT = rf"(?:{_REF_START}|\d+\s*항|\d+{_BRANCH}\s*호|[①-⑳]|[가-하]\s*목)"
_REF_SEP = r"\s*(?:,|·|및|또는|내지|부터|~|∼)?\s*"
_ARTICLE_REF = re.compile(rf"(?P<keep>{_PROMULGATION})|{_REF_START}(?:{_REF_SEP}{_REF_NEXT})*")


def mask_article_refs(text: str) -> str:
    """T1 · T3 질문 텍스트의 조문 표기(조 · 항 · 호 · 목 · 별표 · 같은 조 등 상대 표기, 조문 바로 뒤 괄호)를
    이어진 덩어리마다 `해당 조항` 하나로 바꾼다. 법령명과 공포번호는 남긴다."""
    return _ARTICLE_REF.sub(lambda m: m.group("keep") or ARTICLE_MASK, text or "")


def has_article_ref(text: str) -> bool:
    """조문 표기가 들어 있는가(`m2_disposition` 슬롯 제외 조건, PREREG 4-2)."""
    return mask_article_refs(text) != (text or "")


def exact_groups(texts: dict[str, str], key: Callable[[str], str]) -> dict[str, list[str]]:
    """문서 ID → 텍스트를 키로 묶는다. 키가 빈 문서(빈 위반 문구 등)는 문항이 될 수 없어 묶지 않는다."""
    groups: dict[str, list[str]] = defaultdict(list)
    for i, t in texts.items():
        k = key(t)
        if k:
            groups[k].append(i)
    return {k: sorted(v) for k, v in groups.items()}


def grams(text: str, n: int = N_GRAM) -> frozenset[str]:
    s = re.sub(r"\s+", "", text or "")
    if not s:
        return frozenset()
    return frozenset(s[i:i + n] for i in range(max(len(s) - n + 1, 1)))


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


class NearDup:
    """문자 5-gram Jaccard 근접 중복 찾기. 접두 거름: Jaccard ≥ t면 |A∩B| ≥ ⌈t|A|⌉이므로, A의 가장 드문
    gram |A| − ⌈t|A|⌉ + 1개 중 하나는 반드시 B에 있다. 그 gram들의 역색인만 보고 후보를 만든 뒤 정확히 잰다."""

    def __init__(self, texts: dict[str, str], threshold: float = JACCARD) -> None:
        self.t = threshold
        self.g = {i: grams(x) for i, x in texts.items()}
        self.df: Counter = Counter(x for gs in self.g.values() for x in gs)
        self.index: dict[str, list[str]] = defaultdict(list)
        for i, gs in self.g.items():
            for x in gs:
                self.index[x].append(i)

    def similar(self, i: str) -> set[str]:
        a = self.g.get(i) or frozenset()
        if not a:
            return set()
        need = math.ceil(self.t * len(a))
        prefix = sorted(a, key=lambda x: (self.df[x], x))[: len(a) - need + 1]
        cands = {j for x in prefix for j in self.index[x] if j != i}
        lo, hi = self.t * len(a), len(a) / self.t
        return {j for j in cands if lo <= len(self.g[j]) <= hi and jaccard(a, self.g[j]) >= self.t}

    def neighbors(self, members: Iterable[str]) -> set[str]:
        """묶음 구성원 하나와라도 0.8 이상인 다른 문서(구성원 제외, 연쇄 없음)."""
        mem = set(members)
        return {j for m in mem for j in self.similar(m)} - mem


class SameKey:
    """같은 키(T1: 업소명 + 주소, 공백 제거)를 가진 다른 문서. 키는 메모리 안에서만 쓴다."""

    def __init__(self, key_of: dict[str, str]) -> None:
        self.key_of = {i: re.sub(r"\s+", "", k or "") for i, k in key_of.items()}
        self.by_key: dict[str, set[str]] = defaultdict(set)
        for i, k in self.key_of.items():
            if k:
                self.by_key[k].add(i)

    def neighbors(self, members: Iterable[str]) -> set[str]:
        mem = set(members)
        out: set[str] = set()
        for m in mem:
            k = self.key_of.get(m)
            if k:
                out |= self.by_key[k]
        return out - mem


@dataclass
class Candidate:
    """문항 후보 하나 = 묶음 하나. excluded = 묶음 + 근접 중복 + (T1) 같은 업소 이웃 = 그래프에서 빠질 문서."""
    key: str
    members: list[str]
    answer: list[str]  # 정답 조문 uid
    excluded: set[str] = field(default_factory=set)
    rep: str | None = None

    @property
    def ok(self) -> bool:
        return len(self.excluded) <= NEIGHBOR_MAX


def build_candidates(groups: dict[str, list[str]], answers: dict[str, list[str]], near: NearDup,
                     same: SameKey | None = None) -> list[Candidate]:
    """정답이 있는 묶음마다 후보를 만든다. 정답은 묶음 구성원 정답의 합집합(순서는 처음 나온 순)."""
    out = []
    for k, members in groups.items():
        ans = list(dict.fromkeys(a for m in members for a in answers.get(m, [])))
        if not ans:
            continue
        excluded = set(members) | near.neighbors(members)
        if same is not None:
            excluded |= same.neighbors(members)
        out.append(Candidate(k, members, ans, excluded))
    return out


def art_key(uid: str) -> tuple[str, int, int]:
    """조문 uid 정렬 키(법령ID, 조, 가지). 문자열 비교는 `art:…:10` < `art:…:9`가 되어 쓰지 않는다."""
    _, lid, num = uid.split(":", 2)
    main, _, branch = num.partition("-")
    return lid, int(main), int(branch or 0)


def representative(answer: list[str], freq: Counter) -> str:
    """대표 조문: 같은 스냅숏 전체(제외 전)에서 근거가 된 처분 · 판례가 가장 많은 조문, 같으면 `art_key` 순."""
    return min(answer, key=lambda a: (-freq[a], art_key(a)))


def conflicts(a: Candidate, b: Candidate) -> bool:
    """두 문항이 근접 중복 · 같은 업소로 이어지는가(한쪽 구성원이 다른 쪽 제외 범위에 있음)."""
    return bool(set(a.members) & b.excluded or set(b.members) & a.excluded)


@dataclass
class Selection:
    test: list[Candidate]
    dev: list[Candidate]
    dev_dropped: int  # test와 충돌해 버리고 다음 후보로 넘어간 dev 수
    fillable: int  # 상한 적용 후 채울 수 있는 전체 문항 수


def select(cands_in_seed_order: list[Candidate], freq: Counter, n_test: int, n_dev: int,
           cap: int = CAP_PER_ARTICLE) -> Selection:
    """표본 조건을 통과한 후보를 시드 순서대로 대표 조문당 cap까지 담는다. test를 먼저 채우고, dev는 남은 후보에서
    시드 순서대로 채우되 test 문항과 충돌하는 후보는 버리고 다음 후보를 뽑는다. 상한은 dev + test 합계에 건다."""
    pool = [c for c in cands_in_seed_order if c.ok]
    for c in pool:
        c.rep = representative(c.answer, freq)
    per: Counter = Counter()
    fillable = sum(min(n, cap) for n in Counter(c.rep for c in pool).values())
    test, rest = [], []
    for c in pool:
        if len(test) < n_test and per[c.rep] < cap:
            test.append(c)
            per[c.rep] += 1
        else:
            rest.append(c)
    dev, dropped = [], 0
    for c in rest:
        if len(dev) >= n_dev:
            break
        if per[c.rep] >= cap:
            continue
        if any(conflicts(c, t) for t in test):
            dropped += 1
            continue
        dev.append(c)
        per[c.rep] += 1
    return Selection(test, dev, dropped, fillable)
