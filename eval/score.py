"""검색 · 답변 채점(PLAN B5 `eval/score.py`, PREREG 4-2 · 4-5). 2주차 D4 저녁 동결 때 해시를 AMENDMENTS에 기록한다.

Text2Cypher(4-3 결과 일치 · 거절 세트 쿼리 채점)는 따로 `eval/score_cypher.py`(PLAN B4)다. 구간 · 검정은 `eval/stats.py`.
건수 · 비율만 출력한다(문항 문장 · 답변 본문은 출력하지 않음).

    python eval/score.py --evalset eval/evalset/v1 --split test \\
        --retrieval vector=runs/vector.jsonl --retrieval graph=runs/graph.jsonl --compare graph:vector \\
        --answers local=runs/4b.jsonl --answers api=runs/api.jsonl --ratio local:api --out <파일>.json

## 입력(실행 결과 jsonl, `eval/run.py`가 쓰는 형식)

- 검색: 문항마다 `{"id", "ranked": [근거 uid, ...]}`. 순위 목록은 원래 단위(항 · 호 · 별표 청크 포함) 그대로 둔다.
- 답변: 문항마다 `{"id", "backend": "local" | "api", "status", "retries", "answer", "subgraph"}`.
  - `status`: `ok` · `truncated`(최대 출력에 걸림) · `format_error`(JSON 파싱 실패) · `error`(재시도 뒤에도 실행 실패,
    타임아웃 · 서버 오류 포함 — `run.py`가 이 값으로 옮긴다) · `policy_refusal`(벤더 정책 거절 신호). 이 밖의 값이면 중단한다.
  - `backend`는 한 실행 안에서 하나여야 한다. `--ratio 로컬:API`의 두 구성은 각각 `local` · `api`여야 한다.
- **입력 검사(중단):** 같은 id가 두 번 · 평가셋 split에 없는 id · 필수 문항 누락. 필수 문항은 검수 `틀림`이 아니고 정답
  미확정(T4-M4 정답 없음: M4 파일이 없거나 미선정 · 예비)이 아닌 문항이다. 검색은 그중 정답이 있는 문항(T6 · 거절 세트 ·
  T2 음성 제외), 답변은 전부다. 정답 미확정 문항은 실행에 있어도 되고, 모든 집계에서 빼고 수만 적는다.
  - `answer`: PLAN B3 답변 JSON `{answer, citations, verdict, number, refuse}`. `subgraph`: 그 문항 서브그래프의 근거 ID 목록.

## 규칙(PREREG)

- **단위:** 순위 목록 · 답변 인용을 조문 단위로 바꾼다(항 · 호 → 조문, 별표 청크 `#k` → 별표, DurRule 그대로), 순서를 지키며
  중복을 지운 뒤 앞 5개를 자른다. 정답도 같은 단위다.
- **정답:** T1 · T3 · T7 = 현행 정답(`answer.uids` − `answer.deleted`), T2 = 양성만 `rule_ids`, T4-M1 · M2 = `answer.uids`,
  T4-M3 = `answer.evidence`(DurRule 집합), T4-M4 = `--m4-answers`(사용자 M4 검수 결과, 없으면 "정답 미확정"으로 빼고 셈).
  T6은 검색 지표 없이 답변 숫자로만, 거절 세트(T5)는 답변 거절로만 본다.
- **삭제 조문(4-2):** 일부 삭제(`some`)는 현행 정답으로 채점하고 수를 따로 적는다. 전부 삭제(`all`)는 주 평가에서 빼고
  "삭제 조문 정답 문항" 줄(원래 정답 기준 hit@5)로 보고한다. 삭제 조문(스냅숏 `Article.deleted`, 정답이 아닌 것 포함) 인용은
  앞 5개 자리를 차지하되 적중 · recall · 정밀도에서 중립이다(정밀도 분자 · 분모에서 모두 뺌). 방식별 삭제 조문 인용 수를 적는다.
- **항 보조 지표(4-5):** 정답 참조(`answer.refs`)와 답변 인용 앞 5개를 항까지 올린다(호 → 항, 항 없는 조의 호 → 조,
  그래프에 없는 항 → 조. 라벨 검수 파일과 같은 `mfds.load.graph.article_or_paragraph`).
  같은 조에 항 정답이 있으면 그 조의 조 정답은 버린다. 정답 단위에 항이 없는 문항은 대상이 아니다. 순수 · 섞임 · 합산.
- **실행 실패(4-5):** `truncated` · `format_error`는 오답, 로컬 `error`도 오답. API `error` · `policy_refusal`은 짝에서 양쪽
  모두 뺀다. API 대비 비율에는 S1(뺀 문항을 API 오답으로 다시 넣음) · S2(API 잘림 문항을 양쪽에서 뺌) 두 민감도 행을 둔다.
  뺀 문항이 5%를 넘는 유형이 있으면 "전체(기술 통계 유형 제외)" 행을 더한다(342행). 기본 행 옆에는 같은 짝 문항의 로컬 ·
  API 인용 정밀도를 둔다(343행, 한쪽이라도 중립(`None`)이면 양쪽에서 빼고 수를 적음. 정밀도 비율은 내지 않음).
- **근거 없는 주장 비율(327행, PLAN B3):** 인용 단위가 대표다. 답변 인용 앞 5개(중복 제거 전, 스키마가 5개로 제한) 중
  서브그래프 근거 ID 목록에 **문자열 그대로** 없는 ID의 비율(조문 단위로 올려 비교하지 않음).
- **생성 버전 표기(PREREG 4-2 · 수정 절차):** 판정 파일의 생성 버전 칸은 `<라벨> <test.jsonl sha256 앞 12자>`이고, 라벨은
  `v1` 또는 `재생성 N`(184행 문구 그대로)이다. 라벨 → 디렉터리는 `v1` → `v1`, `재생성 N` → `v1-r{N}`(413행)이다(`label_dir`).
- **검수 `틀림` 제외(4-2 J11):** `--judgments`(커밋된 `label_audit_judgments.csv`)의 `틀림` 문항을, 판정한 버전 jsonl 줄에서
  `id`만 뺀 키 정렬 JSON sha256(내용 값)으로 채점 버전에서 찾아 모든 구성에서 똑같이 뺀다. 구어체 짝(`:c`)도 함께 뺀다.
  구어체 검수 판정(`colloquial_judgments.csv`)은 구어체 파일 형식이 정해지면 같은 `content_key`로 연결한다.
"""

