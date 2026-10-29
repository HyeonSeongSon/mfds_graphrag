"""DUR 고시 성분 표기 → 성분 uid · 별칭 · 조건 (1주차 D4에 잠금).

규칙(docs/schema.md "성분" 절과 같음)
- uid는 `ing:<정규화한 영문명>`. 정규화: NFKC, 따옴표 통일, 소문자, 공백 하나로, 괄호 앞 · 하이픈 옆 공백 제거
- 괄호 안 분류번호 `(분류번호 : 421)` → class_codes(이름에서 뺌)
- 끝 괄호의 한글 제형 `(주사제)` · `(경구제)` → form(이름에서 뺌, DurRule 조건)
- 끝 괄호의 영문 동의어 `(rifampin)` → aliases(이름에서 뺌). 숫자로 시작하는 괄호(`(18F)` 동위원소)는 이름에 남긴다
- `A(EP)= B(USP)` 처럼 등호로 이은 표기는 앞을 이름, 뒤를 별칭으로
- `~ 함유제제` · `~ 함유 외용제` → contains=True (외용제는 form)
- 그 밖의 한글 괄호 `(S-atenolol 포함)` → note
- 복합제 `A + B` → components(각 성분을 같은 규칙으로 정규화), uid는 성분 이름을 " + "로 이은 것(고시 순서 유지)
- 염 제거형(`pyridoxine hydrochloride` → `pyridoxine`)은 base로 두고 검색 매칭에만 쓴다. 노드는 고시 표기 단위
- 이름 안의 `:`는 `_`로 바꾼다(DurRule ID 구분자)
"""

import re
import unicodedata
from dataclasses import dataclass, field

SALT_SUFFIXES = (
    "hydrochloride", "dihydrochloride", "hydrobromide", "sulfate", "sulphate", "mesylate", "mesilate", "maleate",
    "tartrate", "bitartrate", "citrate", "phosphate", "acetate", "besylate", "besilate", "fumarate", "succinate",
    "nitrate", "bromide", "tosylate", "lactate", "gluconate", "hyclate", "monohydrate", "trihydrate", "hydrate",
    "sodium", "potassium", "calcium", "magnesium",
)
FORM_WORDS = ("제", "링", "삽입")  # 한글 괄호가 이 말로 끝나면 제형으로 본다
DELETED = "<삭제>"

_WS = re.compile(r"\s+")
_CLASS = re.compile(r"\(\s*분류번호\s*:\s*([0-9 ,]+)\)")
_TRAIL_PAREN = re.compile(r"\(([^()]*(?:\([^()]*\))?[^()]*)\)\s*$")
_HANGUL = re.compile(r"[가-힣]")
_CONTAINS = re.compile(r"\s*함유\s*(제제|외용제)\s*$")
_ISOTOPE = re.compile(r"^(\d{1,3}[A-Z][a-z]?|[A-Z][a-z]?-\d{1,3})$")
_LEAD_PAREN = re.compile(r"^\(([^()]+)\)\s*(\S.*)$")
CATIONS = ("sodium", "potassium", "calcium", "magnesium", "barium", "manganese", "zinc", "lithium", "iron",
           "ferrous", "aluminium", "aluminum", "silver", "copper")
BAD_BASES = ("dimethyl", "chloral")  # 염을 떼면 다른 뜻이 되는 이름


@dataclass
class Ingredient:
    raw: str
    uid: str
    name: str
    aliases: list[str] = field(default_factory=list)
    form: str | None = None
    class_codes: list[str] = field(default_factory=list)
    contains: bool = False
    note: str | None = None
    components: list["Ingredient"] = field(default_factory=list)
    base: str | None = None


def basic(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).replace("’", "'").replace("‘", "'")
    s = _WS.sub(" ", s).strip()
    s = re.sub(r"\s*-\s*", "-", s)  # 줄바꿈으로 생긴 하이픈 옆 공백
    return re.sub(r"\s+\(", "(", s)


def _lower(s: str) -> str:
    return _WS.sub(" ", s.lower()).strip()


def strip_salt(name: str) -> str | None:
    """끝의 염 · 수화물 단어를 뗀 이름. 뗄 것이 없거나, 양이온만 남거나, 혼합 표기(`/`)면 None."""
    if "/" in name:
        return None
    words = name.split(" ")
    while len(words) > 1 and words[-1] in SALT_SUFFIXES:
        words.pop()
    base = " ".join(words)
    if base == name or base in CATIONS or base in BAD_BASES or words[0] in CATIONS:
        return None
    return base


def _single(s: str) -> Ingredient:
    raw = s
    codes = [c.strip() for m in _CLASS.findall(s) for c in m.split(",") if c.strip()]
    s = _CLASS.sub("", s).strip()
    form = note = None
    aliases: list[str] = []
    contains = False
    m = _CONTAINS.search(s)
    if m:
        contains = True
        if m.group(1) == "외용제":
            form = "외용제"
        s = s[:m.start()].strip()
    if "=" in s:
        head, *rest = [p.strip() for p in s.split("=")]
        aliases += [re.sub(r"\([A-Z]+\)$", "", r).strip() for r in rest]
        s = re.sub(r"\([A-Z]+\)$", "", head).strip()
    while True:
        m = _TRAIL_PAREN.search(s)
        if not m or m.start() == 0:
            break
        inner = m.group(1).strip()
        if _HANGUL.search(inner):
            if inner.endswith(FORM_WORDS) and form is None:
                form = inner
            else:
                note = inner
        elif _ISOTOPE.match(inner):
            break  # 동위원소 `(18F)` · `(I-131)`은 이름의 일부
        elif re.search(r"[A-Za-z]{2}|\d[A-Za-z]", inner):
            aliases += [a.strip() for a in inner.split(",") if a.strip()]
        else:
            break
        s = s[:m.start()].strip()
    m = _LEAD_PAREN.match(s)
    if m:  # `(Micronized)Progesterone` → 별칭 progesterone
        aliases.append(m.group(2))
    name = _lower(s).replace(":", "_")
    aliases = [_lower(a) for a in aliases]
    base = strip_salt(name)
    return Ingredient(raw=raw, uid=f"ing:{name}", name=name, aliases=aliases, form=form, class_codes=codes,
                      contains=contains, note=note, base=base)


def normalize(raw: str) -> Ingredient | None:
    """고시 표기 하나를 정규화한다. `<삭제>` 행은 None."""
    s = basic(raw)
    if not s or s == DELETED:
        return None
    if "+" in s:
        parts = [_single(p.strip()) for p in s.split("+") if p.strip()]
        # 복합제 전체에 붙은 제형 · 함유 표기는 마지막 성분 쪽에서 읽힌다 → 복합제로 올린다
        last = parts[-1]
        name = " + ".join(p.name for p in parts)
        return Ingredient(raw=raw, uid=f"ing:{name}", name=name, form=last.form, contains=last.contains,
                          class_codes=last.class_codes, components=parts)
    return _single(s)
