// T4-M2: 처분 근거 조문을 조문 단위로 올린 뒤, 그 조문을 위임받은 하위 법령 조문(대통령령 · 총리령)
MATCH (d:Disposition {uid: $disposition})-[:BASED_ON]->(x)
OPTIONAL MATCH (up:Article)-[:HAS_PARAGRAPH]->(x)
WITH coalesce(up, x) AS art
MATCH (art:Article)-[:DELEGATES_TO]->(t:Article)
RETURN DISTINCT t.uid AS uid
LIMIT 200
