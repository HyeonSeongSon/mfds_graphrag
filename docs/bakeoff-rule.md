# 모델 선정 비교: 결정 규칙 (결과 전에 고정, 2026-10-27)

사용자 승인(2026-10-27): Qwen3와 Qwen3.5를 아래 관문 1~3과 결정 규칙대로 비교해 선정한다. 이 문서와 아래 해시는 어떤 측정 결과도 보기 전에 썼다. 바꿔야 하면 이 문서 맨 아래에 변경 사유를 추가로 적는다.

## 대상

| 계열 | 작은 모델 | 큰 모델 | 도구 점검용 |
|---|---|---|---|
| A. Qwen3 | Qwen3-4B Q4_K_M | Qwen3-8B Q4_K_M(공식, imatrix 없음) | Qwen3-0.6B |
| B. Qwen3.5 | Qwen3.5-4B Q4_K_M | Qwen3.5-9B Q4_K_M | Qwen3.5-0.8B |

4B Q4_K_M은 두 계열 모두 공식 · 커뮤니티 파일을 쓰되 imatrix 여부를 기록한다(본 실험의 기준 Q4는 2주차에 직접 만든다). 모든 구성은 thinking 끔, temperature 0, 컨텍스트 8192, KV 캐시 q8_0, flash attention 켬. llama.cpp 빌드는 하나로 고정해 번호를 기록한다.

## 관문 1: 도구 호환 (통과/실패)

계열마다 도구 점검용 작은 모델로 확인한다.
1. Unsloth QLoRA 1스텝(fp16, `mfds-train` env)이 오류 없이 끝나고 LoRA가 저장된다
2. LoRA를 GGUF로 변환할 수 있고, `llama-server --lora`로 로드된다
3. 요청마다 어댑터 강도(0 또는 1)를 지정할 수 있고, `--parallel 2`에서 두 요청의 설정이 달라도 된다
4. flash attention + KV q8_0으로 서버가 뜨고 응답한다

하나라도 실패하면 그 계열은 관문 1 실패다.

## 관문 2: 짧은 품질 측정 (4B끼리 비교)

`scripts/bakeoff/run_probe.py`로 같은 문항을 돌린다. 문항은 우리 평가셋과 무관한 외부 공개 데이터와 고정 자료다.

| 항목 | 문항 | 점수 |
|---|---|---|
| 한국어 | HAERAE-HUB/KMMLU test 300(45개 과목 층화) | 정답률 |
| Cypher | neo4j/text2cypher-2025v1 test 200(스키마 6천 자 이하) | 정규화 후 일치율(주), Neo4j EXPLAIN 문법 유효율(참고) |
| JSON | 고정 자료 조문 50(스키마 2종) | 문법 제약 없이 스키마 준수율 |

8B · 9B도 같은 문항을 돌려 참고로 기록한다(결정에는 4B만 쓴다).

## 관문 3: 자원 (기록)

4B의 생성 초당 토큰 중앙값 · VRAM 최대값, 8B · 9B가 6GB에 들어가는 GPU 층 수와 속도.

## 결정 규칙

- **Qwen3.5를 고르는 조건:** 관문 1을 모두 통과하고, 관문 2의 세 항목(한국어 정답률, Cypher 일치율, JSON 준수율) 모두에서 Qwen3-4B보다 3%p 넘게 낮지 않다
- 그 밖에는 **Qwen3**를 고른다
- Qwen3.5를 고르고 9B 파일이 imatrix를 썼다면, 디스크가 허락하면 9B를 직접 Q4_K_M(imatrix 없음)으로 만들고, 아니면 양자화 방식 차이를 핵심 성과 1의 한계로 적는다
- 해석 메모: 문항 수가 작아(300 · 200 · 50) 3%p는 잡음 범위 안일 수 있다. 이 비교는 "Qwen3.5가 분명히 나쁘지 않은지"를 거르는 용도이고, 최종 성능 주장에는 쓰지 않는다

## 고정 해시

| 파일 | sha256 |
|---|---|
| `data/bakeoff/probes/kmmlu.jsonl` | 248625c865133df899dfb0443e9386333eaeba9029d24032d34a324a971a4a20 |
| `data/bakeoff/probes/t2c.jsonl` | 881471d7cf3d8ee270e9f1fb023db5cede9c08767e2b1eb50f6bbf351225ccce |
| `data/bakeoff/probes/json.jsonl` | c881d964c9cfab87a3f712ee8f86dc4b19a7e6d310ccae6065bc9811e1b30636 |
| `scripts/bakeoff/prepare_probes.py` | 60673a0300b114bf00ef874590f5bfc7d3c54e9ccaa7d3e4f3ca4ad8155eb534 |
| `scripts/bakeoff/run_probe.py` | 7f31015ffb1b05771eedff70cc459f501a96862db7f4cdc6989c56f076fa4503 |

