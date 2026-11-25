"""eval/build_evalset.py · eval/sources.py: import 금지, 결정성, CONSTANTS · intents 대조, dry-run 쓰기 금지, T2 음성 관계 쌍,
M3 정답 두 겹, M4 풀 역할, 거절 세트 없는 조문, 확인 규칙 중단, 업소 정보 미출력, held-out 다섯 결과.

스냅숏은 git 밖이라 쓰지 않는다. 합성 자료(가짜 업소명)와 고정 자료만 쓴다.
"""

import ast
import json
import sys
from collections import Counter
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "eval"))
import build_evalset as B  # noqa: E402
import sources  # noqa: E402

from mfds.ingredients import normalize  # noqa: E402
from mfds.refs import LawDict, Resolver  # noqa: E402

FAKE_SHOP = "가짜상회테스트"


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    return out | {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}


def test_generator_does_not_import_loader() -> None:
    for f in ("build_evalset.py", "sources.py", "grouping.py"):
        names = _imports(ROOT / "eval" / f)
        assert not any(n.startswith("mfds.load") for n in names), (f, names)
        # mfds.disposition은 식품 처분 읽기 · 라벨과 의약품 · 화장품 처분(T7) 읽기 · 근거법령 분할을 적재기와 공유한다
        # (정답 독립성 예외, PREREG 4-1)
        allowed = {"mfds.refs", "mfds.ingredients", "mfds.disposition", "mfds.collect"}
        assert {n for n in names if n.startswith("mfds")} <= allowed, (f, names)


def test_constants_and_intents_match_prereg() -> None:
    B.check_constants()
    ev = B.check_intents(yaml.safe_load(B.INTENTS.read_text(encoding="utf-8"))["intents"])
    assert ev["t2_pair_contraindication"]["n"]["test"] == B.T2_POS + B.T2_NEG


def test_constant_mismatch_aborts(monkeypatch) -> None:
    monkeypatch.setattr(B, "SEED", B.SEED + 1)
    with pytest.raises(SystemExit):
        B.check_constants()


def test_intent_n_mismatch_aborts() -> None:
    intents = yaml.safe_load(B.INTENTS.read_text(encoding="utf-8"))["intents"]
    for i in intents:
        if i["name"] == "m4_citing_articles":
            i["n"]["test"] = 31
    with pytest.raises(SystemExit):
        B.check_intents(intents)


def test_seeded_order_is_deterministic_and_stage_specific() -> None:
    xs = [f"k{i}" for i in range(50)]
    assert B.seeded(xs, "a") == B.seeded(list(reversed(xs)), "a")
    assert B.seeded(xs, "a") != B.seeded(xs, "b")


def test_heldout_result_is_one_of_five() -> None:
    conf = yaml.safe_load(B.INTENTS.read_text(encoding="utf-8"))
    ev = {i["name"]: i for i in conf["intents"] if i["use"] == "eval"}
    allowed = [{"m1", "m3"}, {"m1", "m4"}, {"m2", "m3"}, {"m2", "m4"}, {"m3", "m4"}]
    got = B.heldout(conf["heldout_units"], ev)
    units = {u for u, names in conf["heldout_units"].items() if set(names) <= set(got)}
    assert units in allowed
    assert B.heldout(conf["heldout_units"], ev) == got


def test_heldout_write_once_then_check(tmp_path, monkeypatch) -> None:
    p = tmp_path / "intents.yaml"
    p.write_text("version: '0.1'\nheldout_intents: null\nintents: []\n", encoding="utf-8")
    monkeypatch.setattr(B, "INTENTS", p)
    assert B.heldout_io("write", p.read_text(encoding="utf-8"), ["m4_citing_articles"]) == "기록함"
    text = p.read_text(encoding="utf-8")
    assert yaml.safe_load(text)["heldout_intents"] == ["m4_citing_articles"]
    with pytest.raises(SystemExit):
        B.heldout_io("write", text, ["m4_citing_articles"])
    B.heldout_io("check", text, ["m4_citing_articles"])
    with pytest.raises(SystemExit):
        B.heldout_io("check", text, ["m1_delegated_articles"])


