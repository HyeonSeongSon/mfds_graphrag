"""임베딩 텍스트 → dense 벡터 · 외부 정확 색인(PLAN 1주차 D5 7단계).

2026-11-26 사용자 결정: 외부 정확 색인 · GPU fp32(28차), 모델은 bge-m3 → `dragonkue/snowflake-arctic-embed-l-v2.0-ko`(29차,
검색 결과를 보기 전 공개 한국어 리더보드의 법령 데이터셋 LawIRKo 기준).
`mfds-train` 환경(torch · transformers)에서 돌린다. Neo4j는 읽지 않는다. 건수 · 해시만 출력한다.

    conda run -n mfds-train python scripts/embed_index.py [--name eval] [--limit N]

- 모델: `MODEL` 리비전 고정(`REVISION`), HF 캐시에서만 읽는다(내려받지 않음). XLM-RoBERTa 구조, 자체 코드 없음.
  dense = 마지막 층 CLS 벡터 · L2 정규화(모델의 sentence-transformers 설정과 같음), fp32, `max_length` 8192 토큰
  (넘으면 잘림, 수를 보고).
- 접두어: 문서(근거 노드 텍스트)에는 붙이지 않는다. 질의에는 `QUERY_PREFIX`를 붙인다(`query_text`, 모델 설정의
  `prompts.query`). 문서 텍스트는 PREREG 4-5 규칙 그대로다.
- 캐시: `data/cache/embed/<모델 이름>-<리비전 12자>/`의 `keys.json`(텍스트 sha256) · `vectors.npy`. 같은 텍스트는 다시
  계산하지 않는다(PLAN "uid + 텍스트 해시로 캐시", 재적재 · 4주차 전체 그래프에서 재사용). 모델마다 폴더가 따로라
  bge-m3 캐시(`bge-m3-5617a9f61b02/`)는 그대로 남는다. 2,000개마다 캐시에 저장하므로 중간에 멈추거나 메모리 부족으로
  끊겨도 다시 실행하면 이어서 계산한다.
- 색인: `<이름>_index.npy`(텍스트 파일 순서의 벡터, float32) · `<이름>_index.json`(모델 · 리비전 · 설정 · 텍스트 파일 해시 ·
  id · uid · 라벨 목록). 검색은 코사인(내적) 전수 비교라 근사가 없다. 누수 검사는 `check_leak.py --vector`.
- `--limit N`: 앞 N개 텍스트만(소량 시험). 색인 파일 이름에 `_limitN`을 붙인다.
"""

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "cache" / "embed"
MODEL = "dragonkue/snowflake-arctic-embed-l-v2.0-ko"
REVISION = "55ec6e9358a56d56af759bc8372e970caf8c305f"
WEIGHTS_SHA256 = "0b874517f0fd02dd9510fa2733aacaad1def6086387c88d1a21f4041351e15b0"  # model.safetensors
QUERY_PREFIX = "query: "  # 질의에만(문서에는 없음)
MAX_LENGTH = 8192
TOKENS_PER_BATCH = 8192  # 패딩 포함 토큰 수 상한(긴 텍스트는 한 개씩)
SAVE_EVERY = 2000  # 이만큼 계산할 때마다 캐시에 저장(중간에 멈춰도 이어서 계산)
DIM = 1024


def query_text(q: str) -> str:
    """질의 임베딩 입력. 문서 쪽 텍스트에는 쓰지 않는다."""
    return QUERY_PREFIX + q


def cache_dir(model: str = MODEL, revision: str = REVISION) -> Path:
    return OUT / f"{model.split('/')[-1]}-{revision[:12]}"


def sha_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def batches(lengths: list[int], budget: int) -> list[list[int]]:
    """길이 순 인덱스를 (최대 길이 × 개수) ≤ budget 묶음으로. 한 개가 budget을 넘어도 혼자 한 묶음."""
    order = sorted(range(len(lengths)), key=lambda i: lengths[i])
    out, cur, mx = [], [], 0
    for i in order:
        m = max(mx, lengths[i])
        if cur and m * (len(cur) + 1) > budget:
            out.append(cur)
            cur, m = [], lengths[i]
        cur.append(i)
        mx = m
    if cur:
        out.append(cur)
    return out


def load_cache(d: Path) -> tuple[list[str], np.ndarray]:
    if not (d / "keys.json").exists():
        return [], np.zeros((0, DIM), dtype=np.float32)
    keys = json.loads((d / "keys.json").read_text(encoding="utf-8"))
    vec = np.load(d / "vectors.npy")
    if len(keys) != len(vec):
        raise SystemExit(f"캐시 키 {len(keys)} ≠ 벡터 {len(vec)}")
    return keys, vec


