"""비정형 추출(플랜 부록 B1, 2026-11-27 확정판): 판례 · 법령해석례 · 행정심판례 → 조문 인용 `{text, law, is_key}`.

- 입력: 판례는 판시사항 + 판결요지(둘 다 없으면 판례내용), 해석례는 질의요지 · 회답 · 이유, 심판은 주문 · 청구취지 · 이유.
  8천 자를 넘는 판례내용 · 심판 이유는 앞 4천 + " (중략) " + 뒤 4천 자. **참조조문 · 참조판례는 어떤 경우에도 넣지 않는다**
- 대상: 스냅숏 세 종류에서 평가 원천(`eval/exclusion_ids.json` 판례)을 뺀 것
- **LLM은 조문 ID를 만들지 않는다.** ID는 `mfds/refs.py`(잠금)가 만들고, 이 파일이 `text` 앞머리로 경로를 나눈다
  (명시 법령명 · 같은 법 센티넬 · 맨 제N조 센티넬 · 법/영/규칙 토큰 + `law` 보조 가드). 규칙과 판정 기준은 PLAN B1
- 모델: OpenAI GPT-6.1 Terra, Structured Outputs(strict), 추론 effort low. 키는 `.env`의 `OPENAI_API_KEY`
- 출력: 건수 · 토큰 · 비율만. 키 · 문서 본문은 출력하지 않는다(사람 확인 파일만 문맥을 담고 git 밖 `data/cache/extract/`에 둔다)

    python scripts/extract.py inputs                  # 대상 문서 수 · 입력 글자 수 · 설정 해시
    python scripts/extract.py sample [--n 100]        # 시드 표본을 표준 API로 → sample.jsonl, 판정 A · B 지표, D1 확인 파일
    python scripts/extract.py check FILE.jsonl [FILE2.jsonl] [--all] [--resolved OUT.jsonl] [--review OUT.csv]
    python scripts/extract.py review-tally            # 판정이 채워진 b_review · d1_review → 판정 B · D1, eval/extract_review/로 내보냄
    python scripts/extract.py batch-prepare           # batch_input.jsonl(이미 제출했으면 멈춤)
    python scripts/extract.py batch-submit            # 업로드 → 배치 생성(batch.json)
    python scripts/extract.py batch-status
    python scripts/extract.py batch-fetch             # results.jsonl · errors.jsonl + 해시
"""

import argparse
import csv
import hashlib
import json
import random
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from lxml import etree

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mfds.load.build import load_exclusion  # noqa: E402
from mfds.refs import DOTS, LawDict, Resolver, article_set, norm_text  # noqa: E402

SNAPSHOT = ROOT / "data" / "raw" / "20261105"
EXCLUSION = ROOT / "eval" / "exclusion_ids.json"
OUT = ROOT / "data" / "cache" / "extract"
SEED = 20261030  # eval/build_evalset.py SEED와 같은 값
CLIP_OVER, HALF = 8000, 4000
CUT = " (중략) "
MODEL = "gpt-6.1-terra"
EFFORT = "low"
MAX_OUT = 4000
API = "https://api.openai.com/v1"
# USD / 1M 토큰(docs/api-cost.md 6절, 2026-11-10 확인): 입력 · 캐시 입력 · 출력
PRICE_STD = (1.0, 0.05, 5.0)
PRICE_BATCH = (0.5, 0.025, 2.5)
SENTINEL = "제99999조"
REVIEW_PER_SIDE = 10

KINDS = {  # 종류 → (uid 접두사, 일련번호 필드, 종류 이름)
    "prec": ("case", "판례정보일련번호", "판례"),
    "expc": ("expc", "법령해석례일련번호", "법령해석례"),
    "decc": ("decc", "행정심판례일련번호", "행정심판례"),
}

SYSTEM = """너는 한국 법률 문서에서 법령 조문 인용을 뽑는 도구다. 사용자가 주는 문서(판례 · 법령해석례 · 행정심판례 중 하나)를 읽고 지정된 JSON으로만 답한다. 문서에 없는 내용을 지어내지 않는다.

cited_refs의 각 항목은 text · law · is_key 세 값이다. 문서에 나온 순서대로 넣는다.

1. text: 조문 인용 하나를 문서에 적힌 그대로 복사한다.
   - 문서 안에 글자 그대로 있는 부분 문자열이어야 한다. 띄어쓰기 · 괄호 · 「」 · 가운뎃점을 바꾸지 않고, 줄이거나 고치거나 법령명을 채워 넣지 않는다.
   - 한 항목에 인용 하나를 넣는다. 다만 "제44조 제1항 및 제2항", "제3조부터 제5조까지"처럼 이어 쓴 한 덩어리는 하나로 본다.
   - 법령명이 조문 바로 앞에 붙어 있으면 법령명부터 넣는다. 예: "식품위생법 제44조 제1항", "「약사법」 제31조제2항", "구 식품위생법(2019. 1. 1. 법률 제16000호로 개정되기 전의 것) 제44조".
   - 법령명과 조문 사이에 "(이하 '법'이라 한다)" 같은 정의 괄호가 끼어 있으면 법령명부터 괄호까지 통째로 넣는다.
   - 상대 표기는 문서 그대로 둔다. 예: "같은 법 제5조", "동법 시행령 제3조", "법 제7조", "영 제21조", "제58조".
   - 조 번호(제N조)가 들어 있는 인용만 넣는다.
   - 넣지 않는 것: "같은 조" · "동조" · "이 조"처럼 조 번호가 없는 인용, 문서가 옮겨 적은 법령 본문 안의 표현("이 법에서 …란", 옮겨 적은 조문 속 "제2조에 따른"), 법령이 아닌 문서의 조항 번호(회칙 · 정관 · 계약서 · 약관 · 내부 규정), 사건번호.
   - 같은 text가 여러 번 나와도 가리키는 법령이 같으면 한 번만 넣고, 다르면 따로 넣는다.
2. law: 이 인용이 가리키는 법령의 이름.
   - text에 법령명이 있으면 그 법령이다. 예: "「약사법」 제31조" → "약사법".
   - "같은 법" · "동법" · "법" · "영" · "규칙"이나 법령명 없는 "제N조"는 문서의 앞부분을 보고 가리키는 법령을 쓴다. 예: "식품위생법(이하 '법'이라 한다)" 뒤의 "법 제7조" → "식품위생법", "영 제21조" → "식품위생법 시행령".
   - 시행령 · 시행규칙을 가리키면 그 이름까지 쓴다.
   - 문서에 나온 법령만 쓴다. 문서로 알 수 없으면 빈 문자열 ""로 둔다. 추측하지 않는다.
3. is_key: 이 문서의 핵심 판단 대상인 조문이면 true, 아니면 false.
   - 법령해석례: 질의가 해석을 묻는 조문
   - 행정심판례: 처분이 위법 · 부당한지 판단하면서 적용한 근거 조문
   - 판례: 항상 false"""

SCHEMA = {
    "type": "object",
    "properties": {"cited_refs": {"type": "array", "items": {
        "type": "object",
        "properties": {"text": {"type": "string"}, "law": {"type": "string"}, "is_key": {"type": "boolean"}},
        "required": ["text", "law", "is_key"], "additionalProperties": False}}},
    "required": ["cited_refs"],
    "additionalProperties": False,
}

CONFIG_HASH = hashlib.sha256(json.dumps({"model": MODEL, "effort": EFFORT, "max_out": MAX_OUT, "system": SYSTEM,
                                         "schema": SCHEMA}, ensure_ascii=False, sort_keys=True).encode()).hexdigest()

_TAG = re.compile(r"<[^>]+>")
HANGUL = re.compile(r"[가-힣]")
REL = re.compile(r"^(?:같은\s?법|동법)(?:\s?(?P<tier>시행령|시행규칙))?(?=\s?제\s?\d)")
TOKEN = re.compile(r"^(?:이\s?)?(?P<tok>법|영|규칙|시행령|시행규칙)(?=\s?제\s?\d)")
BARE = re.compile(r"^제\s?\d")
SAME = re.compile(r"^(?:같은\s?[조항호]|동[조항호]|이\s?조)")
AFTER_BAD = re.compile(r"\s?의\s?\d|\d")
ARTNO = re.compile(r"제\s?(\d+)\s?조(?:\s?의\s?(\d+))?")
TIER = {"법": None, "영": "시행령", "시행령": "시행령", "규칙": "시행규칙", "시행규칙": "시행규칙"}


