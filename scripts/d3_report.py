"""1주차 D3 수집 보고: 실측 대조, 결정문 범위, DUR 연번, M3 염 형태, T1 · T3 표본 가능성(`eval/grouping.py`), 전체 본문 refs.py 해석률.

스냅숏(git 제외)을 읽고 집계만 출력한다. 업소 정보 · 판결문 본문은 출력하지 않는다. 테스트가 아니다(`tests/`는 고정 자료만).
T1 · T3는 D4 dry-run 전 확인이다: 묶음, 5-gram Jaccard 0.8 이웃(T1은 같은 업소 포함), 이웃 포함 50건 이하,
대표 조문당 20 상한. 시드 대신 고정 순서로 세고, 시드 순서 결과는 D4 dry-run에서 낸다.

    python scripts/d3_report.py data/raw/20261029
"""

import sys
from collections import Counter, defaultdict
from pathlib import Path

from lxml import etree

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # `python scripts/d3_report.py`로도 실행되게
from mfds.collect import dur  # noqa: E402
from mfds.disposition import has_mask, read_rows  # noqa: E402
from mfds.ingredients import normalize  # noqa: E402
from mfds.refs import LawDict, Resolver, article_set  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "eval"))
from grouping import (CAP_PER_ARTICLE, NearDup, SameKey, build_candidates, case_key, clean_html,  # noqa: E402
                      exact_groups, has_article_ref, mask_article_refs, normalize_violation, select, t3_text)

EXPECTED = {"식약처 법령": 87, "법령 조문": 3745, "행정규칙": 513, "식품 처분(고유)": 5417}
text = clean_html


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


def _pool_report(name: str, cands: list, total_docs: int, freq: Counter, answers_of: dict[str, list[str]]) -> None:
    """후보 → 표본 조건 · 상한 후 채울 수 있는 수, 제외 규모, 정답 조문의 남은 원천 수(층별 hit@5 대비)."""
    sizes = [len(c.excluded) for c in cands]
    print(f"- {name} 정답 있는 묶음 {len(cands):,}개, 이웃 포함 50건 이하 {sum(c.ok for c in cands):,}개 "
          f"(크기 1: {sum(s == 1 for s in sizes)}, 2~5: {sum(2 <= s <= 5 for s in sizes)}, "
          f"6~50: {sum(6 <= s <= 50 for s in sizes)}, 50 초과: {sum(s > 50 for s in sizes)})")
    order = sorted(cands, key=lambda c: c.key)  # 시드와 무관한 고정 순서(시드는 PREREG에서 정한 뒤 dry-run에서만 씀)
    full = select(order, freq, n_test=10 ** 6, n_dev=0)  # 상한까지 전부 뽑은 경우
    reps = Counter(c.rep for c in full.test)
    print(f"- 대표 조문 {len(reps)}종, 상위 5개 묶음 수 {[n for _, n in Counter(c.rep for c in order if c.ok).most_common(5)]}")
    print(f"- 대표 조문당 {CAP_PER_ARTICLE} 상한 후 채울 수 있는 {name} 문항: **{full.fillable}** (필요 230) → "
          f"{'충족' if full.fillable >= 230 else '부족: D4 잠금 전 대체 규칙(4-2) 검토'}")
    sel = select(order, freq, n_test=200, n_dev=30)
    excl = set().union(*(c.excluded for c in sel.test + sel.dev)) if sel.test else set()
    print(f"- 고정 순서로 test 200 · dev 30을 뽑으면: test {len(sel.test)} · dev {len(sel.dev)}(충돌로 버린 dev "
          f"{sel.dev_dropped}), 제외 {len(excl):,}건 = 전체 {total_docs:,}의 {len(excl) / total_docs:.1%} (기준 30%)")
    taken = set().union(*(c.excluded for c in full.test)) if full.test else set()
    left = Counter(a for i, ans in answers_of.items() if i not in taken for a in set(ans))
    zero = sum(left[c.rep] == 0 for c in full.test)
    thin = sum(0 < left[c.rep] < 5 for c in full.test)
    print(f"- 상한까지 다 뽑았을 때 대표 조문의 남은 원천: 0건 {zero} · 1~4건 {thin} / {len(full.test)}")


