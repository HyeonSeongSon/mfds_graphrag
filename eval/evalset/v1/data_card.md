# 평가셋 v1 데이터 카드

생성: `eval/build_evalset.py` (잠긴 코드, SEED 20261030). 아래는 생성 보고 그대로입니다.

# D4 생성 보고 (data/raw/20261105, SEED 20261030)
1. 유형별 생성 건수(dev · test):
   - T1: dev 30 · test 200
   - T3: dev 30 · test 200
   - T7: dev 29 · test 130
   - m1_delegated_articles: dev 8 · test 60
   - m2_case_ref_delegated: dev 4 · test 30
   - m2_disposition_basis_delegated: dev 4 · test 30
   - m3_pair_and_age: dev 4 · test 25
   - m3_pair_and_pregnancy: dev 4 · test 25
   - m4_citing_articles: dev 6 · test 36
   - t2_pair_contraindication: dev 30 · test 200
   - t6_delegated_count: dev 2 · test 12
   - t6_dispositions_by_article: dev 3 · test 13
   - t6_dispositions_by_industry_type: dev 3 · test 13
   - t6_pair_rules_by_ingredient: dev 2 · test 12
   - 거절 other_ministry_law: dev 7 · test 50 (배분 [7, 50])
   - 거절 missing_article: dev 7 · test 50 (배분 [7, 50])
   - 거절 out_of_scope: dev 6 · test 50 (배분 [6, 50])
   - 합계: test 986 · dev 159, 거절 test 150 · dev 20, M4 dev 예비 6
2. T1 상한 20 적용 후 채울 수 있는 문항: 261 (필요 230)
2. T3 상한 20 적용 후 채울 수 있는 문항: 354 (필요 230)
2. T7 상한 20 적용 후 채울 수 있는 문항: 160 (필요 230)
3. T1 dev-test 충돌로 버린 dev 2, 이웃 포함 50건 초과 후보 16/873
3. T3 dev-test 충돌로 버린 dev 0, 이웃 포함 50건 초과 후보 0/425
3. T7 dev-test 충돌로 버린 dev 5, 이웃 포함 50건 초과 후보 1/285
4. T1 대체 규칙: 적용 안 함
4. T3 대체 규칙: 적용 안 함
4. T7 대체 규칙: 상한 유지 + test 130로 축소(T7 규칙)
5. T1 제외 목록 564건 / 전체 5,319 = 10.6% (기준 30%), 남는 처분 4,755
5. T3 제외 목록 237건 / 전체 1,401 = 16.9% (기준 30%), 남는 처분 1,164
5. T7 제외 목록 256건 / 전체 544 = 47.1% (T7은 30% 기준을 적용하지 않음), 남는 처분 288
6. T1 test 문항 중 대표 조문의 남은 원천 0건 9 · 1~4건 31 / 200
6. T3 test 문항 중 대표 조문의 남은 원천 0건 20 · 1~4건 34 / 200
6. T7 test 문항 중 대표 조문의 남은 원천 0건 36 · 1~4건 39 / 130
7. T1 문항(dev + test 230) 중 원문에 정답 조문이 읽힘 15, 가린 문항 40, 가린 뒤 남은 조문 참조 0(확인 규칙 통과), 정답 법령명 남음 25 · 같은 계열 28
7. T3 문항(dev + test 230) 중 원문에 정답 조문이 읽힘 121, 가린 문항 173, 가린 뒤 남은 조문 참조 0(확인 규칙 통과), 정답 법령명 남음 178 · 같은 계열 187
7. T7 문항(dev + test 159) 중 원문에 정답 조문이 읽힘 28, 가린 문항 45, 가린 뒤 남은 조문 참조 0(확인 규칙 통과), 정답 법령명 남음 39 · 같은 계열 40
7-1. T1 정답에 현행 삭제 조문: test 일부 0 · 전부 0(주 평가 제외), dev 일부 0 · 전부 0
7-1. T3 정답에 현행 삭제 조문: test 일부 30 · 전부 17(주 평가 제외), dev 일부 1 · 전부 0
7-1. T7 정답에 현행 삭제 조문: test 일부 0 · 전부 9(주 평가 제외), dev 일부 0 · 전부 1
8. m2_disposition 슬롯 후보에서 조문 표기로 빠진 수(정답 있는 것): 51
9. T2 음성 표집에서 뺀 쌍: {'관계 쌍(같은 염 제거형 · 복합제 구성)': 2}
10. 숨은 조건으로 버린 T4 슬롯: m2_case(구법 참조 있음, 정답 있는 것) 12; M4 풀까지 걸러진 조문 {'후보 0': 6, '숨은 조건 불일치(범위 밖 · 구법 인용)': 13}; 정답 200행 초과 · 빈 정답으로 빠진 슬롯 {'m1_delegated_articles': 0, 'm2_case_ref_delegated': 456, 'm2_disposition_basis_delegated': 14, 'm3_pair_and_pregnancy': 65, 'm3_pair_and_age': 375, 't6_dispositions_by_article': 0, 't6_dispositions_by_industry_type': 0, 't6_pair_rules_by_ingredient': 0, 't6_delegated_count': 0}
11. 템플릿 실행 확인: `scripts/check_templates.py`로 따로 확인(결과: `eval/PREREG.md` "D4 dry-run 결과" 절 · `docs/week1-notes.md` 복구 절, 스냅숏 data/raw/20261105에서 532 슬롯 불일치 0)
12. held-out 단위 선택: ['m3_pair_and_pregnancy', 'm3_pair_and_age', 'm4_citing_articles'] (intents.yaml과 같음)
- T1 test 대표 조문 법령의 조문 수 중앙값: 143.0
- T3 test 대표 조문 법령의 조문 수 중앙값: 147.0
- T7 test 대표 조문 법령의 조문 수 중앙값: 219.0
- heldout_law(의료기기법 계열) 문항: 10
- T1 근거 조항 refs 중 구법 표시: 0
- T7 근거법령 분할: 처분 544, 위반 조문 없음(처분 조문만) 34, 해석 실패 44, 위반 줄에서 뺀 처분 조문 20, 가림 표시 든 처분 34, 항 · 호 뒤 붙은 조 표기 위반 쪽 0(원천 제외) · 처분 쪽 1(정답 무관)
- T7 위반 정답 중 제목이 처분 · 제재 성격으로 보이는 조문(품질 검사, 걸리면 검토해 처분 조문 목록 반영 여부 결정): 0
- T7 제외 처분 256의 정답 법령 계열(처분당 한 칸, 계열 둘 이상은 '여러 계열'): 약사법 165(64%), 화장품법 80(31%), 마약류 관리에 관한 법률 5(2%), 정답 빔 4(2%), 인체조직안전 및 관리 등에 관한 법률 2(1%)
- T7 남음 처분 288의 정답 법령 계열(처분당 한 칸, 계열 둘 이상은 '여러 계열'): 화장품법 136(47%), 약사법 120(42%), 정답 빔 30(10%), 마약류 관리에 관한 법률 2(1%)
- T1 같은 split 안에서 근접 중복 · 같은 업소로 이어진 문항 쌍: test 4 · dev 0
- T3 같은 split 안에서 근접 중복 · 같은 업소로 이어진 문항 쌍: test 0 · dev 0
- T7 같은 split 안에서 근접 중복 · 같은 업소로 이어진 문항 쌍: test 13 · dev 0