import argparse
import csv
import hashlib
import json
import math
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "eval"))
import prereg  # noqa: E402
import stats  # noqa: E402

K = 5
CASE_TYPES = ("T1", "T3", "T7")
CITE_TYPES = ("T1", "T3", "T7", "T2", "T4-M1", "T4-M2", "T4-M3", "T4-M4")  # 검색 지표 · 인용률 대상
NO_TEST = ("T7",)  # 기술 통계만(4-2 T7): 비교 검정을 하지 않는다
WRONG_STATUS = ("truncated", "format_error")
API_DROP = ("error", "policy_refusal")
STATUSES = {"ok", *WRONG_STATUS, *API_DROP}
LABEL = re.compile(r"^(?:v1|재생성 ([1-9]\d*))$")


def label_dir(label: str) -> str:
    """생성 버전 라벨(PREREG 184행 `v1` / `재생성 N`) → 평가셋 디렉터리 이름(413행 `v1` / `v1-r{N}`). 형식 밖이면 중단."""
    m = LABEL.match(label)
    if not m:
        raise SystemExit(f"생성 버전 라벨 형식 밖: {label!r}(v1 또는 '재생성 N')")
    return "v1" if m.group(1) is None else f"v1-r{m.group(1)}"


# ---------------------------------------------------------------- 단위


def article_unit(uid: str) -> str:
    """항 · 호 → 조문, 별표 청크 → 별표. 그 밖(조문 · 고시 조문 · DurRule 등)은 그대로."""
    p = uid.split(":")
    if p[0] in ("par", "item") and len(p) >= 3:
        return f"art:{p[1]}:{p[2]}"
    if p[0] == "anx":
        return uid.split("#", 1)[0]
    return uid


def par_unit(uid: str, known: set[str] | None = None) -> str | None:
    """항 보조 지표 단위: 호 → 그 호의 항(항 자리 0, 곧 항 없는 조의 호면 조), 그래프에 없는 항 → 조
    (`mfds.load.graph.article_or_paragraph`, 라벨 검수 파일의 정답 단위와 같은 함수). 조 · 항이 아니면 None.
    `known`(스냅숏의 조 · 항 uid)이 없으면 호 → 항만 한다(단위 테스트용)."""
    if known is not None:
        from mfds.load.graph import article_or_paragraph
        return article_or_paragraph(uid, known)
    p = uid.split(":")
    if p[0] == "item" and len(p) >= 5:
        return f"art:{p[1]}:{p[2]}" if p[3] == "0" else f"par:{p[1]}:{p[2]}:{p[3]}"
    return uid if p[0] in ("art", "par") else None


