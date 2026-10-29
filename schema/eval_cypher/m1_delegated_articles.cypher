// T4-M1: 법률 조문이 위임한 하위 법령 조문($target_kind: 대통령령 | 총리령)
MATCH (s:Article {uid: $article})-[:DELEGATES_TO]->(t:Article)<-[:HAS_ARTICLE]-(:Law {kind: $target_kind})
RETURN DISTINCT t.uid AS uid
LIMIT 200