def one_line(s: str | None) -> str:
    """parse_decisions.one_line과 같은 정리: 태그를 공백으로, 공백을 하나로."""
    return re.sub(r"\s+", " ", _TAG.sub(" ", s or "")).strip()


def clip(s: str) -> str:
    return s if len(s) <= CLIP_OVER else s[:HALF] + CUT + s[-HALF:]


def doc_fields(kind: str, f: dict[str, str]) -> list[tuple[str, str]]:
    """B1 입력 필드(이름, 정리한 값). 빈 필드는 뺀다. 판례 참조조문 · 참조판례는 어떤 경우에도 넣지 않는다."""
    if kind == "prec":
        parts = [(k, f.get(k, "")) for k in ("판시사항", "판결요지")]
        if not any(v for _, v in parts):
            parts = [("판례내용", clip(f.get("판례내용", "")))]
    elif kind == "expc":
        parts = [(k, f.get(k, "")) for k in ("질의요지", "회답", "이유")]
    else:
        parts = [("주문", f.get("주문", "")), ("청구취지", f.get("청구취지", "")), ("이유", clip(f.get("이유", "")))]
    return [(k, v) for k, v in parts if v]


def user_text(kind: str, parts: list[tuple[str, str]]) -> str:
    return f"[문서 종류] {KINDS[kind][2]}\n" + "\n".join(f"[{k}] {v}" for k, v in parts)


def load_docs(snapshot: Path = SNAPSHOT, exclusion: Path = EXCLUSION) -> tuple[list[dict], Counter]:
    """대상 문서(uid 순) · 집계. 문서: uid, kind, text(모델에 주는 사용자 메시지), ref_field(판례 참조조문, 정확도용)."""
    excl = load_exclusion(exclusion)
    docs, stats = [], Counter()
    for kind, (prefix, id_field, _) in KINDS.items():
        for p in sorted((snapshot / "decisions" / kind / "body").glob("*.xml")):
            f = {c.tag: one_line(c.text) for c in etree.parse(str(p)).getroot()}
            uid = f"{prefix}:{f.get(id_field) or p.stem}"
            stats[f"{kind}.전체"] += 1
            if uid in excl:
                stats[f"{kind}.평가 원천 제외"] += 1
                continue
            parts = doc_fields(kind, f)
            if not parts:
                stats[f"{kind}.입력 없음(뺌)"] += 1
                continue
            text = user_text(kind, parts)
            stats[f"{kind}.잘린 입력"] += CUT in text
            docs.append({"uid": uid, "kind": kind, "text": text,
                         "ref_field": f.get("참조조문", "") if kind == "prec" else ""})
            stats[f"{kind}.대상"] += 1
    return docs, stats


def input_hash(doc: dict) -> str:
    return hashlib.sha256(doc["text"].encode("utf-8")).hexdigest()[:16]


def body(doc: dict) -> dict:
    return {"model": MODEL, "reasoning_effort": EFFORT, "max_completion_tokens": MAX_OUT,
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": doc["text"]}],
            "response_format": {"type": "json_schema",
                                "json_schema": {"name": "extraction", "strict": True, "schema": SCHEMA}}}


