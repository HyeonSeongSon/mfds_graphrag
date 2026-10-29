// T4-M3: 성분 X와 병용금기이면서 특정연령대 금기인 성분
MATCH (x:Ingredient {uid: $ingredient})<-[:INVOLVES]-(:DurRule {type: 'pair'})-[:INVOLVES]->(y:Ingredient)
WHERE y <> x
MATCH (y)<-[:INVOLVES]-(:DurRule {type: 'age'})
RETURN DISTINCT y.uid AS uid
LIMIT 200
