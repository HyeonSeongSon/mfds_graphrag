"""평가셋 생성기 (1주차 D4에 잠금, PREREG 4-2 ~ 4-4). 스냅숏 원자료 → 문항 · 제외 목록 · M4 후보 · 데이터 카드.

생성기는 Neo4j를 읽지 않고 `mfds/load/`를 import하지 않는다(정답 독립성). 원자료 읽기는 `eval/sources.py`, 예외 공유 모듈은
`mfds.refs` · `mfds.ingredients` · `mfds.disposition` · `mfds.collect.dur`(표 파서)뿐이다. 시드는 인자로 받지 않고 PREREG
CONSTANTS에서만 읽는다. 업소 정보는 같은 업소 이웃 계산에만 메모리에서 쓰고 출력 · 파일에 남기지 않는다.

생성 순서(4-4): T1 · T3 선택 → 제외 목록 → 남은 원자료로 T2 · T4 · T6 슬롯 → 거절 세트 → held-out.
의도마다 dev · test 슬롯은 겹치지 않는다. 단계마다 시드에서 따로 만든 난수(`rng(단계)`)를 써서 한 단계를 바꿔도
다른 단계의 순서가 흔들리지 않는다.

    python eval/build_evalset.py --dry-run --compare-out <scratchpad>/compare.json   # 보고만(저장소에 쓰지 않음)
    python eval/build_evalset.py --heldout write                                     # 잠금 직전 한 번: intents.yaml 기록
    python eval/build_evalset.py --heldout check --out eval/evalset/v1               # 잠금 뒤 생성
"""

import argparse
import hashlib
import json
import random
import re
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "eval"))
import grouping as G  # noqa: E402
import m4_candidates as M4  # noqa: E402
import prereg  # noqa: E402
import sources as src  # noqa: E402
from mfds.disposition import has_mask, read_rows, violation_text  # noqa: E402
from mfds.refs import LawDict, Resolver, _key, norm_text  # noqa: E402

# PREREG CONSTANTS와 같아야 한다(시작할 때 대조, 다르면 중단)
SEED = 20261030
SNAPSHOT = "data/raw/20261029"
NEIGHBOR_MAX_FALLBACK = 100
EXCLUSION_MAX_RATIO = 0.3
T1_T3_DEV, T1_T3_TEST, T1_T3_REQUIRED = 30, 200, 230
HELDOUT_CAP, HELDOUT_MIN = 110, 80
M4_POOL, M4_TEST = 36, 30
T2_POS, T2_NEG, T2_DEV_POS = 150, 50, 22
REFUSAL_TEST, REFUSAL_DEV = 150, 20
REFUSAL_KINDS = {"other_ministry_law": [7, 50], "missing_article": [7, 50], "out_of_scope": [6, 50]}
RESULT_ROW_MAX = 200
M3_SET_UNIT = "uid"
T3_INPUT_FIELDS = ["판결요지", "판시사항"]

INTENTS = ROOT / "schema" / "intents.yaml"
QUESTIONS = ROOT / "schema" / "eval_questions"
HELDOUT_LAW = "의료기기법"  # 이 법률 계열(LawDict.root가 같음) 조문이 슬롯 · 정답에 있으면 heldout_law 태그
KIND_LABEL = {"대통령령": "시행령", "총리령": "시행규칙"}
M4_DEV_RANKS = (M4_POOL, M4_POOL + 6)  # 풀 다음 37~42번째
M4_RESERVE_RANKS = (M4_POOL + 6, M4_POOL + 12)  # 43~48번째


def check_constants() -> dict:
    c = prereg.read_constants(ROOT)
    mine = {"SEED": SEED, "SNAPSHOT": SNAPSHOT, "CAP_PER_ARTICLE": G.CAP_PER_ARTICLE, "NEIGHBOR_MAX": G.NEIGHBOR_MAX,
            "NEIGHBOR_MAX_FALLBACK": NEIGHBOR_MAX_FALLBACK, "EXCLUSION_MAX_RATIO": EXCLUSION_MAX_RATIO,
            "JACCARD": G.JACCARD, "NGRAM": G.N_GRAM, "T1_T3_DEV": T1_T3_DEV, "T1_T3_TEST": T1_T3_TEST,
            "T1_T3_REQUIRED": T1_T3_REQUIRED, "HELDOUT_CAP": HELDOUT_CAP, "HELDOUT_MIN": HELDOUT_MIN,
            "M4_POOL": M4_POOL, "M4_TEST": M4_TEST, "T2_POS": T2_POS, "T2_NEG": T2_NEG, "T2_DEV_POS": T2_DEV_POS,
            "REFUSAL_TEST": REFUSAL_TEST, "REFUSAL_DEV": REFUSAL_DEV, "REFUSAL_KINDS": REFUSAL_KINDS,
            "RESULT_ROW_MAX": RESULT_ROW_MAX, "M3_SET_UNIT": M3_SET_UNIT, "T3_INPUT_FIELDS": T3_INPUT_FIELDS}
    bad = {k: (v, c.get(k)) for k, v in mine.items() if c.get(k) != v}
    if bad:
        raise SystemExit(f"PREREG CONSTANTS와 다르다(생성기 값, PREREG 값): {bad}")
    return c


def check_intents(intents: list[dict]) -> dict[str, dict]:
    """`intents.yaml`의 n을 CONSTANTS와 대조한다(마크다운 표가 아니라 CONSTANTS가 기준)."""
    ev = {i["name"]: i for i in intents if i.get("use") == "eval"}
    test = sum(i["n"]["test"] for i in ev.values())
    dev = sum(i["n"]["dev"] for i in ev.values())
    errs = []
    if (test, dev) != (450, 70):
        errs.append(f"eval 의도 합계 test {test} · dev {dev} (450 · 70이어야 함)")
    if ev["t2_pair_contraindication"]["n"]["test"] != T2_POS + T2_NEG:
        errs.append("t2 test ≠ T2_POS + T2_NEG")
    if ev["m4_citing_articles"]["n"]["test"] != M4_TEST:
        errs.append("m4 test ≠ M4_TEST")
    if sum(d for d, _ in REFUSAL_KINDS.values()) != REFUSAL_DEV or sum(t for _, t in REFUSAL_KINDS.values()) != REFUSAL_TEST:
        errs.append("REFUSAL_KINDS 합 ≠ REFUSAL_DEV · REFUSAL_TEST")
    if errs:
        raise SystemExit("intents.yaml n이 CONSTANTS와 다르다: " + "; ".join(errs))
    return ev


