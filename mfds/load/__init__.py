"""적재기: 파싱(`parse_*` · `cites` → `graph.Graph`, Neo4j 없음)과 쓰기(`write`, `UNWIND … MERGE` 배치)를 나눈다.

    python -m mfds.load --snapshot data/raw/YYYYMMDD --mode eval|full [--exclusion eval/exclusion_ids.json] [--wipe] [--dry-run]
"""
