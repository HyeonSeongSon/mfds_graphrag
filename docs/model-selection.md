# 모델 선정 조사 (2026-10-27)

목표: 같은 계열의 "작은 모델(파인튜닝 대상, 약 4B)"과 "큰 기준 모델(약 8B)" 짝. 조건은 상업 이용 가능 라이선스, 한국어, Cypher · JSON 출력, llama.cpp · Unsloth 지원, RTX 2060 6GB(Turing, fp16)에서 Q4 · 컨텍스트 8k.

## 후보 정리

| 계열 | 작은 / 큰 | 라이선스 | 판단 |
|---|---|---|---|
| Qwen3 (2025-04) | 4B / 8B | Apache 2.0 | 유력. 공식 8B Q4_K_M 5.03GB, imatrix 없음. 표준 어텐션이라 도구 지원이 성숙 |
| Qwen3.5 (2026-02) | 4B / 9B | Apache 2.0 | 유력. 새 세대 · 다국어 점수 높음. 단 멀티모달 + Gated DeltaNet 혼합 구조, 공식 GGUF 없음 |
| Gemma 4 (2026-03) | E2B · E4B / 26B MoE · 31B | Apache 2.0 | 제외. 8B급 짝이 없음(E4B는 유효 4.5B · 전체 약 8B, 위는 26B MoE) |
| Mi:dm 2.0 (KT) | Mini 2.3B / Base 11.5B | MIT | 제외. 한국어는 강하지만(Base KMMLU 57.3) 11.5B는 6GB에 안 들어가고, Mini는 추론이 약함(Reasoning 평균 40.9) |
| Kanana-2 (카카오, 2026-07) | 1.3B / 3B | Kanana License | 제외. 8B급 없음, SI 납품 · 기기 탑재에 별도 상업 라이선스가 필요하다는 분석이 있어 온프레미스 납품 가정과 충돌 |
| EXAONE | 1.2B · 2.4B / 7.8B · 32B | 비상업 | 제외(이전 결정 유지) |

## 공개 점수

**한국어:** 최신 소형 모델(Qwen3.5 · Gemma 4)의 KMMLU 등 한국어 점수는 공개 자료에서 찾지 못했다. 호랑이 리더보드 4(2026-04-29 갱신)는 표가 W&B 내장 차트라 읽히지 않았다. 찾은 것은 Mi:dm 2.0 논문의 비교표다.

| 모델 | KMMLU | HAE-RAE | Ko-IFEval | KoBEST |
|---|---|---|---|---|
| Qwen3-4B | 50.6 | 50.6 | 75.9 | 91.5 |
| Qwen3-14B | 55.4 | 68.4 | 83.6 | 93.9 |
| EXAONE-3.5-7.8B | 52.6 | 72.9 | 69.1 | 92.0 |
| Mi:dm 2.0 Base | 57.3 | 81.5 | 82.0 | 95.2 |
| Mi:dm 2.0 Mini | 45.1 | 70.8 | 73.3 | 80.5 |

**Qwen3.5 모델 카드(thinking 켠 상태의 점수):** 4B / 9B 순으로 MMMLU 76.1 / 81.2, MMLU-ProX 71.5 / 76.3, IFEval 89.8 / 91.5, BFCL-V4 50.3 / 66.1, LiveCodeBench v6 55.8 / 65.6. Qwen3 4B · 8B와 직접 비교한 표는 카드에 없다. 우리는 thinking을 끄므로 이 점수가 그대로 옮겨진다고 볼 수 없다.

**Text2Cypher(제3자 한 명의 자체 측정, 미검증):** neo4j text2cypher-2024v1 test 4,833행 실행 일치율. Qwen3.5-4B LoRA 53.18%(원 라벨), Qwen3-4B LoRA 55.68%(정제 라벨). 라벨 조건이 달라 직접 비교는 안 되지만, 파인튜닝 뒤에는 두 4B가 비슷한 수준이라는 근거는 된다. Qwen3.5 LoRA는 Gated DeltaNet 투영층까지 대상에 넣어야 했다고 적혀 있다.

## 도구 지원

