"""scripts/label_audit.py: 표본(시드 순서 · 겹침 없음 · 전부 삭제 제외 · T1 추가 10), 판정 · 오류 칸 대조,
커밋 파일에 원문이 옮겨지지 않음, Wilson 구간, 2차 조건, 엑셀 cp949 다시 저장.

스냅숏은 git 밖이라 쓰지 않는다. 합성 문항만 쓴다(표시 칸은 실제 v1에서 확인).
"""

import csv
import importlib.util
import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("label_audit", ROOT / "scripts" / "label_audit.py")
LA = importlib.util.module_from_spec(spec)
sys.modules["label_audit"] = LA
spec.loader.exec_module(LA)

SECRET = "가짜원문문장테스트"


def _items(n=200, masked_every=5):
    out = []
    for typ in LA.TYPES:
        for k in range(n):
            out.append({"id": f"test:{typ.lower()}:{k:03d}", "split": "test", "type": typ,
                        "meta": {"answer_deleted": "all" if k % 40 == 7 else "none",
                                 "article_masked": k % masked_every == 0}})
        out.append({"id": f"dev:{typ.lower()}:000", "split": "dev", "type": typ, "meta": {"answer_deleted": "none"}})
    return out


def test_sampling_is_seeded_disjoint_and_skips_all_deleted():
    items = _items()
    for typ in LA.TYPES:
        s1, s2 = LA.stage_ids(items, typ, 1, "v1"), LA.stage_ids(items, typ, 2, "v1")
        assert len(s1) == len(s2) == 50 and not set(s1) & set(s2)
        assert s1 == LA.stage_ids(list(reversed(items)), typ, 1, "v1")  # 입력 순서와 무관
        bad = {x["id"] for x in items if x["meta"]["answer_deleted"] == "all" or x["split"] == "dev"}
        assert not (set(s1) | set(s2)) & bad
    assert LA.stage_ids(items, "T1", 1, "v1") != LA.stage_ids(items, "T1", 1, "재생성 1")  # 재생성은 새 표본


def test_t1_extra_is_masked_and_outside_stage1_and_stage2():
    items = _items()
    extra = LA.t1_extra_ids(items, "v1")
    by_id = {x["id"]: x for x in items}
    assert len(extra) == 10 and all(by_id[i]["meta"]["article_masked"] for i in extra)
    assert not set(extra) & (set(LA.stage_ids(items, "T1", 1, "v1")) | set(LA.stage_ids(items, "T1", 2, "v1")))
    with pytest.raises(SystemExit):
        LA.t1_extra_ids(_items(masked_every=1000), "v1")


def _row(**kw):
    r = {"문항 id": "test:t7:001", "유형": "T7", "단계": LA.STAGE1, "생성 버전": "v1 abc", "판정": "", "오류": "",
         "_줄 출처": "1=표지;2=표지없음", "질문(가리기 전)": SECRET}
    r.update(kw)
    return r


@pytest.mark.parametrize("kw,ok", [
    ({"판정": "맞음"}, True),
    ({"판정": "틀림", "오류": "split@2; answer@art:001234:15"}, True),
    ({"판정": "맞음", "오류": "mask_over; par_unit@par:001234:15:2"}, True),  # 건수만 세는 코드는 '맞음'과 함께 가능
    ({"판정": "맞음", "오류": "answer@art:001234:15"}, False),  # 오류율 코드인데 맞음
    ({"판정": "틀림", "오류": "mask_over"}, False),  # 틀림인데 오류율 코드 없음
    ({"판정": "틀림", "오류": "typo@1"}, False),  # 모르는 코드
    ({"판정": "틀림", "오류": f"answer@{SECRET}"}, False),  # 위치에 원문 글자
    ({"판정": "틀림", "오류": "split@9"}, False),  # 없는 줄 번호
    ({"판정": "틀림", "오류": "split@2", "유형": "T1"}, False),  # split은 T7만
    ({"판정": "맞음", "오류": "mask_miss@1"}, False),  # mask_miss는 위치 없이
    ({"판정": "", "오류": "resolve"}, False),  # 오류만 있고 판정 없음
    ({"판정": "가림만 확인", "단계": LA.EXTRA, "유형": "T1"}, True),
    ({"판정": "맞음", "단계": LA.EXTRA, "유형": "T1"}, False),
    ({"판정": "가림만 확인", "단계": LA.EXTRA, "유형": "T1", "오류": "resolve"}, False),
])
def test_check_row(kw, ok):
    _, probs = LA.check_row(_row(**kw))
    assert (not probs) == ok, probs


