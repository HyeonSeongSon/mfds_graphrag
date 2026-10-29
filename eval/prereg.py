"""사전 등록(PREREG) 잠금 · 확인 · 수정 기록.

AI-INNOVATION-CHALLENGE-2026 `eval/generator_grounding/gg_common.py`(40~124행)의
sha256 · file_hashes · amendments_entries · append_amendment · prereg_status를 경로만 바꿔 옮겼다.

- lock: 잠금 대상(플랜 부록 4-7) 해시를 `eval/prereg_lock.json`에 한 번만 기록한다. 이미 있으면 거부
- check: 지금 해시를 잠금 기록과 대조한다. 다르면 AMENDMENTS에 그 해시가 기록됐는지 본다
- amend: AMENDMENTS.md에 항목을 추가만 한다. 각 항목은 이전 항목(JSON 문자열)의 sha256을 담는다

    python eval/prereg.py lock
    python eval/prereg.py check
    python eval/prereg.py amend --kind fix --content "사유" --files mfds/refs.py
"""

import argparse
import hashlib
import json
import re
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
    "mfds/refs.py",
    "mfds/ingredients.py",
    "schema/schema.yaml",
    "schema/intents.yaml",
    "schema/eval_cypher",
    "schema/eval_cypher_check",
    "schema/eval_questions",
]
LOCK_FILE = "eval/prereg_lock.json"
AMENDMENTS_FILE = "eval/AMENDMENTS.md"
PLACEHOLDER = "(잠금 전에 채웁니다)"
AMENDMENTS_HEADER = "# AMENDMENTS — 잠금 뒤 기록 (추가만 가능, 각 항목은 이전 항목의 sha256 을 담는다)\n\n"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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
    if prereg.exists() and PLACEHOLDER in prereg.read_text(encoding="utf-8"):
        raise SystemExit(f"PREREG.md 에 '{PLACEHOLDER}'가 남아 있습니다. 채운 뒤 잠급니다.")
    data = {"locked_at": datetime.now(timezone.utc).isoformat(), "targets": targets, "files": files}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def prereg_status(root: Path = ROOT) -> dict[str, Any]:
    """잠금 대상 해시를 기준과 대조한다. 다르면 AMENDMENTS 에 그 해시가 있는지 본다."""
    path = root / LOCK_FILE
    if not path.exists():
        raise SystemExit(f"{LOCK_FILE} 없음 — lock 을 먼저 한다")
    base = json.loads(path.read_text(encoding="utf-8"))
    now = file_hashes(root, expand(root, base["targets"]))
    amended = {k: v for e in amendments_entries(root) for k, v in e.get("new_hashes", {}).items()}
    status = {}
    for k in sorted(set(now) | set(base["files"])):
        v, b = now.get(k), base["files"].get(k)
        if v == b:
            status[k] = "기준과 같음"
        elif amended.get(k) == v:
            status[k] = "AMENDMENTS 에 기록된 수정"
        else:
            status[k] = "기록 없는 변경(탐색적)"
    return {"files": now, "status": status, "ok": all(s != "기록 없는 변경(탐색적)" for s in status.values())}


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("lock")
    sub.add_parser("check")
    am = sub.add_parser("amend")
    am.add_argument("--kind", required=True)
    am.add_argument("--content", required=True)
    am.add_argument("--files", nargs="*", default=[], help="수정한 잠금 대상(지금 해시를 기록)")
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
        print(json.dumps(append_amendment(args.kind, args.content, file_hashes(ROOT, [ROOT / f for f in args.files])),
                         ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
