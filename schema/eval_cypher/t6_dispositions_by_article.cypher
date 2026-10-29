// T6: 조문 X(그 항 포함)를 근거로 한 처분 건수
MATCH (t:Article {uid: $article})
OPTIONAL MATCH (d:Disposition)-[:BASED_ON]->(x)
WHERE x = t OR (t)-[:HAS_PARAGRAPH]->(x)
RETURN count(DISTINCT d) AS n
