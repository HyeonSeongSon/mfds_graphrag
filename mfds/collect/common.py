"""수집 공통: 초당 1회 요청, 재시도, 이어받기, 스냅숏 폴더, manifest.

응답은 `.part`로 받아 검사를 통과해야 최종 이름으로 바꾼다. 국가법령정보 DRF는 키 오류 · 미승인 대상에도
HTTP 200과 오류 문서(`<Response><result>…</result>`)를 돌려주므로, 검사 없이 저장하면 이어받기가 잘못 받은
파일을 "이미 받음"으로 보고 계속 건너뛴다.

스냅숏은 수집 한 번에 폴더 하나(`data/raw/<수집일>/`). manifest에는 키를 가린 URL만 남긴다.
"""

import hashlib
import os
import re
import shutil
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

import requests
from dotenv import load_dotenv
from lxml import etree

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw"
DRF = "https://www.law.go.kr/DRF"
MANIFEST_COLUMNS = ("source", "id", "file", "url", "sha256", "bytes", "fetched_at", "note")
# DRF는 없는 ID에도 200 + 오류 문서를 주므로 404는 일시 장애다(D3 심판 본문 15건이 404 → 재실행에서 모두 200)
RETRY_STATUS = {404, 429, 500, 502, 503, 504}
_KEY_PATTERNS = [
    (re.compile(r"(?<=[?&]OC=)[^&]+"), "***"),  # 국가법령정보
    (re.compile(r"(?<=/api/)[^/]+(?=/)"), "***"),  # 식품안전나라 /api/{key}/{서비스}/...
]


class FetchError(Exception):
    """재시도로 풀리지 않는 실패(오류 문서 · XML 아님 · 재시도 소진)."""


def env(name: str) -> str:
    load_dotenv(ROOT / ".env")
    value = os.environ.get(name, "").strip()
    if not value:
        raise FetchError(f"{name}가 .env에 없다")
    return value


def mask(url: str) -> str:
    for pattern, repl in _KEY_PATTERNS:
        url = pattern.sub(repl, url)
    return url


def check_drf_xml(content: bytes) -> None:
    """DRF 응답이 정상 XML인지. 오류 문서면 그 문구로 FetchError."""
    try:
        root = etree.fromstring(content)
    except etree.XMLSyntaxError as e:
        raise FetchError(f"XML 아님: {e}") from None
    if root.tag == "Response" and root.find("result") is not None:
        raise FetchError(f"오류 문서: {root.findtext('result')} {root.findtext('msg') or ''}".strip())
    if root.tag == "Law" and len(root) == 0:  # 없는 ID: <Law>일치하는 판례가 없습니다. …</Law>
        raise FetchError(f"없는 ID: {(root.text or '').strip()}")


def snapshot_dir(date: str | None = None) -> Path:
    return RAW / (date or datetime.now().strftime("%Y%m%d"))


def drf_url(oc: str, kind: str, **params) -> str:
    return f"{DRF}/{kind}.do?" + urlencode({"OC": oc, "type": "XML", **params})


def list_items(content: bytes, tag: str) -> tuple[int, list[dict[str, str]]]:
    """DRF 검색 결과 → (totalCnt, 항목 dict 목록)."""
    root = etree.fromstring(content)
    items = [{c.tag: (c.text or "").strip() for c in e} for e in root.iter(tag)]
    return int(root.findtext("totalCnt") or 0), items


def expect(path: str, want: str) -> Callable[[bytes], None]:
    """DRF 검사 + 응답 안 `path` 값이 요청한 ID와 같은지. 없는 MST는 오류 없이 다른 법령을 돌려주기 때문이다."""
    def check(content: bytes) -> None:
        check_drf_xml(content)
        got = (etree.fromstring(content).findtext(path) or "").strip()
        if got != want:
            raise FetchError(f"요청한 ID와 다른 응답: {path} {want} → {got or '(없음)'}")
    return check


