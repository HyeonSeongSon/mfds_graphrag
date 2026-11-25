"""사전 등록(PREREG) 잠금 · 확인 · 수정 기록.

AI-INNOVATION-CHALLENGE-2026 `eval/generator_grounding/gg_common.py`(40~124행)의
sha256 · file_hashes · amendments_entries · append_amendment · prereg_status를 경로만 바꿔 옮겼다.

- lock: 잠금 대상(플랜 부록 4-7) 해시를 `eval/prereg_lock.json`에 한 번만 기록한다. 이미 있으면 거부.
  잠그기 전에 자리 표시(`(잠금 전에 채웁니다)`, 상태 줄 설명 밖의 `TBD(D4 dry-run`)와 git 미커밋 변경을 검사하고,
  잠금 기록에 이 도구(`root/eval/prereg.py`)의 해시 · git HEAD를 함께 남긴다(2026-11-25)
- check: 파일마다 마지막으로 기록된 해시 하나(AMENDMENTS에 그 파일 기록이 있으면 가장 마지막 것, 없으면 잠금 기록)와
  대조한다. 잠금 기록에 도구 해시가 있으면 도구도 같은 방식으로 대조한다(2026-11-25, 12단계의 '감사용' 결정을 바꿈)
- amend: AMENDMENTS.md에 항목을 추가만 한다. 각 항목은 이전 항목(JSON 문자열)의 sha256을 담는다

    python eval/prereg.py lock
    python eval/prereg.py check
    python eval/prereg.py amend --kind fix --content "사유" --files mfds/refs.py
    python eval/prereg.py amend --kind output --content "v1 생성" --files eval/evalset/v1/m4_candidates
"""

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

# 1주차 D4에 잠그는 대상(플랜 부록 4-7). 디렉터리는 안의 파일을 모두 잠근다
LOCK_TARGETS = [
    "eval/PREREG.md",
    "eval/build_evalset.py",
    "eval/grouping.py",
    "eval/m4_candidates.py",
    "eval/sources.py",
    "mfds/refs.py",
    "mfds/ingredients.py",
    "mfds/disposition.py",
    "mfds/collect/dur.py",
    "schema/schema.yaml",
    "schema/intents.yaml",
    "schema/eval_cypher",
    "schema/eval_cypher_check",
    "schema/eval_questions",
]
LOCK_FILE = "eval/prereg_lock.json"
AMENDMENTS_FILE = "eval/AMENDMENTS.md"
PLACEHOLDER = "(잠금 전에 채웁니다)"
# 상태 줄의 `TBD(D4 dry-run …)` 자리는 D4 dry-run · 사용자 검토 뒤 채우고, 남아 있으면 잠그지 않는다(PREREG 상태 줄).
# 마크다운은 해석하지 않는다. 상태 줄에서 아래 설명 문자열 하나만 빼고 문서 전체를 글자 그대로 찾는다(2026-11-25)
TBD_MARK = "TBD(D4 dry-run"
TBD_NOTE = "`TBD(D4 dry-run …)`"
STATUS_PREFIX = "**상태:"
# 잠금 도구. lock · check · amend가 모두 root 기준 같은 경로를 해시한다(테스트는 임시 저장소의 사본을 본다, 2026-11-25)
TOOL_PATH = "eval/prereg.py"
AMENDMENTS_HEADER = "# AMENDMENTS — 잠금 뒤 기록 (추가만 가능, 각 항목은 이전 항목의 sha256 을 담는다)\n\n"


# 줄 끝을 LF로 맞춰 해시하는 텍스트 확장자. 그 밖(GGUF · safetensors · dump 등)은 바이트 그대로(표준 sha256sum과 같음)
TEXT_SUFFIXES = {".md", ".py", ".yaml", ".yml", ".json", ".jsonl", ".cypher", ".txt", ".tsv", ".csv"}


def sha256(path: Path) -> str:
    """텍스트 파일(`TEXT_SUFFIXES`)은 줄 끝을 LF로 맞춘 내용의 sha256, 나머지는 바이트 그대로의 sha256.

    Git for Windows 기본 `core.autocrlf=true`에서는 작업 사본이 CRLF, 저장소가 LF라서, 바이트 그대로 해시하면
    다른 PC · WSL에서 클론하거나 LF로 쓴 산출물을 다시 체크아웃할 때 내용이 같아도 해시가 달라진다(2026-11-09).
    생성기가 출력하는 산출물 해시(LF로 씀)와는 같은 값이다. 텍스트 파일을 밖에서 확인할 때는 CRLF를 LF로 바꾼 뒤 잰다.
    모델 파일 · 덤프 같은 바이너리는 바꾸면 내용이 달라지므로 그대로 잰다."""
    data = path.read_bytes()
    if path.suffix.lower() in TEXT_SUFFIXES:
        data = data.replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


