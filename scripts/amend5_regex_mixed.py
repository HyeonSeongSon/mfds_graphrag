"""AMENDMENTS 5 근거 수치를 다시 내고, 기록한 값과 같은지 단언한다(읽기만 함).

실행: 저장소 루트에서 `python scripts/amend5_regex_mixed.py`. 출력은 건수 · 해시 · 문서 uid뿐이다.
값이 하나라도 다르면 다른 항목을 모두 출력하고 exit 1로 끝난다. 그때 문구의 숫자를 고치지 말고 원인부터 찾는다.

셈
1. 정규식 경로(판례, 참조조문 필드 없음 441건)에서 refs.py 결과와 중복 제거 전 인용
   - 층 1: refs.py 결과 uid가 같은 uid의 첫 인용 표시와 같은지
   - 중복 제거 전 인용 기준 섞임(uid · 조문 단위, 첫 인용 현행 · 구법)
   - 자리 기준 (2): 남은 인용 자리(`cur["start"]`, 법령 토큰의 "구 "부터 잡힘)가 "구" 표기로 시작하거나 바로 앞에 있는데 표시 없음
   - 문자열 대조 (2): 표시 없는 참조의 원문 표기(raw)를 입력 전체에서 문자열로 찾아 어느 자리든 바로 앞 3자에
     OLD_MARK가 있음(같은 raw로 표시된 참조는 뺌). 앞 답의 셈 방식이고, 걸린 곳을 중복 제거 · 그 밖으로 나눈다
   - 비식약처 이름으로 시작하는 참조: 중복 제거 뒤 조문 참조(`art:`) 중 raw가
     `^(구\\s?)?「?([가-힣ㆍ ]+?)\\s?(시행령|시행규칙)`으로 시작하고, 그룹2 + 그룹3에서 공백을 뺀 값이
     LawDict.other_names(식약처 87개 밖 법령 이름)에서 공백을 뺀 이름 가운데 하나와 같은 것
2. 법령 사전: 약사법 계열 식약처 사전 이름, other_names의 「약사법 시행규칙」
3. 배치 입력 2,965건에서 "약사법 시행규칙"이 나오는 문서와 괄호 안 약칭 꼴(엄격 · 느슨 두 정규식), norm_text 뒤
4. 표본 100건(data/cache/extract/sample_resolved.jsonl, git 밖 캐시): 항목 4 근거별 건수(480 분해)와
   추출 엣지(문서 · 조문) 섞임(269 분해). 셈 단위는 (문서, 항목 text, 조문)이고 (문서, 조문)으로 묶는다

의존(주의)
- mfds/refs.py(잠금 대상)를 고치지 않는다. 이 프로세스 안에서만 `mfds.refs.Ref`를 하위 클래스로 바꿔
  uid에 일련번호를 붙여 Resolver.resolve 끝의 uid 중복 제거를 건너뛰고, 호출한 emit 프레임의 `cur["start"]`로
  인용 자리를 읽는다. refs.py의 Ref 생성 · 중복 제거 · emit 구조가 바뀌면 이 스크립트는 깨진다.
- 이 파일은 잠금 대상이 아니다. AMENDMENTS 5의 --files는 기록 시점 해시만 남기고, 그 뒤 수정은 check가 잡지 못한다.
"""
import hashlib
import itertools
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from lxml import etree  # noqa: E402

import mfds.refs as R  # noqa: E402
import extract as X  # noqa: E402

ORIG = R.Ref
OLD_HEAD = re.compile(r"구\s?「?")
NON_MFDS = re.compile(r"^(구\s?)?「?([가-힣ㆍ ]+?)\s?(시행령|시행규칙)")
NAME = re.compile(r"약사법\s?시행규칙")
ALIAS_STRICT = re.compile(r"이하\s?[‘’'\"“”「]?\s?약사법\s?시행규칙\s?[‘’'\"“”」]?\s?(?:이라|라)\s?한다")
ALIAS_LOOSE = re.compile(r"이하\s?[^)\n]{0,10}?약사법\s?시행규칙")
SAMPLE = ROOT / "data" / "cache" / "extract" / "sample_resolved.jsonl"
SAMPLE_SHA12 = "b6a02318d021"
CUT = {"식품위생법": "20090206", "식품위생법 시행령": "20090806", "식품위생법 시행규칙": "20090812",
       "약사법": "20070411", "약사법 시행규칙": "20080115", "의료기기법": "20110407", "화장품법": "20110804"}
