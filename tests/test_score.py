"""eval/score.py: PREREG 4-5 단위 테스트 7개(항 보조 지표 5 + 삭제 조문 2), 검수 틀림 제외 3개(PLAN B5),
조문 단위 변환 · 실행 실패 처리 · API 대비 비율 민감도."""

import csv
import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "eval"))
import score as S  # noqa: E402

L = "001805"


def case(i: int = 0, uids=(f"art:{L}:37",), refs=(f"par:{L}:37:1",), deleted=(), kind=None, typ="T1", **meta) -> dict:
    kind = kind or ("none" if not deleted else ("all" if set(deleted) >= set(uids) else "some"))
    return {"id": f"test:{typ.lower()}:{i:03d}", "type": typ, "split": "test", "question": f"q{i}",
            "answer": {"uids": list(uids), "refs": list(refs), "deleted": list(deleted)},
            "meta": {"answer_deleted": kind, "rep_left": meta.get("rep_left", 1),
                     "answer_in_original": meta.get("aio", False)}}


def ans(cits, status="ok", backend="local", **kw) -> dict:
    return {"status": status, "backend": backend, "answer": {"citations": list(cits), **kw}, "subgraph": list(cits)}


# ---------------------------------------------------------------- 단위 변환


def test_units() -> None:
    assert S.article_unit(f"par:{L}:44:2") == f"art:{L}:44"
    assert S.article_unit(f"item:{L}:65:1:4") == f"art:{L}:65"
    assert S.article_unit("anx:001783:7-2#3") == "anx:001783:7-2"
    assert S.article_unit("dur:pair:a:b") == "dur:pair:a:b"
    assert S.par_unit(f"item:{L}:65:1:4") == f"par:{L}:65:1"
    assert S.par_unit(f"item:{L}:48:0:1") == f"art:{L}:48"  # 항 없는 조의 호 → 조
    assert S.top_units([f"par:{L}:1:1", f"art:{L}:1", f"item:{L}:2:1:1", "x", "y", "z", "w"]) == \
        [f"art:{L}:1", f"art:{L}:2", "x", "y", "z"]


# ---------------------------------------------------------------- 항 보조 지표 5개(PREREG 4-5)


def test_par_gold_paragraph_same_paragraph_hits() -> None:
    units, kind = S.par_gold(case())
    assert kind == "순수" and S.par_hit([f"par:{L}:37:1"], units) == 1


def test_par_gold_paragraph_other_paragraph_misses() -> None:
    units, _ = S.par_gold(case())
    assert S.par_hit([f"par:{L}:37:2"], units) == 0
    assert S.par_hit([f"art:{L}:37"], units) == 0  # 조만 인용도 빗나감


def test_par_gold_article_hit_by_its_paragraph() -> None:
    it = case(uids=(f"art:{L}:37", f"art:{L}:40"), refs=(f"par:{L}:37:1", f"art:{L}:40"))
    units, kind = S.par_gold(it)
    assert kind == "섞임" and S.par_hit([f"par:{L}:40:3"], units) == 1


def test_par_and_article_of_same_article_specific_wins() -> None:
    it = case(refs=(f"par:{L}:37:1", f"art:{L}:37"))
    units, kind = S.par_gold(it)
    assert units == {f"par:{L}:37:1"} and kind == "순수"
    assert S.par_hit([f"art:{L}:37"], units) == 0


def test_pure_and_mixed_aggregated_separately() -> None:
    items = [case(0), case(1, uids=(f"art:{L}:37", f"art:{L}:40"), refs=(f"par:{L}:37:1", f"art:{L}:40")),
             case(2, refs=(f"art:{L}:37",))]  # 정답 단위가 모두 조 → 대상 아님
    run = {"a": {items[0]["id"]: ans([f"par:{L}:37:1"]), items[1]["id"]: ans([f"art:{L}:40"]),
                 items[2]["id"]: ans([f"art:{L}:37"])}}
    t = S.answer_table(items, run, set(), set(), None, 1, 200)["a"]["table"]["T1"]
    assert t["par_hit_순수"]["n"] == 1 and t["par_hit_섞임"]["n"] == 1 and t["par_hit_합산"]["n"] == 2
    assert t["par_hit_합산"]["k"] == 2