def sample_docs(docs: list[dict], n: int) -> list[dict]:
    """종류별 비례 배분(최대 나머지) 뒤 종류마다 시드 순서로 n건."""
    by = {k: [d for d in docs if d["kind"] == k] for k in KINDS}
    total = len(docs)
    quota = {k: n * len(v) // total for k, v in by.items()}
    rest = sorted(by, key=lambda k: -(n * len(by[k]) % total))
    for k in rest[: n - sum(quota.values())]:
        quota[k] += 1
    out = []
    for k, v in by.items():
        out += random.Random(f"{SEED}:extract_sample:{k}").sample(v, quota[k])
    return out


# ---- HTTP -----------------------------------------------------------------

def api_key() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("OPENAI_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit(".env에 OPENAI_API_KEY가 없다")


def call(method: str, path: str, data: bytes | None = None, ctype: str = "application/json",
         timeout: int = 300) -> tuple[int, bytes]:
    req = urllib.request.Request(f"{API}{path}", data=data, method=method,
                                 headers={"Authorization": f"Bearer {api_key()}", "Content-Type": ctype})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except (urllib.error.URLError, TimeoutError) as e:
        return 0, str(e).encode()


def chat(doc: dict, retries: int = 2) -> dict:
    """표준 API 한 건(일시 오류는 재시도). 배치 결과 줄과 같은 모양 + 설정 해시 · 입력 해시."""
    for attempt in range(retries + 1):
        st, raw = call("POST", "/chat/completions", json.dumps(body(doc)).encode("utf-8"))
        if st == 200 or attempt == retries or st not in (0, 429, 500, 502, 503, 504):
            break
        time.sleep(5 * (attempt + 1))
    try:
        js = json.loads(raw)
    except json.JSONDecodeError:
        js = {"raw": raw[:500].decode("utf-8", "replace")}
    return {"custom_id": doc["uid"], "config": CONFIG_HASH, "input": input_hash(doc),
            "response": {"status_code": st, "body": js}}


# ---- 응답 파싱 · 정리 -------------------------------------------------------

def parse_record(rec: dict) -> tuple[dict | None, str, dict]:
    """(추출 JSON 또는 None, 실패 사유, usage). 사유: http N · finish X · refusal · json · schema."""
    resp = rec.get("response") or {}
    js = resp.get("body") or {}
    usage = js.get("usage") or {}
    if resp.get("status_code") != 200:
        return None, f"http {resp.get('status_code')}", usage
    ch = (js.get("choices") or [{}])[0]
    if ch.get("finish_reason") != "stop":
        return None, f"finish {ch.get('finish_reason')}", usage
    msg = ch.get("message") or {}
    if msg.get("refusal"):
        return None, "refusal", usage
    try:
        out = json.loads(msg.get("content") or "")
    except json.JSONDecodeError:
        return None, "json", usage
    items = out.get("cited_refs") if isinstance(out, dict) else None
    if set(out) != {"cited_refs"} or not isinstance(items, list) or not all(
            isinstance(x, dict) and set(x) == {"text", "law", "is_key"} and isinstance(x["text"], str)
            and isinstance(x["law"], str) and isinstance(x["is_key"], bool) for x in items):
        return None, "schema", usage
    return out, "", usage


def clean_items(out: dict, kind: str) -> list[dict]:
    """공백 정리, 빈 text 버림, (text, law) 쌍 중복 제거(is_key는 하나라도 true면 true). 판례 is_key는 false."""
    by: dict[tuple[str, str], dict] = {}
    for x in out["cited_refs"]:
        t, law = x["text"].strip(), x["law"].strip()
        if not t:
            continue
        k = (t, law)
        key = bool(x["is_key"]) and kind != "prec"
        if k in by:
            by[k]["is_key"] = by[k]["is_key"] or key
        else:
            by[k] = {"text": t, "law": law, "is_key": key}
    return list(by.values())


# ---- 인용 해석 -------------------------------------------------------------

@dataclass
class Ctx:
    """한 스냅숏의 해석 도구: 조문 검사 Resolver, 센티넬용 Resolver, 법령 가족 이름."""
    laws: LawDict
    articles: set[str] | None = None
    r: Resolver = field(init=False)
    r0: Resolver = field(init=False)
    family: dict[str, set[str]] = field(init=False)

    def __post_init__(self) -> None:
        self.r = Resolver(self.laws, self.articles)
        self.r0 = Resolver(self.laws, None)
        self.family = defaultdict(set)
        spaced = defaultdict(set)
        for name, lid in self.laws.names.items():
            self.family[self.laws.root(lid)].add(name.replace(" ", ""))
            spaced[self.laws.root(lid)].add(name)
        # 법령 가족 이름을 글자 사이 공백 유무와 상관없이 찾는 정규식(가장 긴 이름 우선)
        self.family_rx = {root: re.compile("|".join(r"\s?".join(map(re.escape, n.replace(" ", "")))
                                                    for n in sorted(ns, key=len, reverse=True)))
                          for root, ns in spaced.items()}

    def law_id(self, name: str) -> str | None:
        """모델 law → 법령ID. 공백 · 「」 · 앞 "구" 무시."""
        k = norm_text(re.sub(r"[「」]", "", name)).strip()
        k = re.sub(r"^구\s", "", k).replace(" ", "")
        return self.r.by_key.get(k)


@lru_cache(maxsize=1)
def snapshot_ctx() -> Ctx:
    return Ctx(LawDict.from_snapshot(SNAPSHOT), article_set(sorted((SNAPSHOT / "law" / "body").glob("*.xml"))))


def flat(s: str) -> str:
    return norm_text(s).replace(" ", "")


def find_spans(text: str, s: str, short: bool) -> tuple[list[tuple[int, int]], bool]:
    """`s`가 `text`에 나오는 자리(경계 검사). 그대로 없으면 공백을 무시하고 찾아 입력의 실제 구간을 돌려준다.
    (자리 목록, 공백 복구 여부). 뒤에 숫자 · "의 + 숫자"가 오면 안 되고, short면 앞 글자가 한글이면 안 된다."""
    def ok(a: int, b: int) -> bool:
        if AFTER_BAD.match(text, b):
            return False
        return not (short and a > 0 and HANGUL.match(text[a - 1]))

    spans = [(m.start(), m.start() + len(s)) for m in re.finditer(re.escape(s), text)]
    spans = [x for x in spans if ok(*x)]
    if spans:
        return spans, False
    chars = [re.escape(c) for c in s if not c.isspace()]
    if not chars:
        return [], False
    rx = re.compile(r"\s*".join(chars))
    spans = [(m.start(), m.end()) for m in rx.finditer(text) if ok(m.start(), m.end())]
    return spans, bool(spans)


def segment_start(text: str, pos: int) -> int:
    """잘린 입력이면 pos가 있는 조각의 시작(앞 조각 맥락을 쓰지 않게)."""
    i = text.rfind(CUT, 0, pos)
    return 0 if i < 0 else i + len(CUT)


def sentinel(ctx: Ctx, prefix: str, token: str) -> tuple[str | None, str, bool]:
    """앞부분 + 토큰 + 제99999조를 조문 검사 없이 해석 → (법령ID, 실패 사유, refs의 구법 표시)."""
    res = ctx.r0.resolve(f"{prefix}{token}{SENTINEL}" if not token else f"{prefix}{token} {SENTINEL}")
    for ref in reversed(res.refs):
        if ref.article_uid.endswith(":99999"):
            return ref.law_id, "", ref.historical
    return None, (res.failures[-1].reason if res.failures else "none"), False


OLD_MARK = re.compile(r"구\s?「?$")


def old_before(ctx: Ctx, text: str, pos: int, law_id: str, lo: int = 0) -> bool:
    """pos 앞(lo부터)에서 마지막으로 나온 그 법령 가족 이름 앞에 "구"가 붙었는지. refs.py는 "구 X법(…) 제36조" 뒤의
    "같은 법 · 맨 제N조"에 구법 표시를 넘기지 않는다. 이 추정은 `historical_inferred`로 따로 내고, 적재 때 추출 엣지의
    구법 근거 (3)으로 쓴다(AMENDMENTS 4, docs/schema.md "추출 엣지의 구법 표시"). `historical`에는 refs.py 표시만 담는다.
    표본 14조문을 문맥으로 확인했고 오탐은 확인되지 않았다(PLAN 32차 (6))."""
    rx = ctx.family_rx.get(ctx.laws.root(law_id))
    if rx is None:
        return False
    last = None
    for m in rx.finditer(text, lo, pos):
        last = m
    return bool(last and OLD_MARK.search(text[max(lo, last.start() - 3):last.start()]))


def target_law(ctx: Ctx, law_id: str, tier: str | None) -> str | None:
    root = ctx.laws.root(law_id)
    if tier is None:
        return root
    if tier == "시행령":
        return ctx.laws.decree_of(root)
    return ctx.laws.child(root, "시행규칙")


def guard(ctx: Ctx, text: str, pos: int, law_id: str) -> str:
    """`law` 보조 가드: 법령 가족 이름이 pos보다 앞에 있어야 한다. '' 통과 · 'cut' 앞 조각에만 있어 통과 · 그 밖 사유."""
    names = ctx.family[ctx.laws.root(law_id)]
    seg = segment_start(text, pos)
    if any(n in flat(text[seg:pos]) for n in names):
        return ""
    if any(n in flat(text[:pos]) for n in names):
        return "cut"
    return "뒤에만 있음" if any(n in flat(text) for n in names) else "입력에 없음"


def is_short(t: str) -> bool:
    return bool(TOKEN.match(t) or BARE.match(t))


def resolve_item(ctx: Ctx, text: str, item: dict, use_law: bool = True,
                 spans: tuple[list[tuple[int, int]], bool] | None = None) -> dict:
    """항목 하나 → {path, refs(uid 목록), article_uids, notes(Counter), span(첫 자리), sentinel_laws, assist}.
    spans: resolve_doc이 다른 항목과의 겹침을 정리한 자리(없으면 여기서 찾음)."""
    t = item["text"]
    notes: Counter = Counter()
    out = {"text": t, "law": item["law"], "is_key": item["is_key"], "path": "", "refs": [], "article_uids": [],
           "historical": [], "historical_inferred": [], "notes": notes, "span": None, "sentinel_laws": [], "assist": []}

    def add(res, hist: bool = False, inferred: bool = False) -> None:
        """historical: refs.py가 표시한 구법(해석 결과의 표시 또는 hist=센티넬 결과의 표시) — 적재 때 엣지 historical.
        historical_inferred: refs.py 밖 추정 — 적재 때 근거 (3)(AMENDMENTS 4). 적재기가 historical과 합친다."""
        for ref in res.refs:
            if ref.uid not in out["refs"]:
                out["refs"].append(ref.uid)
            if ref.article_uid not in out["article_uids"]:
                out["article_uids"].append(ref.article_uid)
            if (ref.historical or hist) and ref.article_uid not in out["historical"]:
                out["historical"].append(ref.article_uid)
                notes["구법 표시(refs.py)"] += 1
            elif inferred and not (ref.historical or hist) and ref.article_uid not in out["historical_inferred"]:
                out["historical_inferred"].append(ref.article_uid)
                notes["구법 추정"] += 1

    if SAME.match(t):
        out["path"] = "같은 조(풀지 않음)"
        return out
    rel, tok, bare = REL.match(t), TOKEN.match(t), BARE.match(t)
    spans, recovered = spans if spans is not None else find_spans(text, t, is_short(t))
    if recovered:
        notes["공백 복구"] += 1
    if not spans:
        out["path"] = "입력에 없음(버림)" if not item.get("_covered") else "더 긴 인용에 포함(버림)"
        return out
    out["span"] = spans[0]
    model_law = ctx.law_id(item["law"]) if item["law"] else None

    if not (rel or tok or bare):
        out["path"] = "명시"
        res = ctx.r.resolve(text[spans[0][0]:spans[0][1]])
        add(res)
        for f in res.failures:
            notes[f"명시 해석 실패 {f.reason}"] += 1
        if res.refs:
            got = res.refs[0].law_id
            notes["명시 law " + ("맞음" if model_law == got else "없음" if not item["law"]
                               else "사전에 없음" if model_law is None else "틀림")] += 1
        return out

    pat = REL if rel else TOKEN if tok else BARE
    for a, b in spans:
        seg = segment_start(text, a)
        piece = text[a:b]
        head = pat.match(piece)  # 공백 복구 때는 모델 text와 입력 구간의 공백이 다르므로 입력 구간에서 다시 잡는다
        if head is None:
            notes["경로 판정 불일치(버림)"] += 1
            continue
        rest = piece[head.end():] if (rel or tok) else piece
        law, s_hist = None, False
        if rel:
            out["path"] = "같은 법"
            law, why, s_hist = sentinel(ctx, text[seg:a], head.group(0))
            out["sentinel_laws"].append(law)
            if law is not None and not head.group("tier") and ctx.laws.root(law) != law:
                # refs.py는 "같은 법 시행규칙 제X조" 뒤의 "같은 법"을 시행규칙으로 읽는다. "같은 법"은 법률이다(표본 7건, 2026-11-27)
                notes["같은 법 → 기본 법률로 바로잡음"] += 1
                law = ctx.laws.root(law)
            if law is None and why != "no_context":
                notes[f"센티넬 {why}(버림)"] += 1
                continue
            if law is None:
                notes["센티넬 no_context"] += 1
        elif bare:
            out["path"] = "맨 제N조"
            law, why, s_hist = sentinel(ctx, text[seg:a], "")
            if law is None and why != "no_context":
                notes[f"센티넬 {why}(버림)"] += 1
                continue
        else:
            out["path"] = "법·영·규칙"
        if law is not None:
            add(ctx.r.resolve(rest, context_law=law), s_hist, old_before(ctx, text, a, law, seg))
            notes["센티넬로 해석"] += 1
            continue
        # law 보조
        if not use_law:
            notes["law 보조 꺼짐"] += 1
            continue
        if not item["law"]:
            notes["law 보조: law 없음"] += 1
            continue
        if model_law is None:
            notes["law 보조: 사전에 없음"] += 1
            continue
        g = guard(ctx, text, a, model_law)
        if g and g != "cut":
            notes[f"law 보조 가드: {g}(버림)"] += 1
            continue
        if g == "cut":
            notes["law 보조 가드: 잘린 앞 조각만(통과)"] += 1
        if tok:
            dst = target_law(ctx, model_law, TIER[head.group("tok")])
        elif rel and head.group("tier"):
            dst = target_law(ctx, model_law, head.group("tier"))
        else:
            dst = model_law
        if dst is None:
            notes["law 보조: 대상 단계 없음"] += 1
            continue
        res = ctx.r.resolve(rest, context_law=dst)
        hist = bool(re.match(r"^\s*구\s", item["law"])) or old_before(ctx, text, a, dst)
        add(res, inferred=hist)
        notes["law 보조로 해석" if res.refs else "law 보조: 해석 실패"] += 1
        if res.refs:
            out["assist"].append({"span": (a, b), "law": dst, "article_uids": res.article_uids})
    return out


def resolve_doc(ctx: Ctx, text: str, items: list[dict], use_law: bool = True) -> list[dict]:
    """항목마다 자리를 찾은 뒤, 다른 항목의 더 긴 자리 안에 들어간 자리는 버린다(같은 자리를 두 번 해석하지 않게.
    예: "같은 법 제21조제1항" 안의 "법 제21조" · "제21조"). 자리가 모두 버려진 항목은 "더 긴 인용에 포함"."""
    found = [find_spans(text, it["text"], is_short(it["text"]))
             if not SAME.match(it["text"]) else ([], False) for it in items]
    every = [(a, b, i) for i, (sp, _) in enumerate(found) for a, b in sp]
    out, moved = [], set()
    for i, (it, (sp, rec)) in enumerate(zip(items, found)):
        keep, n_cover, n_part = [], 0, 0
        for a, b in sp:
            cover = [j for a2, b2, j in every if j != i and a2 <= a and b <= b2 and (b2 - a2) > (b - a)]
            # 일부만 겹치면 같은 인용을 경계만 다르게 자른 것(표본 9쌍 모두): 먼저 시작한 자리(법령명 · 같은 법이 붙은 쪽)만 남긴다
            part = [j for a2, b2, j in every if j != i and a2 < a < b2 < b]
            if cover or part:
                n_cover, n_part = n_cover + bool(cover), n_part + (not cover)
                if it["is_key"]:
                    moved.add((cover or part)[0])  # 버린 자리의 is_key는 남는 쪽으로 옮긴다
            else:
                keep.append((a, b))
        x = resolve_item(ctx, text, {**it, "_covered": bool(sp) and not keep}, use_law, (keep, rec))
        if n_cover:
            x["notes"]["겹친 자리 버림(더 긴 인용에 포함)"] += n_cover
        if n_part:
            x["notes"]["겹친 자리 버림(일부 겹침, 뒤에서 시작)"] += n_part
            mine = ARTNO.findall(it["text"])
            for a2, b2, j in every:
                if j != i and any(a2 < a < b2 < b for a, b in sp) and ARTNO.findall(items[j]["text"])[:1] != mine[:1]:
                    x["notes"]["일부 겹침 버림: 다른 조 번호(확인 필요)"] += 1  # 같은 인용이 아닐 수 있음
                    break
        out.append(x)
    for j in moved:
        if not out[j]["is_key"]:
            out[j]["is_key"] = True
            out[j]["notes"]["겹침으로 is_key 옮겨 받음"] += 1
    return out


def b_agreement(ctx: Ctx, resolved: list[dict]) -> Counter:
    """판정 B: 같은 법 항목 중 모든 자리 센티넬 법령이 같은 것에서 모델 law 일치. 시행령 · 시행규칙이 붙으면 가족 단위."""
    st = Counter()
    for x in resolved:
        if x["path"] != "같은 법":
            continue
        laws = x["sentinel_laws"]
        if not laws or any(v is None for v in laws):
            st["B 제외: 센티넬 실패 자리 있음"] += 1
            continue
        model = ctx.law_id(x["law"]) if x["law"] else None
        if len(set(laws)) > 1:
            st["B 제외: 자리마다 갈림"] += 1
            st["갈림 중 law가 하나와 맞음"] += model in set(laws)
            continue
        want = laws[0]
        tier = REL.match(x["text"]).group("tier")
        ok = model is not None and (ctx.laws.root(model) == ctx.laws.root(want) if tier else model == want)
        st["B 분모"] += 1
        st["B 일치"] += ok
        # 기록만: "같은 법"을 기본 법률로 바로잡은 기준(판정은 위 사전 기준)
        st["기록: 같은 법 → 법률 보정 기준 일치"] += model is not None and (
            ctx.laws.root(model) == ctx.laws.root(want) if tier else model == ctx.laws.root(want))
    return st


# ---- 검증 지표 -------------------------------------------------------------

def cost(usages: list[dict], price: tuple[float, float, float]) -> float:
    inp = sum(u.get("prompt_tokens", 0) for u in usages)
    cached = sum((u.get("prompt_tokens_details") or {}).get("cached_tokens", 0) for u in usages)
    out = sum(u.get("completion_tokens", 0) for u in usages)
    return ((inp - cached) * price[0] + cached * price[1] + out * price[2]) / 1e6


def reachable(ctx: Ctx, text: str, gold: set[str], regex_arts: set[str], nc_raws: list[str]) -> set[str]:
    """판정 C 분모: 정규식이 놓쳤고, 입력에 그 조 표기가 있으며, 그 자리가 no_context이고, 정답 법령 가족 이름이 입력에 있음."""
    out = set()
    ft = flat(text)
    for g in gold - regex_arts:
        num = g.split(":")[2]
        main, _, br = num.partition("-")
        rx = re.compile(rf"(?<!\d)제\s?{main}\s?조" + (rf"\s?의\s?{br}(?!\d)" if br else r"(?!\s?의\s?\d)"))
        if rx.search(text) and any(rx.search(r) for r in nc_raws) and any(
                n in ft for n in ctx.family[ctx.laws.root(g.split(":")[1])]):
            out.add(g)
    return out


def check(records: list[dict], docs: dict[str, dict], expected: set[str], n_total: int | None = None,
          ctx: Ctx | None = None, use_law: bool = True) -> tuple[dict, list[dict], dict]:
    """(지표, 문서별 해석 결과, D1 후보). 건수 · 비율만 담는다. expected: 결과가 있어야 하는 문서 uid."""
    ctx = ctx or snapshot_ctx()
    st, usages, paths, keys = Counter(), [], Counter(), Counter()
    b = Counter()
    seen = Counter(r.get("custom_id") for r in records)
    st["커버리지: 중복 custom_id"] = sum(v - 1 for v in seen.values() if v > 1)
    st["커버리지: 대상 밖 custom_id"] = sum(1 for k in seen if k not in expected)
    c_tp = c_pred = c_gold = c_reach = c_rec = 0
    r_tp = r_pred = 0
    d1 = {"llm_only": [], "regex_only": []}
    agree = Counter()
    resolved_docs = []
    for rec in records:
        doc = docs.get(rec.get("custom_id"))
        if doc is None or doc["uid"] not in expected:
            continue
        out, why, usage = parse_record(rec)
        usages.append(usage)
        st[f"{doc['kind']}.건수"] += 1
        if rec.get("config") not in (None, CONFIG_HASH):
            st["설정 해시 다름"] += 1
        if out is None:
            st[f"실패 {why}"] += 1
            continue
        items = clean_items(out, doc["kind"])
        res = resolve_doc(ctx, doc["text"], items, use_law)
        b.update(b_agreement(ctx, res))
        llm_arts, explicit_arts = set(), set()
        for x in res:
            paths[(doc["kind"], x["path"])] += len(x["article_uids"]) if x["article_uids"] else 0
            paths[(doc["kind"], x["path"], "항목")] += 1
            st.update(x["notes"])
            llm_arts |= set(x["article_uids"])
            if x["path"] == "명시":
                explicit_arts |= set(x["article_uids"])
            if x["is_key"]:
                keys[(doc["kind"], x["path"], "있음" if x["article_uids"] else "뺌")] += 1
        regex = ctx.r.resolve(doc["text"])
        regex_arts = set(regex.article_uids)
        st["인용 0개"] += not items
        if doc["kind"] == "prec" and doc["ref_field"]:
            gold = set(ctx.r.resolve(doc["ref_field"]).article_uids)
            nc = [f.raw for f in regex.failures if f.reason == "no_context"]
            reach = reachable(ctx, doc["text"], gold, regex_arts, nc)
            c_tp, c_pred, c_gold = c_tp + len(gold & llm_arts), c_pred + len(llm_arts), c_gold + len(gold)
            r_tp, r_pred = r_tp + len(gold & regex_arts), r_pred + len(regex_arts)
            c_reach, c_rec = c_reach + len(reach), c_rec + len(reach & llm_arts)
            st["판례 참조조문 있는 문서"] += 1
        if doc["kind"] in ("expc", "decc"):
            agree["정규식 조문"] += len(regex_arts)
            agree["그중 LLM도 냄"] += len(regex_arts & llm_arts)
            agree["LLM 명시 경로 조문"] += len(explicit_arts)
            agree["그중 정규식에도 있음"] += len(explicit_arts & regex_arts)
            for x in res:
                for a in x["article_uids"]:
                    if a not in regex_arts:
                        d1["llm_only"].append((doc["uid"], a, x["path"], x["text"], x["span"], ""))
            first_raw = {}
            for ref in regex.refs:  # refs.py는 앞에서 뒤로 읽으며 내고 같은 조문은 한 번만 낸다 → 첫 ref가 처음 낸 자리
                first_raw.setdefault(ref.article_uid, ref.raw)
            item_spans = [(s, e, it["text"], x["article_uids"]) for it, x in zip(items, res)
                          for s, e in find_spans(doc["text"], it["text"], is_short(it["text"]))[0]]
            for a in sorted(regex_arts - llm_arts):
                raw = first_raw.get(a, "")
                spot, n_occ = regex_spot(ctx, doc["text"], a, raw)
                same = same_spot(ctx, item_spans, spot) if spot else f"자리 못 정함(표기 {n_occ}곳, 첫 자리 표시)"
                if spot is None:
                    st["D1 정규식 자리 못 정함"] += 1
                    i = doc["text"].find(raw) if raw else -1
                    spot = (i, i + len(raw)) if i >= 0 else None
                d1["regex_only"].append((doc["uid"], a, "정규식", raw, spot, same))
        resolved_docs.append({"uid": doc["uid"], "kind": doc["kind"], "config": rec.get("config"),
                              "model": ((rec.get("response") or {}).get("body") or {}).get("model"),
                              "items": [{k: v for k, v in x.items() if k not in ("notes",)} for x in res]})
    got = {r.get("custom_id") for r in records}
    st["커버리지: 결과 없는 대상"] = sum(1 for k in expected if k not in got)
    n = len(usages)
    fails = {k: v for k, v in st.items() if k.startswith("실패 ")}
    structural = sum(v for k, v in fails.items() if k.startswith(("실패 finish", "실패 json", "실패 schema")))
    rep = {
        "건수": n,
        "A 실패(원인별)": fails, "A 구조 실패(잘림 · JSON · 스키마)": structural,
        "B": {"분모": b["B 분모"], "일치": b["B 일치"],
              "일치율": round(b["B 일치"] / b["B 분모"], 3) if b["B 분모"] else None,
              **{k: v for k, v in b.items() if not k.startswith("B 분모") and k != "B 일치"}},
        "C(판례 참조조문 대비)": {
            "LLM 정밀도": round(c_tp / c_pred, 3) if c_pred else None,
            "LLM 재현율": round(c_tp / c_gold, 3) if c_gold else None,
            "정규식 정밀도": round(r_tp / r_pred, 3) if r_pred else None,
            "정규식 재현율": round(r_tp / c_gold, 3) if c_gold else None,
            "닿을 수 있는 정답": c_reach, "그중 LLM 회수": c_rec, "정답": c_gold},
        "D1 일치율(기록만)": {**agree,
                           "정규식 조문 중 LLM도 냄": round(agree["그중 LLM도 냄"] / agree["정규식 조문"], 3)
                           if agree["정규식 조문"] else None,
                           "LLM 명시 조문 중 정규식에도": round(agree["그중 정규식에도 있음"] / agree["LLM 명시 경로 조문"], 3)
                           if agree["LLM 명시 경로 조문"] else None},
        "경로별 조문 수": {" · ".join(k): v for k, v in sorted(paths.items())},
        "is_key(경로별 있음 · 뺌)": {" · ".join(k): v for k, v in sorted(keys.items())},
        "기록": {k: v for k, v in sorted(st.items()) if not k.startswith("실패 ")},
        "토큰": {"입력": sum(u.get("prompt_tokens", 0) for u in usages),
                 "캐시 입력": sum((u.get("prompt_tokens_details") or {}).get("cached_tokens", 0) for u in usages),
                 "출력": sum(u.get("completion_tokens", 0) for u in usages),
                 "그중 추론": sum((u.get("completion_tokens_details") or {}).get("reasoning_tokens", 0)
                                  for u in usages)},
        "비용 USD": {"표준": round(cost(usages, PRICE_STD), 4), "배치 환산": round(cost(usages, PRICE_BATCH), 4)},
        "설정 해시": CONFIG_HASH[:12],
    }
    if n_total and n:
        out_tok = sum(u.get("completion_tokens", 0) for u in usages)
        rep["전체 추정 USD(배치, 표본 평균 × 대상 수)"] = round(cost(usages, PRICE_BATCH) / n * n_total, 2)
        rep["전체 추정 USD(배치, 출력 2배 상한)"] = round(
            (cost(usages, PRICE_BATCH) + out_tok * PRICE_BATCH[2] / 1e6) / n * n_total, 2)
    return rep, resolved_docs, d1


def regex_spot(ctx: Ctx, text: str, art: str, raw: str) -> tuple[tuple[int, int] | None, int]:
    """정규식이 그 조문을 처음 낸 자리: 원문 표기(첫 ref의 raw)가 나오는 위치마다 시작 직전까지 · 끝까지 잘라 해석해,
    그 조문이 없다가 생기는 위치. refs.py가 앞에서 뒤로만 읽는다는 가정(테스트로 고정). → (자리 또는 None, 표기 수)."""
    if not raw:
        return None, 0
    hay = text.translate(DOTS).replace("\n", " ")  # refs.norm_text와 같은 길이로 맞춤(가운뎃점 · 필드 사이 줄바꿈)
    occ = [m.start() for m in re.finditer(re.escape(raw), hay)]
    for i in occ:
        if art not in ctx.r.resolve(text[:i]).article_uids and art in ctx.r.resolve(text[:i + len(raw)]).article_uids:
            return (i, i + len(raw)), len(occ)
    return None, len(occ)


def same_spot(ctx: Ctx, item_spans: list, spot: tuple[int, int]) -> str:
    """정규식 자리에 겹친 LLM 항목 중 가장 긴 것 하나와 그 해석(나머지는 "외 n개"). 없으면 "없음"."""
    a, b = spot
    hit = sorted({(s, e, tx, tuple(arts)) for s, e, tx, arts in item_spans if s < b and a < e},
                 key=lambda h: (-(h[1] - h[0]), h[0]))
    if not hit:
        return "없음"
    s, e, tx, arts = hit[0]
    got = ", ".join(f"{ctx.laws.title.get(u.split(':')[1], '')} 제{u.split(':')[2]}조"
                    for u in arts if u.startswith("art:")) or "해석 안 됨"
    return f"{tx} → {got}" + (f" 외 {len(hit) - 1}개" if len(hit) > 1 else "")


def _squash(x: str) -> str:
    return re.sub(r"\s|⏎", "", x)


def anchor_key(before: str, cite: str, after: str) -> str:
    """같은 자리 키: 공백을 지운 앞 30자 + 인용 + 뒤 15자(문서 간 판박이 문장 거르기 · B와 D1 같은 자리 짝짓기)."""
    return _squash(before)[-30:] + "|" + _squash(cite) + "|" + _squash(after)[:15]


def anchor_from_context(c: str, cite: str) -> str | None:
    """확인 파일 문맥 칸에서 자리 키를 다시 만든다. 【】 쌍 가운데 내용이 인용과 같은 것(공백 무시)."""
    want = _squash(cite)
    for m in re.finditer(r"【([^【】]*)】", c):
        if _squash(m.group(1)) == want:
            return anchor_key(c[:m.start()], m.group(1), c[m.end():])
    return None


def review_rows(d1: dict, docs: dict[str, dict], per_side: int = REVIEW_PER_SIDE, spare: int = 5,
                skipped: Counter | None = None, ctx: Ctx | None = None) -> list[dict]:
    """D1 사람 확인 묶음(시드 순서): LLM만 낸 조문 · 정규식만 낸 조문 각 per_side + 예비 spare. 조문(art:)만 — 별표(anx:)는
    REFERENCES 엣지가 되지 않으므로 뺀다(2026-11-27, 판정 전). 보류가 나오면 같은 묶음의 예비를 위에서부터 같은 수만큼
    판정해 수를 채운다(예비가 모자라면 있는 만큼). 문서 간 판박이 문장(같은 자리 키)은 건너뛴다. 문맥은 앞 250자 · 뒤 60자.
    "구분" 칸(정규식만 냄 행만): 제외 위반 · 정규식도 틀림 · 제외 위반+정규식도 틀림 · 빈칸. B와 겹치는지는 표시하지 않는다."""
    ctx = ctx or snapshot_ctx()
    rows = []
    skipped = skipped if skipped is not None else Counter()
    keys: dict[str, str] = {}  # 자리 키 → 그 자리를 먼저 뽑은 묶음. 두 묶음에 걸쳐 같은 자리는 한 번만(LLM만 냄을 먼저 뽑음)
    for side, label, values in (("llm_only", "LLM만 냄", "맞음 · 틀림 · 보류"), ("regex_only", "정규식만 냄", "놓침 · 의도된 제외 · 정규식 틀림 · 보류")):
        cands = sorted({c for c in d1[side] if c[1].startswith("art:")}, key=lambda x: (x[0], x[1], x[3]))
        random.Random(f"{SEED}:extract_d1:{side}").shuffle(cands)
        picked, seen = [], set()
        for c in cands:
            if (c[0], c[1]) in seen:
                continue
            if c[4]:
                t, (a, b) = docs[c[0]]["text"], c[4]
                key = anchor_key(t[max(0, a - 150):a], t[a:b], t[b:b + 60])
                if key in keys:  # 문서 간 판박이 문장, 또는 다른 묶음에서 이미 뽑은 같은 자리
                    skipped[f"{label}: " + ("같은 묶음 판박이" if keys[key] == label else f"{keys[key]}과 같은 자리")] += 1
                    continue
                keys[key] = label
            seen.add((c[0], c[1]))
            picked.append(c)
            if len(picked) == per_side + spare:
                break
        for k, (uid, art, path, text, span, same) in enumerate(picked):
            t = docs[uid]["text"]
            if span is None:  # 자리를 못 찾으면 그 조문 번호가 처음 나온 자리
                num = art.split(":")[2].partition("-")[0]
                m = re.search(rf"(?<!\d)제\s?{num}\s?조", t)
                span = (m.start(), m.end()) if m else (0, 0)
            a, b = span
            law = ctx.laws.title.get(art.split(":")[1], "")
            rows.append({"순서": "본" if k < per_side else "예비", "묶음": label, "고를 값": values, "문서": uid,
                         "조문": art, "법령": law, "경로": path, "인용": t[a:b] if side == "regex_only" else text,
                         "같은 자리 LLM 결과": same, "문맥": context(t, a, b, 250, 60), "판정": "",
                         "표시(본문 · 구법 · 본문+구법)": "",
                         "구분(제외 위반 · 정규식도 틀림 · 제외 위반+정규식도 틀림)": "", "메모": ""})
    return rows


def context(t: str, a: int, b: int, before: int, after: int) -> str:
    """사람 확인용 문맥(한 줄): 필드 사이 줄바꿈은 " ⏎ "로 바꾼다(텍스트 편집기 줄 번호가 행 번호와 어긋나지 않게)."""
    return (t[max(0, a - before):a] + "【" + t[a:b] + "】" + t[b:b + after]).replace("\n", " ⏎ ")


def b_review_rows(resolved_docs: list[dict], docs: dict[str, dict], n: int = 30, spare: int = 10) -> list[dict]:
    """판정 B 사람 확인(90~95%일 때): law 보조로 해석된 자리 n건 + 예비 spare(시드 순서). 보류가 나오면 예비를 위에서부터
    같은 수만큼 판정해 30건을 채운다. 문맥은 앞 150자 · 뒤 40자."""
    ctx = snapshot_ctx()
    cands = []
    for row in resolved_docs:
        for x in row["items"]:
            for s in x.get("assist", []):
                cands.append((row["uid"], tuple(s["span"]), x["text"], x["law"], s["law"], s["article_uids"]))
    cands.sort(key=lambda c: (c[0], c[1]))
    random.Random(f"{SEED}:extract_b").shuffle(cands)
    rows = []
    for k, (uid, (a, b), text, law, dst, arts) in enumerate(cands[:n + spare]):
        t = docs[uid]["text"]
        rows.append({"순서": "본" if k < n else "예비", "고를 값": "맞음 · 틀림 · 보류", "문서": uid, "인용": text,
                     "모델 law": law,
                     "해석 법령": ctx.laws.title.get(dst, dst),
                     "조문": " ".join(x for x in arts if not x.startswith("anx:")),
                     "별표(판정 안 함)": " ".join(x for x in arts if x.startswith("anx:")),
                     "문맥": context(t, a, b, 150, 40), "판정": "", "표시(본문 · 구법 · 본문+구법)": "", "메모": ""})
    return rows


# ---- 명령 -----------------------------------------------------------------

def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def read_review(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_review(path: Path, rows: list[dict]) -> None:
    """사람 확인 파일을 쓴다. 이미 판정이 하나라도 들어 있으면 덮어쓰지 않고 멈춘다(사람 입력 보호)."""
    if path.exists() and any((r.get("판정") or "").strip() for r in read_review(path)):
        raise SystemExit(f"{path.name}에 판정이 들어 있다. 덮어쓰지 않는다(옮기거나 지운 뒤 다시 실행)")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ["묶음"])
        w.writeheader()
        w.writerows(rows)


# ---- 사람 확인 집계(PLAN B1 판정 B · D1, 2026-11-27 판정 기준) ---------------------------

VALUES = {"B": {"맞음", "틀림", "보류"}, "LLM만 냄": {"맞음", "틀림", "보류"},
          "정규식만 냄": {"놓침", "의도된 제외", "정규식 틀림", "보류"}}
MARKS = {"", "본문", "구법", "본문+구법"}
GUBUN_COL = "구분(제외 위반 · 정규식도 틀림 · 제외 위반+정규식도 틀림)"
GUBUN = {"", "제외 위반", "정규식도 틀림", "제외 위반+정규식도 틀림"}
B_N, B_PASS, D1_N, D1_RATE = 30, 24, REVIEW_PER_SIDE, 0.80


def fill(rows: list[dict], n: int, kind: str) -> dict:
    """채움: 본 행의 보류 1건마다 예비를 위에서부터 판정해 채운다(예비도 보류면 다음 예비). 필요한 행이 비었거나
    값이 고를 값 밖이면 멈춘다. → {judged, holds, used_spare, shortfall}."""
    main = [r for r in rows if r["순서"] == "본"]
    spare = [r for r in rows if r["순서"] == "예비"]
    if len(main) != n:
        raise SystemExit(f"{kind}: 본 행이 {len(main)}건(기대 {n})")

    def value(r: dict) -> str:
        v, m = (r.get("판정") or "").strip(), (r.get("표시(본문 · 구법 · 본문+구법)") or "").strip()
        if not v:
            raise SystemExit(f"{kind}: 판정이 빈 행이 있다({r['순서']} {r['문서']})")
        if v not in VALUES[kind]:
            raise SystemExit(f"{kind}: 고를 값 밖 판정 {v!r}({r['문서']})")
        if m not in MARKS:
            raise SystemExit(f"{kind}: 표시 값 {m!r}은 본문 · 구법 · 본문+구법 중 하나여야 한다({r['문서']})")
        if v == "보류" and "구법" not in m:
            raise SystemExit(f"{kind}: 보류는 구법 확인 불가일 때만(표시에 구법 필요, {r['문서']})")
        g = (r.get(GUBUN_COL) or "").strip()
        if g not in GUBUN:
            raise SystemExit(f"{kind}: 구분 값 {g!r}은 제외 위반 · 정규식도 틀림 · 제외 위반+정규식도 틀림 중 하나여야 한다({r['문서']})")
        if g and kind != "정규식만 냄":
            raise SystemExit(f"{kind}: 구분은 정규식만 냄 행에만 쓴다({r['문서']})")
        if g and v != "놓침":
            raise SystemExit(f"{kind}: 구분이 있는 행은 판정이 놓침이어야 한다({r['문서']})")
        return v

    judged, holds, need = [], 0, 0
    for r in main:
        if value(r) == "보류":
            holds, need = holds + 1, need + 1
        else:
            judged.append(r)
    used = 0
    for r in spare:
        if need == 0:
            break
        used += 1
        if value(r) == "보류":
            holds += 1
            continue
        judged.append(r)
        need -= 1
    return {"judged": judged, "holds": holds, "used_spare": used, "shortfall": need}


def tally_b(rows: list[dict]) -> dict:
    f = fill(rows, B_N, "B")
    right = sum(r["판정"].strip() == "맞음" for r in f["judged"])  # 예비가 떨어져 모자란 수는 맞음이 아님으로 셈
    return {"맞음": right, "판정 수": len(f["judged"]), "보류": f["holds"], "판정한 예비": f["used_spare"],
            "모자란 수(맞음 아님으로 셈)": f["shortfall"], "기준": f"{B_PASS}건 이상", "결과": "사용" if right >= B_PASS else "끔"}


def tally_d1(rows: list[dict]) -> dict:
    sides = {k: fill([r for r in rows if r["묶음"] == k], D1_N, k) for k in ("LLM만 냄", "정규식만 냄")}

    def ok(r: dict) -> bool:
        v = r["판정"].strip()
        return v == "맞음" if r["묶음"] == "LLM만 냄" else v in ("의도된 제외", "정규식 틀림")

    def rate(rs: list[dict]) -> tuple[int, int, float | None]:
        good = sum(ok(r) for r in rs)
        return good, len(rs), (round(good / len(rs), 3) if rs else None)

    every = [r for s in sides.values() for r in s["judged"]]
    good, n, r_all = rate(every)
    body_free = [r for r in every if "본문" not in (r.get("표시(본문 · 구법 · 본문+구법)") or "")]
    good2, n2, r_free = rate(body_free)
    return {"LLM 맞음": good, "판정 수": n, "비율": r_all, "기준": f"{D1_RATE:.0%}",
            "결과(B가 사용일 때)": "LLM 결과" if r_all is not None and r_all >= D1_RATE else "정규식 결과",
            "본문 뺀 값(기록)": {"LLM 맞음": good2, "판정 수": n2, "비율": r_free},
            **{f"{k} 보류 · 판정한 예비 · 모자람": (s["holds"], s["used_spare"], s["shortfall"]) for k, s in sides.items()},
            "정규식 틀림": sum(r["판정"].strip() == "정규식 틀림" for r in every),
            "정규식 오류 수(정규식 틀림 + 구분 정규식도 틀림)": sum(
                r["판정"].strip() == "정규식 틀림" or "정규식도 틀림" in (r.get(GUBUN_COL) or "") for r in every),
            "제외 위반": sum("제외 위반" in (r.get(GUBUN_COL) or "") for r in every)}


def dual_agreement(b_rows: list[dict], d_rows: list[dict]) -> dict:
    """B와 D1에서 같은 자리 · 같은 조문을 둘 다 사람이 판정한 행의 일치(기록만, D1 LLM만 냄 행만)."""
    b_by = {}
    for r in b_rows:
        if (r.get("판정") or "").strip():
            k = anchor_from_context(r["문맥"], r["인용"])
            for art in r["조문"].split():
                if k:
                    b_by[(r["문서"], k, art)] = r["판정"].strip()
    n = same = 0
    for r in d_rows:
        if r.get("묶음") != "LLM만 냄" or not (r.get("판정") or "").strip():
            continue
        k = anchor_from_context(r["문맥"], r["인용"])
        v = b_by.get((r["문서"], k, r["조문"])) if k else None
        if v is not None:
            n, same = n + 1, same + (v == r["판정"].strip())
    return {"둘 다 판정한 같은 자리": n, "일치": same, "일치율": round(same / n, 3) if n else None}


EXPORT_COLS = ["순서", "묶음", "문서", "인용", "모델 law", "해석 법령", "법령", "조문", "경로", "같은 자리 LLM 결과",
               "판정", "표시(본문 · 구법 · 본문+구법)", "구분(제외 위반 · 정규식도 틀림 · 제외 위반+정규식도 틀림)", "메모"]


def export_judgments(rows: list[dict], path: Path) -> str:
    """판정 결과를 추적되는 위치에 내보낸다. 문맥(원문) 칸은 빼고 인용 문자열 · 해석 결과 · 판정만(PLAN 79행 규칙)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    cols = [c for c in EXPORT_COLS if c in rows[0]]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    return sha256(path)


def report(records: list[dict], docs: list[dict], expected: set[str], resolved: Path | None,
           review: Path | None) -> dict:
    by = {d["uid"]: d for d in docs}
    rep, res, d1 = check(records, by, expected, n_total=len(docs))
    if resolved:
        write_jsonl(resolved, res)
        rep["해석 결과 파일"] = f"{resolved.name} sha256 {sha256(resolved)[:12]}"
    if review:
        skipped = Counter()
        rows = review_rows(d1, by, skipped=skipped)
        write_review(review, rows)
        rep["D1 확인 파일"] = f"{review.name}: " + ", ".join(
            f"{k} {v}" for k, v in Counter(r["묶음"] for r in rows).items())
        rep["D1 판박이 문장 건너뜀"] = dict(skipped)
        rate = rep["B"]["일치율"]
        if rate is not None and 0.90 <= rate < 0.95:  # PLAN B1 판정 B: 90~95%면 사람 확인 30건
            b_path = review.with_name("b_review.csv")
            rows = b_review_rows(res, by)
            write_review(b_path, rows)
            rep["B 확인 파일"] = f"{b_path.name}: {len(rows)}건"
    return rep


def cmd_inputs(_: argparse.Namespace) -> None:
    docs, stats = load_docs()
    for k, v in sorted(stats.items()):
        print(f"{k}: {v:,}")
    for kind in KINDS:
        print(f"{kind} 입력 글자: {sum(len(d['text']) for d in docs if d['kind'] == kind):,}")
    print(f"대상 합계: {len(docs):,}, 시스템 프롬프트 글자: {len(SYSTEM):,}, 설정 해시 {CONFIG_HASH[:12]}")


def stale(rec: dict | None, doc: dict) -> bool:
    """표본 결과를 다시 불러야 하는지: 없음 · 설정 해시 다름 · 입력 다름 · 200 아님."""
    return (rec is None or rec.get("config") != CONFIG_HASH or rec.get("input") != input_hash(doc)
            or (rec.get("response") or {}).get("status_code") != 200)


def cmd_sample(a: argparse.Namespace) -> None:
    docs, _ = load_docs()
    pick = sample_docs(docs, a.n)
    out = OUT / "sample.jsonl"
    done = {r["custom_id"]: r for r in read_jsonl(out)} if out.exists() else {}
    recs = []
    for i, d in enumerate(pick, 1):
        rec = done.get(d["uid"])
        if stale(rec, d):
            rec = chat(d)
            done[d["uid"]] = rec
            write_jsonl(out, list(done.values()))  # 끊겨도 이어서 돌 수 있게 매번 저장
        recs.append(rec)
        if i % 10 == 0:
            print(f"  {i}/{len(pick)}", flush=True)
    rep = report(recs, docs, {d["uid"] for d in pick}, OUT / "sample_resolved.jsonl", OUT / "d1_review.csv")
    rep["표본 종류별"] = dict(Counter(d["kind"] for d in pick))
    print(json.dumps(rep, ensure_ascii=False, indent=1))


def cmd_review_tally(a: argparse.Namespace) -> None:
    """판정이 채워진 확인 파일 → 판정 B · D1 결과, 그리고 문맥을 뺀 판정 파일을 eval/extract_review/에 내보내고 해시."""
    out = {}
    b_path, d_path = OUT / "b_review.csv", OUT / "d1_review.csv"
    dest = ROOT / "eval" / "extract_review"
    b_rows = read_review(b_path) if b_path.exists() else []
    if b_rows:
        out["B"] = tally_b(b_rows)
        out["B 내보냄"] = f"b_judgments.csv sha256 {export_judgments(b_rows, dest / 'b_judgments.csv')[:12]}"
    else:
        out["B"] = "사람 확인 파일 없음: B 최종 결정은 표본 센티넬 일치율(95% 이상 사용 · 90% 미만 끔)"
    d_rows = read_review(d_path)
    if d1_skipped(out["B"], d_rows):
        out["D1"] = "생략: B 최종 결정이 끔(해석례 · 심판 REFERENCES는 정규식 결과). D1 빈 파일과 해시는 그대로 둔다"
    else:
        out["D1"] = tally_d1(d_rows)
        out["D1 내보냄"] = f"d1_judgments.csv sha256 {export_judgments(d_rows, dest / 'd1_judgments.csv')[:12]}"
        if b_rows:
            out["B · D1 같은 자리 이중 판정(기록만)"] = dual_agreement(b_rows, d_rows)
    print(json.dumps(out, ensure_ascii=False, indent=1))


def d1_skipped(b: dict | str, d_rows: list[dict]) -> bool:
    """B 최종 결정이 끔이고 D1에 판정이 하나도 없으면 D1을 생략한다(PLAN B1: D1은 B가 사용일 때만 결정에 영향)."""
    off = isinstance(b, dict) and b.get("결과") == "끔"
    return off and not any((r.get("판정") or "").strip() for r in d_rows)


def cmd_check(a: argparse.Namespace) -> None:
    docs, _ = load_docs()
    records = [r for f in a.file for r in read_jsonl(Path(f))]  # 배치는 results.jsonl과 errors.jsonl을 함께 준다
    expected = {d["uid"] for d in docs} if a.all else {r.get("custom_id") for r in records}
    rep = report(records, docs, expected, Path(a.resolved) if a.resolved else None,
                 Path(a.review) if a.review else None)
    print(json.dumps(rep, ensure_ascii=False, indent=1))


def cmd_batch_prepare(_: argparse.Namespace) -> None:
    if (OUT / "batch.json").exists():
        raise SystemExit("batch.json이 있다(이미 제출함). 요청 파일을 덮어쓰지 않는다")
    docs, _ = load_docs()
    path = OUT / "batch_input.jsonl"
    write_jsonl(path, [{"custom_id": d["uid"], "method": "POST", "url": "/v1/chat/completions", "body": body(d)}
                       for d in docs])
    print(f"요청 {len(docs):,}건, {path.stat().st_size / 2**20:.1f} MiB, sha256 {sha256(path)[:12]}, "
          f"설정 해시 {CONFIG_HASH[:12]}")


def cmd_batch_submit(_: argparse.Namespace) -> None:
    path = OUT / "batch_input.jsonl"
    meta = OUT / "batch.json"
    if meta.exists():
        raise SystemExit(f"{meta.name}가 이미 있다(같은 배치를 두 번 내지 않음)")
    first = json.loads(path.read_text(encoding="utf-8").split("\n", 1)[0])
    if first["body"]["messages"][0]["content"] != SYSTEM:
        raise SystemExit("요청 파일의 프롬프트가 지금 코드와 다르다. batch-prepare를 다시 실행")
    boundary = uuid.uuid4().hex
    data = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"purpose\"\r\n\r\nbatch\r\n"
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{path.name}\"\r\n"
            f"Content-Type: application/jsonl\r\n\r\n").encode() + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    st, raw = call("POST", "/files", data, f"multipart/form-data; boundary={boundary}", timeout=600)
    if st != 200:
        raise SystemExit(f"업로드 실패 {st}: {raw[:300]!r}")
    fid = json.loads(raw)["id"]
    st, raw = call("POST", "/batches", json.dumps({"input_file_id": fid, "endpoint": "/v1/chat/completions",
                                                   "completion_window": "24h"}).encode())
    if st != 200:
        raise SystemExit(f"배치 생성 실패 {st}: {raw[:300]!r}")
    b = json.loads(raw)
    meta.write_text(json.dumps({"batch_id": b["id"], "input_file_id": fid, "input_sha256": sha256(path),
                                "config": CONFIG_HASH, "model": MODEL,
                                "submitted": time.strftime("%Y-%m-%d %H:%M:%S")}, ensure_ascii=False, indent=1),
                    encoding="utf-8")
    print(f"배치 제출: 상태 {b['status']}, 요청 파일 sha256 {sha256(path)[:12]}")


def _batch() -> dict:
    meta = json.loads((OUT / "batch.json").read_text(encoding="utf-8"))
    st, raw = call("GET", f"/batches/{meta['batch_id']}")
    if st != 200:
        raise SystemExit(f"조회 실패 {st}: {raw[:300]!r}")
    return json.loads(raw)


def cmd_batch_status(_: argparse.Namespace) -> None:
    b = _batch()
    print(f"상태 {b['status']}, 건수 {b.get('request_counts')}")


def cmd_batch_fetch(_: argparse.Namespace) -> None:
    b = _batch()
    if b["status"] != "completed":
        raise SystemExit(f"아직 {b['status']}")
    meta = json.loads((OUT / "batch.json").read_text(encoding="utf-8"))
    for key, name in (("output_file_id", "results.jsonl"), ("error_file_id", "errors.jsonl")):
        if b.get(key):
            st, raw = call("GET", f"/files/{b[key]}/content", timeout=600)
            if st != 200:
                raise SystemExit(f"{name} 받기 실패 {st}")
            rows = [json.loads(x) for x in raw.decode("utf-8").splitlines() if x.strip()]
            for r in rows:
                r["config"] = meta["config"]
            write_jsonl(OUT / name, rows)
            print(f"{name}: {len(rows):,}줄, sha256 {sha256(OUT / name)[:12]}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("inputs").set_defaults(f=cmd_inputs)
    sub.add_parser("review-tally", help="판정 B · D1 집계 + 문맥 뺀 판정 파일 내보내기").set_defaults(f=cmd_review_tally)
    s = sub.add_parser("sample")
    s.add_argument("--n", type=int, default=100)
    s.set_defaults(f=cmd_sample)
    c = sub.add_parser("check")
    c.add_argument("file", nargs="+")
    c.add_argument("--all", action="store_true", help="대상 전체를 기대(배치 결과 커버리지 검사)")
    c.add_argument("--resolved")
    c.add_argument("--review")
    c.set_defaults(f=cmd_check)
    for name, f in (("batch-prepare", cmd_batch_prepare), ("batch-submit", cmd_batch_submit),
                    ("batch-status", cmd_batch_status), ("batch-fetch", cmd_batch_fetch)):
        sub.add_parser(name).set_defaults(f=f)
    a = ap.parse_args()
    a.f(a)


if __name__ == "__main__":
    main()
