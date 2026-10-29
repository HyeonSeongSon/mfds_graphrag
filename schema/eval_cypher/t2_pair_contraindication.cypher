// T2: 두 성분의 병용금기 판정과 근거 DurRule. 성분을 못 찾으면 uid가 null이라 "해당없음"과 구별된다.
OPTIONAL MATCH (a:Ingredient {uid: $ing_a})
OPTIONAL MATCH (b:Ingredient {uid: $ing_b})
OPTIONAL MATCH (a)<-[:INVOLVES]-(r:DurRule {type: 'pair'})-[:INVOLVES]->(b)
WITH a, b, collect(DISTINCT r.uid) AS rule_ids
RETURN a.uid AS ing_a, b.uid AS ing_b,
       CASE WHEN size(rule_ids) > 0 THEN '금기' ELSE '해당없음' END AS verdict,
       rule_ids