def test_extract_has_no_source_text_and_counts_split_origin():
    rows = [_row(**{"문항 id": "test:t7:001", "판정": "틀림", "오류": "split@2; mask_over"}),
            _row(**{"문항 id": "test:t7:002", "판정": "맞음"}),
            _row(**{"문항 id": "test:t7:003", "판정": ""}),
            _row(**{"문항 id": "test:t1:009", "유형": "T1", "단계": LA.EXTRA, "판정": "가림만 확인", "오류": "mask_miss"})]
    r = LA.extract(rows)
    assert not r["problems"]
    flat = json.dumps([r["judgments"], r["errors"]], ensure_ascii=False)
    assert SECRET not in flat
    assert len(r["judgments"]) == 3  # 판정 빈 행은 아직 안 씀
    assert r["stats"][("T7", "v1 abc", LA.STAGE1)] == {"total": 3, "judged": 2, "wrong": 1}
    assert r["split_by_kind"]["T7"] == {"표지없음": 1}
    assert r["counts"]["T7"] == {"mask_over": 1} and r["mask_miss"] == ["test:t1:009"]
    dup = LA.extract([_row(판정="맞음"), _row(판정="맞음")])
    assert dup["problems"]


def test_extract_counts_by_answer_line_source():
    """정답 줄 출처별 분모 = 그 출처로 분류돼 판정을 마친 문항(판정 빈 행 · T1 추가 10 제외, PREREG 4-2 2026-11-25)."""
    rows = [_row(**{"문항 id": "test:t7:001", "판정": "틀림", "오류": "split@2", "_정답 줄 출처": "표지없음"}),
            _row(**{"문항 id": "test:t7:002", "판정": "맞음", "_정답 줄 출처": "표지없음"}),
            _row(**{"문항 id": "test:t7:003", "판정": "맞음", "_정답 줄 출처": "표지"}),
            _row(**{"문항 id": "test:t7:004", "판정": "", "_정답 줄 출처": "섞임"})]
    r = LA.extract(rows)
    assert r["by_source"] == {("T7", "v1 abc", "표지없음"): {"judged": 2, "wrong": 1},
                              ("T7", "v1 abc", "표지"): {"judged": 1, "wrong": 0}}
    assert LA.View.answer_source({"표지없음", "표지"}) == "섞임" and LA.View.answer_source(set()) == ""


def test_wilson_matches_prereg_table():
    for k, n, hi in ((0, 50, 0.071), (1, 100, 0.054), (4, 100, 0.098), (5, 100, 0.112)):
        assert round(LA.wilson(k, n)[1], 3) == hi


def _stats(first, second=None):
    s = {("T7", "v", LA.STAGE1): first}
    if second:
        s[("T7", "v", LA.STAGE2)] = second
    return LA.rate_lines(s)[0]


def test_rate_lines_two_stage():
    assert "50으로 확정" in _stats({"total": 50, "judged": 50, "wrong": 0})
    assert "2차 50 필요" in _stats({"total": 50, "judged": 50, "wrong": 1})
    assert "진행 중" in _stats({"total": 50, "judged": 49, "wrong": 0})
    line = _stats({"total": 50, "judged": 50, "wrong": 2}, {"total": 50, "judged": 50, "wrong": 4})
    assert "6 / 100" in line and "5% 초과 15% 이하" in line


def _write(path, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=LA.COLUMNS)
        w.writeheader()
        w.writerows([{c: r.get(c, "") for c in LA.COLUMNS} for r in rows])


def test_read_audit_accepts_excel_cp949(tmp_path):
    p = tmp_path / "a.csv"
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=LA.COLUMNS, lineterminator="\r\n")
    w.writeheader()
    w.writerow({c: _row(판정="맞음").get(c, "") for c in LA.COLUMNS})
    p.write_bytes(buf.getvalue().encode("cp949"))
    rows, enc = LA.read_audit(p)
    assert enc == "cp949" and rows[0]["판정"] == "맞음"