def expand(root: Path, targets: list[str]) -> list[Path]:
    """잠금 대상을 파일 목록으로 펼친다. 디렉터리는 안의 파일 전부(캐시 제외), 없는 파일은 그대로 둔다."""
    out = []
    for t in targets:
        p = root / t
        if p.is_dir():
            out += sorted(f for f in p.rglob("*") if f.is_file() and "__pycache__" not in f.parts)
        else:
            out.append(p)
    return out


def file_hashes(root: Path, paths: list[Path]) -> dict[str, str | None]:
    return {str(p.relative_to(root)).replace("\\", "/"): (sha256(p) if p.exists() else None) for p in paths}


def amend_hashes(root: Path, files: list[str]) -> dict[str, str | None]:
    """`amend --files` 값 → 기록할 해시. 디렉터리(`m4_candidates/` 등)는 `expand`로 안의 파일 전부를 펼친다."""
    return file_hashes(root, expand(root, files))


def amendments_entries(root: Path = ROOT) -> list[dict[str, Any]]:
    """AMENDMENTS.md 의 항목(JSON 줄)을 읽고 사슬(prev_hash)을 확인한다."""
    path = root / AMENDMENTS_FILE
    if not path.exists():
        return []
    entries = []
    prev = "GENESIS"
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("- `{"):
            continue
        raw = line[3:-1]
        entry = json.loads(raw)
        if entry.get("prev_hash") != prev:
            raise SystemExit(f"AMENDMENTS.md 사슬이 끊겼습니다: {entry.get('id')}")
        prev = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        entries.append(entry)
    return entries


def append_amendment(kind: str, content: str, new_hashes: dict[str, str] | None = None,
                     root: Path = ROOT) -> dict[str, Any]:
    """AMENDMENTS.md 에 추가만 한다. 각 항목은 이전 항목(JSON 문자열)의 sha256 을 담는다."""
    path = root / AMENDMENTS_FILE
    entries = amendments_entries(root)
    prev = "GENESIS"
    if entries:
        last_raw = json.dumps(entries[-1], ensure_ascii=False, sort_keys=True)
        prev = hashlib.sha256(last_raw.encode("utf-8")).hexdigest()
    entry = {"id": len(entries) + 1, "at": datetime.now(timezone.utc).isoformat(), "kind": kind,
             "content": content, "new_hashes": new_hashes or {}, "prev_hash": prev}
    raw = json.dumps(entry, ensure_ascii=False, sort_keys=True)
    if not path.exists():
        path.write_text(AMENDMENTS_HEADER, encoding="utf-8")
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"- `{raw}`\n")
    return entry


def read_constants(root: Path = ROOT) -> dict[str, Any]:
    """PREREG.md 의 `<!-- CONSTANTS {...} -->` 줄을 읽는다. 생성기는 import한 값과 이것을 대조한다."""
    text = (root / "eval" / "PREREG.md").read_text(encoding="utf-8")
    m = re.search(r"<!-- CONSTANTS (\{.*?\}) -->", text, re.S)
    if not m:
        raise SystemExit("PREREG.md 에 CONSTANTS 줄이 없습니다")
    return json.loads(m.group(1))


def lock(root: Path = ROOT, targets: list[str] = LOCK_TARGETS) -> dict[str, Any]:
    path = root / LOCK_FILE
    if path.exists():
        raise SystemExit(f"{LOCK_FILE} 이 이미 있습니다. 잠금 뒤 변경은 AMENDMENTS.md 에 남깁니다.")
    files = file_hashes(root, expand(root, targets))
    missing = [k for k, v in files.items() if v is None]
    if missing:
        raise SystemExit(f"잠금 대상이 없습니다: {missing}")
    prereg = root / "eval" / "PREREG.md"
    if prereg.exists():
        text = prereg.read_text(encoding="utf-8")
        if PLACEHOLDER in text:
            raise SystemExit(f"PREREG.md 에 '{PLACEHOLDER}'가 남아 있습니다. 채운 뒤 잠급니다.")
        check_unfilled_tbd(text)
    tool_file = root / TOOL_PATH
    if not tool_file.exists():
        raise SystemExit(f"잠금 도구 {TOOL_PATH}가 없습니다. 잠그지 않습니다.")
    head, dirty = git_state(root)  # 잠금 파일을 쓰기 전 상태
    tool = {"path": TOOL_PATH, "sha256": sha256(tool_file), "git_head": head, "git_dirty": dirty}
    data = {"locked_at": datetime.now(timezone.utc).isoformat(), "targets": targets, "files": files,
            "tool": tool}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def check_unfilled_tbd(text: str) -> None:
    """상태 줄(`**상태:`로 시작)이 정확히 하나이고, 그 줄의 설명 문자열 하나를 뺀 문서 전체에 `TBD(D4 dry-run`이 없어야 한다.

    인라인 코드 · 코드 블록도 구분하지 않고 글자 그대로 찾는다. 애매하면 잠그지 않는 쪽이다."""
    lines = text.splitlines()
    status = [i for i, line in enumerate(lines) if line.startswith(STATUS_PREFIX)]
    if len(status) != 1:
        raise SystemExit(f"PREREG.md 의 상태 줄('{STATUS_PREFIX}'로 시작)이 {len(status)}개입니다. 정확히 하나여야 잠급니다.")
    lines[status[0]] = lines[status[0]].replace(TBD_NOTE, "", 1)
    left = [i + 1 for i, line in enumerate(lines) if TBD_MARK in line]
    if left:
        raise SystemExit(f"PREREG.md 에 '{TBD_MARK}' 자리가 남아 있습니다(줄 {left}). 채운 뒤 잠급니다.")