FIELD_GROUP = "판례(필드 있음, 추출 입력에 들어 있으나 엣지는 필드가 만듦)"

EXPECTED = {
    "1 regex 문서": 441,
    "1 regex refs.py 결과 조문 참조 uid": 916,
    "1 regex refs.py 결과 = 첫 인용 표시": 916,
    "1 regex 섞임 uid 수": 916,
    "1 regex 섞임 uid · 첫 인용 현행": 10,
    "1 regex 섞임 uid · 첫 인용 구법": 7,
    "1 regex 섞임 조문 수": 776,
    "1 regex 섞임 조문 · 첫 인용 구법": 12,
    "1 regex 섞임 조문 · 첫 인용 현행": 9,
    "1 regex 자리 기준 (2) 남은 인용": 1,
    "1 regex 자리 기준 (2) 중복 제거 전 인용 전체": 1,
    "1 regex 문자열 대조 · 뒤 구법 인용이 중복 제거로 사라짐": 2,
    "1 regex 문자열 대조 · 그 밖": 1,
    "1 regex 비식약처 이름으로 시작하는 참조": 1,
    "2 사전 약사법 계열 식약처 이름": "약사법 | 약사법 시행령",
    "2 사전 other_names에 약사법 시행규칙": True,
    "3 출현 판례(필드 없음) 문서": 441,
    "3 출현 판례(필드 없음) 나옴": 14,
    "3 출현 판례(필드 없음) 약칭 괄호 엄격": 1,
    "3 출현 판례(필드 없음) 약칭 괄호 느슨": 1,
    f"3 출현 {FIELD_GROUP} 문서": 723,
    f"3 출현 {FIELD_GROUP} 나옴": 8,
    f"3 출현 {FIELD_GROUP} 약칭 괄호 엄격": 0,
    f"3 출현 {FIELD_GROUP} 약칭 괄호 느슨": 0,
    "3 출현 해석례 문서": 278,
    "3 출현 해석례 나옴": 11,
    "3 출현 해석례 약칭 괄호 엄격": 0,
    "3 출현 해석례 약칭 괄호 느슨": 0,
    "3 출현 심판 문서": 1523,
    "3 출현 심판 나옴": 70,
    "3 출현 심판 약칭 괄호 엄격": 0,
    "3 출현 심판 약칭 괄호 느슨": 0,
    "4 표본 sha256 앞 12자": SAMPLE_SHA12,
    "4 표본 조문 참조": 480,
    "4 표본 근거 없음": 412,
    "4 표본 근거 (1)": 19,
    "4 표본 근거 (1) + (4)": 2,
    "4 표본 근거 (4)": 29,
    "4 표본 근거 (3)": 14,
    "4 표본 근거 (2)": 4,
    "4 표본 엣지(문서 · 조문)": 269,
    "4 표본 엣지 모두 표시": 45,
    "4 표본 엣지 섞임": 1,
    "4 표본 엣지 없음": 223,
}
EXPECTED_UIDS = {
    "1 regex 자리 기준 (2) 남은 인용": ["case:221915"],
    "1 regex 문자열 대조 · 뒤 구법 인용이 중복 제거로 사라짐": ["case:217515", "case:234497"],
    "1 regex 문자열 대조 · 그 밖": ["case:78505"],
    "1 regex 비식약처 이름으로 시작하는 참조": ["case:221915"],
    "3 출현 판례(필드 없음) 약칭 괄호 엄격": ["case:221915"],
    "3 출현 판례(필드 없음) 약칭 괄호 느슨": ["case:221915"],
}


