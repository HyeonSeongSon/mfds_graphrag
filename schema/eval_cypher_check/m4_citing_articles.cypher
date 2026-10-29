// 숨은 조건 확인용(PREREG 4-2): 정답 템플릿에서 구법 참조 제외 · 범위 제한(대상 법령 · 하위 법령 · 고시)을 뺀 판.
// 슬롯은 이 판과 정답 템플릿의 결과가 같을 때만 쓴다.
MATCH (t:Article {uid: $article})
MATCH (s)-[:CITES]->(x)
WHERE x = t OR (t)-[:HAS_PARAGRAPH]->(x)
OPTIONAL MATCH (up:Article)-[:HAS_PARAGRAPH|HAS_ITEM*1..2]->(s)
WITH t, CASE WHEN s:Article OR s:AdminArticle THEN s ELSE up END AS src
WHERE src IS NOT NULL AND src <> t
RETURN DISTINCT src.uid AS uid
LIMIT 200