def report_t1_t3(S: Path, laws: LawDict, arts: set[str]) -> None:
    section("4. T1 · T3 표본 가능성(`eval/grouping.py`)")
    r = Resolver(laws, arts)
    freq: Counter = Counter()  # 대표 조문 빈도: 같은 스냅숏 전체(제외 전)에서 근거가 된 처분 · 판례 수(4-2)

    rows = read_rows(S, keep_pii=True)  # 업소 키는 이 함수 안 메모리에서만 쓴다
    t1_ans = {r_["DSPSDTLS_SEQ"]: r.resolve(r_["LAWORD_CD_NM"]).article_uids for r_ in rows}
    for ans in t1_ans.values():
        freq.update(set(ans))
    t1_text = {r_["DSPSDTLS_SEQ"]: r_["VILTCN"] for r_ in rows}
    same = SameKey({r_["DSPSDTLS_SEQ"]: (r_.get("PRCSCITYPOINT_BSSHNM") or "") + (r_.get("ADDR") or "")
                    if (r_.get("PRCSCITYPOINT_BSSHNM") or "").strip() else "" for r_ in rows})
    del rows

    precs, case_of, t3_ans, drop = {}, {}, {}, Counter()
    for p in sorted((S / "decisions/prec/body").glob("*.xml")):
        root = etree.parse(str(p)).getroot()
        f = {c.tag: c.text for c in root}
        precs[p.stem] = t3_text(f)
        case_of[p.stem] = f.get("사건번호") or p.stem
        ref = text(f.get("참조조문"))
        if not ref:
            drop["참조조문 없음"] += 1
            continue
        res = r.resolve(ref)
        current = list(dict.fromkeys(x.article_uid for x in res.refs if not x.historical))
        freq.update(set(current))  # 질문 텍스트가 없어 문항이 못 되는 판례도 빈도에는 센다
        if not precs[p.stem]:
            drop["판결요지 · 판시사항 모두 없음"] += 1
        elif not current:
            if res.refs:
                drop["구법 참조만"] += 1
            elif res.failures and all(x.reason == "not_mfds" for x in res.failures):
                drop["타 부처 법령만"] += 1
            else:
                drop["그 밖의 해석 실패(옛 법령 · 맥락 없음 등)"] += 1
        else:
            t3_ans[p.stem] = current
    fallback = sum(1 for i in t3_ans if not clean_html(etree.parse(str(S / f"decisions/prec/body/{i}.xml"))
                                                        .getroot().findtext("판결요지")))

    t1_groups = exact_groups(t1_text, normalize_violation)
    # 위반 문구에 가림 표시가 든 처분은 문항 원천으로 뽑지 않는다(PREREG 4-2). 그래프 · 이웃 비교에는 남는다
    masked = {k for k, v in t1_groups.items() if any(has_mask(t1_text[i]) for i in v)}
    t1_groups = {k: v for k, v in t1_groups.items() if k not in masked}
    print(f"- T1 가림 표시가 든 묶음 {len(masked)}개는 문항 원천에서 뺌(그래프 · 이웃 비교에는 남음)")
    t1 = build_candidates(t1_groups, t1_ans, NearDup(t1_text), same)
    print(f"- T1 처분 {len(t1_text):,} → 정규화 문구 묶음 {len(t1_groups):,}개(빈 문구 · 정답 없음 제외 전)")
    _pool_report("T1", t1, len(t1_text), freq, t1_ans)

    print(f"- T3 법원 판례 {len(precs):,} → 정답 있는 판례 {len(t3_ans):,}(판시사항 대체 {fallback}) (뺀 수 {dict(drop)})")
    t3_groups = exact_groups({i: case_of[i] for i in precs}, case_key)
    t3 = build_candidates(t3_groups, t3_ans, NearDup({i: t for i, t in precs.items() if t}))
    _pool_report("T3", t3, len(precs), freq, t3_ans)

    # 질문 텍스트 조문 표기 가림(PREREG 4-2): 가리기 전 정답 조문이 읽히는 문항 수, 가린 뒤 남은 조문 참조(0이어야 함)
    for name, texts, answers in (("T1", t1_text, t1_ans), ("T3", precs, t3_ans)):
        leak = left = 0
        for i, ans in answers.items():
            if not ans or not texts.get(i):
                continue
            leak += bool(set(ans) & set(r.resolve(texts[i]).article_uids))
            left += bool(r.resolve(mask_article_refs(texts[i])).article_uids)
        print(f"- {name} 질문 조문 표기: 정답 있는 {sum(1 for a in answers.values() if a):,}건 중 가리기 전 정답 조문이 읽힘 "
              f"{leak:,}, 가린 뒤 남은 조문 참조 {left}")
    refs_in_label = sum(1 for i, a in t1_ans.items() if a and has_article_ref(t1_text[i]))
    print(f"- m2 처분 슬롯에서 빠지는 조문 표기 처분(정답 있는 처분 기준): {refs_in_label:,}")


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
    report_t1_t3(S, laws, arts)
    report_refs(S, laws, arts)


if __name__ == "__main__":
    main()
