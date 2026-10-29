// T6: 업종 · 처분 유형별 처분 건수
MATCH (d:Disposition {industry: $industry, type: $disp_type})
RETURN count(d) AS n
