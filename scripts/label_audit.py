"""4-2 라벨 검수: 검수 파일 만들기와 판정 추출(PREREG 4-2 "라벨 검수" · "판정 기록", PLAN 1주차 D5 5단계).

잠그지 않는 도구다. 잠근 v1 문항 파일을 읽기만 하고 문항 선택에는 영향이 없다. 표시 칸은 검수자를 돕는 출력 형식이다.

    # 1차: T1 · T3 · T7 각 50 + T1 추가 10(가림 확인)
    python scripts/label_audit.py --stage 1
    # 2차: 1차에서 '틀림'이 1건 이상인 유형만, 같은 시드 순서의 다음 50
    python scripts/label_audit.py --stage 2 --type T7
    # 앉은 자리가 끝날 때마다: 판정 · 오류 파일 추출 + 대조 + 오류율 보고
    python scripts/label_audit.py --extract

파일:
- `eval/label_audit.csv`(git 제외): 원문 · 가린 문장 · 자동 정답 표시 · 판정 칸. 검수자가 `판정` · `오류` 칸을 채운다.
- `eval/label_audit_judgments.csv`(커밋): 문항 id, 유형, 생성 버전, 단계, 판정
- `eval/label_audit_errors.csv`(커밋): 문항 id, 생성 버전, 위치, 코드. 원문 · 메모 칸은 없다.

`오류` 칸 형식: `코드@위치`를 `;`로 잇는다(예: `split@3; answer@art:001234:15`). 위치는 T7 근거법령 줄 번호
(`정답 원천` 칸의 [N]) 또는 조문 uid이고, 없으면 `코드`만 쓴다. 위치에 다른 글자는 받지 않는다(커밋 파일에 원문이
옮겨지지 않게). `mask_miss`는 위치 없이 코드만 쓴다.

표본(PREREG 4-2):
- 유형별 test 주 평가 문항(`answer_deleted`가 `all`이 아닌 문항)을 id로 정렬해 `SEED:label_audit:{유형}:{버전}`으로 섞은 순서.
  1차 = 앞 50, 2차 = 그다음 50(겹치지 않음).
- T1 추가 10: 그 순서의 101번째부터(1차 · 2차 몫과 겹치지 않게) 원문에 조문 표기가 있던 문항(`meta.article_masked`) 10개.
  가린 문장만 보고(정답 칸 비움) 판정은 `가림만 확인`, 오류율 · Wilson 계산에서 뺀다.
"""

import argparse
import csv
import hashlib
import json
import math
import random
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "eval"))

SEED = 20261030  # PREREG CONSTANTS SEED(생성기와 같은 값, main에서 대조)
EVALSET = ROOT / "eval" / "evalset" / "v1" / "test.jsonl"
AUDIT = ROOT / "eval" / "label_audit.csv"
JUDGMENTS = ROOT / "eval" / "label_audit_judgments.csv"
ERRORS = ROOT / "eval" / "label_audit_errors.csv"

TYPES = ("T1", "T3", "T7")
N_STAGE = 50
N_T1_EXTRA = 10
STAGE1, STAGE2, EXTRA = "1차", "2차", "T1 추가 10(가림 확인)"
REGEN1, REGEN2 = "재생성 후 1차", "재생성 후 2차"
VERDICTS = {"맞음", "틀림"}
EXTRA_VERDICT = "가림만 확인"
RATE_CODES = {"split", "resolve", "answer", "source_typo"}  # 오류율 · 2단계에 넣음
MASK_CODES = {"mask_over", "mask_left_article", "mask_unclear", "mask_miss"}
COUNT_CODES = MASK_CODES | {"par_unit"}  # 따로 건수만
CODES = RATE_CODES | COUNT_CODES
_UID = re.compile(r"^(?:art|par|item|anx):[0-9A-Za-z_\-]+(?::[0-9A-Za-z_\-]+)*$")
_LINE = re.compile(r"^\d{1,3}$")

