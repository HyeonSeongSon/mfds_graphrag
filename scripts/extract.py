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
from mfds.refs import LawDict, Resolver, article_set, norm_text  # noqa: E402

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
        for name, lid in self.laws.names.items():
            self.family[self.laws.root(lid)].add(name.replace(" ", ""))

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


def sentinel(ctx: Ctx, prefix: str, token: str) -> tuple[str | None, str]:
    """앞부분 + 토큰 + 제99999조를 조문 검사 없이 해석 → (법령ID, 실패 사유)."""
    res = ctx.r0.resolve(f"{prefix}{token}{SENTINEL}" if not token else f"{prefix}{token} {SENTINEL}")
    for ref in reversed(res.refs):
        if ref.article_uid.endswith(":99999"):
            return ref.law_id, ""
    return None, (res.failures[-1].reason if res.failures else "none")


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


def resolve_item(ctx: Ctx, text: str, item: dict, use_law: bool = True) -> dict:
    """항목 하나 → {path, refs(uid 목록), article_uids, notes(Counter), span(첫 자리), sentinel_laws}."""
    t = item["text"]
    notes: Counter = Counter()
    out = {"text": t, "law": item["law"], "is_key": item["is_key"], "path": "", "refs": [], "article_uids": [],
           "notes": notes, "span": None, "sentinel_laws": [], "assist": []}

    def add(res) -> None:
        for ref in res.refs:
            if ref.uid not in out["refs"]:
                out["refs"].append(ref.uid)
            if ref.article_uid not in out["article_uids"]:
                out["article_uids"].append(ref.article_uid)

    if SAME.match(t):
        out["path"] = "같은 조(풀지 않음)"
        return out
    rel, tok, bare = REL.match(t), TOKEN.match(t), BARE.match(t)
    short = bool(tok or bare)
    spans, recovered = find_spans(text, t, short)
    if recovered:
        notes["공백 복구"] += 1
    if not spans:
        out["path"] = "입력에 없음(버림)"
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
        law = None
        if rel:
            out["path"] = "같은 법"
            law, why = sentinel(ctx, text[seg:a], head.group(0))
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
            law, why = sentinel(ctx, text[seg:a], "")
            if law is None and why != "no_context":
                notes[f"센티넬 {why}(버림)"] += 1
                continue
        else:
            out["path"] = "법·영·규칙"
        if law is not None:
            add(ctx.r.resolve(rest, context_law=law))
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
        add(res)
        notes["law 보조로 해석" if res.refs else "law 보조: 해석 실패"] += 1
        if res.refs:
            out["assist"].append({"span": (a, b), "law": dst, "article_uids": res.article_uids})
    return out


def resolve_doc(ctx: Ctx, text: str, items: list[dict], use_law: bool = True) -> list[dict]:
    return [resolve_item(ctx, text, it, use_law) for it in items]


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
                        d1["llm_only"].append((doc["uid"], a, x["path"], x["text"], x["span"]))
            raw_of = {}
            for ref in regex.refs:
                raw_of.setdefault(ref.article_uid, ref.raw)
            for a in sorted(regex_arts - llm_arts):
                raw = raw_of.get(a, "")
                i = doc["text"].find(raw) if raw else -1
                d1["regex_only"].append((doc["uid"], a, "정규식", raw, (i, i + len(raw)) if i >= 0 else None))
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


def review_rows(d1: dict, docs: dict[str, dict], per_side: int = REVIEW_PER_SIDE) -> list[dict]:
    """D1 사람 확인 묶음(시드 순서): LLM만 낸 조문 · 정규식만 낸 조문 각 per_side. 문맥은 앞뒤 80자."""
    ctx = snapshot_ctx()
    rows = []
    for side, label in (("llm_only", "LLM만 냄"), ("regex_only", "정규식만 냄")):
        cands = sorted(set(d1[side]), key=lambda x: (x[0], x[1], x[3]))
        random.Random(f"{SEED}:extract_d1:{side}").shuffle(cands)
        picked, seen = [], set()
        for c in cands:
            if (c[0], c[1]) in seen:
                continue
            seen.add((c[0], c[1]))
            picked.append(c)
            if len(picked) == per_side:
                break
        for uid, art, path, text, span in picked:
            t = docs[uid]["text"]
            if span is None:  # 자리를 못 찾으면 그 조문 번호가 처음 나온 자리
                num = art.split(":")[2].partition("-")[0]
                m = re.search(rf"(?<!\d)제\s?{num}\s?조", t)
                span = (m.start(), m.end()) if m else (0, 0)
            a, b = span
            law = ctx.laws.title.get(art.split(":")[1], "")
            rows.append({"묶음": label, "문서": uid, "조문": art, "법령": law, "경로": path, "인용": text,
                         "문맥": t[max(0, a - 80):a] + "【" + t[a:b] + "】" + t[b:b + 80],
                         "판정(맞음 · 틀림 · 의도된 제외)": "", "메모": ""})
    return rows


def b_review_rows(resolved_docs: list[dict], docs: dict[str, dict], n: int = 30) -> list[dict]:
    """판정 B 사람 확인(90~95%일 때): law 보조로 해석된 자리 n건(시드 순서). 문맥은 앞 150자 · 뒤 40자."""
    ctx = snapshot_ctx()
    cands = []
    for row in resolved_docs:
        for x in row["items"]:
            for s in x.get("assist", []):
                cands.append((row["uid"], tuple(s["span"]), x["text"], x["law"], s["law"], s["article_uids"]))
    cands.sort(key=lambda c: (c[0], c[1]))
    random.Random(f"{SEED}:extract_b").shuffle(cands)
    rows = []
    for uid, (a, b), text, law, dst, arts in cands[:n]:
        t = docs[uid]["text"]
        rows.append({"문서": uid, "인용": text, "모델 law": law, "해석 법령": ctx.laws.title.get(dst, dst),
                     "조문": " ".join(arts), "문맥": t[max(0, a - 150):a] + "【" + t[a:b] + "】" + t[b:b + 40],
                     "판정(맞음 · 틀림)": "", "메모": ""})
    return rows


# ---- 명령 -----------------------------------------------------------------

def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def write_review(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ["묶음"])
        w.writeheader()
        w.writerows(rows)


def report(records: list[dict], docs: list[dict], expected: set[str], resolved: Path | None,
           review: Path | None) -> dict:
    by = {d["uid"]: d for d in docs}
    rep, res, d1 = check(records, by, expected, n_total=len(docs))
    if resolved:
        write_jsonl(resolved, res)
        rep["해석 결과 파일"] = f"{resolved.name} sha256 {sha256(resolved)[:12]}"
    if review:
        rows = review_rows(d1, by)
        write_review(review, rows)
        rep["D1 확인 파일"] = f"{review.name}: " + ", ".join(
            f"{k} {v}" for k, v in Counter(r["묶음"] for r in rows).items())
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
