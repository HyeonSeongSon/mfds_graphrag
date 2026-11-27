# 그래프 스키마 v0.1

단일 출처는 `schema/schema.yaml`, 제약 · 인덱스는 `schema/constraints.cypher`다. 이 문서는 사람이 읽는 설명과 결정 근거를 둔다. 1주차 D4에 `schema.yaml`과 함께 잠근다(문서 자체는 잠금 대상이 아님).

## 원칙

- **모든 노드는 `uid`를 갖는다.** 사람이 읽을 수 있고, 모델이 답변 인용에 그대로 옮겨 적을 수 있는 형태다(`art:001783:44`, `dur:pair:acemetacin:methotrexate`)
- **법령은 법령ID로 묶는다.** 법령일련번호(MST)는 개정마다 바뀌므로 `Law.mst` 속성으로만 둔다. 이번 4주는 현행 스냅숏 하나라 `effective_to`는 모두 null이다
- **근거 단위는 조문이 기본, 원천에 항이 있으면 항.** 채점은 조문 단위로 맞춘다(PREREG 4-5)
- **개인정보는 그래프에 없다.** 처분 노드에 업소명 · 주소 · 전화 · 대표자 · 인허가번호를 넣지 않는다

## 법령 구조

| 노드 | uid | 예 |
|---|---|---|
| Law | `law:{법령ID}` | `law:001783` (약사법) |
| Article | `art:{법령ID}:{조}` · 가지조문 `{조}-{가지}` | `art:001783:24-3` (제24조의3) |
| Paragraph | `par:{법령ID}:{조}:{항}` | `par:001783:44:1` |
| Item | `item:{법령ID}:{조}:{항}:{호}` | `item:001783:44:1:3`, 항번호 없이 호만 있으면 항 자리에 0 |
| Annex | `anx:{법령ID}:{별표}` · 행정규칙 별표 `anx:adm{행정규칙ID}:{별표}` | `anx:011794:1` |

- 조문여부가 `전문`인 단위(장 · 절 제목)는 노드로 만들지 않는다. 약사법은 246단위 중 조문 219개
- 목(가 · 나 · 다)은 노드로 만들지 않고 호 본문에 이어 붙인다
- 위임: 3단비교(`thdCmp`, knd=2)의 `법률조문 → 시행령조문 · 시행규칙조문`을 `DELEGATES_TO`로 잇는다. 대상 법령이 식약처 87개가 아니면(예: 보건복지부령 「약사법 시행규칙」) 버린다
- 상하위: 법령체계도(`lsStmd`)의 `법률 → 시행령 → 시행규칙`을 `SUBORDINATE_OF`(아래 → 위)로 잇는다

## 행정규칙

`AdminRule`은 `adm:{행정규칙ID}`, 조문은 `admart:{행정규칙ID}:{조}`. 고시 본문은 항 구조가 없어 조문 단위까지만 나눈다. 근거 법령 조항은 `BASED_ON_LAW`(refs.py로 해석).

## 판례 · 해석례 · 행정심판 · 처분

| 노드 | uid | 정답과의 관계 |
|---|---|---|
| Case | `case:{판례일련번호}` | 참조조문 필드(`ref_text`)는 T3 정답 출처. 추출 입력에서 항상 뺀다 |
| Interpretation | `expc:{법령해석례일련번호}` | |
| AdminAppeal | `decc:{행정심판재결례일련번호}` | |
| Disposition | `disp:food:{DSPSDTLS_SEQ}` | 근거 조항 필드(`law_ref_text`)는 T1 정답 출처 |

`REFERENCES.source`는 `field`(참조조문 필드), `extracted`(필드 없는 문서만, 2주차 API 추출), `regex`(판정 C가 정규식으로 나올 때만, 필드 없는 판례의 추출 입력 본문 + `refs.py`) 중 하나다.