def top_units(ranked: list[str], k: int = K) -> list[str]:
    out: list[str] = []
    for u in ranked:
        a = article_unit(u)
        if a not in out:
            out.append(a)
        if len(out) == k:
            break
    return out


# ---------------------------------------------------------------- 정답


@dataclass
class Gold:
    cur: set[str]               # 채점 정답(현행)
    orig: set[str] = field(default_factory=set)  # 원래 정답(전부 삭제 줄)
    status: str = "main"        # main · all_deleted · pending(정답 미확정)


def gold(item: dict, m4: dict | None = None) -> Gold | None:
    """검색 · 인용 정답. 대상이 아니면 None(T2 음성 · T6 · 거절 세트)."""
    t, a = item["type"], item.get("answer")
    if t in CASE_TYPES:
        orig = {article_unit(u) for u in a["uids"]}
        if item["meta"].get("answer_deleted") == "all":
            return Gold(set(), orig, "all_deleted")
        return Gold(orig - {article_unit(u) for u in a["deleted"]}, orig)
    if t == "T2":
        return Gold(set(a["rule_ids"])) if item["meta"].get("polarity") == "pos" else None
    if t == "T4-M3":
        return Gold(set(a["evidence"]))
    if t in ("T4-M1", "T4-M2"):
        return Gold({article_unit(u) for u in a["uids"]})
    if t == "T4-M4":
        got = (m4 or {}).get(item["id"])
        return Gold({article_unit(u) for u in got}) if got else Gold(set(), status="pending")
    return None


def par_gold(item: dict, known: set[str] | None = None) -> tuple[set[str], str] | None:
    """항 보조 지표 정답 단위와 '순수' · '섞임'. 대상 아니면 None(정답 단위가 모두 조 · 전부 삭제 · 사례 유형 아님).
    삭제 조문 참조는 뺀다(4-5 규모 수치와 같은 기준)."""
    if item["type"] not in CASE_TYPES or item["meta"].get("answer_deleted") == "all":
        return None
    dele = {article_unit(u) for u in item["answer"]["deleted"]}
    units = {par_unit(r, known) for r in item["answer"]["refs"] if article_unit(r) not in dele} - {None}
    with_par = {article_unit(u) for u in units if u.startswith("par:")}
    units = {u for u in units if u.startswith("par:") or u not in with_par}  # 같은 조의 항 정답이 이긴다
    if not any(u.startswith("par:") for u in units):
        return None
    return units, ("순수" if all(u.startswith("par:") for u in units) else "섞임")


# ---------------------------------------------------------------- 문항 점수


def rank_scores(ranked: list[str], g: Gold, deleted: set[str]) -> dict:
    """조문 단위 앞 5개로 hit@5 · recall@5 · hit@1 · MRR@5, 삭제 조문 인용 수. 삭제 조문은 자리만 차지한다."""
    top = top_units(ranked)
    target = g.orig if g.status == "all_deleted" else g.cur
    hits = [u in target for u in top]
    first = next((i for i, h in enumerate(hits) if h), None)
    return {"hit5": int(any(hits)), "recall5": (sum(hits) / len(target)) if target else math.nan,
            "hit1": int(bool(hits) and hits[0]), "mrr5": 0.0 if first is None else 1 / (first + 1),
            "deleted_cited": sum(u in deleted and u not in target for u in top)}


def precision(top: list[str], target: set[str], deleted: set[str]) -> float | None:
    """앞 5개 중 정답 비율. 인용 0개는 0, 정답 아닌 삭제 조문만 있으면 None(정밀도에서 빼고 셈)."""
    if not top:
        return 0.0
    counted = [u for u in top if u in target or u not in deleted]
    return (sum(u in target for u in counted) / len(counted)) if counted else None


def par_hit(citations: list[str], units: set[str], known: set[str] | None = None) -> int:
    """답변 인용 앞 5개(항까지 올린 뒤 중복 제거)에 정답 단위가 있는가. 조 · 항이 아닌 인용도 자리는 차지한다."""
    cited: list[str] = []
    for c in citations:
        u = par_unit(c, known) or c
        if u not in cited:
            cited.append(u)
    cited = cited[:K]
    for g in units:
        if g.startswith("par:"):
            if g in cited:
                return 1
        elif any(c == g or (c.startswith("par:") and article_unit(c) == g) for c in cited):
            return 1
    return 0