COLUMNS = ["순서", "문항 id", "유형", "단계", "생성 버전", "질문(가리기 전)", "질문(조문 가림)", "질문(법령명까지 가림)",
           "정답 원천", "자동 정답(조, 주 지표)", "정답 단위(항 보조 지표)", "처분(정답에서 뺌)", "해석 못 함", "참고",
           "판정", "오류", "_줄 출처", "_정답 줄 출처"]
# T7 문항의 정답 조문이 나온 줄 종류(PREREG 4-2 라벨 검수, 2026-11-25): 출처별로 검수 문항 수 · 틀림 수를 따로 센다
SRC_KINDS = ("표지없음", "표지", "섞임")


# ---------- 표본 ----------

def version_label(evalset_bytes: bytes, label: str = "v1") -> str:
    return f"{label} {hashlib.sha256(evalset_bytes).hexdigest()[:12]}"


def audit_order(items: list[dict], typ: str, label: str) -> list[str]:
    pool = sorted(x["id"] for x in items if x["type"] == typ and x["split"] == "test"
                  and x["meta"].get("answer_deleted") != "all")
    random.Random(f"{SEED}:label_audit:{typ}:{label}").shuffle(pool)
    return pool


def stage_ids(items: list[dict], typ: str, stage: int, label: str) -> list[str]:
    order = audit_order(items, typ, label)
    if len(order) < N_STAGE * stage:
        raise SystemExit(f"{typ}: 주 평가 test {len(order)}문항이라 {stage}차 {N_STAGE}개를 뽑을 수 없다")
    return order[N_STAGE * (stage - 1):N_STAGE * stage]


def t1_extra_ids(items: list[dict], label: str) -> list[str]:
    by_id = {x["id"]: x for x in items}
    order = audit_order(items, "T1", label)
    picked = [i for i in order[2 * N_STAGE:] if by_id[i]["meta"].get("article_masked")][:N_T1_EXTRA]
    if len(picked) < N_T1_EXTRA:
        raise SystemExit(f"T1 추가: 1 · 2차 몫 밖에 가린 문항이 {len(picked)}개뿐이다(필요 {N_T1_EXTRA})")
    return picked


# ---------- 판정 읽기 · 대조 ----------

def parse_errors(cell: str) -> list[tuple[str, str]]:
    out = []
    for tok in re.split(r"[;\n]", cell or ""):
        tok = tok.strip()
        if not tok:
            continue
        code, _, pos = tok.partition("@")
        out.append((code.strip(), pos.strip()))
    return out


def check_row(row: dict) -> tuple[list[tuple[str, str]], list[str]]:
    """한 행의 판정 · 오류 칸을 읽고 문제 목록을 돌려준다(빈 목록이면 통과)."""
    rid, stage, verdict = row["문항 id"], row["단계"], (row.get("판정") or "").strip()
    errs = parse_errors(row.get("오류", ""))
    probs = []
    lines = {k.split("=")[0] for k in (row.get("_줄 출처") or "").split(";") if k}
    for code, pos in errs:
        if code not in CODES:
            probs.append(f"{rid}: 모르는 코드 '{code}'")
            continue
        if pos and not (_LINE.match(pos) or _UID.match(pos)):
            probs.append(f"{rid}: {code} 위치는 줄 번호나 조문 uid만 받는다")
        if code == "mask_miss" and pos:
            probs.append(f"{rid}: mask_miss는 위치 없이 코드만 쓴다")
        if code == "split":
            if row["유형"] != "T7":
                probs.append(f"{rid}: split은 T7 근거법령 줄 오류에만 쓴다")
            elif not _LINE.match(pos) or pos not in lines:
                probs.append(f"{rid}: split 위치는 '정답 원천'의 줄 번호여야 한다")
    rate = [c for c, _ in errs if c in RATE_CODES]
    if stage == EXTRA:
        if verdict not in ("", EXTRA_VERDICT):
            probs.append(f"{rid}: T1 추가 10의 판정은 '{EXTRA_VERDICT}'만 받는다")
        if any(c not in MASK_CODES for c, _ in errs):
            probs.append(f"{rid}: T1 추가 10에는 가림 코드만 적는다")
    else:
        if verdict not in ("", *VERDICTS):
            probs.append(f"{rid}: 판정은 '맞음' / '틀림'만 받는다")
        if verdict == "맞음" and rate:
            probs.append(f"{rid}: '맞음'인데 오류율 코드 {rate}가 있다")
        if verdict == "틀림" and not rate:
            probs.append(f"{rid}: '틀림'인데 오류율 코드(split · resolve · answer · source_typo)가 없다")
    if errs and not verdict:
        probs.append(f"{rid}: 오류는 적었는데 판정이 비었다")
    return errs, probs


