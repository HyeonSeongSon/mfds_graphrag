// T6: 법률 조문 X가 위임한 하위 법령 조문 수
MATCH (s:Article {uid: $article})
OPTIONAL MATCH (s)-[:DELEGATES_TO]->(t:Article)
RETURN count(DISTINCT t) AS n