| 항목 | Qwen3 | Qwen3.5 |
|---|---|---|
| llama.cpp | 성숙 | b7973(PR #19435)부터 텍스트 전용 지원. GGUF 아키텍처 태그 `qwen35`, 예전 `convert_lora_to_gguf.py`로 만든 LoRA는 태그가 안 맞아 로드 거부 사례 |
| 8B급 GGUF | 공식 Q4_K_M, imatrix 없음 | 공식 없음. unsloth 9B Q4_K_M 5.68GB, imatrix 사용. 직접 양자화하려면 9B 원본 약 18GB + fp16 GGUF 약 18GB 임시 공간 |
| Turing에서 linear attention CUDA 커널 · 런타임 LoRA · flash attention | 문제 없을 것 | 확인 안 됨 |

## 결론과 제안

공개 자료만으로는 Qwen3와 Qwen3.5 중 어느 쪽이 이 과제(한국어 질문 → Cypher, thinking 끔, Q4)에 나은지 판단할 수 없다. 플랜 규칙("새 세대가 있으면 같은 세대 짝")은 Qwen3.5를 가리키지만, 도구 호환과 공정 비교 조건이 확인되지 않았다.

제안: 디스크 확보 뒤 남은 D1 점검(Unsloth · llama.cpp)을 두 계열로 함께 돌리고, 같은 장비에서 짧은 비교를 해서 미리 정한 규칙으로 고른다(아래). 직접 잰 근거라 포트폴리오에서 "왜 이 모델인가"에 답하기도 좋다.

### 장비 위 비교 (안)

- 대상: Qwen3-4B · 8B(공식 Q4_K_M) vs Qwen3.5-4B · 9B(Q4_K_M), 모두 thinking 끔 · 컨텍스트 8k · KV q8
- 관문 1, 도구 호환(통과/실패): 같은 구조의 작은 모델(Qwen3-0.6B / Qwen3.5-0.8B)로 Unsloth QLoRA 1스텝 → LoRA GGUF 변환 → `llama-server --lora` 로드 · 요청별 강도 지정 · flash attention + KV q8
- 관문 2, 짧은 품질 측정(외부 공개 데이터, 우리 평가셋과 무관): 한국어 객관식 300문항(KMMLU-Redux 일부, 라이선스 확인 후), Cypher 200문항(neo4j text2cypher-2025v1 test 일부, 스키마 제공 · 정규화 후 일치율과 문법 유효율), JSON 스키마 준수 50문항(문법 제약 없이)
- 관문 3, 자원: 4B 초당 토큰 · VRAM, 8B/9B의 GPU 층 수와 속도
- 결정 규칙: Qwen3.5는 관문 1을 모두 통과하고, 관문 2의 세 항목 모두에서 Qwen3보다 3%p 넘게 낮지 않을 때만 고른다. 아니면 Qwen3. 9B는 imatrix 차이를 한계로 적거나, 디스크가 허락하면 직접 양자화한다
- 비용: GGUF 4개 약 16GB, 반나절 + 밤 실행

## 최종 선정 (2026-10-27)

**Qwen3-4B(파인튜닝 대상) · Qwen3-8B(기준선, 공식 Q4_K_M).** 장비 위 비교의 관문 1에서 Qwen3.5가 fp16 QLoRA를 통과하지 못했다(Unsloth가 qwen3_5는 fp16 불가로 fp32 전환, 30스텝 약 14배 느림). 상세와 기록 파일은 `docs/bakeoff-rule.md` "결과". Turing에는 bf16이 없으므로 이 장비에서는 대안이 없다.

## Sources

- Qwen3.5-4B 모델 카드: https://huggingface.co/Qwen/Qwen3.5-4B
- Mi:dm 2.0 논문: https://arxiv.org/html/2601.09066v1
- Text2Cypher LoRA 모델 카드(제3자): https://huggingface.co/BeastxD/text2cypher_lora_v8_raw
- Neo4j Text2Cypher 벤치마크: https://neo4j.com/blog/developer/benchmarking-neo4j-text2cypher-dataset/
- llama.cpp b7973 릴리스(Qwen3.5 지원): https://newreleases.io/project/github/ggml-org/llama.cpp/release/b7973
- Gemma 4 vs Qwen 3.5 비교: https://codersera.com/blog/gemma-4-vs-qwen-3-5-comparison-2026/amp/
- 10B 이하 소형 모델 정리: https://labellerr.com/blog/best-small-language-models-under-10b-parameters/
- Kanana-2 공개: https://www.kakaocorp.com/page/detail/12102?lang=ENG , 라이선스 분석: https://braindetox.kr/en/posts/kakao_kanana2_open_models_2026.html
- Mi:dm 2.0 공개 기사: https://www.koreatimes.co.kr/amp/business/tech-science/20250703/kt-to-release-homegrown-ai-model-midm-20-as-open-source
- 호랑이 리더보드 4: https://wandb.ai/horangi/horangi4/reports/Horangi-W-B-Korean-LLM-Leaderboard-4--VmlldzoxNTAyNjAwMA
- KMMLU 소개: https://www.eleuther.ai/papers-blog/kmmlu-measuring-massive-multitask-language-understanding-in-korean