def test_dry_run_writes_nothing_in_repo(tmp_path, monkeypatch) -> None:
    out = {"items": [], "refusal": [], "compare": [{"id": "x"}], "m4_cands": {}, "heldout": [], "exclusion": {}}
    monkeypatch.setattr(B, "build", lambda S, report: out)
    monkeypatch.setattr(B, "print_report", lambda r, note: None)
    monkeypatch.setattr(B, "heldout_io", lambda mode, text, comp: "")
    written: list[Path] = []
    monkeypatch.setattr(Path, "write_bytes", lambda self, b: written.append(self))
    cmp_path = tmp_path / "compare.json"
    monkeypatch.setattr(sys, "argv", ["build_evalset.py", "--dry-run", "--compare-out", str(cmp_path)])
    B.main()
    assert written == [] and json.loads(cmp_path.read_text(encoding="utf-8")) == [{"id": "x"}]
    monkeypatch.setattr(sys, "argv", ["build_evalset.py", "--dry-run", "--compare-out", str(ROOT / "x.json")])
    with pytest.raises(SystemExit):
        B.main()


def test_write_needs_lock_and_heldout_check(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(B, "build", lambda S, report: pytest.fail("잠금 확인 전에 생성하면 안 된다"))
    monkeypatch.setattr(sys, "argv", ["build_evalset.py"])
    with pytest.raises(SystemExit, match="--heldout check"):
        B.main()
    monkeypatch.setattr(B, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["build_evalset.py", "--heldout", "check"])
    with pytest.raises(SystemExit, match="lock"):
        B.main()
    (tmp_path / B.prereg.LOCK_FILE).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / B.prereg.LOCK_FILE).write_text("{}", encoding="utf-8")
    bad = {"ok": False, "status": {"eval/grouping.py": "기록 없는 변경(탐색적)", "eval/sources.py": "잠금 그대로"}}
    monkeypatch.setattr(B.prereg, "prereg_status", lambda root: bad)
    with pytest.raises(SystemExit, match="grouping"):
        B.check_locked("check")
    bad_tool = {"ok": False, "status": {"eval/prereg.py": "기록 없는 변경(도구)", "eval/sources.py": "기준과 같음"}}
    monkeypatch.setattr(B.prereg, "prereg_status", lambda root: bad_tool)
    with pytest.raises(SystemExit, match="eval/prereg.py"):  # 도구 항목도 원인으로 보인다(2026-11-25)
        B.check_locked("check")
    monkeypatch.setattr(B.prereg, "prereg_status", lambda root: {"ok": True, "status": {}})
    B.check_locked("check")


def test_write_refuses_nonempty_out(tmp_path, monkeypatch) -> None:
    """출력 디렉터리에 파일이 있으면 생성(build) 전에 멈춘다. 없거나 빈 디렉터리는 통과(2026-11-25)."""
    monkeypatch.setattr(B, "build", lambda S, report: pytest.fail("출력 디렉터리 확인 전에 생성하면 안 된다"))
    monkeypatch.setattr(B, "check_locked", lambda mode: None)
    monkeypatch.setattr(B, "ROOT", tmp_path)
    out = tmp_path / "eval" / "evalset" / "v1"
    (out / "m4_candidates").mkdir(parents=True)
    (out / "m4_candidates" / "a.jsonl").write_text("{}\n", encoding="utf-8")  # 하위 디렉터리 파일도 잡는다
    monkeypatch.setattr(sys, "argv", ["build_evalset.py", "--heldout", "check"])
    with pytest.raises(SystemExit, match="v1-r"):
        B.main()
    B.check_out_empty(tmp_path / "eval" / "evalset" / "v2")  # 없는 디렉터리
    (tmp_path / "eval" / "evalset" / "v3").mkdir()
    B.check_out_empty(tmp_path / "eval" / "evalset" / "v3")  # 빈 디렉터리


def _world(**kw) -> NS:
    base = dict(laws={}, articles={}, delegations={}, precs={}, disp={}, dur=[], admin={}, sub={}, other_laws=[],
                drug_disp={})
    base.update(kw)
    return NS(**base)


