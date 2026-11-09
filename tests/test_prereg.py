"""prereg.py: lock → check 통과, 1바이트 수정 → check 실패, amend 기록 → check 통과, 사슬 변조 감지."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "eval"))
import prereg  # noqa: E402

TARGETS = ["eval/PREREG.md", "mfds/refs.py", "schema/eval_cypher"]


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "eval").mkdir()
    (tmp_path / "mfds").mkdir()
    (tmp_path / "schema" / "eval_cypher").mkdir(parents=True)
    (tmp_path / "eval" / "PREREG.md").write_text('# P\n<!-- CONSTANTS {"SEED": 1} -->\n', encoding="utf-8")
    (tmp_path / "mfds" / "refs.py").write_text("X = 1\n", encoding="utf-8")
    (tmp_path / "schema" / "eval_cypher" / "t2.cypher").write_text("RETURN 1\n", encoding="utf-8")
    return tmp_path


def test_lock_then_check_passes(repo: Path) -> None:
    data = prereg.lock(repo, TARGETS)
    assert set(data["files"]) == {"eval/PREREG.md", "mfds/refs.py", "schema/eval_cypher/t2.cypher"}
    assert prereg.prereg_status(repo)["ok"]


def test_lock_twice_refused(repo: Path) -> None:
    prereg.lock(repo, TARGETS)
    with pytest.raises(SystemExit):
        prereg.lock(repo, TARGETS)


def test_lock_refuses_placeholder_and_missing(repo: Path) -> None:
    (repo / "eval" / "PREREG.md").write_text(f"# P {prereg.PLACEHOLDER}\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        prereg.lock(repo, TARGETS)
    with pytest.raises(SystemExit):
        prereg.lock(repo, TARGETS + ["eval/missing.py"])


def test_one_byte_change_fails_then_amend_passes(repo: Path) -> None:
    prereg.lock(repo, TARGETS)
    (repo / "mfds" / "refs.py").write_text("X = 2\n", encoding="utf-8")
    st = prereg.prereg_status(repo)
    assert not st["ok"]
    assert st["status"]["mfds/refs.py"] == "기록 없는 변경(탐색적)"
    prereg.append_amendment("fix", "테스트 수정", prereg.file_hashes(repo, [repo / "mfds/refs.py"]), root=repo)
    st = prereg.prereg_status(repo)
    assert st["ok"]
    assert st["status"]["mfds/refs.py"] == "AMENDMENTS 에 기록된 수정"


def test_new_file_in_locked_dir_is_flagged(repo: Path) -> None:
    prereg.lock(repo, TARGETS)
    (repo / "schema" / "eval_cypher" / "t4.cypher").write_text("RETURN 2\n", encoding="utf-8")
    assert not prereg.prereg_status(repo)["ok"]


def test_amend_expands_directory(repo: Path) -> None:
    out = repo / "eval" / "evalset" / "v1" / "m4_candidates"
    out.mkdir(parents=True)
    for name in ("a.jsonl", "b.jsonl"):
        (out / name).write_text(name, encoding="utf-8")
    h = prereg.amend_hashes(repo, ["eval/evalset/v1/m4_candidates", "mfds/refs.py"])
    assert sorted(h) == ["eval/evalset/v1/m4_candidates/a.jsonl", "eval/evalset/v1/m4_candidates/b.jsonl",
                         "mfds/refs.py"]
    assert all(h.values())


def test_amendment_chain_tamper_detected(repo: Path) -> None:
    prereg.append_amendment("a", "첫째", root=repo)
    prereg.append_amendment("b", "둘째", root=repo)
    assert [e["id"] for e in prereg.amendments_entries(repo)] == [1, 2]
    path = repo / prereg.AMENDMENTS_FILE
    path.write_text(path.read_text(encoding="utf-8").replace("첫째", "첫번째"), encoding="utf-8")
    with pytest.raises(SystemExit):
        prereg.amendments_entries(repo)


def test_line_endings_do_not_change_hash(repo: Path) -> None:
    """LF로 잠근 뒤 CRLF로 체크아웃해도(또는 그 반대) 같은 내용이면 기준과 같다. 내용이 바뀌면 여전히 잡는다."""
    files = [repo / "eval/PREREG.md", repo / "mfds/refs.py", repo / "schema/eval_cypher/t2.cypher"]
    for p in files:
        p.write_bytes(p.read_bytes().replace(b"\r\n", b"\n"))
    prereg.lock(repo, TARGETS)
    for p in files:
        p.write_bytes(p.read_bytes().replace(b"\n", b"\r\n"))
    assert prereg.prereg_status(repo)["ok"]
    assert prereg.sha256(files[1]) == prereg.hashlib.sha256(b"X = 1\n").hexdigest()
    files[1].write_bytes(b"X = 2\r\n")
    assert prereg.prereg_status(repo)["status"]["mfds/refs.py"] == "기록 없는 변경(탐색적)"


def test_binary_hashed_as_is(tmp_path: Path) -> None:
    """모델 파일 · 덤프는 바이트 그대로 잰다(표준 sha256sum과 같고, 안의 CRLF 바이트를 바꾸지 않음)."""
    raw = b"GGUF\x00\r\n\x01"
    (tmp_path / "m.gguf").write_bytes(raw)
    assert prereg.sha256(tmp_path / "m.gguf") == prereg.hashlib.sha256(raw).hexdigest()


def test_read_constants(repo: Path) -> None:
    assert prereg.read_constants(repo) == {"SEED": 1}
    assert json.loads(json.dumps(prereg.read_constants(repo)))["SEED"] == 1