def wilson(k: int, n: int, z: float = 1.959963984540054) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (max(0.0, (c - h) / d), min(1.0, (c + h) / d))


def branch(rate: float) -> str:
    if rate <= 0.05:
        return "5% 이하: 그대로 진행"
    if rate <= 0.15:
        return "5% 초과 15% 이하: 규칙 수정(AMENDMENTS) · 그 유형 재생성 · 새 표본 2단계"
    return "15% 초과: 핵심 성과 2 헤드라인에서 빼고 보조로만"


def read_audit(path: Path) -> tuple[list[dict], str]:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp949"):  # 엑셀이 ANSI로 다시 저장한 경우
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise SystemExit(f"{path}: utf-8 · cp949로 읽지 못했다")
    rows = list(csv.DictReader(text.splitlines(keepends=True)))
    missing = [c for c in ("문항 id", "유형", "단계", "생성 버전", "판정", "오류", "_줄 출처") if rows and c not in rows[0]]
    if missing:
        raise SystemExit(f"{path}: 칸이 없다 {missing}")
    return rows, enc


def extract(rows: list[dict]) -> dict:
    """판정 · 오류 파일 내용(원문 없음)과 보고 수치. 문제가 있으면 problems에 모은다."""
    judg, errs, problems = [], [], []
    seen = Counter((r["문항 id"], r["생성 버전"]) for r in rows)
    problems += [f"{i}: 같은 버전에 두 번 있다" for (i, _), n in seen.items() if n > 1]
    stats: dict = defaultdict(lambda: {"total": 0, "judged": 0, "wrong": 0})
    counts: dict = defaultdict(Counter)
    split_by_kind: dict = defaultdict(Counter)
    by_source: dict = defaultdict(lambda: {"judged": 0, "wrong": 0})  # (유형, 버전, 정답 줄 출처), T1 추가 10 제외
    mask_miss = []
    for r in rows:
        e, p = check_row(r)
        problems += p
        key = (r["유형"], r["생성 버전"], r["단계"])
        stats[key]["total"] += 1
        verdict = (r.get("판정") or "").strip()
        if not verdict:
            continue
        stats[key]["judged"] += 1
        if verdict == "틀림":
            stats[key]["wrong"] += 1
        src = (r.get("_정답 줄 출처") or "").strip()
        if src and r["단계"] != EXTRA:
            by_source[(r["유형"], r["생성 버전"], src)]["judged"] += 1
            by_source[(r["유형"], r["생성 버전"], src)]["wrong"] += verdict == "틀림"
        judg.append([r["문항 id"], r["유형"], r["생성 버전"], r["단계"], verdict])
        kinds = dict(k.split("=") for k in (r.get("_줄 출처") or "").split(";") if "=" in k)
        for code, pos in e:
            errs.append([r["문항 id"], r["생성 버전"], pos, code])
            if code in COUNT_CODES:
                counts[r["유형"]][code] += 1
            if code == "split":
                split_by_kind[r["유형"]][kinds.get(pos, "?")] += 1
            if code == "mask_miss":
                mask_miss.append(r["문항 id"])
    return {"judgments": sorted(judg), "errors": sorted(errs), "problems": problems, "stats": dict(stats),
            "counts": {k: dict(v) for k, v in counts.items()},
            "split_by_kind": {k: dict(v) for k, v in split_by_kind.items()}, "mask_miss": mask_miss,
            "by_source": dict(by_source)}


