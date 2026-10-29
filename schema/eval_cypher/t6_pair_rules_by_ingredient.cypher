// T6: 성분 X가 포함된 병용금기 규칙 수
MATCH (x:Ingredient {uid: $ingredient})
OPTIONAL MATCH (x)<-[:INVOLVES]-(r:DurRule {type: 'pair'})
RETURN count(DISTINCT r) AS n