def rng(tag: str) -> random.Random:
    return random.Random(f"{SEED}:{tag}")


def seeded(items, tag: str, key=None) -> list:
    """정렬해 고정한 뒤 단계별 난수로 섞은 순서(시드 순서)."""
    out = sorted(items, key=key)
    rng(tag).shuffle(out)
    return out


@dataclass
class World:
    """생성기가 읽은 원자료(스냅숏 하나). 업소 정보는 담지 않는다."""
    S: Path
    laws: dict
    lawdict: LawDict
    resolver: Resolver
    articles: dict
    admin: dict
    sub: dict
    delegations: dict
    dur: list
    precs: dict
    disp: dict  # DSPSDTLS_SEQ → 행(업소 정보 열 없음), 스냅숏 처음 나온 행
    other_laws: list


def load_world(S: Path) -> World:
    laws = src.laws(S)
    arts = src.articles(S)
    lawdict = LawDict.from_snapshot(S)
    disp = {}
    for r in read_rows(S):
        disp.setdefault(r["DSPSDTLS_SEQ"], r)
    return World(S, laws, lawdict, Resolver(lawdict, set(arts)), arts, src.admin_articles(S),
                 src.subordinate(S, set(laws)), src.delegations(S, laws, set(arts)), src.dur_rules(S),
                 src.precedents(S), disp, src.other_laws(S))


# ---------- 라벨 ----------

def article_label(w: World, uid: str) -> str:
    _, lid, no = uid.split(":")
    n, _, b = no.partition("-")
    return f"{w.laws[lid].name} 제{n}조" + (f"의{b}" if b else "")


def case_label(f: dict) -> str:
    return f"{f.get('법원명')} {(f.get('사건번호') or '').strip()} 판결"


def law_family(w: World, uids) -> set[str]:
    return {w.lawdict.root(u.split(":")[1]) for u in uids if u.startswith("art:")}


def heldout_law_tag(w: World, uids) -> bool:
    root = w.lawdict.names.get(HELDOUT_LAW)
    return root in law_family(w, uids)


# ---------- 원자료 정답(그래프 엣지와 같은 정의) ----------

def ref_articles(res, current_only: bool) -> list[str]:
    """refs 결과 → 조문 uid(항 · 호는 조로 올림, 별표 제외). 적재기 `BASED_ON` · `REFERENCES`와 같은 대상."""
    return list(dict.fromkeys(r.article_uid for r in res.refs
                              if not r.uid.startswith("anx:") and not (current_only and r.historical)))


def disposition_answers(w: World) -> dict[str, list[str]]:
    """처분 → 근거 조문(근거 조항 필드를 맥락 없이 refs로 읽음, 구법 표시도 엣지라 포함)."""
    return {s: ref_articles(w.resolver.resolve(r.get("LAWORD_CD_NM") or ""), False) for s, r in w.disp.items()}


def case_refs(w: World) -> dict[str, object]:
    return {pid: w.resolver.resolve(G.clean_html(f.get("참조조문"))) for pid, f in w.precs.items()
            if G.clean_html(f.get("참조조문"))}


def citing_index(w: World) -> dict[str, dict[str, bool]]:
    """대상 조문 → {인용한 조문(조 · 고시 조문 단위): 구법 아닌 인용이 있는가}. 적재기 `CITES`처럼 조문 전체를 한 번에
    읽는다(법령 조문은 그 법령 · 조를 맥락으로, 고시 조문은 맥락 없이). 자기 자신은 뺀다."""
    out: dict[str, dict[str, bool]] = defaultdict(dict)

    def add(source: str, res) -> None:
        for r in res.refs:
            if r.uid.startswith("anx:") or r.article_uid == source:
                continue
            out[r.article_uid][source] = out[r.article_uid].get(source, False) or not r.historical

    for a in w.articles.values():
        text = " ".join(t for t in (norm_text(x) for _, x in a.units) if t)
        if text:
            add(a.uid, w.resolver.resolve(text, a.law_id, a.no))
    for a in w.admin.values():
        if norm_text(a.text):
            add(a.uid, w.resolver.resolve(norm_text(a.text)))
    return out


# ---------- T1 · T3 ----------

@dataclass
class Pool:
    name: str
    sel: G.Selection
    cands: list
    excluded: set
    texts: dict  # 문서 ID → 질문 원문
    answers: dict  # 문서 ID → 정답 조문
    refs: dict  # 문서 ID → 정답 참조(항 · 호 uid)
    total: int
    fallback: str
    over_neighbor: int


