"""테스트용 고정 자료(약 16건)를 공개 시연 키로 받는다.

국가법령정보 DRF는 OC=test, 식품안전나라는 sample 키를 쓴다(둘 다 공개 시연용).
대량 수집에는 쓰지 않는다. 결과는 tests/fixtures/ 와 tests/fixtures/manifest.tsv.
식품 처분은 업소 정보 열을 지운 뒤 저장한다(manifest 해시는 저장한 내용 기준).

    python scripts/fetch_fixtures.py
"""

import hashlib
import json
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

import requests
from lxml import etree

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tests" / "fixtures"
DRF = "https://www.law.go.kr/DRF"
FOOD = "http://openapi.foodsafetykorea.go.kr/api/sample"
MFDS_ORG = "1471000"
# 업소명 · 주소 · 전화 · 대표자 · 인허가번호. 그래프 · 평가에 쓰지 않고, 공개 저장소에 올리지 않는다
FOOD_PII = ("PRCSCITYPOINT_BSSHNM", "ADDR", "TELNO", "PRSDNT_NM", "LCNS_NO")

session = requests.Session()
manifest: list[tuple[str, str, str, int]] = []


def strip_food_pii(b: bytes) -> bytes:
    data = json.loads(b)
    for svc in data.values():
        for row in svc.get("row", []):
            for col in FOOD_PII:
                row.pop(col, None)
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def fetch(url: str, name: str, clean: Callable[[bytes], bytes] | None = None) -> bytes:
    time.sleep(1.0)
    r = session.get(url, timeout=60)
    r.raise_for_status()
    content = clean(r.content) if clean else r.content
    (OUT / name).write_bytes(content)
    manifest.append((name, url, hashlib.sha256(content).hexdigest(), len(content)))
    print(f"{name}: {len(content):,} bytes")
    return content


def drf(kind: str, **params) -> str:
    return f"{DRF}/{kind}.do?" + urlencode({"OC": "test", "type": "XML", **params})


def xml(b: bytes):
    return etree.fromstring(b)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)

    laws = xml(fetch(drf("lawSearch", target="law", org=MFDS_ORG, display=100), "law_list_mfds.xml"))
    fetch(drf("lawSearch", target="law", display=100), "law_list_all_p1.xml")

    mst = {l.findtext("법령명한글").strip(): l.findtext("법령일련번호") for l in laws.iter("law")}
    for name, fn in [("약사법", "law_yaksa.xml"), ("약사법 시행령", "law_yaksa_enf.xml"),
                     ("의약품 등의 안전에 관한 규칙", "law_yaksa_rule.xml"), ("식품위생법", "law_food.xml")]:
        fetch(drf("lawService", target="law", MST=mst[name]), fn)

    fetch(drf("lawService", target="thdCmp", MST=mst["약사법"], knd=2), "thdcmp_yaksa_delegation.xml")
    fetch(drf("lawService", target="lsStmd", MST=mst["약사법"]), "lsstmd_yaksa.xml")

    fetch(drf("lawSearch", target="admrul", org=MFDS_ORG, display=100), "admrul_list_mfds_p1.xml")
    found = xml(fetch(drf("lawSearch", target="admrul", query="병용금기", display=10), "admrul_search_dur.xml"))
    dur_id = next(a.findtext("행정규칙일련번호") for a in found.iter("admrul")
                  if "병용금기" in (a.findtext("행정규칙명") or ""))
    fetch(drf("lawService", target="admrul", ID=dur_id), "admrul_dur.xml")

    # 판례: 대법원 출처에서 참조조문이 있는 것 하나, 없는 것 하나
    precs = xml(fetch(drf("lawSearch", target="prec", query="약사법", search=2, display=100), "prec_search_yaksa.xml"))
    want = {"with_ref": None, "no_ref": None}
    for p in precs.iter("prec"):
        if p.findtext("데이터출처명") != "대법원" or all(want.values()):
            continue
        pid = p.findtext("판례일련번호")
        time.sleep(1.0)
        body = session.get(drf("lawService", target="prec", ID=pid), timeout=60).content
        has_ref = bool((xml(body).findtext(".//참조조문") or "").strip())
        key = "with_ref" if has_ref else "no_ref"
        if want[key] is None:
            want[key] = pid
            fetch(drf("lawService", target="prec", ID=pid), f"prec_{key}.xml")

    ex = xml(fetch(drf("lawSearch", target="expc", query="약사법", display=5), "expc_search_yaksa.xml"))
    fetch(drf("lawService", target="expc", ID=next(ex.iter("expc")).findtext("법령해석례일련번호")), "expc_one.xml")
    dc = xml(fetch(drf("lawSearch", target="decc", query="식품위생법", search=2, display=5), "decc_search_food.xml"))
    fetch(drf("lawService", target="decc", ID=next(dc.iter("decc")).findtext("행정심판재결례일련번호")), "decc_one.xml")

    fetch(f"{FOOD}/I0470/json/1/5", "food_disposition_I0470_5rows.json", clean=strip_food_pii)

    now = datetime.now(timezone.utc).isoformat()
    lines = ["file\turl\tsha256\tbytes\tfetched_at"] + [f"{n}\t{u}\t{h}\t{s}\t{now}" for n, u, h, s in manifest]
    (OUT / "manifest.tsv").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
