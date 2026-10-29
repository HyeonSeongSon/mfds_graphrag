// T4-M4: 조문 X(그 항 포함)를 인용하는 조문. 범위는 X의 법령 · 그 하위 법령 · 고시(M4 정답 후보와 같음).
// 인용한 쪽이 항 · 호면 조문 단위로 올린다. 구법 참조와 자기 자신은 뺀다.
MATCH (l:Law)-[:HAS_ARTICLE]->(t:Article {uid: $article})
MATCH (s)-[c:CITES]->(x)
WHERE (x = t OR (t)-[:HAS_PARAGRAPH]->(x)) AND coalesce(c.historical, false) = false
OPTIONAL MATCH (up:Article)-[:HAS_PARAGRAPH|HAS_ITEM*1..2]->(s)
WITH l, t, CASE WHEN s:Article OR s:AdminArticle THEN s ELSE up END AS src
WHERE src IS NOT NULL AND src <> t
OPTIONAL MATCH (sl:Law)-[:HAS_ARTICLE]->(src)
WITH l, t, src, sl
WHERE src:AdminArticle OR sl = l OR (sl)-[:SUBORDINATE_OF*1..2]->(l)
RETURN DISTINCT src.uid AS uid
LIMIT 200
