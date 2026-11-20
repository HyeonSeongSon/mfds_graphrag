// T6: 조문 X(그 항 포함)를 근거로 한 식품 처분 건수(의약품 · 화장품 처분 source='drug'는 세지 않는다)
MATCH (t:Article {uid: $article})
OPTIONAL MATCH (d:Disposition {source: 'food'})-[:BASED_ON]->(x)
WHERE x = t OR (t)-[:HAS_PARAGRAPH]->(x)
RETURN count(DISTINCT d) AS n
