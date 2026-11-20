"""공공데이터포털: 의약품 · 화장품 행정처분(식품의약품안전처_의약품 행정처분 정보, `MdcinExaathrService04`).

설명에는 의약품만 적혀 있지만 화장품법 처분도 들어 있다(2026-11-20 확인, 544건). 응답의 `EXPOSE_CONT`(위반내용)와
`BEF_APPLY_LAW`(근거법령, 조문 번호까지)가 T7 문항 · `BASED_ON` 엣지의 원천이다. 의약품안전나라 웹 상세 페이지는
`robots.txt`가 자동 수집을 막으므로 쓰지 않는다.

키는 `.env`의 `DATA_GO_KR_KEY`. 포털이 주는 Encoding 키(퍼센트 인코딩됨)는 URL에 그대로 붙이고, Decoding 키는
`quote`해서 붙인다(`Collector`가 URL 문자열을 그대로 보내므로 디코딩한 값을 붙이면 `+` · `/` · `=` 때문에 인증이 깨진다).

원본 스냅숏(git 제외)에는 업체명 · 주소 · 사업자번호가 남는다. 읽을 때는 `mfds.disposition.read_drug_rows`가 가린다.

    python -m mfds.collect.drugdisp [--snapshot data/raw/YYYYMMDD]
"""

import argparse
import json
from pathlib import Path
from urllib.parse import quote

from mfds.collect.common import Collector, FetchError, env, snapshot_dir

SOURCE = "drugdisp"
API = "https://apis.data.go.kr/1471000/MdcinExaathrService04/getMdcinExaathrList04"
PAGE = 100


def service_key(raw: str) -> str:
    """URL에 붙일 키. 이미 퍼센트 인코딩된 Encoding 키는 그대로, Decoding 키는 인코딩한다."""
    return raw if "%" in raw else quote(raw, safe="")


def page_url(key: str, n: int) -> str:
    return f"{API}?serviceKey={key}&pageNo={n}&numOfRows={PAGE}&type=json"


def check_json(content: bytes) -> None:
    try:
        data = json.loads(content)
    except json.JSONDecodeError as e:
        raise FetchError(f"JSON 아님: {e}") from None
    head = data.get("header") or {}
    if head.get("resultCode") != "00":
        raise FetchError(f"오류 응답: {head.get('resultCode')} {head.get('resultMsg')}")


def items_of(body: dict) -> list[dict]:
    items = body.get("items") or []
    return [items] if isinstance(items, dict) else list(items)


def collect(c: Collector, key: str) -> int:
    """쪽마다 받아 모은 수가 `totalCount`와 같고 `ADM_DISPS_SEQ`가 고유한지 확인한다."""
    total, items, n = None, [], 1
    while total is None or (n - 1) * PAGE < total:
        body = json.loads(c.fetch(SOURCE, f"p{n}", f"{SOURCE}/p{n:03d}.json", page_url(key, n),
                                  check=check_json))["body"]
        total = int(body["totalCount"])
        items += items_of(body)
        n += 1
    if len(items) != total:
        raise FetchError(f"{SOURCE}: totalCount {total}인데 {len(items)}건")
    seqs = [str(i.get("ADM_DISPS_SEQ") or "") for i in items]
    if "" in seqs or len(set(seqs)) != len(seqs):
        raise FetchError(f"{SOURCE}: ADM_DISPS_SEQ 비었거나 중복")
    return total


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", type=Path, default=None)
    args = ap.parse_args()
    c = Collector(args.snapshot or snapshot_dir())
    n = collect(c, service_key(env("DATA_GO_KR_KEY")))
    print(f"{SOURCE}: {n:,}건, 이번 요청 {c.requests}회 → {c.snapshot}")


if __name__ == "__main__":
    main()