EMPTY = {"citations": [], "verdict": None, "number": None, "refuse": False}


def answer_scores(item: dict, rec: dict | None, deleted: set[str], m4: dict | None = None,
                  known: set[str] | None = None) -> dict | None:
    """답변 한 줄 채점. None이면 짝에서 뺄 문항(API 최종 실패 · 정책 거절, 결과 없음은 오류로 봄)."""
    status = (rec or {}).get("status", "error")
    backend = (rec or {}).get("backend", "local")
    if status in API_DROP and backend == "api":
        return None
    ans = rec.get("answer") if status == "ok" else None
    ans = {**EMPTY, **(ans or {})}
    cits = [c for c in (ans.get("citations") or []) if isinstance(c, str)]
    out: dict = {"status": status, "refuse": int(bool(ans.get("refuse")))}
    t = item["type"]
    g = gold(item, m4)
    if g is not None and g.status != "pending":
        top = top_units(cits)
        target = g.orig if g.status == "all_deleted" else g.cur
        out["cite_hit"] = int(any(u in target for u in top))
        out["precision"] = precision(top, target, deleted)
        out["deleted_cited"] = sum(u in deleted and u not in target for u in top)
        out["gold_status"] = g.status
        pg = par_gold(item, known)
        if pg:
            out["par_hit"], out["par_kind"] = par_hit(cits, pg[0], known), pg[1]
    if t == "T2" and item["meta"].get("polarity") == "neg":
        out["verdict_ok"] = int(ans.get("verdict") == item["answer"]["verdict"])
    if t == "T6":
        out["number_ok"] = int(ans.get("number") is not None and ans.get("number") == item["answer"]["n"])
    sub = set((rec or {}).get("subgraph") or [])
    out["n_cite"], out["n_unsupported"] = len(cits[:K]), sum(c not in sub for c in cits[:K])
    return out


# ---------------------------------------------------------------- 검수 틀림 제외


