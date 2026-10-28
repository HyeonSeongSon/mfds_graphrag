"""관문 1-2 ~ 1-4: llama-server에서 LoRA 로드 · 요청별 강도 · --parallel 2 · flash attention + KV q8을 확인한다.

서버는 미리 이렇게 띄운다(포트 8081):
    llama-server -m <base.gguf> --lora <lora.gguf> --lora-init-without-apply -c 8192 -fa on -ctk q8_0 -ctv q8_0 -np 2 --port 8081

    python scripts/bakeoff/gate1_serve_check.py --family qwen3 --log <서버 로그 파일>
"""

import argparse
import concurrent.futures as cf
import json
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
MARK = "MATCH (n:GateCheck) RETURN n.ok AS gate1"
PROMPT = "질문 99: 이 그래프에서 점검 노드를 찾아줘"


def ask(server: str, scale: float) -> str:
    body = {"messages": [{"role": "user", "content": PROMPT}], "temperature": 0, "max_tokens": 40,
            "chat_template_kwargs": {"enable_thinking": False}, "lora": [{"id": 0, "scale": scale}]}
    r = requests.post(f"{server}/v1/chat/completions", json=body, timeout=120)
    r.raise_for_status()
    return r.json()["choices"][0]["message"].get("content") or ""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--family", required=True)
    ap.add_argument("--server", default="http://127.0.0.1:8081")
    ap.add_argument("--log", required=True)
    args = ap.parse_args()

    adapters = requests.get(f"{args.server}/lora-adapters", timeout=30).json()
    off, on = ask(args.server, 0.0), ask(args.server, 1.0)
    with cf.ThreadPoolExecutor(2) as ex:
        par_on, par_off = ex.submit(ask, args.server, 1.0), ex.submit(ask, args.server, 0.0)
        par_on, par_off = par_on.result(), par_off.result()
    log = Path(args.log).read_text(encoding="utf-8", errors="replace")
    report = {
        "family": args.family,
        "lora_loaded": bool(adapters),
        "adapters": adapters,
        "scale0_output": off[:120],
        "scale1_output": on[:120],
        "per_request_scale_works": MARK in on and MARK not in off,
        "parallel_mixed_ok": par_on == on and par_off == off,
        "flash_attn_on": "flash_attn" in log and ("flash_attn = 1" in log or "flash_attn    = enabled" in log or "Flash Attention was auto" not in log),
        "kv_q8": "q8_0" in log,
    }
    out = ROOT / "models" / "gate1" / args.family / "serve_report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