def rate_lines(stats: dict) -> list[str]:
    """유형 · 버전마다 최종 표본(1차 오류 0이면 50, 아니면 1 · 2차 100)의 오류율 · Wilson 구간 · 분기."""
    out = []
    by = defaultdict(dict)
    for (typ, ver, stage), s in stats.items():
        by[(typ, ver)][stage] = s
    for (typ, ver), st in sorted(by.items()):
        first = st.get(STAGE1) or st.get(REGEN1)
        second = st.get(STAGE2) or st.get(REGEN2)
        if not first:
            continue
        head = f"{typ} ({ver})"
        if first["judged"] < first["total"]:
            out.append(f"{head}: 1차 판정 {first['judged']} / {first['total']} 진행 중")
            continue
        if first["wrong"] == 0:
            k, n, note = 0, first["total"], "1차 오류 0 → 50으로 확정"
        elif not second:
            out.append(f"{head}: 1차 틀림 {first['wrong']} / {first['total']} → 2차 50 필요"
                       f"(`--stage 2 --type {typ}`)")
            continue
        elif second["judged"] < second["total"]:
            out.append(f"{head}: 1차 틀림 {first['wrong']}, 2차 판정 {second['judged']} / {second['total']} 진행 중")
            continue
        else:
            k, n, note = first["wrong"] + second["wrong"], first["total"] + second["total"], "1 · 2차 100으로 확정"
        lo, hi = wilson(k, n)
        out.append(f"{head}: 틀림 {k} / {n} = {k / n:.1%}, Wilson 95% [{lo:.1%}, {hi:.1%}] · {note} · {branch(k / n)}")
    return out


def write_csv(path: Path, header: list[str], rows: list[list[str]]) -> bytes:
    import io
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    w.writerows(rows)
    b = buf.getvalue().encode("utf-8")
    path.write_bytes(b)  # 바이트로 써서 OS 줄 끝 변환이 해시를 바꾸지 않게
    return b


# ---------- 표시(스냅숏 필요) ----------

