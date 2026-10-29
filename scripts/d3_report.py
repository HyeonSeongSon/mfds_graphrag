"""1주차 D3 수집 보고: 실측 대조, 결정문 범위, DUR 연번, M3 염 형태, T3 표본 가능성(개략), 전체 본문 refs.py 해석률.

스냅숏(git 제외)을 읽고 집계만 출력한다. 업소 정보 · 판결문 본문은 출력하지 않는다. 테스트가 아니다(`tests/`는 고정 자료만).
T3는 D4 dry-run 전 개략 확인이다: 같은 사건번호 묶음, 판결요지 5-gram Jaccard 0.8 이웃, 이웃 포함 50건 이하,
대표 조문당 20 상한까지 본다. dev-test 충돌은 세지 않는다(D4 dry-run).

    python scripts/d3_report.py data/raw/20261029
"""

import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

from lxml import etree

from mfds.collect import dur
from mfds.collect.foodsafety import read_rows
from mfds.ingredients import normalize
from mfds.refs import LawDict, Resolver, article_set

EXPECTED = {"식약처 법령": 87, "법령 조문": 3745, "행정규칙": 513, "식품 처분(고유)": 5417}
CAP, NEIGHBOR_MAX, JACCARD = 20, 50, 0.8
TAG = re.compile(r"<[^>]+>")


def text(s: str | None) -> str:
    return re.sub(r"\s+", " ", TAG.sub(" ", s or "")).strip()


def grams(s: str, n: int = 5) -> set[str]:
    s = re.sub(r"\s+", "", s)
    return {s[i:i + n] for i in range(len(s) - n + 1)}


def jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def art_key(uid: str) -> tuple:
    _, lid, num = uid.split(":", 2)
    main, _, branch = num.partition("-")
    return lid, int(main), int(branch or 0)


def section(title: str) -> None:
    print(f"\n## {title}")


def report_counts(S: Path, arts: set[str]) -> None:
    section("1. 실측 대조")
    got = {"식약처 법령": len(list((S / "law/body").glob("*.xml"))), "법령 조문": len(arts),
           "행정규칙": len(list((S / "admrul/body").glob("*.xml"))),
           "식품 처분(고유)": len({r["DSPSDTLS_SEQ"] for r in read_rows(S)})}
    for k, v in EXPECTED.items():
        print(f"- {k}: {got[k]:,} (실측 {v:,}) {'일치' if got[k] == v else '차이'}")
    rows = (S / "manifest.tsv").read_text(encoding="utf-8").splitlines()[1:]
    print(f"- manifest {len(rows):,}행, 출처별 {dict(Counter(r.split(chr(9))[0] for r in rows))}")


def report_decisions(S: Path) -> None:
    section("2. 판례 · 해석례 · 심판 범위")
    for t, item, idf in (("prec", "prec", "판례일련번호"), ("expc", "expc", "법령해석례일련번호"),
                         ("decc", "decc", "행정심판재결례일련번호")):
        per_law: dict[str, set[str]] = defaultdict(set)
        pages = 0
        for d in sorted((S / "decisions" / t / "search").iterdir()):
            for p in sorted(d.glob("*.xml")):
                pages += 1
                for e in etree.parse(str(p)).getroot().iter(item):
                    per_law[d.name].add(e.findtext(idf))
        bodies = len(list((S / "decisions" / t / "body").glob("*.xml")))
        print(f"- {t}: 검색 {pages}쪽, 검색 합 {sum(map(len, per_law.values())):,}, 1건 이상 법률 "
              f"{sum(1 for v in per_law.values() if v)}개, 본문 {bodies:,}")
        if t == "decc":
            top8 = sorted(per_law, key=lambda k: -len(per_law[k]))[:8]
            ids8 = set().union(*(per_law[k] for k in top8))
            pages8 = sum(len(list((S / "decisions/decc/search" / k).glob("*.xml"))) for k in top8)
            print(f"  - 26개 확대 효과(건수 상위 8개 법률만 받았을 때와 비교, 실측 8개 목록은 기록 없음): 본문 "
                  f"{len(ids8):,} → {bodies:,}(+{bodies - len(ids8):,}), 검색 {pages8} → {pages}쪽, "
                  f"요청 +{bodies - len(ids8) + pages - pages8:,}회")


