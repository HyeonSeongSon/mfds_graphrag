"""외장 백업 목록 · 대조(PLAN "백업(재발 방지)" 표 1 ~ 5). 건수 · 크기 · 해시만 출력한다(파일 내용은 읽어 해시만 계산).

    python scripts/backup_manifest.py make 1                 # 백업 1 대상 → data/backups/backup1.sha256
    python scripts/backup_manifest.py verify 1 E:\\mfds-backup1  # 복사한 곳에서 다시 계산해 대조

- 목록 파일은 `sha256sum` 형식(`<sha256>  <저장소 기준 경로>`)이라 Git Bash의 `sha256sum -c`로도 대조할 수 있다.
- 복사할 때는 저장소 기준 경로를 그대로 둔다(예: `<백업 폴더>/data/raw/20261105/...`). 목록 파일도 같이 복사한다.
- `data/backups/`는 git 제외다. 목록 파일 자체의 sha256은 PLAN 변경 이력에 적는다.
- 백업 2 ~ 5 대상은 그 시점에 `SETS`에 더한다(각 백업에는 그 시점까지 새로 생긴 것만 더함).
"""

import argparse
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "backups"
# PLAN 백업 표 1: 11-24 밤 잠금 직후 — 스냅숏, data/raw/manual/, 평가셋 v1 · 제외 목록 · M4 후보.
# 라벨 검수 파일은 판정 중이라 바뀌지만, 원문이 든 git 제외 파일이라 그때그때의 사본으로 함께 둔다.
SETS = {
    "1": ["data/raw/20261105", "data/raw/20261029", "data/raw/manual", "eval/evalset/v1", "eval/exclusion_ids.json",
          "eval/label_audit.csv"],
}


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def files_of(root: Path, targets: list[str]) -> list[str]:
    """대상(파일 · 폴더) 아래 모든 파일의 저장소 기준 경로, 정렬. 없는 대상은 오류."""
    out = []
    for t in targets:
        p = root / t
        if not p.exists():
            raise SystemExit(f"대상 없음: {t}")
        out += [t] if p.is_file() else [q.relative_to(root).as_posix() for q in p.rglob("*") if q.is_file()]
    return sorted(set(out))


def make(root: Path, targets: list[str]) -> tuple[str, int]:
    """목록 본문과 총 바이트."""
    rel = files_of(root, targets)
    lines = [f"{sha256(root / r)}  {r}" for r in rel]
    return "\n".join(lines) + "\n", sum((root / r).stat().st_size for r in rel)


def parse(text: str) -> dict[str, str]:
    return {line[66:]: line[:64] for line in text.splitlines() if line.strip()}


def verify(expected: dict[str, str], dest: Path) -> dict[str, int]:
    r = {"같음": 0, "다름": 0, "없음": 0}
    for rel, h in expected.items():
        p = dest / rel
        r["없음" if not p.is_file() else ("같음" if sha256(p) == h else "다름")] += 1
    return r


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["make", "verify"])
    ap.add_argument("set", choices=sorted(SETS))
    ap.add_argument("dest", nargs="?", help="verify: 백업을 복사한 폴더(저장소 기준 경로가 그대로 있는 곳)")
    args = ap.parse_args()
    mpath = OUT / f"backup{args.set}.sha256"
    if args.cmd == "make":
        text, size = make(ROOT, SETS[args.set])
        OUT.mkdir(parents=True, exist_ok=True)
        mpath.write_text(text, encoding="utf-8", newline="\n")
        n = len(parse(text))
        print(f"# 백업 {args.set}: 파일 {n:,}, {size / 2**20:.1f} MiB")
        for t in SETS[args.set]:
            print(f"  {t}")
        print(f"## 목록 {mpath.relative_to(ROOT).as_posix()}, sha256 {sha256(mpath)}")
        return
    if not args.dest:
        raise SystemExit("verify에는 백업 폴더 경로가 필요하다")
    r = verify(parse(mpath.read_text(encoding="utf-8")), Path(args.dest))
    print(f"# 백업 {args.set} 대조({args.dest}): " + ", ".join(f"{k} {v:,}" for k, v in r.items()))
    print(f"## 결과: {'통과' if not r['다름'] and not r['없음'] else '실패'}")
    if r["다름"] or r["없음"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
