// T4-M3: 성분 X와 병용금기이면서 임부금기인 성분(uid 단위, 염 단위 선택은 1주차 D4 dry-run에서 확정)
MATCH (x:Ingredient {uid: $ingredient})<-[:INVOLVES]-(:DurRule {type: 'pair'})-[:INVOLVES]->(y:Ingredient)
WHERE y <> x
MATCH (y)<-[:INVOLVES]-(:DurRule {type: 'preg'})
RETURN DISTINCT y.uid AS uid
LIMIT 200