def report_dur(S: Path) -> None:
    section("3. DUR 별표 · M3 염 형태")
    path = next(p for p in (S / "admrul/body").glob("*.xml")
                if etree.parse(str(p)).getroot().findtext("행정규칙기본정보/행정규칙명")
                == "의약품 병용금기 성분 등의 지정에 관한 규정")
    annex = dur.annexes_from_xml(path)
    for k, rows in annex.items():
        print(f"- 별표 {k}: {len(rows):,}행, 연번 누락 · 중복 {dur.check_seq(rows) or '없음'}")
    cols = {"1": ("ingredient_a", "ingredient_b"), "2": ("ingredient",), "3": ("ingredient",)}
    uids: dict[str, dict[str, str]] = {}  # 별표 → uid → 염 제거 키
    for k, rows in annex.items():
        uids[k] = {}
        for r in rows:
            for c in cols[k]:
                ing = normalize(r[c])
                if ing and not ing.components:  # 복합제 제외
                    uids[k][ing.uid] = ing.base or ing.name
    by_key: dict[str, set[str]] = defaultdict(set)
    for k in uids:
        for u, key in uids[k].items():
            by_key[key].add(u)
    groups = {key: us for key, us in by_key.items() if len(us) > 1}
    print(f"- 염 제거 키가 같은데 uid가 다른 묶음(별표 1~3 전체, 복합제 제외): {len(groups)}개, uid {sum(map(len, groups.values()))}개")
    for other in ("1", "2"):
        hit = {u for u, key in uids["3"].items() if any(v != u and v in uids[other] for v in by_key[key])}
        print(f"- 별표 3 성분 {len(uids['3']):,}개 중 별표 {other}에 염만 다른 uid가 있는 성분: {len(hit)}개 "
              f"({len(hit) / len(uids['3']):.1%})")
    hit_any = {u for u, key in uids["3"].items()
               if any(v != u and (v in uids["1"] or v in uids["2"]) for v in by_key[key])}
    share = len(hit_any) / len(uids["3"])
    print(f"- 합: {len(hit_any)}개({share:.1%}) → M3 집합 연산 단위 {'염 제거 별칭' if share >= 0.05 else 'uid(한계로 기록)'}"
          f" (기준 5%)")


def report_t3(S: Path, laws: LawDict, arts: set[str]) -> None:
    section("4. T3 표본 가능성(개략)")
    r = Resolver(laws, arts)
    precs, drop = {}, Counter()
    freq: Counter = Counter()  # 대표 조문 빈도: 같은 스냅숏 전체(제외 전)에서 근거가 된 처분 · 판례 수(4-2)
    for p in sorted((S / "decisions/prec/body").glob("*.xml")):
        root = etree.parse(str(p)).getroot()
        ref, summ = text(root.findtext("참조조문")), text(root.findtext("판결요지"))
        precs[p.stem] = {"case": root.findtext("사건번호") or p.stem, "summary": summ, "answer": []}
        if not ref:
            drop["참조조문 없음"] += 1
            continue
        res = r.resolve(ref)
        current = list(dict.fromkeys(x.article_uid for x in res.refs if not x.historical))
        freq.update(set(current))  # 판결요지가 없어 문항이 못 되는 판례도 빈도에는 센다
        if not summ:
            drop["판결요지 없음"] += 1
            continue
        if not current:
            if res.refs:
                drop["구법 참조만"] += 1
            elif res.failures and all(f.reason == "not_mfds" for f in res.failures):
                drop["타 부처 법령만"] += 1
            else:
                drop["그 밖의 해석 실패(옛 법령 · 맥락 없음 등)"] += 1
            continue
        precs[p.stem]["answer"] = current
    usable = {i: v for i, v in precs.items() if v["answer"]}
    print(f"- 법원 판례 {len(precs):,} → 정답 있는 판례 {len(usable):,} (뺀 수 {dict(drop)})")

    for row in read_rows(S):
        freq.update(set(r.resolve(row["LAWORD_CD_NM"]).article_uids))

    groups: dict[str, list[str]] = defaultdict(list)
    for i, v in usable.items():
        groups[v["case"]].append(i)
    g5 = {i: grams(v["summary"]) for i, v in precs.items() if v["summary"]}
    sizes, ok = [], {}
    for case, members in groups.items():
        mem = set(members)
        neigh = {j for j, gj in g5.items() if j not in mem and precs[j]["case"] != case
                 and any(jaccard(g5[m], gj) >= JACCARD for m in members)}
        size = len(mem | {j for j, v in precs.items() if v["case"] == case}) + len(neigh)
        sizes.append(size)
        if size <= NEIGHBOR_MAX:
            ok[case] = members
    print(f"- 사건번호 묶음 {len(groups):,}개, 이웃 포함 50건 이하 {len(ok):,}개 "
          f"(크기 분포 1: {sum(s == 1 for s in sizes)}, 2~5: {sum(2 <= s <= 5 for s in sizes)}, "
          f"6~50: {sum(6 <= s <= 50 for s in sizes)}, 50 초과: {sum(s > 50 for s in sizes)})")

    rep: Counter = Counter()
    for members in ok.values():
        answer = sorted({a for m in members for a in usable[m]["answer"]}, key=lambda a: (-freq[a], art_key(a)))
        rep[answer[0]] += 1
    fill = sum(min(n, CAP) for n in rep.values())
    print(f"- 대표 조문 {len(rep)}종, 상위 5개 묶음 수 {[n for _, n in rep.most_common(5)]}")
    print(f"- 대표 조문당 {CAP} 상한 후 채울 수 있는 T3 문항: **{fill}** (필요 230) → "
          f"{'충족' if fill >= 230 else '부족: D4 잠금 전 대체 규칙(4-2) 검토'}")
    print(f"- 최빈 조문 5개 고정 기준선(상한 없을 때 묶음 기준): {sum(n for _, n in rep.most_common(5)) / max(len(ok), 1):.1%}")


