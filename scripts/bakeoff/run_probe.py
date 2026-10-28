"""llama-server(OpenAI 호환)에 측정 문항을 보내고 채점한다. 관문 2 · 3용.

설정은 플랜 4-6과 같다: thinking 끔(chat_template_kwargs), temperature 0. 서버는 컨텍스트 8k · KV q8로 띄운다.
Cypher 문법 유효성은 로컬 Neo4j(빈 그래프)에 EXPLAIN을 보내 확인한다. 실행하는 동안 nvidia-smi로 VRAM 최대값을 잰다.

    python scripts/bakeoff/run_probe.py --tag qwen3-4b-q4km --probe all
"""

import argparse
import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path

import jsonschema
import requests
from dotenv import load_dotenv
from neo4j import GraphDatabase
from neo4j.exceptions import CypherSyntaxError, Neo4jError

ROOT = Path(__file__).resolve().parents[2]
PROBES = ROOT / "data" / "bakeoff" / "probes"
RESULTS = ROOT / "data" / "bakeoff" / "results"

KMMLU_SYS = "다음 객관식 문제의 정답 번호(1, 2, 3, 4 중 하나)만 숫자로 답하세요."
T2C_SYS = "Generate a Cypher query that answers the question using the given graph schema. Output only the Cypher query, without explanation or code fences."
JSON_SYS = "주어진 법령 조문을 읽고, 요청한 JSON 스키마에 맞는 JSON 객체 하나만 출력하세요. 설명이나 코드 블록 표시는 쓰지 마세요."


class VramMonitor(threading.Thread):
    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.peak = 0
        self.stop = threading.Event()

    def run(self) -> None:
        while not self.stop.is_set():
            out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                                 capture_output=True, text=True).stdout.strip()
            if out:
                self.peak = max(self.peak, int(out.splitlines()[0]))
            time.sleep(1.0)


def chat(server: str, system: str, user: str, max_tokens: int) -> dict:
    body = {"messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0, "max_tokens": max_tokens,
            "chat_template_kwargs": {"enable_thinking": False}}
    t0 = time.time()
    r = requests.post(f"{server}/v1/chat/completions", json=body, timeout=600)
    r.raise_for_status()
    j = r.json()
    text = j["choices"][0]["message"].get("content") or ""
    timings = j.get("timings", {})
    return {"text": text, "sec": time.time() - t0, "gen_tps": timings.get("predicted_per_second"),
            "prompt_tps": timings.get("prompt_per_second"), "think_leak": "<think>" in text}


def strip_fences(s: str) -> str:
    s = s.strip()
    s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
    return re.sub(r"\s*```$", "", s).strip()


def norm_cypher(s: str) -> str:
    s = strip_fences(s).replace("\\n", " ").rstrip(";")
    return " ".join(s.split()).lower()


def run_kmmlu(server: str, rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        q = r["question"] + "\n" + "\n".join(f"{i}. {c}" for i, c in enumerate(r["choices"], 1)) + "\n정답:"
        res = chat(server, KMMLU_SYS, q, 16)
        m = re.search(r"[1-4]", res["text"])
        out.append({"id": r["id"], **res, "pred": int(m.group()) if m else None, "correct": bool(m) and int(m.group()) == r["answer"]})
    return out


def run_t2c(server: str, rows: list[dict], driver) -> list[dict]:
    out = []
    with driver.session() as session:
        for r in rows:
            res = chat(server, T2C_SYS, f"Schema:\n{r['schema']}\n\nQuestion: {r['question']}", 512)
            query = strip_fences(res["text"]).replace("\\n", "\n")
            try:
                session.run("EXPLAIN " + query).consume()
                valid = True
            except (CypherSyntaxError, Neo4jError):
                valid = False
            out.append({"id": r["id"], **res, "valid": valid, "exact": norm_cypher(res["text"]) == norm_cypher(r["cypher"])})
    return out


def run_json(server: str, rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        user = f"법령: {r['law']}\n조문: {r['text']}\n\nJSON 스키마:\n{json.dumps(r['schema'], ensure_ascii=False)}"
        res = chat(server, JSON_SYS, user, 512)
        try:
            jsonschema.validate(json.loads(strip_fences(res["text"])), r["schema"])
            ok = True
        except Exception:
            ok = False
        out.append({"id": r["id"], **res, "schema_ok": ok})
    return out


def load(name: str) -> list[dict]:
    return [json.loads(l) for l in (PROBES / f"{name}.jsonl").read_text(encoding="utf-8").splitlines()]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True, help="결과 파일 이름 (예: qwen3-4b-q4km)")
    ap.add_argument("--server", default="http://127.0.0.1:8082")
    ap.add_argument("--probe", default="all", choices=["all", "kmmlu", "t2c", "json"])
    args = ap.parse_args()

    load_dotenv(ROOT / ".env")
    RESULTS.mkdir(parents=True, exist_ok=True)
    mon = VramMonitor()
    mon.start()
    summary = {"tag": args.tag}
    names = ["kmmlu", "t2c", "json"] if args.probe == "all" else [args.probe]
    driver = GraphDatabase.driver(os.environ["NEO4J_URI"], auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"]))
    for name in names:
        rows = load(name)
        res = {"kmmlu": lambda: run_kmmlu(args.server, rows),
               "t2c": lambda: run_t2c(args.server, rows, driver),
               "json": lambda: run_json(args.server, rows)}[name]()
        (RESULTS / f"{args.tag}.{name}.jsonl").write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in res), encoding="utf-8")
        key = {"kmmlu": "correct", "t2c": "exact", "json": "schema_ok"}[name]
        summary[name] = {"n": len(res), "score": sum(x[key] for x in res) / len(res),
                         "think_leak": sum(x["think_leak"] for x in res),
                         "gen_tps_median": sorted(x["gen_tps"] or 0 for x in res)[len(res) // 2]}
        if name == "t2c":
            summary[name]["valid"] = sum(x["valid"] for x in res) / len(res)
        print(name, summary[name])
    driver.close()
    mon.stop.set()
    summary["vram_peak_mib"] = mon.peak
    (RESULTS / f"{args.tag}.summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print("vram_peak_mib", mon.peak)


if __name__ == "__main__":
    main()
