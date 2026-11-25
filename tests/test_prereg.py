"""prereg.py: lock → check 통과, 1바이트 수정 → check 실패, amend 기록 → check 통과, 사슬 변조 감지."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "eval"))
import prereg  # noqa: E402

TARGETS = ["eval/PREREG.md", "mfds/refs.py", "schema/eval_cypher"]
STATUS = "**상태: 초안.** `TBD(D4 dry-run …)` 자리는 채운 뒤 잠급니다.\n"
HAS_GIT = shutil.which("git") is not None
REAL_TOOL = Path(prereg.__file__).resolve()


@pytest.fixture(autouse=True)
def _real_tool_untouched():
    """테스트는 임시 저장소의 도구 사본만 고친다. 실제 `eval/prereg.py`가 바뀌면 실패."""
    before = REAL_TOOL.read_bytes()
    yield
    assert REAL_TOOL.read_bytes() == before


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "eval").mkdir()
    (tmp_path / "mfds").mkdir()
    (tmp_path / "schema" / "eval_cypher").mkdir(parents=True)
    (tmp_path / "eval" / "PREREG.md").write_text(STATUS + '# P\n<!-- CONSTANTS {"SEED": 1} -->\n',
                                                 encoding="utf-8")
    (tmp_path / "mfds" / "refs.py").write_text("X = 1\n", encoding="utf-8")
    (tmp_path / "schema" / "eval_cypher" / "t2.cypher").write_text("RETURN 1\n", encoding="utf-8")
    shutil.copyfile(REAL_TOOL, tmp_path / prereg.TOOL_PATH)  # lock · check · amend가 보는 도구 사본
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
    (repo / "eval" / "PREREG.md").write_text(f"{STATUS}# P {prereg.PLACEHOLDER}\n", encoding="utf-8")
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


# ---- 2026-11-25 잠금 도구 보강: TBD(D4 dry-run) 자리 · git 상태 · 도구 기록 ----

def _write_prereg(repo: Path, body: str) -> None:
    (repo / "eval" / "PREREG.md").write_text(body, encoding="utf-8")


def test_lock_allows_status_line_note_only(repo: Path) -> None:
    _write_prereg(repo, STATUS + "# P\n본문\n")
    assert prereg.lock(repo, TARGETS)["files"]


@pytest.mark.parametrize("body", [
    STATUS + "값: TBD(D4 dry-run 검토)\n",                         # 일반 문장
    STATUS + "값: `TBD(D4 dry-run 검토)`\n",                       # 다른 줄의 인라인 코드 안
    STATUS + "```text\nTBD(D4 dry-run\n```\n",                   # 백틱 코드 블록 안
    STATUS + "~~~\n`TBD(D4 dry-run`\n~~~\n",                     # 물결표 코드 블록 안(인라인 코드로 감쌈)
    STATUS.rstrip("\n") + " 추가: TBD(D4 dry-run 추가 항목)\n",   # 상태 줄에 허용 문자열 밖의 표시
    "# P\n본문\n",                                                 # 상태 줄 없음
    STATUS + STATUS,                                                # 상태 줄 둘
], ids=["plain", "inline-code", "backtick-fence", "tilde-fence", "status-extra", "no-status", "two-status"])
def test_lock_refuses_unfilled_tbd(repo: Path, body: str) -> None:
    _write_prereg(repo, body)
    with pytest.raises(SystemExit):
        prereg.lock(repo, TARGETS)
    assert not (repo / prereg.LOCK_FILE).exists()


def test_lock_records_tool_hash_outside_git(repo: Path) -> None:
    tool = prereg.lock(repo, TARGETS)["tool"]
    assert tool["path"] == "eval/prereg.py"
    assert tool["sha256"] == prereg.sha256(repo / prereg.TOOL_PATH) == prereg.sha256(REAL_TOOL)
    assert tool["git_head"] is None and tool["git_dirty"] is None  # 저장소가 아님


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t",
                           "-c", "core.autocrlf=false", *args],
                          check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def git_repo(repo: Path) -> Path:
    if not HAS_GIT:
        pytest.skip("git 없음")
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


def test_lock_in_clean_git_records_head(git_repo: Path) -> None:
    tool = prereg.lock(git_repo, TARGETS)["tool"]
    assert tool["git_head"] == _git(git_repo, "rev-parse", "HEAD")
    assert tool["git_dirty"] is False


@pytest.mark.parametrize("kind", ["staged", "unstaged", "untracked"])
def test_lock_refuses_uncommitted(git_repo: Path, kind: str) -> None:
    if kind == "untracked":
        (git_repo / "new.txt").write_text("x\n", encoding="utf-8")
    else:
        (git_repo / "mfds" / "refs.py").write_text("X = 3\n", encoding="utf-8")
        if kind == "staged":
            _git(git_repo, "add", "mfds/refs.py")
    with pytest.raises(SystemExit):
        prereg.lock(git_repo, TARGETS)
    assert not (git_repo / prereg.LOCK_FILE).exists()


def test_lock_without_git_executable_records_null(git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(prereg.shutil, "which", lambda name: None)
    tool = prereg.lock(git_repo, TARGETS)["tool"]
    assert tool["git_head"] is None and tool["git_dirty"] is None


# ---- 2026-11-25 check: 마지막 기록 하나만 기준 · 잠금 도구 해시 대조 ----

TOOL = "eval/prereg.py"


def _tool(repo: Path) -> Path:
    t = repo / prereg.TOOL_PATH
    assert t.resolve().is_relative_to(repo.resolve()) and t.resolve() != REAL_TOOL  # 임시 사본만 고친다
    return t


def _amend(repo: Path, *paths: str) -> None:
    prereg.append_amendment("fix", "테스트 수정", prereg.amend_hashes(repo, list(paths)), root=repo)


def test_tool_unchanged_passes(repo: Path) -> None:
    prereg.lock(repo, TARGETS)
    st = prereg.prereg_status(repo)
    assert st["ok"] and st["status"][TOOL] == "기준과 같음"


def test_tool_edit_fails_then_amend_passes(repo: Path) -> None:
    prereg.lock(repo, TARGETS)
    t = _tool(repo)
    t.write_bytes(t.read_bytes() + b"# edit\n")
    st = prereg.prereg_status(repo)
    assert not st["ok"] and st["status"][TOOL] == "기록 없는 변경(도구)"
    _amend(repo, TOOL)
    st = prereg.prereg_status(repo)
    assert st["ok"] and st["status"][TOOL] == "AMENDMENTS 에 기록된 수정"


def test_tool_rollback_after_amend_fails(repo: Path) -> None:
    prereg.lock(repo, TARGETS)
    t = _tool(repo)
    a = t.read_bytes()
    t.write_bytes(a + b"# B\n")
    _amend(repo, TOOL)
    assert prereg.prereg_status(repo)["ok"]
    t.write_bytes(a)  # 잠금 해시 A로 되돌림: 마지막 기록(B)과 다르다
    st = prereg.prereg_status(repo)
    assert not st["ok"] and st["status"][TOOL] == "기록 없는 변경(도구)"


def test_tool_baseline_survives_later_other_file_entry(repo: Path) -> None:
    prereg.lock(repo, TARGETS)
    t = _tool(repo)
    t.write_bytes(t.read_bytes() + b"# B\n")
    _amend(repo, TOOL)
    (repo / "mfds" / "refs.py").write_text("X = 2\n", encoding="utf-8")
    _amend(repo, "mfds/refs.py")
    assert list(prereg.amendments_entries(repo)[-1]["new_hashes"]) == ["mfds/refs.py"]
    st = prereg.prereg_status(repo)
    assert st["ok"] and st["status"][TOOL] == "AMENDMENTS 에 기록된 수정"


def test_lock_without_tool_key_skips_tool(repo: Path) -> None:
    prereg.lock(repo, TARGETS)
    lock_path = repo / prereg.LOCK_FILE
    data = json.loads(lock_path.read_text(encoding="utf-8"))
    del data["tool"]
    lock_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    t = _tool(repo)
    t.write_bytes(t.read_bytes() + b"# edit\n")
    st = prereg.prereg_status(repo)
    assert TOOL not in st["status"] and st["ok"]


def test_target_rollback_after_amend_fails(repo: Path) -> None:
    prereg.lock(repo, TARGETS)
    refs = repo / "mfds" / "refs.py"
    refs.write_text("X = 2\n", encoding="utf-8")
    _amend(repo, "mfds/refs.py")
    assert prereg.prereg_status(repo)["ok"]
    refs.write_text("X = 1\n", encoding="utf-8")  # 잠금 해시로 되돌림
    st = prereg.prereg_status(repo)
    assert not st["ok"] and st["status"]["mfds/refs.py"] == "기록 없는 변경(탐색적)"


def test_lock_refuses_missing_tool(repo: Path) -> None:
    _tool(repo).unlink()
    with pytest.raises(SystemExit):
        prereg.lock(repo, TARGETS)
    assert not (repo / prereg.LOCK_FILE).exists()