# ---------------------------------------------------------------- 삭제 조문 2개(PREREG 4-2 · 4-5)


def test_partial_deleted_scored_on_current_answer_deleted_citation_neutral() -> None:
    dele = {f"art:{L}:25", f"art:{L}:99"}
    it = case(uids=(f"art:{L}:25", f"art:{L}:61"), refs=(f"par:{L}:25:3", f"art:{L}:61"),
              deleted=(f"art:{L}:25",), typ="T3")
    g = S.gold(it)
    assert g.cur == {f"art:{L}:61"} and g.status == "main"
    # 삭제 정답 · 정답 아닌 삭제 조문은 자리를 차지하고(6번째가 올라오지 않음) 적중 · 정밀도에서 중립
    ranked = [f"art:{L}:25", f"art:{L}:99", "a", "b", "c", f"art:{L}:61"]
    s = S.rank_scores(ranked, g, dele)
    assert s["hit5"] == 0 and s["deleted_cited"] == 2
    s = S.rank_scores([f"art:{L}:25", f"art:{L}:61"], g, dele)
    assert s["hit5"] == 1 and s["hit1"] == 0 and s["mrr5"] == 0.5 and s["recall5"] == 1
    assert S.precision([f"art:{L}:25", f"art:{L}:61", "x"], g.cur, dele) == 0.5  # 분자 · 분모에서 삭제 조문 뺌
    assert S.precision([f"art:{L}:99"], g.cur, dele) is None  # 삭제 조문만 → 정밀도에서 빼고 셈
    assert S.precision([], g.cur, dele) == 0.0
    assert S.par_gold(it) is None  # 삭제 조문 참조(par 25:3)를 빼면 정답 단위가 조뿐 → 항 보조 대상 아님


def test_all_deleted_excluded_from_main_and_reported_separately() -> None:
    dele = {f"art:{L}:25"}
    items = [case(0, uids=(f"art:{L}:25",), refs=(f"par:{L}:25:1",), deleted=(f"art:{L}:25",), typ="T3"),
             case(1, typ="T3")]
    assert S.gold(items[0]).status == "all_deleted" and S.par_gold(items[0]) is None
    run = {"v": {items[0]["id"]: {"ranked": [f"art:{L}:25"]}, items[1]["id"]: {"ranked": [f"art:{L}:37"]}}}
    rt = S.retrieval_table(items, run, dele, set(), None, 1, 200)["v"]
    assert rt["table"]["T3"]["hit5"]["n"] == 1  # 주 평가에는 일부 · 없음만
    ad = rt["table"]["T3"]["all_deleted_hit5"]
    assert ad["n"] == 1 and ad["k"] == 1  # 원래 정답 기준 별도 줄
    assert items[0]["id"] not in rt["hit5"]["T3"]  # McNemar 짝에도 없음


# ---------------------------------------------------------------- 검수 틀림 제외(PLAN B5)


def _write_version(root: Path, label: str, items: list[dict]) -> str:
    d = root / label
    d.mkdir(parents=True)
    b = "".join(json.dumps(x, ensure_ascii=False, sort_keys=True) + "\n" for x in items).encode("utf-8")
    (d / "test.jsonl").write_bytes(b)
    return f"{label} {hashlib.sha256(b).hexdigest()[:12]}"


def _judg(path: Path, rows: list[tuple]) -> Path:
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["문항 id", "유형", "생성 버전", "단계", "판정"])
        w.writerows(rows)
    return path