def _inside_git_tree(root: Path) -> bool:
    """git 메시지 언어에 기대지 않도록 `.git`(폴더 또는 파일)을 위로 찾아 저장소 여부를 정한다."""
    r = root.resolve()
    return any((p / ".git").exists() for p in (r, *r.parents))


def _git(git: str, root: Path, *args: str) -> bytes:
    r = subprocess.run([git, "-C", str(root), *args], capture_output=True)
    if r.returncode != 0:
        err = r.stderr.decode("utf-8", "replace").strip()
        raise SystemExit(f"git {' '.join(args)} 실패({r.returncode}): {err}. 잠그지 않습니다.")
    return r.stdout


def git_state(root: Path) -> tuple[str | None, bool | None]:
    """(HEAD, dirty). git 실행 파일이 없거나 저장소가 아니면 (None, None). 미커밋 변경이 있거나 git 오류면 거부.

    순서: 실행 파일 → 저장소(`.git` 탐색 뒤 `rev-parse --show-toplevel`) → `status --porcelain=v1 --untracked-files=all -z`.
    무시 목록(.gitignore)의 파일은 보지 않는다. 그 원천 자료는 스냅숏 manifest(파일별 sha256)로 따로 확인한다."""
    git = shutil.which("git")
    if git is None or not _inside_git_tree(root):
        return None, None
    _git(git, root, "rev-parse", "--show-toplevel")
    head = _git(git, root, "rev-parse", "HEAD").decode("utf-8").strip()
    out = _git(git, root, "status", "--porcelain=v1", "--untracked-files=all", "-z")
    entries = [e.decode("utf-8", "replace") for e in out.split(b"\0") if e]
    if entries:
        shown = "\n".join(entries[:30]) + ("\n…" if len(entries) > 30 else "")
        raise SystemExit(f"미커밋 변경 {len(entries)}개가 있습니다. 커밋한 뒤 잠급니다.\n{shown}")
    return head, False


UNRECORDED = "기록 없는 변경"


def prereg_status(root: Path = ROOT) -> dict[str, Any]:
    """파일마다 마지막으로 기록된 해시 하나와 대조한다: AMENDMENTS에 그 파일 기록이 있으면 가장 마지막 것, 없으면 잠금 기록.

    기록한 수정을 되돌린 것도 기록 없는 변경이다(2026-11-25). 잠금 기록에 `tool`이 있으면 잠금 도구(`root/eval/prereg.py`)도
    같은 방식으로 대조한다. 도구가 자기 자신을 검사하므로 의도적 변조가 아니라 기록 누락을 잡는 장치다."""
    path = root / LOCK_FILE
    if not path.exists():
        raise SystemExit(f"{LOCK_FILE} 없음 — lock 을 먼저 한다")
    base = json.loads(path.read_text(encoding="utf-8"))
    now = file_hashes(root, expand(root, base["targets"]))
    amended = {k: v for e in amendments_entries(root) for k, v in e.get("new_hashes", {}).items()}

    def judge(k: str, v: str | None, b: str | None, bad: str) -> str:
        if k in amended:  # 마지막 기록이 기준(잠금 해시로 되돌린 것도 다름)
            return "AMENDMENTS 에 기록된 수정" if v == amended[k] else bad
        return "기준과 같음" if v == b else bad

    status = {k: judge(k, now.get(k), base["files"].get(k), f"{UNRECORDED}(탐색적)")
              for k in sorted(set(now) | set(base["files"]))}
    if "tool" in base:
        k = base["tool"]["path"]
        t = root / k
        status[k] = judge(k, sha256(t) if t.exists() else None, base["tool"]["sha256"], f"{UNRECORDED}(도구)")
    return {"files": now, "status": status, "ok": not any(s.startswith(UNRECORDED) for s in status.values())}


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("lock")
    sub.add_parser("check")
    am = sub.add_parser("amend")
    am.add_argument("--kind", required=True)
    am.add_argument("--content", required=True)
    am.add_argument("--files", nargs="*", default=[], help="수정한 잠금 대상 · 산출물(지금 해시를 기록, 디렉터리는 안의 파일 전부)")
    args = ap.parse_args()

    if args.cmd == "lock":
        print(json.dumps(lock(), ensure_ascii=False, indent=2))
    elif args.cmd == "check":
        st = prereg_status()
        for k, s in st["status"].items():
            print(f"{s}\t{k}")
        if not st["ok"]:
            raise SystemExit("기록 없는 변경이 있습니다")
    else:
        print(json.dumps(append_amendment(args.kind, args.content, amend_hashes(ROOT, args.files)),
                         ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