def _rule(uid: str, typ: str, *raw: str) -> sources.DurRule:
    return sources.DurRule(uid, typ, tuple(normalize(r) for r in raw))


def test_t2_negatives_skip_pair_and_relation(monkeypatch) -> None:
    monkeypatch.setattr(B, "T2_POS", 1)
    monkeypatch.setattr(B, "T2_DEV_POS", 0)
    monkeypatch.setattr(B, "T2_NEG", 3)
    dur = [_rule("dur:pair:a:b", "pair", "Aaa", "Bbb"),
           _rule("dur:pair:c:d", "pair", "Ccc hydrochloride", "Ddd"),
           _rule("dur:pair:e:f", "pair", "Eee", "Fff"),
           _rule("dur:pair:g:h", "pair", "Ggg", "Hhh"),
           _rule("dur:pair:x:ccc", "pair", "Xxx", "Ccc sulfate")]  # Ccc 염 형태 → c 계열과 관계
    report: dict = {}
    items, slots = B.t2_items(_world(dur=dur), ["{ing_a_label}와 {ing_b_label}"], report, n_dev=1)
    pos = {tuple(sorted(r.ingredients[i].uid for i in range(2))) for r in dur}
    for it in items:
        pair = tuple(sorted(it["answer"]["uids"]))
        if it["meta"]["polarity"] == "neg":
            assert pair not in pos and it["answer"]["rule_ids"] == []
            assert not ({"ing:ccc hydrochloride", "ing:ddd"} <= set(pair)) and not ({"ing:ccc sulfate", "ing:ddd"} <= set(pair))
    test_pairs = {tuple(sorted(x["answer"]["uids"])) for x in items if x["split"] == "test"}
    dev_pairs = {tuple(sorted(x["answer"]["uids"])) for x in items if x["split"] == "dev"}
    assert not test_pairs & dev_pairs and len(slots) == len(items)


def test_m3_answers_have_uids_and_evidence() -> None:
    dur = [_rule("dur:pair:a:b", "pair", "Aaa", "Bbb"), _rule("dur:pair:a:c", "pair", "Aaa", "Ccc"),
           _rule("dur:preg:b", "preg", "Bbb"), _rule("dur:age:c:x", "age", "Ccc")]
    pools = B.t4_t6_slots(_world(dur=dur), {}, {}, set(), set(), {}, {})
    preg = {s.slots["ingredient"][0]: s.answer for s in pools["m3_pair_and_pregnancy"]}
    assert preg["ing:aaa"] == {"uids": ["ing:bbb"], "evidence": ["dur:pair:a:b", "dur:preg:b"]}
    age = {s.slots["ingredient"][0]: s.answer for s in pools["m3_pair_and_age"]}
    assert age["ing:aaa"] == {"uids": ["ing:ccc"], "evidence": ["dur:age:c:x", "dur:pair:a:c"]}
    assert preg["ing:ccc"] == {}  # 상대 aaa에 임부금기 규칙 없음 → 빈 정답(슬롯으로 뽑지 않음)


def test_m4_pool_roles_do_not_overlap(monkeypatch) -> None:
    monkeypatch.setattr(B, "M4_POOL", 2)
    monkeypatch.setattr(B, "M4_DEV_RANKS", (2, 3))
    monkeypatch.setattr(B, "M4_RESERVE_RANKS", (3, 4))
    law = sources.Law("L", "약사법", "법률", None)
    arts = {f"art:L:{k}": sources.Article(f"art:L:{k}", "L", str(k), None, False, [(f"art:L:{k}", "본문")])
            for k in range(1, 8)}
    arts["art:L:9"] = sources.Article("art:L:9", "L", "9", None, False,
                                      [("art:L:9", "제1조, 제2조, 제3조, 제4조, 제5조, 제6조 및 제7조에 따라")])
    citing = {f"art:L:{k}": {"art:L:9": True} for k in range(1, 8)}
    citing["art:L:5"]["admart:Z:1"] = False  # 구법 인용 → 숨은 조건 불일치
    report: dict = {}
    pool, cands = B.m4_pool(_world(laws={"L": law}, articles=arts), citing, report)
    roles = [m["role"] for m in pool]
    assert roles == ["test", "test", "dev", "reserve"]
    assert len({m["uid"] for m in pool}) == 4 and "art:L:5" not in {m["uid"] for m in pool}
    assert set(cands) == {m["uid"] for m in pool if m["role"] == "test"}
    assert report["m4_filtered"].get("숨은 조건 불일치(범위 밖 · 구법 인용)", 0) <= 1