class View:
    """검수 파일 표시용. 생성기 `load_world`로 같은 스냅숏을 읽는다(업소 정보 열 없음, 본문 가림 적용)."""

    def __init__(self, snapshot: Path):
        import build_evalset as B
        import grouping as G
        from mfds import disposition as D
        self.B, self.G, self.D = B, G, D
        self.w = B.load_world(snapshot)
        arts = self.w.articles
        self.known = set(arts) | {u for a in arts.values() for u, _ in a.units if u.startswith("par:")}

    # 이름 · 표시
    def name(self, uid: str) -> str:
        kind, law, art, *rest = uid.split(":")
        m, *b = art.split("-")
        s = f"{self.w.lawdict.title.get(law, law)} 제{m}조" + (f"의{b[0]}" if b else "")
        if kind == "par":
            s += f"제{rest[0]}항"
        elif kind == "item":
            s += (f"제{rest[0]}항" if rest[0] != "0" else "") + f"제{rest[1]}호"
        return s

    def deleted_note(self, a: str) -> str:
        art = self.w.articles.get(a)
        return f"   ⚠ 현행 그래프에서 삭제된 조문(본문: {art.text.strip()[:16]})" if art and art.deleted else ""

    def answer_lines(self, item: dict) -> str:
        refs = item["answer"].get("refs") or []
        out = []
        for a in item["answer"]["uids"]:
            src = [u for u in refs if u.split(":")[1:3] == a.split(":")[1:3]]
            raw = ", ".join(self.name(u) for u in src) or self.name(a)
            out.append(f"- {self.name(a)}   ← 원문 인용(번호 표기 정리): {raw}{self.deleted_note(a)}")
        return "\n".join(out)

    def unit_line(self, item: dict) -> str:
        tagged = unit_tags(item["answer"].get("refs") or [], self.w.articles, self.known)
        if not any(t == "항" for _, t in tagged):
            kind = "대상 아님(정답 단위가 모두 조)"
        else:
            kind = "순수" if all(t == "항" for _, t in tagged) else "섞임"
        return f"({kind}) " + (", ".join(f"{self.name(u)} [{t}]" for u, t in tagged) or "(없음)")

    @staticmethod
    def fails(failures) -> str:
        reason = {"unknown_law": "사전에 없는 법령(고시 · 법령명 오기 등)", "no_context": "앞에 법령명이 없는 제N조(맥락 없음)",
                  "not_mfds": "타 부처 법령", "no_article": "없는 조문(오타 가능)"}
        uniq = dict.fromkeys((f.raw, f.reason) for f in failures)
        return " | ".join(f"{raw} [{reason.get(rs, rs)}]" for raw, rs in uniq)

    # T7 줄별(생성기 basis_articles와 같은 규칙으로 다시 나눠 표시만 한다)
    def t7_lines(self, text: str):
        D, R = self.D, self.w.resolver
        out, last = [], {}
        for label, line in D.split_basis(text):
            prev = last.get(label)
            res = R.resolve(f"{prev} {line}" if prev else line)
            base = set(R.resolve(prev).article_uids) if prev else set()
            a = [u for u in res.article_uids if u not in base]
            fails = res.failures[len(R.resolve(prev).failures) if prev else 0:]
            last[label] = line
            final = label
            if label == "unlabeled":
                final = "disposition" if a and set(a) <= D.DISPOSITION_ARTICLES else "violation"
            out.append((label, final, line, fails, a))
        return out

    @staticmethod
    def answer_source(kinds: set) -> str:
        """정답 조문을 낸 줄 종류 집합 → 표지없음 · 표지 · 섞임(정답 줄이 없으면 빈 문자열)."""
        return "섞임" if len(kinds) > 1 else next(iter(kinds), "")

    def row(self, item: dict, order: int, stage: str, version: str) -> dict:
        typ, src = item["type"], item["meta"]["source"]
        r = {"순서": order, "문항 id": item["id"], "유형": typ, "단계": stage, "생성 버전": version,
             "질문(가리기 전)": item["question_unmasked"], "질문(조문 가림)": item["question"],
             "질문(법령명까지 가림)": item["question_law_masked"], "판정": "", "오류": "", "_줄 출처": "",
             "_정답 줄 출처": ""}
        note = [f"묶음 구성원 {len(item['meta']['members'])}건(질문은 첫 구성원)", f"answer_deleted={item['meta']['answer_deleted']}",
                f"조문 가림 {'있음' if item['meta'].get('article_masked') else '없음'}"]
        r["참고"] = " · ".join(note)
        if stage == EXTRA:  # 가린 문장만 본다
            r.update({"정답 원천": "(T1 추가 10: 정답은 보지 않음)", "자동 정답(조, 주 지표)": "", "정답 단위(항 보조 지표)": "",
                      "처분(정답에서 뺌)": "", "해석 못 함": ""})
            return r
        R = self.w.resolver
        r["자동 정답(조, 주 지표)"] = self.answer_lines(item)
        r["정답 단위(항 보조 지표)"] = self.unit_line(item)
        r["처분(정답에서 뺌)"] = ""
        # 자동 정답은 묶음 구성원 정답의 합집합이라 구성원마다 원천을 보인다(같은 문자열은 한 번만)
        members = item["meta"]["members"]
        field = {"T1": (self.w.disp, "LAWORD_CD_NM"), "T3": (self.w.precs, "참조조문"),
                 "T7": (self.w.drug_disp, "BEF_APPLY_LAW")}[typ]
        texts: dict[str, list[int]] = {}
        for k, m in enumerate(members, 1):
            t = field[0][m].get(field[1]) or ""
            texts.setdefault(self.G.clean_html(t) if typ == "T3" else t, []).append(k)

        def head(ks):
            return "" if len(members) == 1 else f"(구성원 {', '.join(map(str, ks))}) "

        blocks, fails = [], []
        if typ == "T1":
            for text, ks in texts.items():
                blocks.append(f"{head(ks)}근거 조항: {text}")
                fails += R.resolve(text).failures
        elif typ == "T3":
            for text, ks in texts.items():
                res = R.resolve(text)
                hist = list(dict.fromkeys(x.article_uid for x in res.refs if x.historical and x.article_uid))
                blocks.append(f"{head(ks)}참조조문: {text}" + (
                    "\n(구법 표시라 정답에서 뺀 조문: " + ", ".join(self.name(a) for a in hist) + ")" if hist else ""))
                fails += res.failures
        else:
            kor = {"violation": "위반", "disposition": "처분", "unlabeled": "표지 없음"}
            kinds, disp, glued_from, n, ans_kinds = [], [], set(), 0, set()
            tail = re.compile(r"\s*제\s*\d+\s*조$")
            for text, ks in texts.items():
                lines = []
                for label, final, line, fl, arts in self.t7_lines(text):
                    n += 1
                    if final == "violation" and set(arts) - self.D.DISPOSITION_ARTICLES:
                        ans_kinds.add("표지없음" if label == "unlabeled" else "표지")
                    tag = kor[label] if label != "unlabeled" else f"표지 없음 → {kor[final]}"
                    warn = ""
                    if self.D.glued_article(line):
                        warn = "   ⚠ 항 · 호 뒤 조가 붙음(오타 의심, " + (
                            "이 처분은 문항에서 빠짐)" if final == "violation" else "처분 쪽이라 정답 무관)")
                        if final == "disposition":  # 표시 전용 재해석(disposition.py는 그대로)
                            cleaned = self.D._GLUED_ARTICLE.sub(lambda m: tail.sub("", m.group(0)), line)
                            glued_from |= set(R.resolve(line).article_uids) - set(R.resolve(cleaned).article_uids)
                    lines.append(f"[{n}] [{tag}] {line}{warn}")
                    kinds.append(f"{n}={'표지없음' if label == 'unlabeled' else '표지'}")
                    fails += list(fl)
                blocks.append(head(ks) + "근거법령(줄별, [번호]가 split 위치):\n" + "\n".join(lines))
                disp += self.D.basis_articles(text, R.resolve)["disposition"]
            r["_줄 출처"] = ";".join(kinds)
            r["_정답 줄 출처"] = self.answer_source(ans_kinds)
            r["처분(정답에서 뺌)"] = ", ".join(
                self.name(a) + ("(오타 의심 표기에서 읽힘)" if a in glued_from else "") for a in dict.fromkeys(disp)) or "(없음)"
        r["정답 원천"] = "\n\n".join(blocks)
        r["해석 못 함"] = self.fails(fails)
        return r