## 변경 기록

- 2026-10-27(측정 전): `run_probe.py` 기본 포트를 8080 → 8082로 변경(8080은 WSL `wslrelay`가 점유해 404). 채점 로직은 그대로. 새 sha256 `9ed975b5be975ba942178f0d22132d51449146f088894d5d00f134cf71c62cad`

## 결과 (2026-10-27)

환경: Unsloth 2026.10.9 · transformers 5.17.0 · trl 1.15.0 · torch 2.14.1+cu126 · Triton 3.8.0(네이티브 Windows), llama.cpp b11913(커밋 7aca0d2ea, CUDA 12.4 빌드, ARCHS에 750 포함).

### 관문 1

| 항목 | Qwen3 (Qwen3-0.6B) | Qwen3.5 (Qwen3.5-0.8B) |
|---|---|---|
| 1. Unsloth QLoRA fp16 | 통과. 30스텝 17.2초, loss 4.26 → 0.11, VRAM 711MiB | **실패.** Unsloth가 "float16 for qwen3_5 won't work"로 fp32 학습으로 전환. `causal_conv1d` 없음(기준 구현 사용). 30스텝 247.3초(약 14배), VRAM 1,430MiB |
| 2. LoRA GGUF 변환 · 로드 | 통과(LoRA 20.2MB) | 통과(LoRA 21.6MB) |
| 3. 요청별 강도 · `--parallel 2` 혼합 | 통과(강도 0 일반 답변, 1 학습 패턴, 동시 요청 결과 동일) | 통과 |
| 4. flash attention + KV q8_0 | 통과(`flash_attn = enabled`, K · V q8_0, 4096칸 28층 476MiB) | 통과(6층만 KV, 51MiB) |

기록 파일: `models/gate1/<계열>/train_report.json` · `serve_report.json` · `server_verbose.log`

### 판정

**Qwen3.5는 관문 1의 1번(fp16 학습)을 통과하지 못해 결정 규칙에 따라 Qwen3(Qwen3-4B · Qwen3-8B)를 고른다.** Turing은 bf16이 없어 fp32 외의 대안이 없고, 4B를 fp32로 학습하면 6GB에 들어갈지 불확실하며 학습 시간이 플랜 가정(1에폭 1~2시간)의 열 배를 넘는다. 관문 2는 결정에 영향을 줄 수 없어 Qwen3.5는 돌리지 않았다. 참고: bf16을 지원하는 GPU(Ampere 이상)라면 Qwen3.5는 서빙 쪽 점검을 모두 통과했으므로 다시 검토할 만하다(한계 · 다시 한다면에 기록).

Qwen3 4B · 8B의 관문 2 · 3 수치는 기준 기록으로 아래에 추가한다.

### 관문 2 · 3: Qwen3 기준 기록 (결정과 무관)

설정: 공식 GGUF Q4_K_M(둘 다 imatrix 없음), 컨텍스트 8192, KV q8_0, flash attention 켬, thinking 끔, 포트 8082.

| 항목 | Qwen3-4B Q4_K_M | Qwen3-8B Q4_K_M |
|---|---|---|
| GPU 층 | 37/37(전부) | **26/37**(`llama-fit-params` 결과, CPU 1.55GB) |
| 모델 버퍼(GPU) | 2,376MiB | 3,236MiB |
| KV 캐시 | 612MiB | 612MiB |
| VRAM 최대(화면 사용 포함) | 5,076MiB | 5,851MiB |
| 생성 속도 중앙값 | **68.3 tok/s** | **13.9 tok/s** |
| 한국어 KMMLU 300 | 40.7% | 44.3% |
| Cypher 정규화 일치 / 문법 유효 | 3.5% / 76.5% | 2.5% / 85.5% |
| JSON 스키마 준수 50 | 100% | 100% |
| thinking 노출 | 0 | 0 |

메모
- Cypher 정규화 일치는 문자열 완전 일치라 매우 엄격하다(공개 벤치마크에서 GPT-4o도 약 30%). 문법 유효율과 함께 본다
- 4B 생성 68 tok/s면 플랜의 "4B 답변 문항당 10초" 가정은 여유가 있다. 8B는 13.9 tok/s로 "문항당 25초" 가정과 비슷하다
- 4B KMMLU 40.7%는 Mi:dm 2.0 논문의 Qwen3-4B 50.6%보다 낮다. 문항 표본 · 프롬프트(정답 번호만, thinking 끔) · Q4 양자화 차이로 보이며, 직접 비교하지 않는다
- 원자료: `data/bakeoff/results/qwen3-4b-q4km.*`, `qwen3-8b-q4km-ngl26.*`(git 제외), 서버 로그 `models/bakeoff/*.server.log`
