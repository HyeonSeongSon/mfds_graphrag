// T4-M2: 판례 참조조문(필드 기준, 구법 참조 제외)을 조문 단위로 올린 뒤, 그 조문을 위임받은 시행령 조문
MATCH (c:Case {uid: $case})-[ref:REFERENCES {source: 'field'}]->(x)
WHERE coalesce(ref.historical, false) = false
OPTIONAL MATCH (up:Article)-[:HAS_PARAGRAPH]->(x)
WITH coalesce(up, x) AS art
MATCH (art:Article)-[:DELEGATES_TO]->(t:Article)<-[:HAS_ARTICLE]-(:Law {kind: '대통령령'})
RETURN DISTINCT t.uid AS uid
LIMIT 200