def all_citations(r: R.Resolver, text: str) -> list[tuple[str, str, bool, int]]:
    """중복 제거 전 모든 인용: [(uid, article_uid, historical, start)] 낸 순서대로. start는 norm_text 기준 자리."""
    cnt = itertools.count()

    class U(ORIG):
        def __init__(self, uid, *a, **k):
            self.start = sys._getframe(1).f_locals["cur"]["start"]
            super().__init__(f"{uid}#{next(cnt)}", *a, **k)

    R.Ref = U
    try:
        refs = r.resolve(text).refs
    finally:
        R.Ref = ORIG
    return [(x.uid.split("#")[0], x.article_uid, x.historical, x.start) for x in refs]


def old_at(t: str, s: int) -> bool:
    return bool(X.OLD_MARK.search(t[max(0, s - 3):s]) or OLD_HEAD.match(t, s))


def regex_path(docs: list[dict], ctx, c: dict, ex: dict) -> None:
    r = ctx.r
    others = {n.replace(" ", "") for n in ctx.laws.other_names}
    cnt = Counter()
    for d in docs:
        if d["kind"] != "prec" or d["ref_field"]:
            continue
        cnt["문서"] += 1
        t = R.norm_text(d["text"])
        dedup = [x for x in r.resolve(d["text"]).refs if x.article_uid.startswith("art:")]
        occ = [o for o in all_citations(r, d["text"]) if o[1].startswith("art:")]
        first: dict = {}
        by_uid = defaultdict(list)
        for uid, _, h, s in occ:
            first.setdefault(uid, (h, s))
            by_uid[uid].append(h)
        flagged_raws = {y.raw for y in dedup if y.historical}
        for x in dedup:
            cnt["refs.py 결과 조문 참조 uid"] += 1
            h, s = first[x.uid]
            cnt["refs.py 결과 = 첫 인용 표시"] += int(h == x.historical)
            if not x.historical and old_at(t, s):
                cnt["자리 기준 (2) 남은 인용"] += 1
                ex["1 regex 자리 기준 (2) 남은 인용"].append(d["uid"])
            if not x.historical and x.raw not in flagged_raws:
                pos = [m.start() for m in re.finditer(re.escape(x.raw), t)]
                if any(X.OLD_MARK.search(t[max(0, p - 3):p]) for p in pos):
                    hs = by_uid[x.uid]
                    kind = "뒤 구법 인용이 중복 제거로 사라짐" if (not hs[0] and any(hs)) else "그 밖"
                    cnt[f"문자열 대조 · {kind}"] += 1
                    ex[f"1 regex 문자열 대조 · {kind}"].append(d["uid"])
            m = NON_MFDS.match(x.raw)
            if m and (m.group(2) + m.group(3)).replace(" ", "") in others:
                cnt["비식약처 이름으로 시작하는 참조"] += 1
                ex["1 regex 비식약처 이름으로 시작하는 참조"].append(d["uid"])
        for uid, _, h, s in occ:
            if not h and old_at(t, s):
                cnt["자리 기준 (2) 중복 제거 전 인용 전체"] += 1
        for level, i in (("uid", 0), ("조문", 1)):
            g = defaultdict(list)
            for o in occ:
                g[o[i]].append(o[2])
            for hs in g.values():
                cnt[f"섞임 {level} 수"] += 1
                if len(set(hs)) > 1:
                    cnt[f"섞임 {level} · 첫 인용 {'구법' if hs[0] else '현행'}"] += 1
    for k, v in cnt.items():
        c[f"1 regex {k}"] = v


def dictionary(ctx, c: dict) -> None:
    c["2 사전 약사법 계열 식약처 이름"] = " | ".join(sorted(n for n in ctx.laws.names if "약사법" in n))
    c["2 사전 other_names에 약사법 시행규칙"] = "약사법 시행규칙" in ctx.laws.other_names


def appearance(docs: list[dict], c: dict, ex: dict) -> None:
    label = {"expc": "해석례", "decc": "심판"}
    for d in docs:
        if d["kind"] == "prec":
            g = FIELD_GROUP if d["ref_field"] else "판례(필드 없음)"
        else:
            g = label[d["kind"]]
        t = R.norm_text(d["text"])
        c.setdefault(f"3 출현 {g} 문서", 0)
        c[f"3 출현 {g} 문서"] += 1
        for key, hit in (("나옴", NAME.search(t)), ("약칭 괄호 엄격", ALIAS_STRICT.search(t)),
                         ("약칭 괄호 느슨", ALIAS_LOOSE.search(t))):
            c.setdefault(f"3 출현 {g} {key}", 0)
            if hit:
                c[f"3 출현 {g} {key}"] += 1
                if key != "나옴":
                    ex[f"3 출현 {g} {key}"].append(d["uid"])