**구법 참조:** `refs.py`가 `historical=True`로 표시한 참조(`구 X법(… 개정되기 전의 것) 제N조`)는 `REFERENCES` · `CITES`에 `historical: true`로 적재하고, 그래프 확장 · 정답 Cypher 템플릿 · T3 정답에서 뺀다. 전부개정으로 번호가 바뀌었으면 현행 같은 번호 조문이 엉뚱한 조문이기 때문이다.

**추출 엣지의 구법 표시 (AMENDMENTS 4 · 5):**
- **대상:** `REFERENCES {source: 'extracted'}` · `INTERPRETS` · `APPLIES`. 아래 넷 중 하나에 해당하면 `historical: true`로 적재하고 그래프 확장에서 뺀다. 조문 ID는 바꾸지 않는다.
- **용어:** "법령 가족 이름"은 법령명과 법령약칭명을, 법률 · 시행령 · 시행규칙을 한 묶음으로 본 것이다(`scripts/extract.py` `Ctx.family_rx`). "구" 표기는 "구" 뒤에 공백과 「가 하나씩 올 수 있는 것이다(`OLD_MARK`).
  1. `refs.py`가 `historical=True`로 표시
  2. 인용 text가 "구" 표기로 시작하고 바로 뒤에 그 조문의 법령 가족 이름이 오는데 `refs.py`가 표시하지 않음(붙여 쓴 "구「식품위생법」" · "구식품위생법")
  3. `historical_inferred`: 같은 법 · 맨 제N조 · 법 · 영 · 규칙 인용의 자리 앞에서 마지막으로 나온 그 조문의 법령 가족 이름 앞에 "구" 표기가 붙음. 또는 law 보조 경로에서 모델 law가 "구" 뒤에 공백을 두고 시작
  4. 문서 날짜(판례 선고일자 · 해석례 해석일자 · 심판 의결일자)가 그 조문 법령의 전부개정 공포일보다 앞섬. 공포일 표에 없는 법령과 날짜가 빈 문서에는 쓰지 않는다

| 법령 | 전부개정 공포일 | 결정문 기록 |
|---|---|---|
| 식품위생법 | 2009-02-06 | 20회 |
| 식품위생법 시행령 | 2009-08-06 | 5회 |
| 식품위생법 시행규칙 | 2009-08-12 | 6회 |
| 약사법 | 2007-04-11 | 13회 |
| 약사법 시행규칙 | 2008-01-15 | 6회 (식약처 87개 법령 사전에 없어 적용되는 조문 없음, AMENDMENTS 5 (a)) |
| 의료기기법 | 2011-04-07 | 8회 |
| 화장품법 | 2011-08-04 | 1회 |

- **regex 엣지 (AMENDMENTS 5 (b), 판정 C가 정규식일 때만):** 판례 `REFERENCES {source: 'regex'}`는 근거 (1) · (4)만 쓴다.
  - (3)은 law 보조 경로의 모델 law와 인용 자리를 쓰는 LLM 전용 근거다.
  - (2)를 재지 않는 이유: 이 스냅숏의 필드 없는 판례 441건에서 refs.py가 낸 조문 참조 uid 916개 중, 인용 자리에 "구" 표기가 있는데 표시가 없는 것은 1개뿐이고, 그 1개는 refs.py 오독이다(case:221915). 전체 코퍼스 수치가 아니다.
  - 해석례 · 심판은 D1이 LLM으로 정해져 regex 엣지가 없다.
- **엣지 합치기 (AMENDMENTS 5 (c)):**
  - extracted · INTERPRETS · APPLIES는 인용 항목마다 조문별로 근거를 매긴다(항목 안에서는 `extract.py`가 낸 값). 같은 문서에서 같은 대상으로 가는 엣지로 합칠 때 모두 참일 때만 참이다(`mfds/load/graph.py`).
  - regex는 `refs.py`가 같은 uid의 첫 인용만 남기므로 첫 인용의 표시를 따른다. 서로 다른 uid가 같은 조 · 항으로 올라갈 때만 같은 규칙으로 합친다.