def unit_tags(refs, arts, known):
    """원래 참조들 → [(정답 단위 uid, 태그)]. 같은 조에 항 정답이 있으면 조 단위는 버린다(PREREG 4-5 항 보조 지표).
    태그(판정 순서): 항 구조 없음 → 원문이 항 없이 호만 → 원문이 적은 항이 그래프에 없음 → 원문이 조까지만."""
    from mfds.load.graph import article_or_paragraph

    def has_par(art_uid):
        a = arts.get(art_uid)
        return bool(a) and any(u.startswith("par:") for u, _ in a.units)

    def ref_kind(u):
        kind, law, art, *rest = u.split(":")
        art_uid = f"art:{law}:{art}"
        if kind in ("par", "item") and rest and rest[0] != "0" and f"par:{law}:{art}:{rest[0]}" in known:
            return "par_ok"
        if not has_par(art_uid):
            if kind == "art":
                return "A_art"
            return "A" if rest[0] == "0" else "C_nopar"
        if kind == "art":
            return "D"
        return "B" if rest[0] == "0" else "C_miss"

    order = [("항 구조 없음", {"A", "A_art", "C_nopar"}), ("원문이 항 없이 호만", {"B"}),
             ("원문이 적은 항이 그래프에 없음", {"C_miss"}), ("원문이 조까지만", {"D"})]
    units: dict = {}
    for u in refs:
        if u.startswith("anx:"):
            continue
        x = article_or_paragraph(u, known)
        if x:
            units.setdefault(x, []).append(u)
    pars = [x for x in units if x.startswith("par:")]
    par_arts = {"art:" + ":".join(p.split(":")[1:3]) for p in pars}
    out = [(p, "항") for p in pars]
    for x, origs in units.items():
        if not x.startswith("art:") or x in par_arts:
            continue
        kinds = {ref_kind(o) for o in origs}
        for name, ks in order:
            if kinds & ks:
                tag = "조: " + name
                if name == "항 구조 없음" and "C_nopar" in kinds:
                    wrote = sorted({o.split(":")[3] for o in origs if ref_kind(o) == "C_nopar"}, key=lambda s: (len(s), s))
                    tag += f"(원문은 제{'·'.join(wrote)}항 표기, 이 조문엔 항 노드 없음)"
                out.append((x, tag))
                break
    return out