def sample(ctx, c: dict) -> None:
    sha = hashlib.sha256(SAMPLE.read_bytes()).hexdigest()
    print("표본 sha256", sha)
    c["4 표본 sha256 앞 12자"] = sha[:12]
    laws = ctx.laws
    rows = list(X.read_jsonl(SAMPLE))
    want = {r["uid"] for r in rows}
    date_field = {"prec": "선고일자", "expc": "해석일자", "decc": "의결일자"}
    dates = {}
    for kind, (prefix, idf, _) in X.KINDS.items():
        for p in (X.SNAPSHOT / "decisions" / kind / "body").glob("*.xml"):
            f = {e.tag: X.one_line(e.text) for e in etree.parse(str(p)).getroot()}
            uid = f"{prefix}:{f.get(idf) or p.stem}"
            if uid in want:
                dates[uid] = f.get(date_field[kind], "")
    cut = {}
    for r in rows:
        for it in r["items"]:
            for a in it["article_uids"]:
                lid = a.split(":")[1]
                if laws.title.get(lid, "") in CUT:
                    cut[lid] = CUT[laws.title[lid]]

    def glued(it: dict, a: str) -> bool:
        m = re.match(r"^구\s?「?\s?", it["text"])
        rx = ctx.family_rx.get(laws.root(a.split(":")[1]))
        return bool(m and rx and rx.match(it["text"], m.end()))

    basis = Counter()
    per = {}
    for r in rows:
        dt = dates.get(r["uid"], "")
        for it in r["items"]:
            for a in it["article_uids"]:
                if not a.startswith("art:"):
                    continue
                lid = a.split(":")[1]
                b = []
                if a in it["historical"]:
                    b.append("(1)")
                if glued(it, a) and a not in it["historical"]:
                    b.append("(2)")
                if a in it["historical_inferred"]:
                    b.append("(3)")
                if lid in cut and dt and dt < cut[lid]:
                    b.append("(4)")
                per[(r["uid"], it["text"], a)] = b
                basis[" + ".join(b) or "없음"] += 1
    c["4 표본 조문 참조"] = sum(basis.values())
    for k, v in basis.items():
        c[f"4 표본 근거 {k}"] = v
    edge = defaultdict(list)
    for (uid, _, a), b in per.items():
        edge[(uid, a)].append(bool(b))
    c["4 표본 엣지(문서 · 조문)"] = len(edge)
    e = Counter("모두 표시" if all(v) else "섞임" if any(v) else "없음" for v in edge.values())
    for k in ("모두 표시", "섞임", "없음"):
        c[f"4 표본 엣지 {k}"] = e[k]


def main() -> int:
    docs, _ = X.load_docs()
    ctx = X.snapshot_ctx()
    c: dict = {}
    ex = defaultdict(list)
    regex_path(docs, ctx, c, ex)
    dictionary(ctx, c)
    appearance(docs, c, ex)
    sample(ctx, c)
    for k in sorted(c):
        print(k, c[k])
    for k in sorted(ex):
        print(k, sorted(set(ex[k])))
    bad = [f"{k}: 기대 {v!r}, 실제 {c.get(k)!r}" for k, v in EXPECTED.items() if c.get(k) != v]
    bad += [f"{k}: 셈에 있고 기대값에 없음 {c[k]!r}" for k in c if k not in EXPECTED and c[k]]
    bad += [f"{k} 문서: 기대 {v}, 실제 {sorted(set(ex.get(k, [])))}" for k, v in EXPECTED_UIDS.items()
            if sorted(set(ex.get(k, []))) != v]
    if bad:
        print("\n기대값과 다름 (문구 숫자를 고치지 말고 원인부터 찾는다):")
        for b in bad:
            print(" -", b)
        return 1
    print("\n모든 값이 기대값과 같음")
    return 0


if __name__ == "__main__":
    sys.exit(main())