def test_wrong_judgment_follows_content_into_shifted_regeneration(tmp_path: Path) -> None:
    v1 = [case(0), case(1, uids=(f"art:{L}:40",))]
    ver = _write_version(tmp_path, "v1", v1)
    # 재생성: 앞 문항이 하나 늘어 id가 밀림
    r1 = [case(0, uids=(f"art:{L}:9",)), {**v1[0], "id": "test:t1:001"}, {**v1[1], "id": "test:t1:002"}]
    j = _judg(tmp_path / "j.csv", [("test:t1:001", "T1", ver, "1차", "틀림"), ("test:t1:000", "T1", ver, "1차", "맞음")])
    ex = S.audit_exclusions(tmp_path, j, r1)
    assert "test:t1:002" in ex["ids"] and "test:t1:001" not in ex["ids"] and ex["moved"] == {"T1": 1}


def test_wrong_judgment_not_moved_when_content_changed(tmp_path: Path) -> None:
    v1 = [case(0)]
    ver = _write_version(tmp_path, "v1", v1)
    changed = [{**case(0), "answer": {**case(0)["answer"], "uids": [f"art:{L}:38"]}}]
    j = _judg(tmp_path / "j.csv", [("test:t1:000", "T1", ver, "1차", "틀림")])
    ex = S.audit_exclusions(tmp_path, j, changed)
    assert ex["ids"] == set() and ex["lost"] == {"T1": 1}


def test_label_wrong_also_drops_colloquial_pair(tmp_path: Path) -> None:
    v1 = [case(0)]
    ver = _write_version(tmp_path, "v1", v1)
    j = _judg(tmp_path / "j.csv", [("test:t1:000", "T1", ver, "1차", "틀림")])
    assert S.audit_exclusions(tmp_path, j, v1)["ids"] == {"test:t1:000", "test:t1:000:c"}
    bad = _judg(tmp_path / "bad.csv", [("test:t1:000", "T1", "v1 000000000000", "1차", "틀림")])
    with pytest.raises(SystemExit):
        S.audit_exclusions(tmp_path, bad, v1)  # 판정 버전 해시가 다르면 멈춤


# ---------------------------------------------------------------- 실행 실패 · API 대비 비율(PREREG 4-5)


def test_failure_handling() -> None:
    it = case()
    assert S.answer_scores(it, ans([], status="truncated", backend="api"), set())["cite_hit"] == 0  # 잘림 = 오답
    assert S.answer_scores(it, ans([], status="format_error"), set())["cite_hit"] == 0
    assert S.answer_scores(it, ans([f"art:{L}:37"], status="error"), set())["cite_hit"] == 0  # 로컬 실패 = 오답
    assert S.answer_scores(it, ans([], status="error", backend="api"), set()) is None  # API 실패 → 짝에서 뺌
    assert S.answer_scores(it, ans([], status="policy_refusal", backend="api"), set()) is None
    assert S.answer_scores(it, ans([f"art:{L}:37"]), set())["cite_hit"] == 1
    s = S.answer_scores(it, {**ans([f"art:{L}:37", "ghost"]), "subgraph": [f"art:{L}:37"]}, set())
    assert s["n_unsupported"] == 1


def test_refusal_verdict_number() -> None:
    ref = {"id": "test:refusal:x:000", "type": "T5", "split": "test", "answer": {"refuse": True}, "meta": {}}
    neg = {"id": "test:t2:000", "type": "T2", "split": "test", "meta": {"polarity": "neg"},
           "answer": {"rule_ids": [], "uids": ["ing:a", "ing:b"], "verdict": "해당없음"}}
    t6 = {"id": "test:t6:000", "type": "T6", "split": "test", "meta": {}, "answer": {"n": 3}}
    assert S.answer_scores(ref, ans([], refuse=True), set())["refuse"] == 1
    assert S.gold(neg) is None and S.answer_scores(neg, ans([], verdict="해당없음"), set())["verdict_ok"] == 1
    assert S.answer_scores(t6, ans([], number=3), set())["number_ok"] == 1
    assert S.answer_scores(t6, ans([], number=None), set())["number_ok"] == 0


