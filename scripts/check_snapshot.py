"""수집 스냅숏 검수: manifest · 파일 · 해시 · ID · 완결성 · 내용 이상치 · 키 · 업소 정보. 집계만 출력한다.

    python scripts/check_snapshot.py data/raw/20261029
"""

import hashlib
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

from dotenv import dotenv_values
from lxml import etree

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # `python scripts/check_snapshot.py`로도 실행되게
from mfds.collect.common import ROOT, list_items  # noqa: E402
from mfds.disposition import PII, TEXT_FIELDS, read_rows  # noqa: E402

problems: list[str] = []


def check(ok: bool, msg: str) -> None:
    print(f"- [{'OK' if ok else '문제'}] {msg}")
    if not ok:
        problems.append(msg)


def section(t: str) -> None:
    print(f"\n## {t}")


def main() -> None:
    S = Path(sys.argv[1])

    section("1. manifest ↔ 파일")
    lines = (S / "manifest.tsv").read_text(encoding="utf-8").splitlines()
    head, rows = lines[0].split("\t"), [l.split("\t") for l in lines[1:]]
    check(all(len(r) == len(head) for r in rows), f"모든 행 {len(head)}열")
    files = Counter(r[2] for r in rows)
    check(not [f for f, n in files.items() if n > 1], f"파일 중복 기록 없음({len(files):,}개)")
    on_disk = {p.relative_to(S).as_posix() for p in S.rglob("*") if p.is_file() and p.name != "manifest.tsv"}
    check(on_disk == set(files), f"디스크 파일 {len(on_disk):,} = manifest 파일 (디스크만 {len(on_disk - set(files))}, "
          f"manifest만 {len(set(files) - on_disk)})")
    bad_hash = [r[2] for r in rows if (S / r[2]).exists() and
                (hashlib.sha256((S / r[2]).read_bytes()).hexdigest() != r[4] or (S / r[2]).stat().st_size != int(r[5]))]
    check(not bad_hash, f"sha256 · 크기 일치 (불일치 {len(bad_hash)})")
    check(not list(S.rglob("*.part")), ".part 잔여 없음")
    recovered = sum(r[7] == "manifest 복구" for r in rows)
    print(f"- 참고: 'manifest 복구' 행 {recovered}")

    section("2. 키 · 업소 정보")
    secrets = [v for k, v in dotenv_values(ROOT / ".env").items() if k.endswith(("_OC", "_KEY", "PASSWORD")) and v]
    text = (S / "manifest.tsv").read_text(encoding="utf-8")
    check(not any(s in text for s in secrets), f"manifest에 키 값 없음(키 {len(secrets)}개 대조)")
    check(all("OC=***" in r[3] for r in rows if "law.go.kr" in r[3]), "DRF URL 전부 OC 가림")
    raw = [r for r in read_rows(S, keep_pii=True)]
    # 업소명만 짧게 맞는 값(5자 미만)은 일반 낱말일 수 있어 참고로만 센다(2026-10-29 검수: 4자 업소명 1개가 일반어)
    strong = {r[c] for r in raw for c in PII if r.get(c) and len(r[c]) >= (5 if c == "PRCSCITYPOINT_BSSHNM" else 4)}
    weak = {r[c] for r in raw if 4 <= len(r.get("PRCSCITYPOINT_BSSHNM") or "") < 5
            for c in ("PRCSCITYPOINT_BSSHNM",)} - strong
    tracked = os.popen(f'git -C "{ROOT}" ls-files --cached --others --exclude-standard').read().split()
    leak, weak_hits = [], []
    for t in tracked:
        p = ROOT / t
        if p.suffix in {".py", ".md", ".tsv", ".yaml", ".json", ".xml", ".txt", ".cypher", ".csv"} and p.is_file():
            body = p.read_text(encoding="utf-8", errors="ignore")
            if any(v in body for v in strong):
                leak.append(t)
            elif any(v in body for v in weak):
                weak_hits.append(t)
    check(not leak, f"git 추적 · 추적 예정 파일 {len(tracked)}개에 업소 정보 값 없음 (발견 파일 {len(leak)})")
    print(f"- 참고: 4자 업소명과 같은 낱말이 있는 파일 {len(weak_hits)}개(일반어 일치 가능, 값은 출력하지 않음)")
    # 위반 · 처분 내용 원문에 그 업소의 업소명 · 인허가번호가 든 행이 있다 → read_rows가 가린 뒤에는 0이어야 한다
    own = sum(1 for r in raw for f in TEXT_FIELDS for c, n in (("PRCSCITYPOINT_BSSHNM", 2), ("LCNS_NO", 6))
              if len(v := re.sub(r"[\s\-]", "", r.get(c) or "")) >= n and v in re.sub(r"[\s\-]", "", r.get(f) or ""))
    check(own == 0, f"read_rows 텍스트 열에 그 업소의 업소명 · 인허가번호 없음 (발견 {own})")
    raw_tracked = [t for t in tracked if t.startswith("data/raw/") and not t.endswith("manifest.tsv")]
    check(not raw_tracked, f"원자료가 git 추적 대상 아님 (발견 {len(raw_tracked)})")

    section("3. ID · 완결성")
    _, laws = list_items((S / "law/list_mfds/p001.xml").read_bytes(), "law")
    lids = {l["법령ID"] for l in laws}
    bodies = {p.stem for p in (S / "law/body").glob("*.xml")}
    check(bodies == lids, f"법령 본문 = 목록 {len(lids)}")
    acts = {l["법령ID"] for l in laws if l["법령구분명"] == "법률"}
    check({p.stem for p in (S / "law/thdcmp").glob("*.xml")} == acts, f"3단비교 = 법률 {len(acts)}")
    stmd = {p.stem for p in (S / "law/lsstmd").glob("*.xml")}
    check(lids - stmd == {"011801"}, f"체계도 {len(stmd)} + 체계도 없음 {sorted(lids - stmd)}")
    for sub, path in (("body", "기본정보/법령ID"), ("thdcmp", "기본정보/법령ID"), ("lsstmd", "기본정보/법령ID")):
        wrong = [p.stem for p in (S / "law" / sub).glob("*.xml")
                 if etree.parse(str(p)).getroot().findtext(path) != p.stem]
        check(not wrong, f"law/{sub} 파일 이름 = 응답 법령ID (불일치 {len(wrong)})")
    mst = {l["법령ID"]: l["법령일련번호"] for l in laws}
    wrong_mst = [p.stem for p in (S / "law/body").glob("*.xml")
                 if (etree.parse(str(p)).getroot().get("법령키") or "")[:6] != p.stem]
    check(not wrong_mst, f"본문 법령키 앞 6자리 = 법령ID (불일치 {len(wrong_mst)})")

    total, rules = 0, []
    for p in sorted((S / "admrul/list_mfds").glob("*.xml")):
        t, items = list_items(p.read_bytes(), "admrul")
        total, rules = t, rules + items
    aids = {r["행정규칙ID"] for r in rules}
    check(len(rules) == total == len(aids), f"행정규칙 목록 {len(rules)} = totalCnt {total}, ID 고유 {len(aids)}")
    serial = {r["행정규칙ID"]: r["행정규칙일련번호"] for r in rules}
    wrong = []
    for p in (S / "admrul/body").glob("*.xml"):
        info = etree.parse(str(p)).getroot().find("행정규칙기본정보")
        if info.findtext("행정규칙ID") != p.stem or info.findtext("행정규칙일련번호") != serial.get(p.stem):
            wrong.append(p.stem)
    check({p.stem for p in (S / "admrul/body").glob("*.xml")} == aids and not wrong,
          f"행정규칙 본문 = 목록, ID · 일련번호 일치 (불일치 {len(wrong)})")

    for t, item, idf, body_id in (("prec", "prec", "판례일련번호", "판례정보일련번호"),
                                  ("expc", "expc", "법령해석례일련번호", "법령해석례일련번호"),
                                  ("decc", "decc", "행정심판재결례일련번호", "행정심판례일련번호")):
        want, excl = set(), set()
        for d in (S / "decisions" / t / "search").iterdir():
            pages = sorted(d.glob("*.xml"))
            got, n = 0, 0
            for p in pages:
                tc, items = list_items(p.read_bytes(), item)
                n, got = tc, got + len(items)
                for it in items:
                    (excl if t == "prec" and it.get("데이터출처명") != "대법원" else want).add(it[idf])
            if got != n:
                problems.append(f"{t} {d.name} 검색 {got} ≠ totalCnt {n}")
        have = {p.stem for p in (S / "decisions" / t / "body").glob("*.xml")}
        check(have == want, f"{t} 본문 {len(have):,} = 검색 고유(제외 출처 뺀) {len(want):,}, 제외만 {len(excl - want)}")
        wrong = [p.stem for p in (S / "decisions" / t / "body").glob("*.xml")
                 if etree.parse(str(p)).getroot().findtext(body_id) != p.stem]
        check(not wrong, f"{t} 파일 이름 = 응답 ID (불일치 {len(wrong)})")

    section("4. 내용 이상치")
    no_art = []
    for p in (S / "law/body").glob("*.xml"):
        r = etree.parse(str(p)).getroot()
        if not any(u.findtext("조문여부") == "조문" for u in r.iter("조문단위")):
            no_art.append(p.stem)
    check(not no_art, f"조문 없는 법령 {len(no_art)}")
    empty_rule = []
    kinds = Counter()
    for p in (S / "admrul/body").glob("*.xml"):
        r = etree.parse(str(p)).getroot()
        text_len = sum(len(e.text or "") for e in r.iter("조문내용")) + sum(len(e.text or "") for e in r.iter("별표내용"))
        if text_len < 200:
            empty_rule.append(p.stem)
            kinds[r.findtext("행정규칙기본정보/행정규칙종류")] += 1
    print(f"- 참고: 본문(조문 + 별표 텍스트) 200자 미만 행정규칙 {len(empty_rule)}개 {dict(kinds)} — 첨부파일에만 내용이 있는 고시(식품공전 등, 범위 밖)")
    for t, fields in (("prec", ("판례내용",)), ("expc", ("회답", "이유")), ("decc", ("주문", "이유"))):
        empty = Counter()
        dup = defaultdict(list)
        for p in (S / "decisions" / t / "body").glob("*.xml"):
            r = etree.parse(str(p)).getroot()
            for f in fields:
                if not (r.findtext(f) or "").strip():
                    empty[f] += 1
            dup[(r.findtext("사건번호") or r.findtext("안건번호") or "").strip()].append(p.stem)
        multi = {k: v for k, v in dup.items() if k and len(v) > 1}
        print(f"- 참고: {t} 빈 필드 {dict(empty) or '없음'}, 같은 사건 · 안건번호가 여러 ID인 번호 {len(multi)}개"
              f"({sum(map(len, multi.values()))}건)")
    rows_ = read_rows(S)
    dates = sorted(r["DSPS_DCSNDT"] for r in rows_ if r.get("DSPS_DCSNDT"))
    check(len(rows_) == len({r["DSPSDTLS_SEQ"] for r in rows_}) == 5417, f"식품 처분 {len(rows_):,}행 · ID 고유")
    print(f"- 참고: 처분 확정일 {dates[0]} ~ {dates[-1]}, 빈 근거 조항 {sum(not r['LAWORD_CD_NM'].strip() for r in rows_)}, "
          f"빈 위반 내용 {sum(not r['VILTCN'].strip() for r in rows_)}")

    print(f"\n## 결과: 문제 {len(problems)}건")
    for p in problems:
        print("  -", p)


if __name__ == "__main__":
    main()