def save_cache(d: Path, keys: list[str], vec: np.ndarray) -> None:
    d.mkdir(parents=True, exist_ok=True)
    np.save(d / "vectors.tmp.npy", vec)
    (d / "keys.tmp.json").write_text(json.dumps(keys), encoding="utf-8")
    (d / "vectors.tmp.npy").replace(d / "vectors.npy")
    (d / "keys.tmp.json").replace(d / "keys.json")


def encode(texts: list[str], device: str, log, on_part) -> int:
    """`SAVE_EVERY`개씩 계산해 `on_part(시작 위치, 벡터)`로 넘긴다. 잘린 텍스트 수를 돌려준다."""
    import torch
    from huggingface_hub import snapshot_download
    from transformers import AutoModel, AutoTokenizer

    path = Path(snapshot_download(MODEL, revision=REVISION, local_files_only=True))
    if sha_file(path / "model.safetensors") != WEIGHTS_SHA256:
        raise SystemExit("모델 가중치 해시가 고정값과 다름")
    tok = AutoTokenizer.from_pretrained(path)
    model = AutoModel.from_pretrained(path, dtype=torch.float32, attn_implementation="sdpa").to(device).eval()
    truncated, t0 = 0, time.perf_counter()
    with torch.inference_mode():
        for start in range(0, len(texts), SAVE_EVERY):
            part = texts[start:start + SAVE_EVERY]
            ids = [tok(t, truncation=False, add_special_tokens=True)["input_ids"] for t in part]
            truncated += sum(len(x) > MAX_LENGTH for x in ids)
            out = np.zeros((len(part), DIM), dtype=np.float32)
            for b in batches([min(len(x), MAX_LENGTH) for x in ids], TOKENS_PER_BATCH):
                enc = tok([part[i] for i in b], padding=True, truncation=True, max_length=MAX_LENGTH,
                          return_tensors="pt").to(device)
                cls = model(**enc).last_hidden_state[:, 0]
                out[b] = torch.nn.functional.normalize(cls, dim=-1).float().cpu().numpy()
            on_part(start, out)  # 중간 저장: 멈춰도 다시 실행하면 저장한 데까지 캐시에서 쓴다
            log(f"  텍스트 {start + len(part):,}/{len(texts):,} 저장, {time.perf_counter() - t0:.0f}초")
    return truncated


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="eval")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--device", default="cuda")
    args = ap.parse_args()
    log = lambda m: print(m, flush=True)  # noqa: E731
    tpath = OUT / f"{args.name}_texts.jsonl"
    rows = [json.loads(line) for line in tpath.read_text(encoding="utf-8").splitlines() if line.strip()]
    if args.limit:
        rows = rows[:args.limit]
    cdir = cache_dir()
    keys, vec = load_cache(cdir)
    have = {k: i for i, k in enumerate(keys)}
    todo = {}
    for r in rows:
        if r["sha"] not in have and r["sha"] not in todo:
            todo[r["sha"]] = r["text"]
    log(f"# {MODEL} · {tpath.relative_to(ROOT).as_posix()} 텍스트 {len(rows):,}, 캐시 {len(keys):,}, 새로 계산 {len(todo):,}")
    truncated = 0
    if todo:
        shas = list(todo)
        state = {"keys": keys, "vec": vec}

        def on_part(start: int, new: np.ndarray) -> None:
            state["keys"] = state["keys"] + shas[start:start + len(new)]
            state["vec"] = np.concatenate([state["vec"], new]) if len(state["vec"]) else new
            save_cache(cdir, state["keys"], state["vec"])

        truncated = encode(list(todo.values()), args.device, log, on_part)
        keys, vec = state["keys"], state["vec"]
        have = {k: i for i, k in enumerate(keys)}
    index = vec[[have[r["sha"]] for r in rows]].astype(np.float32)
    suffix = f"_limit{args.limit}" if args.limit else ""
    ipath, jpath = OUT / f"{args.name}_index{suffix}.npy", OUT / f"{args.name}_index{suffix}.json"
    np.save(ipath, index)
    norms = np.linalg.norm(index, axis=1)
    meta = {"model": MODEL, "revision": REVISION, "weights_sha256": WEIGHTS_SHA256, "dtype": "float32",
            "device": args.device, "max_length": MAX_LENGTH, "pooling": "cls+l2", "doc_prefix": "",
            "query_prefix": QUERY_PREFIX, "texts": tpath.name, "texts_sha256": sha_file(tpath), "limit": args.limit,
            "n": len(rows), "truncated_new": truncated, "vectors_sha256": sha_file(ipath),
            "ids": [r["id"] for r in rows], "uids": [r["uid"] for r in rows], "labels": [r["label"] for r in rows]}
    jpath.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    log(f"- 잘림(새로 계산한 것 중 {MAX_LENGTH} 토큰 초과) {truncated}, 벡터 노름 {norms.min():.4f} ~ {norms.max():.4f}")
    log(f"## 색인 {ipath.relative_to(ROOT).as_posix()} {index.shape}, sha256 {meta['vectors_sha256']}")


if __name__ == "__main__":
    main()