# ---------- 실행 ----------

def load_items(path: Path) -> tuple[list[dict], bytes]:
    b = path.read_bytes()
    return [json.loads(x) for x in b.decode("utf-8").splitlines() if x.strip()], b


def write_audit(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as f:  # 엑셀이 한글을 바로 읽게 BOM
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)


def make(args) -> None:
    items, b = load_items(Path(args.evalset))
    version = version_label(b, args.label)
    by_id = {x["id"]: x for x in items}
    path = Path(args.out)
    existing, _ = read_audit(path) if path.exists() else ([], "")
    regen = args.label != "v1"
    picks: list[tuple[str, str]] = []  # (id, 단계)
    if args.stage == 1:
        if any(r["생성 버전"] == version for r in existing):
            raise SystemExit(f"{path}에 이미 {version} 행이 있다(판정을 덮어쓰지 않는다)")
        for typ in TYPES:
            picks += [(i, REGEN1 if regen else STAGE1) for i in stage_ids(items, typ, 1, args.label)]
        if not regen:
            picks += [(i, EXTRA) for i in t1_extra_ids(items, args.label)]
    else:
        if not args.type:
            raise SystemExit("--stage 2에는 --type이 필요하다")
        first_stage = REGEN1 if regen else STAGE1
        first = [r for r in existing if r["유형"] == args.type and r["생성 버전"] == version and r["단계"] == first_stage]
        if len(first) != N_STAGE:
            raise SystemExit(f"{args.type} {version} 1차 행이 {len(first)}개다(필요 {N_STAGE})")
        if any(not (r.get("판정") or "").strip() for r in first):
            raise SystemExit(f"{args.type} 1차 판정이 아직 다 끝나지 않았다")
        if not any(r["판정"].strip() == "틀림" for r in first):
            raise SystemExit(f"{args.type} 1차에 '틀림'이 없어 2차를 뽑지 않는다(50으로 확정)")
        if any(r["유형"] == args.type and r["생성 버전"] == version and r["단계"] in (STAGE2, REGEN2) for r in existing):
            raise SystemExit(f"{args.type} 2차 행이 이미 있다")
        picks = [(i, REGEN2 if regen else STAGE2) for i in stage_ids(items, args.type, 2, args.label)]
    view = View(ROOT / args.snapshot)
    new = [view.row(by_id[i], k + 1, stage, version) for k, (i, stage) in enumerate(picks)]
    write_audit(path, existing + new)
    c = Counter((r["유형"], r["단계"]) for r in new)
    print(f"{path.relative_to(ROOT) if path.is_relative_to(ROOT) else path}: {len(new)}행 추가({version})")
    for (typ, stage), n in sorted(c.items()):
        print(f"  {typ} {stage}: {n}")