def test_refusal_missing_article_really_missing(monkeypatch) -> None:
    monkeypatch.setattr(B, "REFUSAL_KINDS", {"other_ministry_law": [1, 1], "missing_article": [1, 1],
                                             "out_of_scope": [1, 1]})
    ld = LawDict({"약사법": "L", "식품위생법": "F"}, {"L": "법률", "F": "법률"}, {"L": "약사법", "F": "식품위생법"})
    laws = {"L": sources.Law("L", "약사법", "법률", None), "F": sources.Law("F", "식품위생법", "법률", None)}
    arts = {f"art:{l}:{k}": sources.Article(f"art:{l}:{k}", l, str(k), None, False, []) for l in "LF" for k in (1, 2, 3)}
    w = _world(laws=laws, articles=arts, lawdict=ld, resolver=Resolver(ld, set(arts)),
               other_laws=["도로교통법", "약사법 특례법", "민법"])
    conf = yaml.safe_load((B.QUESTIONS / "refusal.yaml").read_text(encoding="utf-8"))
    items, used = B.refusal_items(w, conf)
    for m in used["missing_articles"]:
        assert f"art:{m['law_id']}:{m['article_no']}" not in arts and m["article_no"] >= 3 + 5
    assert "약사법 특례법" not in used["law_names"]  # 식약처 법령명 토큰이 든 이름은 뺀다
    assert all(x["answer"]["refuse"] for x in items)


def _t1_world(tmp_path: Path) -> NS:
    """가짜 업소 처분 4건(그중 1건은 위반 내용에 업소명이 들어 있어 가려짐)."""
    rows = []
    for i, v in enumerate(["유통기한 경과 제품 보관", "영업자 준수사항 위반(제44조)", f"{FAKE_SHOP}에서 위생교육 미이수",
                           "건강진단 미실시"], 1):
        rows.append({"DSPSDTLS_SEQ": str(i), "VILTCN": f"(2025010{i}){v}", "LAWORD_CD_NM": "식품위생법 제44조제1항",
                     "PRCSCITYPOINT_BSSHNM": FAKE_SHOP if i == 3 else f"가짜가게{i}", "ADDR": f"가짜시 가짜로 {i}번길 10",
                     "INDUTY_CD_NM": "일반음식점", "DSPS_TYPECD_NM": "과태료"})
    d = tmp_path / "foodsafety" / "I0470"
    d.mkdir(parents=True)
    (d / "p1.json").write_text(json.dumps({"I0470": {"row": rows}}, ensure_ascii=False), encoding="utf-8")
    ld = LawDict({"식품위생법": "F"}, {"F": "법률"}, {"F": "식품위생법"})
    arts = {"art:F:44": sources.Article("art:F:44", "F", "44", None, False, [("art:F:44", "본문")])}
    from mfds.disposition import read_rows
    disp = {r["DSPSDTLS_SEQ"]: r for r in read_rows(tmp_path)}
    return _world(S=tmp_path, laws={"F": sources.Law("F", "식품위생법", "법률", None)}, articles=arts, lawdict=ld,
                  resolver=Resolver(ld, set(arts)), disp=disp)