def content_key(item: dict) -> str:
    """판정을 옮기는 내용 값: id만 뺀 키 정렬 JSON(생성기 직렬화와 같은 방식)의 sha256."""
    x = {k: v for k, v in item.items() if k != "id"}
    return hashlib.sha256(json.dumps(x, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def read_jsonl(p: Path) -> list[dict]:
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def audit_exclusions(evalset_root: Path, judgments: Path, scoring: list[dict]) -> dict:
    """라벨 검수 `틀림` → 채점 버전에서 뺄 id(구어체 짝 `:c` 포함)와 옮긴 · 못 옮긴 수(유형별)."""
    rows = list(csv.DictReader(judgments.open(encoding="utf-8-sig")))
    wrong = [r for r in rows if r["판정"] == "틀림"]
    by_key = {content_key(x): x["id"] for x in scoring}
    ids, moved, lost = set(), Counter(), Counter()
    cache: dict[str, dict[str, dict]] = {}
    for r in wrong:
        label, sha12 = r["생성 버전"].rsplit(None, 1)
        if label not in cache:
            p = evalset_root / label_dir(label) / "test.jsonl"
            if hashlib.sha256(p.read_bytes()).hexdigest()[:12] != sha12:
                raise SystemExit(f"판정 버전 {r['생성 버전']}과 {p.as_posix()} 해시가 다름")
            cache[label] = {x["id"]: x for x in read_jsonl(p)}
        judged = cache[label].get(r["문항 id"])
        hit = by_key.get(content_key(judged)) if judged else None
        if hit:
            ids |= {hit, hit + ":c"}
            moved[r["유형"]] += 1
        else:
            lost[r["유형"]] += 1
    return {"ids": ids, "moved": dict(moved), "lost": dict(lost), "wrong": len(wrong)}


# ---------------------------------------------------------------- 집계


def ci01(xs: list[int]) -> dict:
    k, n = int(sum(xs)), len(xs)
    lo, hi = stats.wilson(k, n)
    return {"n": n, "k": k, "value": (k / n) if n else math.nan, "lo": lo, "hi": hi}


def ci_mean(xs: list[float], seed: int, n_boot: int) -> dict:
    m, lo, hi = stats.bootstrap_mean(xs, seed, n_boot)
    return {"n": len(xs), "value": m, "lo": lo, "hi": hi}


def compare(a: dict[str, int], b: dict[str, int], seed: int, n_boot: int) -> dict:
    """같은 문항 짝(둘 다 점수가 있는 문항)으로 차이(a − b) · 짝 부트스트랩 구간 · 양측 정확 McNemar."""
    ids = sorted(set(a) & set(b))
    x, y = [a[i] for i in ids], [b[i] for i in ids]
    d, lo, hi = stats.paired_diff(x, y, seed, n_boot)
    nb, nc = stats.discordant(x, y)
    return {"n": len(ids), "diff": d, "lo": lo, "hi": hi, "b": nb, "c": nc, "p": stats.mcnemar_exact(nb, nc)}


def retrieval_table(items: list[dict], runs: dict[str, dict[str, dict]], deleted: set[str], excl: set[str],
                    m4: dict | None, seed: int, n_boot: int) -> dict:
    """구성 → 표. 문항별 hit5도 돌려줘 비교에 쓴다."""
    out = {}
    for name, recs in runs.items():
        per = defaultdict(list)    # (유형, 지표) → 값
        hit5: dict[str, dict[str, int]] = defaultdict(dict)
        counts = Counter()
        for it in items:
            if it["id"] in excl:
                counts[(it["type"], "검수 틀림 제외")] += 1
                continue
            g = gold(it, m4)
            if g is None:
                continue
            if g.status == "pending":
                counts[(it["type"], "정답 미확정")] += 1
                continue
            rec = recs.get(it["id"])
            if rec is None:
                counts[(it["type"], "결과 없음")] += 1
            s = rank_scores((rec or {}).get("ranked") or [], g, deleted)
            t = it["type"]
            if g.status == "all_deleted":
                per[(t, "all_deleted_hit5")].append(s["hit5"])
                continue
            if it["meta"].get("answer_deleted") == "some":
                counts[(t, "일부 삭제")] += 1
            for m in ("hit5", "hit1", "recall5", "mrr5"):
                per[(t, m)].append(s[m])
            counts[(t, "삭제 조문 인용")] += s["deleted_cited"]
            hit5[t][it["id"]] = s["hit5"]
            if t in ("T1", "T3"):
                hit5["T1+T3"][it["id"]] = s["hit5"]
            if t in CASE_TYPES:
                per[(t, "rep_left=0" if it["meta"].get("rep_left", 0) == 0 else "rep_left>=1")].append(s["hit5"])
                per[(t, "원문 읽힘" if it["meta"].get("answer_in_original") else "원문 안 읽힘")].append(s["hit5"])
        table = {}
        for (t, m), xs in sorted(per.items()):
            table.setdefault(t, {})[m] = ci_mean(xs, seed, n_boot) if m in ("recall5", "mrr5") else ci01(xs)
        if hit5.get("T1+T3"):
            table["T1+T3"] = {"hit5": ci01(list(hit5["T1+T3"].values()))}
        for (t, c), n in sorted(counts.items()):
            table.setdefault(t, {}).setdefault("counts", {})[c] = n
        out[name] = {"table": table, "hit5": {k: dict(v) for k, v in hit5.items()}}
    return out


def answer_table(items: list[dict], runs: dict[str, dict[str, dict]], deleted: set[str], excl: set[str],
                 m4: dict | None, seed: int, n_boot: int, known: set[str] | None = None) -> dict:
    out = {}
    for name, recs in runs.items():
        per = defaultdict(list)
        main: dict[str, dict[str, int]] = defaultdict(dict)  # 유형 → id → 주 지표(정답 근거 인용 적중)
        aux: dict[str, dict[str, int]] = defaultdict(dict)   # 유형 → id → T2 음성 판정 · T6 숫자
        n_main = Counter()  # 주 지표 대상 문항 수(뺀 문항 5% 판정의 분모)
        prec: dict[str, float | None] = {}  # 주 지표 문항의 인용 정밀도(중립이면 None)
        st = Counter()
        sens = {"dropped": defaultdict(set), "truncated": defaultdict(set)}
        for it in items:
            if it["id"] in excl:
                continue
            rec = recs.get(it["id"])
            t = it["type"]
            g = gold(it, m4)
            if g is not None and g.status == "pending":
                st[(t, "정답 미확정")] += 1  # 평가에 쓰지 않는 문항(M4 미선정 · 예비): 모든 집계에서 뺌
                continue
            is_main = g is not None and g.status == "main"
            n_main[t] += is_main
            st[(rec or {}).get("status", "결과 없음")] += 1
            st["재시도"] += int((rec or {}).get("retries") or 0)
            s = answer_scores(it, rec, deleted, m4, known)
            if s is None:
                if is_main:
                    sens["dropped"][t].add(it["id"])
                st[(t, "짝에서 뺌(API 실패 · 정책 거절)")] += 1
                continue
            if is_main and s["status"] == "truncated" and (rec or {}).get("backend") == "api":
                sens["truncated"][t].add(it["id"])
            per[(t, "n_cite")].append(s["n_cite"])
            per[(t, "n_unsupported")].append(s["n_unsupported"])
            if t == "T5":
                per[(t, "refuse_ok")].append(s["refuse"])
                continue
            per[(t, "false_refusal")].append(s["refuse"])
            if "cite_hit" in s:
                if s["gold_status"] == "all_deleted":
                    per[(t, "all_deleted_cite_hit")].append(s["cite_hit"])
                else:
                    per[(t, "cite_hit")].append(s["cite_hit"])
                    main[t][it["id"]] = s["cite_hit"]
                    prec[it["id"]] = s["precision"]
                    if s["precision"] is None:
                        st[(t, "정밀도 중립(삭제 조문만)")] += 1
                    else:
                        per[(t, "precision")].append(s["precision"])
                    st[(t, "삭제 조문 인용")] += s["deleted_cited"]
                    if "par_hit" in s:
                        per[(t, f"par_hit_{s['par_kind']}")].append(s["par_hit"])
                        per[(t, "par_hit_합산")].append(s["par_hit"])
            for m in ("verdict_ok", "number_ok"):
                if m in s:
                    per[(t, m)].append(s[m])
                    aux[t][it["id"]] = s[m]
        table = {}
        for (t, m), xs in sorted(per.items()):
            if m in ("n_cite", "n_unsupported"):
                continue
            table.setdefault(t, {})[m] = ci_mean(xs, seed, n_boot) if m == "precision" else ci01(xs)
        for t in {t for (t, m) in per}:
            nc, nu = sum(per[(t, "n_cite")]), sum(per[(t, "n_unsupported")])
            table.setdefault(t, {})["unsupported"] = {"citations": nc, "unsupported": nu,
                                                      "rate": (nu / nc) if nc else math.nan}
        for k, n in sorted(st.items(), key=str):
            if isinstance(k, tuple):
                table.setdefault(k[0], {}).setdefault("counts", {})[k[1]] = n
        table["_status"] = {k: n for k, n in st.items() if not isinstance(k, tuple)}
        table["_dropped"] = {t: len(v) for t, v in sens["dropped"].items()}
        out[name] = {"table": table, "main": {k: dict(v) for k, v in main.items()},
                     "aux": {k: dict(v) for k, v in aux.items()}, "n_main": dict(n_main), "prec": prec,
                     "dropped": {t: sorted(v) for t, v in sens["dropped"].items()},
                     "truncated": {t: sorted(v) for t, v in sens["truncated"].items()}}
    return out


def ratio_rows(local: dict, api: dict, seed: int, n_boot: int) -> dict:
    """API 대비 비율(주 지표): 기본 · S1 · S2, 유형별 + 전체. 뺀 문항이 주 지표 대상의 5%를 넘으면 기술 통계로 표시."""
    rows = {}
    n_items = api["n_main"]
    types = sorted(t for t in set(local["main"]) | set(api["main"]) | set(api["dropped"]) if n_items.get(t))

    def one(ids_vals_l: dict, ids_vals_a: dict) -> dict:
        ids = sorted(set(ids_vals_l) & set(ids_vals_a))
        r, lo, hi = stats.paired_ratio([ids_vals_l[i] for i in ids], [ids_vals_a[i] for i in ids], seed, n_boot)
        return {"n": len(ids), "ratio": r, "lo": lo, "hi": hi}

    def merged(t_list):
        lm = {i: v for t in t_list for i, v in local["main"].get(t, {}).items()}
        am = {i: v for t in t_list for i, v in api["main"].get(t, {}).items()}
        dropped = {i for t in t_list for i in api["dropped"].get(t, [])}
        trunc = {i for t in t_list for i in api["truncated"].get(t, [])}
        base = one(lm, am)
        both = sorted(set(lm) & set(am))
        pp = [i for i in both if local["prec"].get(i) is not None and api["prec"].get(i) is not None]
        base["precision"] = {"n": len(pp), "neutral_dropped": len(both) - len(pp),
                             "local": stats.bootstrap_mean([local["prec"][i] for i in pp], seed, n_boot),
                             "api": stats.bootstrap_mean([api["prec"][i] for i in pp], seed, n_boot)}
        s1 = one(lm, {**am, **{i: 0 for i in dropped if i in lm}})
        s2 = one({i: v for i, v in lm.items() if i not in trunc}, {i: v for i, v in am.items() if i not in trunc})
        n_all = sum(n_items.get(t, 0) for t in t_list)
        share = len(dropped) / n_all if n_all else math.nan
        return {"base": base, "S1": s1, "S2": s2, "dropped": len(dropped), "dropped_share": share,
                "descriptive_only": bool(n_all and share > 0.05)}

    for t in types:
        rows[t] = merged([t])
    rows["전체"] = merged(types)
    desc = [t for t in types if rows[t]["descriptive_only"]]
    if desc:  # 342행: 기술 통계가 된 유형을 뺀 범위의 비율도 헤드라인 옆에
        rows["전체(기술 통계 유형 제외)"] = {**merged([t for t in types if t not in desc]), "excluded_types": desc}
    return rows


# ---------------------------------------------------------------- 실행


def load_runs(specs: list[str]) -> dict[str, dict[str, dict]]:
    runs = {}
    for s in specs or []:
        name, path = s.split("=", 1)
        recs = read_jsonl(Path(path))
        dup = [i for i, n in Counter(r["id"] for r in recs).items() if n > 1]
        if dup:
            raise SystemExit(f"{name}: 같은 id가 두 번 이상 있는 문항 {len(dup)}개")
        runs[name] = {r["id"]: r for r in recs}
    return runs


def required(item: dict, kind: str, m4: dict | None) -> bool:
    """실행에 반드시 있어야 하는 문항인가(검수 틀림 제외는 호출 쪽)."""
    g = gold(item, m4)
    if g is not None and g.status == "pending":
        return False
    return g is not None if kind == "검색" else True


def check_runs(items: list[dict], runs: dict[str, dict[str, dict]], kind: str, excl: set[str],
               m4: dict | None) -> None:
    """모르는 id · 필수 문항 누락 · (답변) 모르는 status · backend 섞임이면 중단. 같은 id 중복은 load_runs가 막는다."""
    ids = {x["id"] for x in items}
    need = {x["id"] for x in items if x["id"] not in excl and required(x, kind, m4)}
    for name, recs in runs.items():
        bad = []
        if set(recs) - ids:
            bad.append(f"평가셋 split에 없는 id {len(set(recs) - ids)}개")
        if need - set(recs):
            bad.append(f"필수 문항 누락 {len(need - set(recs))}개")
        if kind == "답변":
            st = Counter(str(r.get("status")) for r in recs.values() if r.get("status") not in STATUSES)
            if st:
                bad.append(f"모르는 status {dict(st)}")
            be = {r.get("backend") for r in recs.values()}
            if len(be) != 1 or not be <= {"local", "api"}:
                bad.append(f"backend가 하나가 아님 {sorted(map(str, be))}")
        if bad:
            raise SystemExit(f"{kind} 실행 {name}: " + ", ".join(bad))


def backend_of(recs: dict[str, dict]) -> str:
    return next(iter(recs.values()))["backend"] if recs else ""


def article_index(root: Path, snapshot: str) -> tuple[set[str], set[str]]:
    """스냅숏의 (삭제 조문 uid, 조 · 항 uid). 생성기 · 라벨 검수 파일과 같은 `eval/sources.py` 판정."""
    import sources
    arts = sources.articles(root / snapshot)
    known = set(arts) | {u for a in arts.values() for u, _ in a.units if u.startswith("par:")}
    return {u for u, a in arts.items() if a.deleted}, known


def load_items(evalset: Path, split: str) -> list[dict]:
    items = read_jsonl(evalset / f"{split}.jsonl")
    if (evalset / "refusal.jsonl").exists():
        items += [x for x in read_jsonl(evalset / "refusal.jsonl") if x["split"] == split]
    return items


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--evalset", default="eval/evalset/v1")
    ap.add_argument("--split", default="test", choices=["test", "dev"])
    ap.add_argument("--retrieval", action="append", help="이름=검색 결과 jsonl")
    ap.add_argument("--answers", action="append", help="이름=답변 결과 jsonl")
    ap.add_argument("--compare", action="append", help="앞:뒤 (차이 = 앞 − 뒤)")
    ap.add_argument("--ratio", help="로컬:API (답변 구성 이름)")
    ap.add_argument("--m4-answers", help="T4-M4 검색 · 답변 정답 JSON {문항 id: [uid, ...]}")
    ap.add_argument("--judgments", help="라벨 검수 판정 csv(eval/label_audit_judgments.csv)")
    ap.add_argument("--out", help="전체 결과 JSON")
    args = ap.parse_args()
    c = prereg.read_constants(ROOT)
    seed, n_boot = c["BOOTSTRAP_SEED"], c["BOOTSTRAP_N"]
    ev = ROOT / args.evalset
    items = load_items(ev, args.split)
    deleted, known = article_index(ROOT, c["SNAPSHOT"])
    m4 = json.loads(Path(args.m4_answers).read_text(encoding="utf-8")) if args.m4_answers else None
    ex = audit_exclusions(ev.parent, Path(args.judgments), items) if args.judgments else {"ids": set()}
    res: dict = {"evalset": args.evalset, "split": args.split, "n_items": len(items), "deleted_articles": len(deleted),
                 "exclusions": {k: (sorted(v) if isinstance(v, set) else v) for k, v in ex.items()}}
    print(f"# {args.evalset} {args.split}: 문항 {len(items):,}, 삭제 조문 {len(deleted):,}, "
          f"검수 틀림 제외 {len(ex['ids']):,}")
    if args.retrieval:
        runs = load_runs(args.retrieval)
        check_runs(items, runs, "검색", ex["ids"], m4)
        rt = retrieval_table(items, runs, deleted, ex["ids"], m4, seed, n_boot)
        res["retrieval"] = {k: v["table"] for k, v in rt.items()}
        for name, v in rt.items():
            print(f"## 검색 {name}")
            for t, ms in v["table"].items():
                h = ms.get("hit5")
                if h:
                    print(f"  {t}\thit@5 {h['value']:.3f} [{h['lo']:.3f}, {h['hi']:.3f}] n={h['n']}"
                          + (" (기술 통계)" if t in NO_TEST else ""))
        cmp = {}
        for pair in args.compare or []:
            a, b = pair.split(":")
            for t in sorted(set(rt[a]["hit5"]) & set(rt[b]["hit5"])):
                if t in NO_TEST:
                    continue
                r = compare(rt[a]["hit5"][t], rt[b]["hit5"][t], seed, n_boot)
                cmp[f"{a} vs {b} {t}"] = r
                print(f"  {a} vs {b} {t}\t차이 {r['diff']:+.3f} [{r['lo']:+.3f}, {r['hi']:+.3f}] "
                      f"p={r['p']:.4f} (b={r['b']}, c={r['c']}, n={r['n']})")
        res["retrieval_compare"] = cmp
    if args.answers:
        runs = load_runs(args.answers)
        check_runs(items, runs, "답변", ex["ids"], m4)
        if args.ratio:
            lo_name, api_name = args.ratio.split(":")
            if backend_of(runs[lo_name]) != "local" or backend_of(runs[api_name]) != "api":
                raise SystemExit(f"--ratio {args.ratio}: 앞은 local, 뒤는 api 실행이어야 함")
        at = answer_table(items, runs, deleted, ex["ids"], m4, seed, n_boot, known)
        res["answers"] = {k: v["table"] for k, v in at.items()}
        for name, v in at.items():
            print(f"## 답변 {name}: 상태 {v['table']['_status']}, 짝에서 뺌 {v['table']['_dropped']}")
            for t, ms in v["table"].items():
                h = ms.get("cite_hit") if isinstance(ms, dict) else None
                if h:
                    print(f"  {t}\t인용률 {h['value']:.3f} [{h['lo']:.3f}, {h['hi']:.3f}] n={h['n']}")
        cmp = {}
        for pair in args.compare or []:
            a, b = pair.split(":")
            if a in at and b in at:
                for t in sorted(set(at[a]["main"]) & set(at[b]["main"])):
                    if t not in NO_TEST:
                        cmp[f"{a} vs {b} {t}"] = compare(at[a]["main"][t], at[b]["main"][t], seed, n_boot)
        res["answers_compare"] = cmp
        if args.ratio:
            lo_name, api_name = args.ratio.split(":")
            res["api_ratio"] = ratio_rows(at[lo_name], at[api_name], seed, n_boot)
            r = res["api_ratio"]["전체"]
            print(f"## API 대비 비율 {lo_name}/{api_name}: {r['base']['ratio']:.3f} "
                  f"[{r['base']['lo']:.3f}, {r['base']['hi']:.3f}] n={r['base']['n']}, 뺌 {r['dropped']}"
                  + (" (5% 초과, 기술 통계)" if r["descriptive_only"] else ""))
    if args.out:
        Path(args.out).write_text(json.dumps(res, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
        print(f"## 결과 {args.out}")


if __name__ == "__main__":
    main()