def _select(name: str, cands: list, freq: Counter, total: int) -> tuple[G.Selection, str, int]:
    """시드 순서로 고르고 대체 규칙(4-2)을 적용한다."""
    order = seeded(cands, f"{name}:order", key=lambda c: c.key)
    sel = G.select(order, freq, T1_T3_TEST, T1_T3_DEV)
    over = sum(len(c.excluded) > G.NEIGHBOR_MAX for c in cands)
    if sel.fillable >= T1_T3_REQUIRED:
        return sel, "적용 안 함", over
    up = G.select(order, freq, T1_T3_TEST, T1_T3_DEV, neighbor_max=NEIGHBOR_MAX_FALLBACK)
    ratio = len(set().union(*(c.excluded for c in up.test + up.dev))) / max(total, 1)
    note = f"상한 {NEIGHBOR_MAX_FALLBACK}"
    if ratio <= EXCLUSION_MAX_RATIO:
        sel = up
        over = sum(len(c.excluded) > NEIGHBOR_MAX_FALLBACK for c in cands)
        if sel.fillable >= T1_T3_REQUIRED:
            return sel, note, over
        nm = NEIGHBOR_MAX_FALLBACK
    else:
        note = f"상한 유지(올리면 제외 {ratio:.1%} > 30%)"
        nm = G.NEIGHBOR_MAX
    n_test = min(T1_T3_TEST, (sel.fillable - T1_T3_DEV) // 2)
    return G.select(order, freq, n_test, T1_T3_DEV, neighbor_max=nm), f"{note} + test {n_test}로 축소", over


def t1_t3_pools(w: World, disp_ans: dict, crefs: dict) -> tuple[Pool, Pool, Counter]:
    freq: Counter = Counter()
    for a in disp_ans.values():
        freq.update(set(a))
    t3_ans = {pid: ref_articles(res, True) for pid, res in crefs.items()}
    for a in t3_ans.values():
        freq.update(set(a))

    # T1: 묶음 · 이웃은 위반 내용 원문(그래프에 남는 텍스트), 같은 업소 키는 메모리에서만
    rows = read_rows(w.S, keep_pii=True)
    seen: set[str] = set()
    key_of = {}
    for r in rows:
        if r["DSPSDTLS_SEQ"] in seen:
            continue
        seen.add(r["DSPSDTLS_SEQ"])
        name = (r.get("PRCSCITYPOINT_BSSHNM") or "").strip()
        key_of[r["DSPSDTLS_SEQ"]] = name + (r.get("ADDR") or "") if name else ""
    del rows
    same = G.SameKey(key_of)
    del key_of
    t1_raw = {s: r.get("VILTCN") or "" for s, r in w.disp.items()}
    groups = G.exact_groups(t1_raw, G.normalize_violation)
    groups = {k: v for k, v in groups.items() if not any(has_mask(t1_raw[i]) for i in v)}
    t1_c = G.build_candidates(groups, disp_ans, G.NearDup(t1_raw), same)
    sel, fb, over = _select("t1", t1_c, freq, len(t1_raw))
    t1 = Pool("T1", sel, t1_c, set().union(*(c.excluded for c in sel.test + sel.dev)),
              {s: violation_text(t) for s, t in t1_raw.items()}, disp_ans,
              {s: [r.uid for r in w.resolver.resolve(w.disp[s].get("LAWORD_CD_NM") or "").refs if not r.uid.startswith("anx:")]
               for s in w.disp}, len(t1_raw), fb, over)

    texts = {pid: G.t3_text(f) for pid, f in w.precs.items()}
    t3_ans = {pid: a for pid, a in t3_ans.items() if a and texts[pid]}
    groups = G.exact_groups({pid: f.get("사건번호") or pid for pid, f in w.precs.items()}, G.case_key)
    t3_c = G.build_candidates(groups, t3_ans, G.NearDup({i: t for i, t in texts.items() if t}))
    sel, fb, over = _select("t3", t3_c, freq, len(w.precs))
    t3 = Pool("T3", sel, t3_c, set().union(*(c.excluded for c in sel.test + sel.dev)), texts, t3_ans,
              {pid: [r.uid for r in res.refs if not r.historical and not r.uid.startswith("anx:")]
               for pid, res in crefs.items()}, len(w.precs), fb, over)
    return t1, t3, freq


def law_ids_in(w: World, text: str) -> set[str]:
    r = w.resolver
    return {r.by_key[_key(m.group("law"))] for m in r.pattern.finditer(norm_text(text)) if m.group("law")}


def t1_t3_items(w: World, pool: Pool, left: Counter, templates: list[str], slot: str) -> list[dict]:
    """문항 하나 = 후보 묶음 하나. 질문은 첫 구성원(본문이 있는)의 텍스트. 확인 규칙이 걸리면 중단."""
    names = list(w.lawdict.names)
    out = []
    pick = rng(f"{pool.name}:template")
    for split, cands in (("test", pool.sel.test), ("dev", pool.sel.dev)):
        for k, c in enumerate(cands):
            doc = next(m for m in c.members if pool.texts.get(m))
            raw = pool.texts[doc]
            masked = G.mask_article_refs(raw)
            law_masked = G.mask_law_names(masked, names)
            ti = pick.randrange(len(templates))
            q = templates[ti].format(**{slot: masked})
            q_law = templates[ti].format(**{slot: law_masked})
            if w.resolver.resolve(q).article_uids:
                raise SystemExit(f"{pool.name} {doc}: 조문 가린 질문에 refs 조문이 남았다")
            if law_ids_in(w, q_law):
                raise SystemExit(f"{pool.name} {doc}: 법령명까지 가린 질문에 식약처 법령 토큰이 남았다")
            gold_laws = {a.split(":")[1] for a in c.answer}
            got = law_ids_in(w, masked)
            rep_law = c.rep.split(":")[1]
            out.append({
                "id": f"{split}:{pool.name.lower()}:{k:03d}", "split": split, "type": pool.name, "intent": None,
                "template_idx": ti, "question": q, "question_unmasked": templates[ti].format(**{slot: raw}),
                "question_law_masked": q_law, "slots": {},
                "answer": {"uids": c.answer, "refs": list(dict.fromkeys(r for m in c.members for r in pool.refs.get(m, [])))},
                "meta": {"source": doc, "members": c.members, "group_size": len(c.excluded), "rep": c.rep,
                         "rep_left": left[c.rep], "article_masked": masked != raw,
                         "answer_in_original": bool(set(c.answer) & set(w.resolver.resolve(raw).article_uids)),
                         "law_name_left": bool(got & gold_laws),
                         "law_family_left": bool({w.lawdict.root(x) for x in got} & {w.lawdict.root(x) for x in gold_laws}),
                         "rep_law_articles": sum(1 for a in w.articles.values() if a.law_id == rep_law),
                         "heldout_law": heldout_law_tag(w, c.answer)}})
    return out


# ---------- T2 ----------

def ing_keys(i) -> set[str]:
    """관계 판정 키: uid, 염 제거형, 복합제 구성(양쪽 방향)."""
    keys = {i.uid} | {c.uid for c in i.components}
    if i.base:
        keys.add(f"base:{i.base}")
    keys |= {f"base:{c.base}" for c in i.components if c.base}
    return keys


def t2_items(w: World, templates: list[str], report: dict, n_dev: int) -> tuple[list[dict], list[dict]]:
    pairs: dict[tuple[str, str], list[str]] = defaultdict(list)
    ing: dict[str, object] = {}
    for r in w.dur:
        if r.type == "pair" and len(r.ingredients) == 2:
            a, b = sorted(r.ingredients, key=lambda i: i.uid)
            ing[a.uid], ing[b.uid] = a, b
            pairs[(a.uid, b.uid)].append(r.uid)
    order = seeded(pairs, "t2:pos")
    pos = {"test": order[:T2_POS], "dev": order[T2_POS:T2_POS + T2_DEV_POS]}
    # 음성: 별표 1 성분끼리 시드 순서로 짝짓고, 양성 쌍(별표 1 전체 pair 규칙) 자체와 관계 쌍을 뺀다
    side: dict[str, dict[str, set[int]]] = {"a": defaultdict(set), "b": defaultdict(set)}
    for n, (a, b) in enumerate(pairs):
        for k in ing_keys(ing[a]):
            side["a"][k].add(n)
        for k in ing_keys(ing[b]):
            side["b"][k].add(n)

    def hits(u: str, s: str) -> set[int]:
        return set().union(*(side[s].get(k, set()) for k in ing_keys(ing[u])))

    names = sorted(ing)
    pick = rng("t2:neg")
    neg: list[tuple[str, str]] = []
    dropped: Counter = Counter()
    seen: set[tuple[str, str]] = set()
    while len(neg) < T2_NEG + n_dev - T2_DEV_POS:
        x, y = sorted(pick.sample(names, 2))
        if (x, y) in seen:
            continue
        seen.add((x, y))
        if (x, y) in pairs:
            dropped["양성 쌍 자체"] += 1
        elif (hits(x, "a") & hits(y, "b")) or (hits(x, "b") & hits(y, "a")):
            dropped["관계 쌍(같은 염 제거형 · 복합제 구성)"] += 1
        else:
            neg.append((x, y))
    negs = {"test": neg[:T2_NEG], "dev": neg[T2_NEG:]}
    report["t2_neg_dropped"] = dict(dropped)
    items, slots = [], []
    tp = rng("t2:template")
    swap = rng("t2:swap")
    for split in ("test", "dev"):
        rows = [(p, pairs[p]) for p in pos[split]] + [(p, []) for p in negs[split]]
        for k, ((a, b), rules) in enumerate(rows):
            if swap.random() < 0.5:
                a, b = b, a
            ti = tp.randrange(len(templates))
            la, lb = ing[a].name, ing[b].name
            items.append({"id": f"{split}:t2_pair_contraindication:{k:03d}", "split": split, "type": "T2",
                          "intent": "t2_pair_contraindication", "template_idx": ti,
                          "question": templates[ti].format(ing_a_label=la, ing_b_label=lb),
                          "slots": {"ing_a": {"uid": a, "label": la}, "ing_b": {"uid": b, "label": lb}},
                          "answer": {"verdict": "금기" if rules else "해당없음", "rule_ids": sorted(rules), "uids": [a, b]},
                          "meta": {"polarity": "pos" if rules else "neg", "heldout_law": False}})
            slots.append({"intent": "t2_pair_contraindication", "slots": {"ing_a": a, "ing_b": b}})
    return items, slots


# ---------- T4 · T6 슬롯 ----------

@dataclass
class Slot:
    key: tuple  # 시드 정렬 키
    slots: dict  # 이름 → (uid 또는 값, 라벨)
    answer: dict
    meta: dict


def pick_slots(intent: str, cands: list[Slot], n: dict, report: dict) -> dict[str, list[Slot]]:
    """정답이 비지 않고 200행 이하인 슬롯을 시드 순서로 test → dev(겹치지 않음)."""
    ok = [s for s in cands if (s.answer.get("n", 0) > 0 if "n" in s.answer
                                 else 0 < len(s.answer.get("uids") or []) <= RESULT_ROW_MAX)]
    report.setdefault("row_cap_dropped", {})[intent] = len(cands) - len(ok)
    order = seeded(ok, f"{intent}:order", key=lambda s: s.key)
    if len(order) < n["test"] + n["dev"]:
        raise SystemExit(f"{intent}: 슬롯 {len(order)}개 < 필요 {n['test'] + n['dev']}")
    return {"test": order[:n["test"]], "dev": order[n["test"]:n["test"] + n["dev"]]}


def slot_items(w: World, intent: str, chosen: dict[str, list[Slot]], templates: list[str]) -> list[dict]:
    out = []
    tp = rng(f"{intent}:template")
    for split in ("test", "dev"):
        for k, s in enumerate(chosen[split]):
            ti = tp.randrange(len(templates))
            fill = {}
            for name, (v, label) in s.slots.items():
                fill[f"{name}_label"] = label
                fill[name] = label
            law_uids = [v for v, _ in s.slots.values() if str(v).startswith("art:")]
            law_uids += [u for u in (s.answer.get("uids") or []) if u.startswith("art:")]
            out.append({"id": f"{split}:{intent}:{k:03d}", "split": split, "type": INTENT_TYPE[intent],
                        "intent": intent, "template_idx": ti, "question": templates[ti].format(**fill),
                        "slots": {name: {"uid": v, "label": label} for name, (v, label) in s.slots.items()},
                        "answer": s.answer, "meta": {**s.meta, "heldout_law": heldout_law_tag(w, law_uids)}})
    return out


INTENT_TYPE: dict[str, str] = {}


def t4_t6_slots(w: World, disp_ans: dict, crefs: dict, excl_disp: set, excl_case: set, citing: dict,
                report: dict) -> dict[str, list[Slot]]:
    out: dict[str, list[Slot]] = {}
    statute = {lid for lid, l in w.laws.items() if l.kind == "법률"}
    kind_of = {a: w.laws[a.split(":")[1]].kind for a in w.articles}

    # m1: (법률 조문, 대통령령 | 총리령) → 그 종류 법령의 위임 조문
    out["m1_delegated_articles"] = [
        Slot((a, kind), {"article": (a, article_label(w, a)), "target_kind": (kind, KIND_LABEL[kind])},
             {"uids": [t for t in ts if kind_of[t] == kind]}, {})
        for a, ts in w.delegations.items() if a.split(":")[1] in statute
        for kind in KIND_LABEL if any(kind_of[t] == kind for t in ts)]

    # m2_case: 남은 판례, 라벨(법원 + 사건번호)이 유일, 참조조문에 구법 참조 없음(숨은 조건) → 현행 참조 조문의 시행령 위임
    labels = Counter(case_label(f) for pid, f in w.precs.items() if pid not in excl_case)
    cases, hidden = [], 0
    for pid, res in crefs.items():
        if pid in excl_case or labels[case_label(w.precs[pid])] > 1:
            continue
        uids = list(dict.fromkeys(t for a in ref_articles(res, True) for t in w.delegations.get(a, [])
                                  if kind_of[t] == "대통령령"))
        if any(r.historical for r in res.refs):
            hidden += bool(uids)
            continue
        cases.append(Slot((pid,), {"case": (f"case:{pid}", case_label(w.precs[pid]))}, {"uids": uids}, {}))
    out["m2_case_ref_delegated"] = cases
    report["m2_case_hidden_dropped"] = hidden

    # m2_disposition: 남은 처분, 정규화 문구가 남은 처분 안에서 유일, 가림 표시 · 조문 표기 없음 → 근거 조문의 위임 조문
    left = {s: r for s, r in w.disp.items() if s not in excl_disp}
    norm = Counter(G.normalize_violation(r.get("VILTCN") or "") for r in left.values())
    disps, art_ref = [], 0
    for s, r in left.items():
        label = violation_text(r.get("VILTCN"))
        if not label or norm[G.normalize_violation(r.get("VILTCN") or "")] > 1 or has_mask(label):
            continue
        uids = list(dict.fromkeys(t for a in disp_ans[s] for t in w.delegations.get(a, [])))
        if G.has_article_ref(label):
            art_ref += bool(uids)
            continue
        disps.append(Slot((s,), {"disposition": (f"disp:food:{s}", label)}, {"uids": uids}, {}))
    out["m2_disposition_basis_delegated"] = disps
    report["m2_disposition_article_ref_dropped"] = art_ref

    # m3: 성분 X → pair 상대 중 preg · age 규칙이 있는 성분(uid 단위), 근거는 그 pair 규칙과 preg · age 규칙
    pair_of: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    rules_of: dict[str, dict[str, list[str]]] = {"preg": defaultdict(list), "age": defaultdict(list)}
    names = {}
    for r in w.dur:
        for i in r.ingredients:
            names[i.uid] = i.name
        if r.type == "pair":
            us = [i.uid for i in r.ingredients]
            for x in us:
                for y in us:
                    if x != y:
                        pair_of[x][y].append(r.uid)
        else:
            for i in r.ingredients:
                rules_of[r.type][i.uid].append(r.uid)
    for intent, typ in (("m3_pair_and_pregnancy", "preg"), ("m3_pair_and_age", "age")):
        slots = []
        for x in pair_of:
            ys = sorted(y for y in pair_of[x] if rules_of[typ].get(y))
            ev = sorted({u for y in ys for u in pair_of[x][y] + rules_of[typ][y]})
            slots.append(Slot((x,), {"ingredient": (x, names[x])}, {"uids": ys, "evidence": ev} if ys else {}, {}))
        out[intent] = slots

    # T6(제외 목록 적용 값, 대조용으로 적용 전 값도 둠)
    by_art_all, by_art_left = Counter(), Counter()
    for s, ans in disp_ans.items():
        for a in set(ans):
            by_art_all[a] += 1
            by_art_left[a] += s not in excl_disp
    out["t6_dispositions_by_article"] = [
        Slot((a,), {"article": (a, article_label(w, a))}, {"n": n}, {"n_without_exclusion": by_art_all[a]})
        for a, n in by_art_left.items() if n]
    it_all, it_left = Counter(), Counter()
    for s, r in w.disp.items():
        k = (r.get("INDUTY_CD_NM"), r.get("DSPS_TYPECD_NM"))
        if all(k):
            it_all[k] += 1
            it_left[k] += s not in excl_disp
    out["t6_dispositions_by_industry_type"] = [
        Slot(k, {"industry": (k[0], k[0]), "disp_type": (k[1], k[1])}, {"n": n}, {"n_without_exclusion": it_all[k]})
        for k, n in it_left.items() if n]
    pair_n = Counter(u for r in w.dur if r.type == "pair" for u in {i.uid for i in r.ingredients})
    out["t6_pair_rules_by_ingredient"] = [
        Slot((x,), {"ingredient": (x, names[x])}, {"n": n}, {"n_without_exclusion": n}) for x, n in pair_n.items()]
    out["t6_delegated_count"] = [
        Slot((a,), {"article": (a, article_label(w, a))}, {"n": len(ts)}, {"n_without_exclusion": len(ts)})
        for a, ts in w.delegations.items() if a.split(":")[1] in statute and ts]
    return out


def m4_pool(w: World, citing: dict, report: dict) -> tuple[list[dict], dict]:
    """M4 대상: 식약처 법률의 현행 조문을 시드 순서로, 문자열 후보 1~200개 + 숨은 조건(refs 기준) 일치인 조문.
    풀 1~36번째(test), 37~42번째(dev), 43~48번째(dev 예비)."""
    pop = [a.uid for a in w.articles.values() if w.laws[a.law_id].kind == "법률" and not a.deleted]
    aliases = M4.law_aliases(w.admin)
    out, cands, why = [], {}, Counter()
    for a in seeded(pop, "m4:order", key=G.art_key):
        if len(out) >= M4_RESERVE_RANKS[1]:
            break
        cs = M4.candidates(a, w.laws, w.articles, w.admin, w.sub, aliases)
        n = len(M4.sources(cs))
        lid = a.split(":")[1]
        scope = M4.descendants(lid, w.sub) | {lid}
        cited = citing.get(a, {})
        everything = set(cited)
        scoped = {s for s, current in cited.items()
                  if current and (s.startswith("admart:") or s.split(":")[1] in scope)}
        if n == 0:
            why["후보 0"] += 1
        elif n > RESULT_ROW_MAX:
            why["후보 200 초과"] += 1
        elif everything != scoped:
            why["숨은 조건 불일치(범위 밖 · 구법 인용)"] += 1
        else:
            rank = len(out) + 1
            role = "test" if rank <= M4_POOL else "dev" if rank <= M4_DEV_RANKS[1] else "reserve"
            out.append({"uid": a, "rank": rank, "role": role, "n_candidates": n, "refs_scoped": sorted(scoped)})
            if role == "test":
                cands[a] = cs
    report["m4_filtered"] = dict(why)
    return out, cands


# ---------- 거절 세트 ----------

def refusal_items(w: World, conf: dict) -> tuple[list[dict], dict]:
    kinds = conf["kinds"]
    topics = list(conf["topics"])
    out, used = [], {"law_names": [], "missing_articles": [], "topics": []}

    def emit(kind: str, split: str, k: int, ti: int, fill: dict, slots: dict, must: dict) -> None:
        out.append({"id": f"{split}:refusal:{kind}:{k:03d}", "split": split, "type": "T5", "intent": None,
                    "template_idx": ti, "question": kinds[kind]["templates"][ti].format(**fill),
                    "slots": slots, "answer": {"refuse": True, "t2c": kinds[kind]["t2c_correct"], "must_contain": must},
                    "meta": {"kind": kind, "heldout_law": False}})

    names = [n for n in w.other_laws if not law_ids_in(w, n)]
    names = seeded(names, "refusal:other")
    tp = rng("refusal:template")
    k_all = 0
    for split, idx in (("test", 1), ("dev", 0)):
        n = REFUSAL_KINDS["other_ministry_law"][idx]
        for k in range(n):
            name = names[k_all]
            k_all += 1
            ti = tp.randrange(len(kinds["other_ministry_law"]["templates"]))
            art = rng(f"refusal:other:{name}").randint(1, 30)
            topic = topics[rng(f"refusal:other-topic:{name}").randrange(len(topics))]
            emit("other_ministry_law", split, k, ti, {"law_name": name, "article_no": art, "topic": topic},
                 {"law_name": {"uid": None, "label": name}}, {"law_name": name})
            used["law_names"].append(name)
    lids = seeded([lid for lid in w.laws], "refusal:missing")
    k_all = 0
    for split, idx in (("test", 1), ("dev", 0)):
        for k in range(REFUSAL_KINDS["missing_article"][idx]):
            lid = lids[k_all]
            k_all += 1
            last = max(int(a.no.split("-")[0]) for a in w.articles.values() if a.law_id == lid)
            no = last + rng(f"refusal:missing:{lid}").randint(5, 30)
            if f"art:{lid}:{no}" in w.articles:
                raise SystemExit(f"없는 조문이 있다: {lid} {no}")
            ti = tp.randrange(len(kinds["missing_article"]["templates"]))
            name = w.laws[lid].name
            emit("missing_article", split, k, ti, {"law_name": name, "article_no": no},
                 {"law_name": {"uid": f"law:{lid}", "label": name}, "article_no": {"uid": None, "label": str(no)}},
                 {"law_name": name, "article_no": no})
            used["missing_articles"].append({"law_id": lid, "article_no": no})
    combos = seeded([(t, ti) for t in topics for ti in range(len(kinds["out_of_scope"]["templates"]))], "refusal:oos")
    k_all = 0
    for split, idx in (("test", 1), ("dev", 0)):
        for k in range(REFUSAL_KINDS["out_of_scope"][idx]):
            topic, ti = combos[k_all]
            k_all += 1
            emit("out_of_scope", split, k, ti, {"topic": topic}, {"topic": {"uid": None, "label": topic}}, {})
            used["topics"].append(topic)
    used["topics"] = sorted(set(used["topics"]))
    return out, used


# ---------- held-out ----------

def heldout(units: dict[str, list[str]], ev: dict) -> list[str]:
    size = {u: sum(ev[i]["n"]["test"] for i in names) for u, names in units.items()}
    total, chosen = 0, []
    for u in seeded(units, "heldout"):
        if total + size[u] > HELDOUT_CAP:
            continue
        chosen.append(u)
        total += size[u]
        if total >= HELDOUT_MIN:
            break
    return [i for u in sorted(chosen) for i in units[u]]


def heldout_io(mode: str | None, intents_text: str, computed: list[str]) -> str:
    current = yaml.safe_load(intents_text).get("heldout_intents")
    if mode == "write":
        if current is not None:
            raise SystemExit("intents.yaml heldout_intents가 이미 있다(한 번만 기록)")
        line = "heldout_intents: [" + ", ".join(computed) + "]"
        INTENTS.write_text(re.sub(r"(?m)^heldout_intents: null$", line, intents_text), encoding="utf-8")
        return "기록함"
    if mode == "check":
        if current != computed:
            raise SystemExit(f"held-out 계산값 {computed} ≠ intents.yaml {current}")
        return "intents.yaml과 같음"
    return f"기록 안 함(지금 intents.yaml: {current})"


# ---------- 산출 ----------

def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def dump_jsonl(items: list[dict]) -> bytes:
    return "".join(json.dumps(x, ensure_ascii=False, sort_keys=True) + "\n" for x in items).encode("utf-8")


def build(S: Path, report: dict) -> dict:
    check_constants()
    conf_intents = yaml.safe_load(INTENTS.read_text(encoding="utf-8"))
    ev = check_intents(conf_intents["intents"])
    for name, i in ev.items():
        INTENT_TYPE[name] = i["type"]
    qs = {f: yaml.safe_load((QUESTIONS / f"{f}.yaml").read_text(encoding="utf-8"))
          for f in ("t1_t3", "t2_t4_t6", "refusal")}
    tpl = {x["intent"]: x["templates"] for x in qs["t2_t4_t6"]["intents"]}

    w = load_world(S)
    disp_ans = disposition_answers(w)
    crefs = case_refs(w)
    report["t1_historical_refs"] = sum(r.historical for s in w.disp
                                       for r in w.resolver.resolve(w.disp[s].get("LAWORD_CD_NM") or "").refs)
    t1, t3, freq = t1_t3_pools(w, disp_ans, crefs)

    # 층 메타: 제외 뒤 남은 원천에서 대표 조문을 근거로 한 처분(T1) · 판례(T3) 수
    left_t1 = Counter(a for s, ans in disp_ans.items() if s not in t1.excluded for a in set(ans))
    left_t3 = Counter(a for pid, res in crefs.items() if pid not in t3.excluded for a in set(ref_articles(res, True)))
    items = t1_t3_items(w, t1, left_t1, qs["t1_t3"]["t1"], "violation")
    items += t1_t3_items(w, t3, left_t3, qs["t1_t3"]["t3"], "summary")
    report["t1"], report["t3"] = t1, t3

    excl_slots: list[dict] = []
    t2, s2 = t2_items(w, tpl["t2_pair_contraindication"], report, ev["t2_pair_contraindication"]["n"]["dev"])
    items += t2
    excl_slots += s2

    citing = citing_index(w)
    pools = t4_t6_slots(w, disp_ans, crefs, t1.excluded, t3.excluded, citing, report)
    compare = []
    for intent, cands in pools.items():
        chosen = pick_slots(intent, cands, ev[intent]["n"], report)
        new = slot_items(w, intent, chosen, tpl[intent])
        items += new
        for it in new:
            excl_slots.append({"intent": intent, "slots": {k: v["uid"] for k, v in it["slots"].items()}})
            compare.append({"id": it["id"], "intent": intent, "params": {k: v["uid"] for k, v in it["slots"].items()},
                            "answer": it["answer"], "meta": it["meta"]})
    for it in t2:
        compare.append({"id": it["id"], "intent": "t2_pair_contraindication",
                        "params": {k: v["uid"] for k, v in it["slots"].items()}, "answer": it["answer"]})

    m4, m4_cands = m4_pool(w, citing, report)
    tp = rng("m4_citing_articles:template")
    counters = Counter()
    for m in m4:
        split = "test" if m["role"] == "test" else "dev"
        k = counters[split]
        counters[split] += 1
        ti = tp.randrange(len(tpl["m4_citing_articles"]))
        label = article_label(w, m["uid"])
        items.append({"id": f"{split}:m4_citing_articles:{k:03d}", "split": split, "type": "T4-M4",
                      "intent": "m4_citing_articles", "template_idx": ti,
                      "question": tpl["m4_citing_articles"][ti].format(article_label=label),
                      "slots": {"article": {"uid": m["uid"], "label": label}}, "answer": None,
                      "meta": {"pool_rank": m["rank"], "reserve": m["role"] == "reserve", "role": m["role"],
                               "n_candidates": m["n_candidates"], "heldout_law": heldout_law_tag(w, [m["uid"]])}})
        excl_slots.append({"intent": "m4_citing_articles", "slots": {"article": m["uid"]}})
        compare.append({"id": items[-1]["id"], "intent": "m4_citing_articles", "params": {"article": m["uid"]},
                        "answer": {"uids": m["refs_scoped"]}, "meta": {"role": m["role"]}})

    refusal, used = refusal_items(w, qs["refusal"])
    held = heldout(conf_intents["heldout_units"], ev)
    report.update({"world": w, "items": items, "refusal": refusal, "m4": m4, "heldout": held, "used": used})
    return {
        "items": items, "refusal": refusal, "compare": compare, "m4_cands": m4_cands, "heldout": held,
        "exclusion": {"disposition": sorted(t1.excluded, key=int), "case": sorted(t3.excluded, key=int),
                      "slots": excl_slots, "refusal": used,
                      "questions": sorted({x["question"] for x in items + refusal})},
    }


def print_report(r: dict, held_note: str) -> None:
    w, items, refusal = r["world"], r["items"], r["refusal"]
    ev_items = [x for x in items if not (x["type"] == "T4-M4" and x["meta"].get("reserve"))]
    print(f"# D4 생성 보고 ({SNAPSHOT}, SEED {SEED})")  # 로컬 절대경로를 데이터 카드에 남기지 않는다
    cnt = Counter((x["split"], x["intent"] or x["type"]) for x in ev_items)
    cnt_r = Counter((x["split"], x["meta"]["kind"]) for x in refusal)
    print("1. 유형별 생성 건수(dev · test):")
    for k in sorted({k for _, k in cnt}):
        print(f"   - {k}: dev {cnt['dev', k]} · test {cnt['test', k]}")
    for k in REFUSAL_KINDS:
        print(f"   - 거절 {k}: dev {cnt_r['dev', k]} · test {cnt_r['test', k]} (배분 {REFUSAL_KINDS[k]})")
    by_type = Counter((x["split"], x["type"]) for x in ev_items)
    print(f"   - 합계: test {sum(v for (s, _), v in by_type.items() if s == 'test')} · dev "
          f"{sum(v for (s, _), v in by_type.items() if s == 'dev')}, 거절 test {sum(1 for x in refusal if x['split'] == 'test')} · "
          f"dev {sum(1 for x in refusal if x['split'] == 'dev')}, M4 dev 예비 "
          f"{sum(1 for x in items if x['type'] == 'T4-M4' and x['meta'].get('reserve'))}")
    for p in (r["t1"], r["t3"]):
        print(f"2. {p.name} 상한 20 적용 후 채울 수 있는 문항: {p.sel.fillable} (필요 {T1_T3_REQUIRED})")
    for p in (r["t1"], r["t3"]):
        print(f"3. {p.name} dev-test 충돌로 버린 dev {p.sel.dev_dropped}, 이웃 포함 50건 초과 후보 {p.over_neighbor}/{len(p.cands)}")
    for p in (r["t1"], r["t3"]):
        print(f"4. {p.name} 대체 규칙: {p.fallback}")
    for p in (r["t1"], r["t3"]):
        print(f"5. {p.name} 제외 목록 {len(p.excluded):,}건 / 전체 {p.total:,} = {len(p.excluded) / p.total:.1%} (기준 30%)")
    for t in ("T1", "T3"):
        test = [x for x in items if x["type"] == t and x["split"] == "test"]
        z = sum(x["meta"]["rep_left"] == 0 for x in test)
        f = sum(1 <= x["meta"]["rep_left"] <= 4 for x in test)
        print(f"6. {t} test 문항 중 대표 조문의 남은 원천 0건 {z} · 1~4건 {f} / {len(test)}")
    for t in ("T1", "T3"):
        xs = [x for x in items if x["type"] == t]
        print(f"7. {t} 문항(dev + test {len(xs)}) 중 원문에 정답 조문이 읽힘 {sum(x['meta']['answer_in_original'] for x in xs)}, "
              f"가린 문항 {sum(x['meta']['article_masked'] for x in xs)}, 가린 뒤 남은 조문 참조 0(확인 규칙 통과), "
              f"정답 법령명 남음 {sum(x['meta']['law_name_left'] for x in xs)} · 같은 계열 {sum(x['meta']['law_family_left'] for x in xs)}")
    print(f"8. m2_disposition 슬롯 후보에서 조문 표기로 빠진 수(정답 있는 것): {r['m2_disposition_article_ref_dropped']}")
    print(f"9. T2 음성 표집에서 뺀 쌍: {r['t2_neg_dropped']}")
    print(f"10. 숨은 조건으로 버린 T4 슬롯: m2_case(구법 참조 있음, 정답 있는 것) {r['m2_case_hidden_dropped']}; "
          f"M4 풀까지 걸러진 조문 {r['m4_filtered']}; 정답 200행 초과 · 빈 정답으로 빠진 슬롯 {r['row_cap_dropped']}")
    print("11. 템플릿 실행 확인: `scripts/check_templates.py`로 따로 확인"
          "(결과: `docs/week1-notes.md` D4, 532 슬롯 불일치 0)")
    print(f"12. held-out 단위 선택: {r['heldout']} ({held_note})")
    for t in ("T1", "T3"):
        test = [x["meta"]["rep_law_articles"] for x in items if x["type"] == t and x["split"] == "test"]
        print(f"- {t} test 대표 조문 법령의 조문 수 중앙값: {statistics.median(test)}")
    print(f"- heldout_law(의료기기법 계열) 문항: {sum(x['meta'].get('heldout_law', False) for x in items)}")
    print(f"- T1 근거 조항 refs 중 구법 표시: {r['t1_historical_refs']}")
    for p in (r["t1"], r["t3"]):  # select는 dev-test 충돌만 막는다(PREREG 4-4). 같은 split 안 이어짐은 수치로만 남긴다
        links = {s: sum(G.conflicts(a, b) for i, a in enumerate(xs) for b in xs[i + 1:])
                 for s, xs in (("test", p.sel.test), ("dev", p.sel.dev))}
        print(f"- {p.name} 같은 split 안에서 근접 중복 · 같은 업소로 이어진 문항 쌍: test {links['test']} · dev {links['dev']}")


def check_locked(heldout_mode: str | None) -> None:
    """파일을 쓰는 생성은 잠긴 코드로만 한다(PREREG 4-7): `--heldout check`, 잠금 기록 있음, 기록 없는 변경 없음."""
    if heldout_mode != "check":
        raise SystemExit("파일 생성은 --heldout check로만 한다(보고만 보려면 --dry-run)")
    if not (ROOT / prereg.LOCK_FILE).exists():
        raise SystemExit(f"{prereg.LOCK_FILE}이 없다. 사용자 검토 뒤 `python eval/prereg.py lock`을 먼저 한다")
    status = prereg.prereg_status(ROOT)
    if not status["ok"]:
        bad = [k for k, s in status["status"].items() if s == "기록 없는 변경(탐색적)"]
        raise SystemExit(f"잠금 대상에 기록 없는 변경이 있다: {bad}")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")  # 가드 SystemExit 메시지(한글)가 UTF-8 터미널에서 깨지지 않게
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", default=SNAPSHOT)
    ap.add_argument("--dry-run", action="store_true", help="보고만 출력하고 저장소 안에 아무것도 쓰지 않는다")
    ap.add_argument("--out", default="eval/evalset/v1")
    ap.add_argument("--compare-out", help="D4 템플릿 대조용 슬롯 · 원자료 정답 JSON(저장소 밖 scratchpad 경로)")
    ap.add_argument("--heldout", choices=["write", "check"])
    args = ap.parse_args()
    if args.snapshot != SNAPSHOT:
        raise SystemExit(f"스냅숏은 PREREG SNAPSHOT({SNAPSHOT})만 받는다")
    if args.dry_run and args.heldout == "write":
        raise SystemExit("--dry-run과 --heldout write는 함께 쓰지 않는다")
    if args.compare_out and Path(args.compare_out).resolve().is_relative_to(ROOT):
        raise SystemExit("--compare-out은 저장소 밖 경로여야 한다")
    if not args.dry_run and args.heldout != "write":
        check_locked(args.heldout)
    report: dict = {}
    out = build(ROOT / args.snapshot, report)
    held_note = heldout_io(args.heldout, INTENTS.read_text(encoding="utf-8"), out["heldout"])
    print_report(report, held_note)
    if args.compare_out:
        Path(args.compare_out).write_text(json.dumps(out["compare"], ensure_ascii=False, indent=1), encoding="utf-8")
    if args.dry_run or args.heldout == "write":
        return
    d = ROOT / args.out
    (d / "m4_candidates").mkdir(parents=True, exist_ok=True)
    files = {
        d / "test.jsonl": dump_jsonl([x for x in out["items"] if x["split"] == "test"]),
        d / "dev.jsonl": dump_jsonl([x for x in out["items"] if x["split"] == "dev"]),
        d / "refusal.jsonl": dump_jsonl(out["refusal"]),
        ROOT / "eval" / "exclusion_ids.json": json.dumps(out["exclusion"], ensure_ascii=False, indent=1,
                                                         sort_keys=True).encode("utf-8"),
    }
    for uid, cs in out["m4_cands"].items():
        files[d / "m4_candidates" / f"{uid.replace(':', '_')}.jsonl"] = dump_jsonl(
            [{"target": uid, "source": c.source, "unit": c.unit, "snippet": c.snippet} for c in cs])
    for p, b in files.items():
        p.write_bytes(b)
    card = data_card(report, held_note).encode("utf-8")  # 바이트로 써서 OS 줄 끝 변환이 해시를 바꾸지 않게
    (d / "data_card.md").write_bytes(card)
    files[d / "data_card.md"] = card
    for p, b in sorted(files.items()):
        print(f"{sha256_bytes(b)}  {p.relative_to(ROOT).as_posix()}")


def data_card(r: dict, held_note: str) -> str:
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        print_report(r, held_note)
    return "# 평가셋 v1 데이터 카드\n\n생성: `eval/build_evalset.py` (잠긴 코드, SEED " \
           f"{SEED}). 아래는 생성 보고 그대로입니다.\n\n" + buf.getvalue()


if __name__ == "__main__":
    main()
