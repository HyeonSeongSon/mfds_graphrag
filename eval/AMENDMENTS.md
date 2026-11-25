# AMENDMENTS: 잠금 뒤 기록 (추가만 가능, 각 항목은 이전 항목의 sha256을 담는다)

항목은 `python eval/prereg.py amend`로만 추가합니다. 손으로 고치거나 지우면 사슬 확인(`prereg.py check`)이 실패합니다.

- `{"at": "2026-11-25T15:05:24.291580+00:00", "content": "스냅숏 20261105 확인 통과(python scripts/check_snapshot.py data/raw/20261105, 문제 0건 · OK 29 · exit 0), 잠금 커밋 C2=49c318b5c99275be663246afae47f0c3582d6617", "id": 1, "kind": "snapshot", "new_hashes": {"data/raw/20261105/manifest.tsv": "e7bad5bc14eca2446b7ff5f860927a07f7581ee9eb118558b747baf391422053", "scripts/check_snapshot.py": "18c5e765c0151a5204bb993c63c4fc176aff8c1f3e91a2de399c1eb95a61a540"}, "prev_hash": "GENESIS"}`