def test_t1_items_hide_business_info_and_mask(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(B, "T1_T3_TEST", 2)
    monkeypatch.setattr(B, "T1_T3_DEV", 1)
    monkeypatch.setattr(B, "T1_T3_REQUIRED", 1)
    w = _t1_world(tmp_path)
    t1, _, _ = B.t1_t3_pools(w, B.disposition_answers(w), {})
    items = B.t1_t3_items(w, t1, Counter(), ["위반 내용: {violation}"], "violation")
    dump = json.dumps(items, ensure_ascii=False)
    assert FAKE_SHOP not in dump and "가짜가게" not in dump and "가짜시" not in dump
    assert len(items) == 3 and all(x["answer"]["uids"] == ["art:F:44"] for x in items)
    masked = [x for x in items if x["meta"]["article_masked"]]
    assert masked and all("제44조" not in x["question"] for x in masked)
    assert all("식품위생법" not in x["question_law_masked"] for x in items)


def test_check_rule_aborts_when_mask_misses(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(B, "T1_T3_TEST", 2)
    monkeypatch.setattr(B, "T1_T3_DEV", 1)
    monkeypatch.setattr(B, "T1_T3_REQUIRED", 1)
    w = _t1_world(tmp_path)
    t1, _, _ = B.t1_t3_pools(w, B.disposition_answers(w), {})
    monkeypatch.setattr(B.G, "mask_article_refs", lambda t: t)
    with pytest.raises(SystemExit):
        B.t1_t3_items(w, t1, Counter(), ["식품위생법 위반 내용: {violation}"], "violation")


FAKE_CO = "가짜바이오제약"
YAK = "001783"  # 약사법(처분 조문 목록 DISPOSITION_ARTICLES가 실제 법령ID를 쓴다)


def _t7_world(tmp_path: Path, with_food: bool = False) -> NS:
    """가짜 업체 의약품 처분 7건: 정상 4(그중 1건은 본문에 업체명 → 가림 → 원천 제외), 처분 조문만 1, 식품과 같은 번호 1,
    위반 쪽에 항 뒤 조가 붙은 표기(오타 의심 → 원천 제외) 1."""
    viol = ["품질 부적합 제품 판매", "제조관리기록서 미작성(제31조)", f"{FAKE_CO}이 허가 없이 제조", "소재지 멸실",
            "업무정지 기간 중 제조", "표시기재 위반", "시설 미비"]
    basis = ["○ 위반법령: 「약사법」 제62조\n○ 처분근거법령: 「약사법」 제76조", "○ 「약사법」 제31조제4항, 제76조",
             "○ 위반법령: 「약사법」 제31조", "○ 위반법령: 「약사법」 제31조", "○ 「약사법」 제76조",
             "○ 위반법령: 「약사법」 제62조", "○ 위반법령: 「약사법」 제31조제4항제62조"]
    rows = [{"ADM_DISPS_SEQ": str(i), "ENTP_NAME": f"(주){FAKE_CO}" if i == 3 else f"(주)가짜업체{i}",
             "ADDR": "가짜도 가짜시", "BIZRNO": f"99999999{i:02d}", "ENTP_NO": f"9999{i:04d}", "EXPOSE_CONT": v,
             "BEF_APPLY_LAW": b, "ADM_DISPS_NAME": "경고", "LAST_SETTLE_DATE": "20260101"}
            for i, (v, b) in enumerate(zip(viol, basis), 1)]
    d = tmp_path / "drugdisp"
    d.mkdir(parents=True)
    (d / "p001.json").write_text(json.dumps({"body": {"totalCount": len(rows), "items": rows}}, ensure_ascii=False),
                                 encoding="utf-8")
    ld = LawDict({"약사법": YAK}, {YAK: "법률"}, {YAK: "약사법"})
    arts = {f"art:{YAK}:{n}": sources.Article(f"art:{YAK}:{n}", YAK, str(n), None, False, [(f"art:{YAK}:{n}", "본문")])
            for n in (31, 62, 76)}
    from mfds.disposition import read_drug_rows
    drug = {r["ADM_DISPS_SEQ"]: r for r in read_drug_rows(tmp_path)}
    return _world(S=tmp_path, laws={YAK: sources.Law(YAK, "약사법", "법률", None)}, articles=arts, lawdict=ld,
                  resolver=Resolver(ld, set(arts)), drug_disp=drug)


def test_t7_items_answer_violation_only_and_hide_company(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(B, "T1_T3_TEST", 2)
    monkeypatch.setattr(B, "T1_T3_DEV", 1)
    monkeypatch.setattr(B, "T1_T3_REQUIRED", 1)
    w = _t7_world(tmp_path)
    t7, freq = B.t7_pool(w, B.drug_answers(w))
    items = B.t1_t3_items(w, t7, Counter(), ["위반 내용: {violation}"], "violation")
    dump = json.dumps(items, ensure_ascii=False)
    assert FAKE_CO not in dump and "가짜업체" not in dump and "가짜도" not in dump and "99999999" not in dump
    sources_ = {x["meta"]["source"] for x in items}
    assert "3" not in sources_ and "5" not in sources_  # 업체명 가림 처분 · 처분 조문만 있는 처분은 원천에서 빠진다
    assert "7" not in sources_ and "7" not in t7.answers  # 위반 쪽 붙은 조 표기(오타 의심) 처분도 빠진다
    assert all(f"art:{YAK}:76" not in x["answer"]["uids"] for x in items)  # 처분 조문은 정답이 아니다
    assert {tuple(x["answer"]["uids"]) for x in items} <= {(f"art:{YAK}:62",), (f"art:{YAK}:31",)}
    assert all("제31조" not in x["question"] for x in items)
    assert f"art:{YAK}:76" not in freq  # T7 전용 빈도에도 처분 조문은 없다


def test_deleted_answer_articles_are_marked_without_reselection(tmp_path, monkeypatch) -> None:
    """현행 본문이 "삭제"뿐인 정답 조문(2026-11-23): 선택은 그대로, 정답에 `deleted` · 메타 `answer_deleted`를 단다.
    일부 삭제 문항의 파생 메타는 현행 정답 기준, 전부 삭제 문항은 선택 당시(원래 정답) 값."""
    monkeypatch.setattr(B, "T1_T3_TEST", 10)
    monkeypatch.setattr(B, "T1_T3_DEV", 1)
    monkeypatch.setattr(B, "T1_T3_REQUIRED", 1)
    w = _t7_world(tmp_path)
    w.drug_disp["4"]["EXPOSE_CONT"] = "「약사법」 제62조를 위반한 소재지 멸실"
    w.drug_disp["4"]["BEF_APPLY_LAW"] = "○ 위반법령: 「약사법」 제31조, 제62조"
    w.drug_disp["1"]["EXPOSE_CONT"] = "「약사법」 제62조 위반 품질 부적합 제품 판매"
    a62 = f"art:{YAK}:62"
    t7_live = B.t7_pool(w, B.drug_answers(w))[0]
    live = {x["meta"]["source"]: x for x in B.t1_t3_items(w, t7_live, Counter(), ["위반 내용: {violation}"], "violation")}
    w.articles[a62] = sources.Article(a62, YAK, "62", None, True, [(a62, "삭제 <2025.1.1>")])
    t7, _ = B.t7_pool(w, B.drug_answers(w))
    assert [c.key for c in t7.sel.test + t7.sel.dev] == [c.key for c in t7_live.sel.test + t7_live.sel.dev]  # 다시 뽑지 않음
    items = {x["meta"]["source"]: x for x in B.t1_t3_items(w, t7, Counter(), ["위반 내용: {violation}"], "violation")}
    some, whole, none_ = items["4"], items["1"], items["2"]
    assert some["meta"]["answer_deleted"] == "some" and some["answer"]["deleted"] == [a62]
    assert set(some["answer"]["uids"]) == {f"art:{YAK}:31", a62}  # 원래 정답은 그대로 남긴다
    assert live["4"]["meta"]["answer_in_original"] and not some["meta"]["answer_in_original"]  # 현행 정답 기준
    assert whole["meta"]["answer_deleted"] == "all" and whole["answer"]["deleted"] == [a62]
    assert whole["meta"]["answer_in_original"] == live["1"]["meta"]["answer_in_original"] is True  # 선택 당시 값
    assert none_["meta"]["answer_deleted"] == "none" and none_["answer"]["deleted"] == []


def test_t7_family_mix_counts_each_disposition_once() -> None:
    """정답 법령 계열 구성(PREREG 4-2 T7 제외 비율): 시행령은 모법 계열, 계열 둘 이상은 '여러 계열', 정답 빔은 따로, 칸 합 = 처분 수."""
    ld = NS(root=lambda lid: {"004138": YAK}.get(lid, lid), title={YAK: "약사법", "002015": "화장품법", "002025": "마약류법"})
    w = NS(lawdict=ld)
    basis = {"1": {"violation": [f"art:{YAK}:62", "art:004138:33"]},       # 약사법 + 시행령 → 약사법 하나
             "2": {"violation": ["art:002015:13"]},
             "3": {"violation": [f"art:{YAK}:62", "art:002025:3"]},        # 두 계열
             "4": {"violation": []}}
    mix = B.t7_family_mix(w, basis, {"1", "3"})
    assert mix["제외"] == Counter({"약사법": 1, B.MULTI_FAMILY: 1})
    assert mix["남음"] == Counter({"화장품법": 1, B.EMPTY_ANSWER: 1})
    assert sum(mix["제외"].values()) + sum(mix["남음"].values()) == len(basis)


def test_t7_fallback_keeps_neighbor_cap_and_uses_all_fillable(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(B, "T1_T3_TEST", 200)
    monkeypatch.setattr(B, "T1_T3_DEV", 1)
    monkeypatch.setattr(B, "T1_T3_REQUIRED", 230)
    w = _t7_world(tmp_path)
    t7, _ = B.t7_pool(w, B.drug_answers(w))
    assert "T7 규칙" in t7.fallback and "상한 유지" in t7.fallback
    assert len(t7.sel.test) == t7.sel.fillable - 1  # test = 채울 수 있는 수 − dev(절반이 아님)


@pytest.mark.parametrize("total, kept", [(10, "상한 유지"), (1000, "상한 100")])
def test_t1_t3_fallback_shrinks_test_to_fillable_minus_dev(monkeypatch, total, kept) -> None:
    """230 미만이면 test = 채울 수 있는 수 − dev(옛 `÷ 2` 아님, PREREG 4-2 2026-11-25). 상한을 못 올릴 때(제외 > 30%)와
    올려도 모자랄 때 모두 같다."""
    import grouping as G

    monkeypatch.setattr(B, "T1_T3_TEST", 200)
    monkeypatch.setattr(B, "T1_T3_DEV", 3)
    monkeypatch.setattr(B, "T1_T3_REQUIRED", 230)
    cands = [G.Candidate(key=f"k{i}", members=[f"d{i}"], answer=[f"art:L1:{i + 1}"], excluded={f"d{i}"}) for i in range(10)]
    freq = Counter(a for c in cands for a in c.answer)
    sel, note, _ = B._select("t1", cands, freq, total)
    assert kept in note and sel.fillable == 10
    assert len(sel.test) == 7 and len(sel.dev) == 3


def test_t7_does_not_touch_food_pools_or_t3_freq(tmp_path, monkeypatch) -> None:
    """식품 처분과 같은 번호의 의약품 처분이 있어도 T1 풀 · 식품 제외 집합 · T1 + T3 빈도는 그대로다."""
    monkeypatch.setattr(B, "T1_T3_TEST", 2)
    monkeypatch.setattr(B, "T1_T3_DEV", 1)
    monkeypatch.setattr(B, "T1_T3_REQUIRED", 1)
    food = _t1_world(tmp_path / "food")
    t1_a, _, freq_a = B.t1_t3_pools(food, B.disposition_answers(food), {})
    drug = _t7_world(tmp_path / "food")  # 같은 스냅숏 폴더에 의약품 처분을 더한다(번호 1~6이 식품 1~4와 겹친다)
    food.drug_disp = drug.drug_disp
    t1_b, _, freq_b = B.t1_t3_pools(food, B.disposition_answers(food), {})
    assert t1_a.excluded == t1_b.excluded and freq_a == freq_b
    assert set(B.disposition_answers(food)) == set(food.disp)  # 식품 정답은 식품 처분만


def test_m2_label_uniqueness_counts_remaining_drug_texts() -> None:
    """의약품 처분과 문구가 같은 식품 처분은 m2_disposition 슬롯 라벨이 유일하지 않으므로 뺀다(같은 Disposition 라벨)."""
    import grouping as G
    from collections import Counter as C
    norm = C(G.normalize_violation(x) for x in ["(20250101)표시기재 위반"])
    norm.update(G.normalize_violation(x) for x in ["표시기재 위반"])
    assert norm[G.normalize_violation("표시기재 위반")] == 2