def test_api_ratio_base_s1_s2() -> None:
    items = [case(i) for i in range(4)]
    ok = ans([f"art:{L}:37"])
    local = {items[i]["id"]: ok for i in range(4)}
    api = {items[0]["id"]: {**ok, "backend": "api"}, items[1]["id"]: {**ok, "backend": "api"},
           items[2]["id"]: ans([], status="error", backend="api"),
           items[3]["id"]: ans([], status="truncated", backend="api")}
    at = S.answer_table(items, {"l": local, "a": api}, set(), set(), None, 1, 300)
    r = S.ratio_rows(at["l"], at["a"], 1, 300)["전체"]
    assert r["base"]["n"] == 3 and r["base"]["ratio"] == pytest.approx(1 / (2 / 3))  # 잘림은 API 오답
    assert r["S1"]["n"] == 4 and r["S1"]["ratio"] == pytest.approx(1 / (2 / 4))  # 뺀 문항을 API 오답으로
    assert r["S2"]["n"] == 2 and r["S2"]["ratio"] == pytest.approx(1.0)  # 잘림 문항을 양쪽에서 뺌
    assert r["dropped"] == 1 and r["descriptive_only"]  # 1/4 > 5%
    cmp = S.compare(at["l"]["main"]["T1"], at["a"]["main"]["T1"], 1, 300)
    assert cmp["n"] == 3 and (cmp["b"], cmp["c"]) == (1, 0)


def test_paragraph_missing_from_graph_lifts_to_article() -> None:
    known = {f"art:{L}:37", f"par:{L}:37:1", f"art:{L}:48"}
    it = case(uids=(f"art:{L}:37", f"art:{L}:48"), refs=(f"par:{L}:37:1", f"par:{L}:48:2"))
    units, kind = S.par_gold(it, known)  # 48조에 항 노드 없음 → 조로 채점
    assert units == {f"par:{L}:37:1", f"art:{L}:48"} and kind == "섞임"
    assert S.par_hit([f"item:{L}:48:0:1"], units, known) == 1


# ---------------------------------------------------------------- 입력 검사 · M4 미확정 · 버전 · 비율 보강


def _m4(i: int, role: str = "test") -> dict:
    return {"id": f"test:m4_citing_articles:{i:03d}", "type": "T4-M4", "split": "test", "answer": None,
            "meta": {"role": role, "reserve": role == "reserve"}}


def _t6() -> dict:
    return {"id": "test:t6:000", "type": "T6", "split": "test", "meta": {}, "answer": {"n": 1}}