def report_refs(S: Path, laws: LawDict, arts: set[str]) -> None:
    section("5. 전체 본문 refs.py 해석률")
    r = Resolver(laws, arts)
    for name, files, ctx in (("법령", sorted((S / "law/body").glob("*.xml")), True),
                             ("행정규칙", sorted((S / "admrul/body").glob("*.xml")), False)):
        refs = hist = 0
        why: Counter = Counter()
        other: Counter = Counter()
        for p in files:
            root = etree.parse(str(p)).getroot()
            if ctx:
                lid = root.findtext("기본정보/법령ID")
                units = [(u, f"{int(u.findtext('조문번호'))}" + (f"-{int(b)}" if (b := (u.findtext('조문가지번호') or '').strip()) else ""))
                         for u in root.iter("조문단위") if u.findtext("조문여부") == "조문"]
                texts = [(" ".join(e.text or "" for e in u.iter() if e.tag in ("조문내용", "항내용", "호내용", "목내용")),
                          lid, num) for u, num in units]
            else:
                texts = [(e.text or "", None, None) for e in root.iter("조문내용")]
            for t, lid, num in texts:
                res = r.resolve(t, lid, num)
                refs += len(res.refs)
                hist += sum(x.historical for x in res.refs)
                for f in res.failures:
                    why[f.reason] += 1
                    if f.reason == "unknown_law":
                        other[f.raw] += 1
        total = refs + sum(why.values())
        print(f"- {name}: 해석 {refs:,} / {total:,} ({refs / max(total, 1):.1%}), 구법 표시 {hist:,}, 실패 {dict(why)}")
        named = total - why["no_context"]
        if not ctx:  # 행정규칙의 `제N조`는 대부분 자기 조문 참조라 법령 맥락이 없다(no_context). 법령명이 있는 참조만 따로
            print(f"  - 자기 조문 참조(no_context)를 뺀 해석률: {refs:,} / {named:,} ({refs / max(named, 1):.1%}). "
                  f"unknown_law는 대부분 다른 행정규칙 이름(「…규정」)과 그 뒤 항 · 호")
        if other:
            print(f"  - unknown_law 상위: {other.most_common(8)}")


def main() -> None:
    S = Path(sys.argv[1])
    laws = LawDict.from_snapshot(S)
    arts = article_set(sorted((S / "law/body").glob("*.xml")))
    print(f"# D3 수집 보고 ({S.as_posix()})")
    report_counts(S, arts)
    report_decisions(S)
    report_dur(S)
    report_t3(S, laws, arts)
    report_refs(S, laws, arts)


if __name__ == "__main__":
    main()
