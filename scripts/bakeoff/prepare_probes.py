"""모델 선정 비교용 측정 문항을 만든다(우리 평가셋과 무관한 외부 공개 데이터 + 고정 자료).

- kmmlu.jsonl : HAERAE-HUB/KMMLU test, 45개 과목에서 층화 추출 300문항 (CC BY-ND, 원문 그대로 사용)
- t2c.jsonl   : neo4j/text2cypher-2025v1 test, 스키마가 있고 6천 자 이하인 행에서 200문항 (Apache 2.0)
- json.jsonl  : 고정 자료 조문(식품위생법 · 약사법)에서 50문항, JSON 스키마 2종을 번갈아 요구

문항 파일은 data/bakeoff/probes/ (git 제외). 해시는 docs/bakeoff-rule.md에 기록한다.

    python scripts/bakeoff/prepare_probes.py
"""

import hashlib
import io
import json
import random
from pathlib import Path

import pandas as pd
import requests
from lxml import etree

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "bakeoff" / "probes"
FIX = ROOT / "tests" / "fixtures"
SEED = 20261027
HF = "https://huggingface.co"

JSON_SCHEMAS = [
    {
        "type": "object",
        "properties": {
            "article_number": {"type": "integer"},
            "title": {"type": "string"},
            "paragraph_count": {"type": "integer"},
        },
        "required": ["article_number", "title", "paragraph_count"],
        "additionalProperties": False,
    },
    {
        "type": "object",
        "properties": {
            "cites_other_article": {"type": "boolean"},
            "delegates_to_decree": {"type": "boolean"},
            "keywords": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
        },
        "required": ["cites_other_article", "delegates_to_decree", "keywords"],
        "additionalProperties": False,
    },
]


def write_jsonl(path: Path, rows: list[dict]) -> str:
    data = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows).encode("utf-8")
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def kmmlu(rng: random.Random) -> list[dict]:
    meta = requests.get(f"{HF}/api/datasets/HAERAE-HUB/KMMLU", timeout=60).json()
    files = sorted(s["rfilename"] for s in meta["siblings"] if s["rfilename"].endswith("-test.csv"))
    rows = []
    for f in files:
        df = pd.read_csv(io.BytesIO(requests.get(f"{HF}/datasets/HAERAE-HUB/KMMLU/resolve/main/{f}", timeout=120).content))
        subject = f.split("/")[-1].removesuffix("-test.csv")
        for i in rng.sample(range(len(df)), 7):
            r = df.iloc[i]
            rows.append({"id": f"{subject}:{i}", "subject": subject, "question": str(r["question"]),
                         "choices": [str(r[c]) for c in "ABCD"], "answer": int(r["answer"])})
    rng.shuffle(rows)
    return sorted(rows[:300], key=lambda x: x["id"])


def t2c(rng: random.Random) -> list[dict]:
    raw = requests.get(f"{HF}/datasets/neo4j/text2cypher-2025v1/resolve/main/data/test.parquet", timeout=120).content
    df = pd.read_parquet(io.BytesIO(raw))
    ok = df[df["schema"].fillna("").str.len().between(1, 6000)].reset_index()
    idx = sorted(rng.sample(range(len(ok)), 200))
    return [{"id": f"t2c:{int(ok.loc[i, 'index'])}", "question": ok.loc[i, "question"],
             "schema": ok.loc[i, "schema"], "cypher": ok.loc[i, "cypher"]} for i in idx]


def json_probe(rng: random.Random) -> list[dict]:
    arts = []
    for fn in ["law_food.xml", "law_yaksa.xml"]:
        root = etree.parse(str(FIX / fn)).getroot()
        for u in root.iter("조문단위"):
            if u.findtext("조문여부") == "조문" and len((u.findtext("조문내용") or "")) > 40:
                text = etree.tostring(u, method="text", encoding="unicode")
                arts.append({"law": root.findtext(".//법령명_한글") or fn, "key": u.get("조문키"), "text": " ".join(text.split())[:2500]})
    picked = rng.sample(arts, 50)
    return [{"id": f"json:{p['key'] or i}", "law": p["law"], "text": p["text"], "schema": JSON_SCHEMAS[i % 2]}
            for i, p in enumerate(picked)]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    hashes = {}
    for name, fn in [("kmmlu", kmmlu), ("t2c", t2c), ("json", json_probe)]:
        rows = fn(random.Random(f"{SEED}:{name}"))
        hashes[name] = write_jsonl(OUT / f"{name}.jsonl", rows)
        print(f"{name}: {len(rows)} rows sha256={hashes[name]}")
    (OUT / "hashes.json").write_text(json.dumps(hashes, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