def test_check_runs_scope_missing_duplicate_unknown(tmp_path: Path) -> None:
    items = [case(0), _t6(), _m4(0), _m4(1)]
    m4 = {_m4(0)["id"]: [f"art:{L}:9"]}  # 1번은 미선정
    S.check_runs(items, {"v": {case(0)["id"]: {}, _m4(0)["id"]: {}}}, "검색", set(), m4)  # T6 · 미선정 M4 없어도 통과
    with pytest.raises(SystemExit):
        S.check_runs(items, {"v": {case(0)["id"]: {}}}, "검색", set(), m4)  # 선정 M4 누락
    S.check_runs(items, {"v": {case(0)["id"]: {}}}, "검색", set(), None)  # M4 파일 없음: 36개 모두 미확정
    S.check_runs(items, {"v": {case(0)["id"]: {}, _m4(0)["id"]: {}, _m4(1)["id"]: {}}}, "검색", set(), m4)  # 미확정 있어도 됨
    with pytest.raises(SystemExit):
        S.check_runs(items, {"v": {case(0)["id"]: {}, "test:t1:999": {}}}, "검색", set(), None)  # 모르는 id
    S.check_runs(items, {"v": {}}, "검색", {case(0)["id"]}, None)  # 검수 틀림은 필수 아님
    with pytest.raises(SystemExit):
        S.check_runs(items, {"a": {case(0)["id"]: ans([])}}, "답변", set(), None)  # 답변은 T6 줄도 필수
    run = tmp_path / "r.jsonl"
    run.write_text(json.dumps({"id": "a"}) + "\n" + json.dumps({"id": "a"}) + "\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        S.load_runs([f"x={run}"])  # 같은 id 두 번


def test_check_runs_status_and_backend() -> None:
    items = [case(0), case(1)]
    S.check_runs(items, {"a": {items[0]["id"]: ans([]), items[1]["id"]: ans([], status="truncated")}}, "답변", set(), None)
    with pytest.raises(SystemExit):
        S.check_runs(items, {"a": {items[0]["id"]: ans([]), items[1]["id"]: ans([], status="OK")}}, "답변", set(), None)
    with pytest.raises(SystemExit):
        S.check_runs(items, {"a": {items[0]["id"]: ans([]), items[1]["id"]: ans([], backend="api")}}, "답변", set(),
                     None)


def test_pending_m4_left_out_of_all_answer_counts() -> None:
    items = [case(0), _m4(0), _m4(1)]
    run = {"a": {items[0]["id"]: ans([f"art:{L}:37"]), _m4(0)["id"]: ans([], refuse=True),
                 _m4(1)["id"]: ans([], refuse=True)}}
    t = S.answer_table(items, run, set(), set(), {_m4(0)["id"]: [f"art:{L}:9"]}, 1, 200)["a"]["table"]
    assert t["T4-M4"]["false_refusal"]["n"] == 1 and t["T4-M4"]["counts"]["정답 미확정"] == 1
    assert t["_status"]["ok"] == 2  # 미확정 문항은 상태 건수에도 없음


def test_label_dir_and_version_parse(tmp_path: Path) -> None:
    assert S.label_dir("v1") == "v1" and S.label_dir("재생성 3") == "v1-r3"
    for bad in ("v1-r1", "재생성 0", "재생성1", "v2"):
        with pytest.raises(SystemExit):
            S.label_dir(bad)
    v = [case(0)]
    ver = _write_version(tmp_path, "v1-r1", v).replace("v1-r1", "재생성 1")  # 라벨 '재생성 1' → 디렉터리 v1-r1
    j = _judg(tmp_path / "j.csv", [("test:t1:000", "T1", ver, "재생성 후 1차", "틀림")])
    assert S.audit_exclusions(tmp_path, j, v)["ids"] == {"test:t1:000", "test:t1:000:c"}


def test_ratio_excluded_type_row_and_paired_precision() -> None:
    t1 = [case(i) for i in range(40)]
    t3 = [case(i, typ="T3") for i in range(4)]
    items = t1 + t3
    local = {x["id"]: ans([f"art:{L}:37"]) for x in items}
    api = {x["id"]: {**ans([f"art:{L}:37"]), "backend": "api"} for x in items}
    api[t3[0]["id"]] = ans([], status="error", backend="api")  # T3 1/4 = 25% 뺌 → 기술 통계 유형
    local[t1[0]["id"]] = ans([f"art:{L}:99"])  # 정답 아닌 삭제 조문만 → 로컬 정밀도 중립
    at = S.answer_table(items, {"l": local, "a": api}, {f"art:{L}:99"}, set(), None, 1, 200)
    rows = S.ratio_rows(at["l"], at["a"], 1, 200)
    assert rows["T3"]["descriptive_only"] and not rows["T1"]["descriptive_only"]
    ex = rows["전체(기술 통계 유형 제외)"]
    assert ex["excluded_types"] == ["T3"] and ex["base"]["n"] == 40
    pr = rows["전체"]["base"]["precision"]
    assert pr["neutral_dropped"] == 1 and pr["n"] == 42  # 짝 43 중 한쪽이 중립인 1개를 양쪽에서 뺌