def test_extract_refuses_to_write_on_problems(tmp_path):
    p = tmp_path / "a.csv"
    _write(p, [_row(판정="맞음", 오류="answer@art:001:1")])
    args = NS(out=str(p), judgments=str(tmp_path / "j.csv"), errors=str(tmp_path / "e.csv"))
    assert LA.do_extract(args) == 1
    assert not (tmp_path / "j.csv").exists()
    _write(p, [_row(판정="틀림", 오류="answer@art:001:1")])
    assert LA.do_extract(args) == 0
    assert SECRET not in (tmp_path / "j.csv").read_text(encoding="utf-8") + (tmp_path / "e.csv").read_text(encoding="utf-8")


def test_stage2_only_after_complete_stage1_with_wrong(tmp_path, monkeypatch):
    items = _items()
    ev = tmp_path / "v1" / "test.jsonl"  # 라벨 v1은 v1/ 평가셋에만(check_label)
    ev.parent.mkdir()
    ev.write_bytes(b"".join(json.dumps(x, ensure_ascii=False).encode() + b"\n" for x in items))
    version = LA.version_label(ev.read_bytes())
    out = tmp_path / "audit.csv"
    s1 = LA.stage_ids(items, "T7", 1, "v1")

    class FakeView:
        def __init__(self, _):
            pass

        def row(self, item, order, stage, ver):
            return {"순서": order, "문항 id": item["id"], "유형": item["type"], "단계": stage, "생성 버전": ver}

    monkeypatch.setattr(LA, "View", FakeView)
    args = NS(evalset=str(ev), label="v1", out=str(out), stage=2, type="T7", snapshot="x")
    rows = [_row(**{"문항 id": i, "생성 버전": version, "판정": "맞음"}) for i in s1]
    _write(out, rows)
    with pytest.raises(SystemExit, match="틀림"):
        LA.make(args)
    rows[3]["판정"], rows[3]["오류"] = "틀림", "answer"
    rows[4]["판정"] = ""
    _write(out, rows)
    with pytest.raises(SystemExit, match="끝나지"):
        LA.make(args)
    rows[4]["판정"] = "맞음"
    _write(out, rows)
    LA.make(args)
    got, _ = LA.read_audit(out)
    assert [r["문항 id"] for r in got if r["단계"] == LA.STAGE2] == LA.stage_ids(items, "T7", 2, "v1")
    assert [r["판정"] for r in got[:50]] == [r["판정"] for r in rows]  # 1차 판정 보존
    with pytest.raises(SystemExit, match="이미"):
        LA.make(args)
    args.stage, args.type = 1, None
    with pytest.raises(SystemExit, match="덮어쓰지"):
        LA.make(args)


def test_label_must_match_evalset_dir(tmp_path) -> None:
    """라벨은 표본 순서 시드에 들어간다: 형식(v1 / 재생성 N)과 디렉터리(v1 / v1-r{N})가 맞아야 한다."""
    LA.check_label("v1", tmp_path / "v1" / "test.jsonl")
    LA.check_label("재생성 2", tmp_path / "v1-r2" / "test.jsonl")
    for label, d in (("v1", "v1-r1"), ("재생성 1", "v1"), ("재생성 1", "v1-r2"), ("v1-r1", "v1-r1"), ("재생성1", "v1-r1")):
        with pytest.raises(SystemExit):
            LA.check_label(label, tmp_path / d / "test.jsonl")


def test_existing_audit_file_extracts_unchanged() -> None:
    """지금 쓰는 eval/label_audit.csv(생성 버전 'v1 …')는 바뀐 도구로도 그대로 읽고 대조된다(파일은 쓰지 않음)."""
    real = LA.AUDIT
    if not real.exists():
        pytest.skip("label_audit.csv 없음(git 제외 파일)")
    rows, _ = LA.read_audit(real)
    assert rows and {r["생성 버전"].rsplit(None, 1)[0] for r in rows} == {"v1"}
    r = LA.extract(rows)  # 판정 중이어도 생성 버전 때문에 생기는 문제는 없어야 한다
    assert not [p for p in r["problems"] if "버전" in str(p)]
