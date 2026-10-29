// 숨은 조건 확인용(PREREG 4-2): 정답 템플릿에서 구법 참조 제외를 뺀 판. 질문만 보고 짤 수 있는 쿼리에 해당한다.
// 슬롯은 이 판과 정답 템플릿의 결과가 같을 때만 쓴다.
MATCH (c:Case {uid: $case})-[:REFERENCES {source: 'field'}]->(x)
OPTIONAL MATCH (up:Article)-[:HAS_PARAGRAPH]->(x)
WITH coalesce(up, x) AS art
MATCH (art:Article)-[:DELEGATES_TO]->(t:Article)<-[:HAS_ARTICLE]-(:Law {kind: '대통령령'})
RETURN DISTINCT t.uid AS uid
LIMIT 200