def do_extract(args) -> int:
    rows, enc = read_audit(Path(args.out))
    r = extract(rows)
    print(f"# 라벨 검수 추출 ({len(rows)}행, {enc})")
    if r["problems"]:
        print(f"문제 {len(r['problems'])}건 — 판정 · 오류 파일을 쓰지 않는다:")
        for p in r["problems"]:
            print(f"  - {p}")
        return 1
    jb = write_csv(Path(args.judgments), ["문항 id", "유형", "생성 버전", "단계", "판정"], r["judgments"])
    eb = write_csv(Path(args.errors), ["문항 id", "생성 버전", "위치", "코드"], r["errors"])
    for (typ, ver, stage), s in sorted(r["stats"].items()):
        print(f"- {typ} {stage} ({ver}): 판정 {s['judged']} / {s['total']}, 틀림 {s['wrong']}")
    print("## 오류율(최종 표본, T1 추가 10 제외)")
    for line in rate_lines(r["stats"]):
        print(f"- {line}")
    print("## 따로 세는 코드")
    for typ, c in sorted(r["counts"].items()):
        print(f"- {typ}: " + ", ".join(f"{k} {v}" for k, v in sorted(c.items())))
    for typ, c in sorted(r["split_by_kind"].items()):
        print(f"- {typ} split 줄 출처: 표지 줄 {c.get('표지', 0)} · 표지 없는 줄 {c.get('표지없음', 0)}")
    for typ, ver in sorted({(t, v) for t, v, _ in r["by_source"]}):
        parts = [f"{k} 판정 {r['by_source'][(typ, ver, k)]['judged']} · 틀림 {r['by_source'][(typ, ver, k)]['wrong']}"
                 for k in SRC_KINDS if (typ, ver, k) in r["by_source"]]
        print(f"- {typ} 정답 줄 출처별({ver}, 분모 = 그 출처로 분류돼 판정을 마친 문항): " + " / ".join(parts))
    if r["mask_miss"]:
        print(f"!! mask_miss {len(r['mask_miss'])}건 {r['mask_miss']}: PREREG 4-2 조치 순서대로 그 원천의 API 전송을 멈추고 "
              "가림 규칙을 `prereg.py amend --kind fix`로 고친 뒤 다시 생성 · 적재하고 AMENDMENTS에 기록한다")
    print("## 해시(오류율을 확정하는 날 AMENDMENTS에 기록)")
    for p, b in ((args.judgments, jb), (args.errors, eb)):
        print(f"{hashlib.sha256(b).hexdigest()}  {Path(p).relative_to(ROOT).as_posix() if Path(p).is_relative_to(ROOT) else p}")
    return 0


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--stage", type=int, choices=(1, 2))
    g.add_argument("--extract", action="store_true")
    ap.add_argument("--type", choices=TYPES)
    ap.add_argument("--label", default="v1", help="생성 버전 이름(v1 / 재생성 N)")
    ap.add_argument("--evalset", default=str(EVALSET))
    ap.add_argument("--snapshot", default="data/raw/20261105")
    ap.add_argument("--out", default=str(AUDIT))
    ap.add_argument("--judgments", default=str(JUDGMENTS))
    ap.add_argument("--errors", default=str(ERRORS))
    args = ap.parse_args()
    import prereg
    if prereg.read_constants()["SEED"] != SEED:
        raise SystemExit("SEED가 PREREG CONSTANTS와 다르다")
    if args.extract:
        sys.exit(do_extract(args))
    make(args)


if __name__ == "__main__":
    main()