def fetch_pages(c: "Collector", source: str, rel_dir: str, oc: str, target: str, tag: str,
                display: int = 100, **params) -> list[dict[str, str]]:
    """검색 결과를 쪽마다 받아 항목을 모은다. 모은 수가 totalCnt와 다르면 FetchError."""
    def page(n: int) -> tuple[int, list[dict[str, str]]]:
        url = drf_url(oc, "lawSearch", target=target, display=display, page=n, **params)
        return list_items(c.fetch(source, f"{target} p{n}", f"{rel_dir}/p{n:03d}.xml", url), tag)

    total, items = page(1)
    for n in range(2, -(-total // display) + 1):
        items += page(n)[1]
    if len(items) != total:
        raise FetchError(f"{rel_dir}: totalCnt {total}인데 항목 {len(items)}개")
    return items


class Collector:
    """스냅숏 폴더 하나에 받는다. `session` · `sleep` · `clock`은 테스트에서 바꿔 끼운다."""

    def __init__(self, snapshot: Path, *, min_interval: float = 1.0, retries: int = 3, timeout: int = 60,
                 session=None, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.snapshot = snapshot
        self.min_interval = min_interval
        self.retries = retries
        self.timeout = timeout
        self.session = session or requests.Session()
        self.sleep = sleep
        self.clock = clock
        self.requests = 0
        self._last: float | None = None
        snapshot.mkdir(parents=True, exist_ok=True)
        self.manifest = snapshot / "manifest.tsv"
        if not self.manifest.exists():
            self.manifest.write_text("\t".join(MANIFEST_COLUMNS) + "\n", encoding="utf-8")
        with self.manifest.open(encoding="utf-8") as f:
            next(f)
            self._recorded = {line.split("\t")[2] for line in f if line.strip()}

    def _record(self, source: str, id_: str, rel: str, url: str, content: bytes, fetched_at: str,
                note: str = "") -> None:
        row = (source, id_, rel, mask(url), hashlib.sha256(content).hexdigest(), str(len(content)), fetched_at, note)
        with self.manifest.open("a", encoding="utf-8") as f:
            f.write("\t".join(row) + "\n")
        self._recorded.add(rel)

    def _get(self, url: str) -> bytes:
        for attempt in range(self.retries + 1):
            if self._last is not None:
                wait = self.min_interval - (self.clock() - self._last)
                if wait > 0:
                    self.sleep(wait)
            self._last = self.clock()
            self.requests += 1
            try:
                r = self.session.get(url, timeout=self.timeout)
            except requests.RequestException as e:
                r, reason = None, type(e).__name__
            if r is not None:
                if r.status_code not in RETRY_STATUS:
                    if r.status_code >= 400:
                        raise FetchError(f"HTTP {r.status_code}: {mask(url)}")
                    return r.content
                reason = f"HTTP {r.status_code}"
            if attempt < self.retries:
                self.sleep(2.0 ** attempt * 5)
        raise FetchError(f"재시도 소진({reason}): {mask(url)}")

    def fetch(self, source: str, id_: str, rel: str, url: str,
              check: Callable[[bytes], None] | None = check_drf_xml) -> bytes:
        """이미 받은 파일이면 요청 없이 읽어 돌려준다. 아니면 받고 검사한 뒤 저장 · manifest 기록."""
        path = self.snapshot / rel
        if path.exists():
            content = path.read_bytes()
            if rel not in self._recorded:  # 저장 직후 manifest 기록 전에 끊긴 경우
                mtime = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
                self._record(source, id_, rel, url, content, mtime, "manifest 복구")
            return content
        content = self._get(url)
        if check:
            check(content)
        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_name(path.name + ".part")
        part.write_bytes(content)
        part.replace(path)
        self._record(source, id_, rel, url, content, datetime.now(timezone.utc).isoformat())
        return content

    def copy_in(self, source: str, id_: str, rel: str, src: Path, note: str) -> bytes:
        """다른 경로의 파일(사용자가 직접 받은 원본 등)을 스냅숏에 복사해 기록한다. 원본은 건드리지 않는다."""
        path = self.snapshot / rel
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, path)
        content = path.read_bytes()
        if hashlib.sha256(content).digest() != hashlib.sha256(src.read_bytes()).digest():
            raise FetchError(f"복사본과 원본의 sha256이 다르다: {rel}")
        if rel not in self._recorded:
            origin = src.resolve()
            origin = origin.relative_to(ROOT).as_posix() if origin.is_relative_to(ROOT) else origin.name
            self._record(source, id_, rel, origin, content, datetime.now(timezone.utc).isoformat(), note)
        return content
