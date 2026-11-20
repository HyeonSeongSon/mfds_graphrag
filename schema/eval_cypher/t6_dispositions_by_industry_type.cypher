// T6: 업종 · 처분 유형별 식품 처분 건수
MATCH (d:Disposition {source: 'food', industry: $industry, type: $disp_type})
RETURN count(d) AS n