- **바뀌지 않는 것:** `source: 'field'` 엣지, T3 정답, 정답 Cypher 템플릿(`m2_case_ref_delegated`는 `source: 'field'`만 읽음)은 위 첫 문단 규칙(`refs.py` 표시만) 그대로다.
- **적재 보고:** 근거별 건수를 겹침까지 포함해 기록한다. 셈 방법과 알려진 한계는 `docs/amend-draft-historical-20261127.md`에 있다.

## 성분 · DUR

### 성분 (`mfds/ingredients.py`)

고시 별표의 성분명은 영문이고 표기가 흔들린다(대소문자, 괄호 앞 공백, 제형 · 분류번호 · 동의어 괄호). 규칙:

| 표기 | 처리 | 예 |
|---|---|---|
| 대소문자 · 공백 · 따옴표 | 소문자, 공백 하나, `’`→`'` | `Acemetacin` = `acemetacin` |
| `(분류번호 : 421)` | `class_codes`로 빼냄 | `methotrexate (분류번호 : 421)` → `ing:methotrexate` |
| 끝 한글 제형 괄호 | DurRule `form` 조건 | `Rilpivirine(경구제)` → `ing:rilpivirine`, form 경구제 |
| 끝 영문 동의어 괄호 | `aliases` | `rifampicin(rifampin)` → `ing:rifampicin` |
| `A(EP)= B(USP)` | 앞은 이름, 뒤는 별칭 | `ing:norethisterone` |
| 동위원소 괄호 | 이름에 남김 | `ing:florbetaben(18f)` |
| `~ 함유제제` | `contains`, 이름에서 뺌 | `ing:cobicistat` |
| 복합제 `A + B` | 복합제 노드 하나 + `HAS_COMPONENT` | `ing:atorvastatin + amlodipine` |
| 다른 염 | **다른 노드**. 염 제거형 `base`는 검색 매칭에만 | `ing:morphine` ≠ `ing:morphine sulfate` (base morphine) |
| `<삭제>` | 행을 만들지 않음 | |

고정 자료 실측: 별표 1~3의 서로 다른 표기 1,939개 → `<삭제>` 2개를 빼고 uid 약 1,510개(대소문자 · 공백 · 괄호 변형이 합쳐짐), 복합제 203개.

### DurRule

| 별표 | uid | 조건 속성 |
|---|---|---|
| 1 병용금기 | `dur:pair:{A}:{B}` (두 이름 정렬) | form, condition_note(비고) |
| 2 특정연령대 금기 | `dur:age:{성분}:{연령기준}` | form(해당 제형) |
| 3 임부금기 | `dur:preg:{성분}` | grade(등급), condition_note |

같은 uid가 여러 행에서 나오면(제형 · 비고가 다른 행) 행 내용 sha256 순서로 `#2` · `#3`을 붙인다. 행이 늘거나 줄면 번호가 밀릴 수 있다(스냅숏 하나라 이번에는 문제없음, 한계로 기록).

T2 정답 단위는 개별 DurRule이다. T2 정답 Cypher는 두 성분 uid · 판정 · DurRule uid 집합을 함께 돌려준다.

## Text2Cypher 조회 속성

평가 문항의 슬롯 라벨은 스키마 요약에 있는 속성으로 찾을 수 있어야 한다(`schema.yaml`의 `text2cypher_lookup`). 정답 템플릿은 uid로 찾지만 모델은 이 속성으로 찾아 같은 결과를 내면 정답이다.

| 라벨 | 조회 속성 |
|---|---|
| 성분 `methotrexate` | `Ingredient.name`(정규화한 이름) · `aliases` · `base` |
| 조문 「약사법 제23조의2」 | `Law.name` + `Article.number` 23 · `branch` 2 (가지조문이 아니면 branch 0) |
| 판례 「대법원 2025도16747 판결」 | `Case.court` · `case_no` |
| 처분 | `Disposition.violation_text`(앞의 `(YYYYMMDD)` 날짜만 뗀 원문) · `industry` · `type` |

## 범위 밖

Term 노드, 법령 연혁(시점 질의), 첨부파일에만 본문이 있는 고시(식품공전 등), 의약품 처분 · 품목 매핑.
