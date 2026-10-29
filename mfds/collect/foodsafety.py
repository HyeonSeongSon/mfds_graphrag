"""식품안전나라: 식품 행정처분 I0470(전체). I2630 · I0481 · I0480 · I0482는 I0470의 업종별 부분집합이라 받지 않는다.

- 키(`FOODSAFETY_KEY`)가 있으면 API로 1,000행씩 JSON을 받는다(원본 그대로 저장).
- 키가 없으면 사용자가 2026-10-28에 직접 받은 CSV를 스냅숏으로 복사한다(요청 0회, manifest에 원본 sha256).

원본 스냅숏(git 제외)에는 업소 정보가 남는다. 읽을 때는 `read_rows`가 기본으로 업소 정보 열을 지운다.
업소 정보가 필요한 곳은 평가셋 생성기의 같은 업소 이웃 조건뿐이고, 메모리에서만 쓴다(`keep_pii=True`).

    python -m mfds.collect.foodsafety [--snapshot data/raw/YYYYMMDD] [--source <수동 수집 CSV>]
"""

import argparse
import csv
import gzip
import io
import json
import os
from pathlib import Path

from dotenv import load_dotenv

from mfds.collect.common import RAW, ROOT, Collector, FetchError, snapshot_dir

SERVICE = "I0470"
PAGE = 1000
FOOD_API = "http://openapi.foodsafetykorea.go.kr/api"
# 업소명 · 주소 · 전화 · 대표자 · 인허가번호
PII = ("PRCSCITYPOINT_BSSHNM", "ADDR", "TELNO", "PRSDNT_NM", "LCNS_NO")
MANUAL = RAW / "20261028" / "foodsafety" / f"{SERVICE}.csv.gz"
MANUAL_NOTE = "사용자 수동 수집, 2026-10-28"


def check_json(content: bytes) -> None:
    try:
        data = json.loads(content)
    except json.JSONDecodeError as e:
        raise FetchError(f"JSON 아님: {e}") from None
    result = (data.get(SERVICE) or data).get("RESULT") or {}
    if result.get("CODE") != "INFO-000":
        raise FetchError(f"오류 응답: {result.get('CODE')} {result.get('MSG')}")


def collect_api(c: Collector, key: str) -> int:
    total, rows, start = None, 0, 1
    while total is None or start <= total:
        end = start + PAGE - 1
        url = f"{FOOD_API}/{key}/{SERVICE}/json/{start}/{end}"
        page = json.loads(c.fetch("foodsafety", f"{SERVICE} {start}-{end}",
                                  f"foodsafety/{SERVICE}/{start:06d}.json", url, check=check_json))[SERVICE]
        total = int(page["total_count"])
        rows += len(page.get("row", []))
        start = end + 1
    if rows != total:
        raise FetchError(f"{SERVICE}: total_count {total}인데 {rows}행")
    return rows


def collect_copy(c: Collector, src: Path) -> int:
    c.copy_in("foodsafety", SERVICE, f"foodsafety/{SERVICE}.csv.gz", src, MANUAL_NOTE)
    return len(read_rows(c.snapshot))


def read_rows(snapshot: Path, keep_pii: bool = False) -> list[dict[str, str]]:
    """스냅숏의 I0470 행(수동 수집 CSV 또는 API JSON). 기본으로 업소 정보 열을 지운다."""
    path = snapshot / "foodsafety" / f"{SERVICE}.csv.gz"
    if path.exists():
        with gzip.open(path, "rb") as f:
            rows = list(csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig")))
    else:
        pages = sorted((snapshot / "foodsafety" / SERVICE).glob("*.json"))
        if not pages:
            raise FileNotFoundError(f"{snapshot}에 {SERVICE}가 없다")
        rows = [r for p in pages for r in json.loads(p.read_bytes())[SERVICE].get("row", [])]
    if not keep_pii:
        rows = [{k: v for k, v in r.items() if k not in PII} for r in rows]
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", type=Path, default=None)
    ap.add_argument("--source", type=Path, default=MANUAL, help="키가 없을 때 복사할 수동 수집 CSV")
    args = ap.parse_args()
    c = Collector(args.snapshot or snapshot_dir())
    load_dotenv(ROOT / ".env")
    key = os.environ.get("FOODSAFETY_KEY", "").strip()
    n = collect_api(c, key) if key else collect_copy(c, args.source)
    print(f"foodsafety: {SERVICE} {n:,}행({'API' if key else '복사'}), 이번 요청 {c.requests}회 → {c.snapshot}")


if __name__ == "__main__":
    main()
