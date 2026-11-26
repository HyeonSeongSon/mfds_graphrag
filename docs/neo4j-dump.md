# Neo4j 평가용 그래프 dump · 복원 절차

1주차 D5(2026-11-26)에 연습한 절차예요(PLAN 1주차 D5 4단계). 2주차 D4 저녁 평가용 그래프 동결, 4주차 D3 재적재 전 보관, 그 뒤 평가용 그래프 복원이 모두 이 절차를 따라요.

## 원칙

- **그래프 상태는 dump 파일 해시가 아니라 결과 해시로 비교해요.** dump 파일에는 메타데이터가 들어 있어서, 같은 그래프라도 파일 해시가 달라질 수 있어요(PLAN 변경 이력 3 반영).
- **해시 두 개를 같이 봐요.**
  - 결과 해시: 정답 템플릿 결과
  - 내용 해시: 노드 · 엣지의 모든 속성
- **Neo4j Community는 실행 중인 DB를 dump할 수 없어요.** 컨테이너를 멈춘 뒤, 같은 볼륨을 붙인 별도 컨테이너에서 `neo4j-admin`을 실행해요.
- **dump 파일은 `data/neo4j/backups/`에 둬요.** 이 폴더는 `.gitignore` 대상(`data/neo4j/`)이라 저장소에 올라가지 않아요. 외장 백업은 PLAN 백업 표를 따라요.

## 절차 (PowerShell, 저장소 루트)

Git Bash에서 실행하면 `-v` 인자의 `/data` 같은 경로가 Windows 경로로 바뀌어 버려요. 그래서 PowerShell을 써요. Docker Desktop이 켜져 있어야 하고, `docker.exe`가 PATH에 없으면 `C:\Users\<사용자>\AppData\Local\Programs\DockerDesktop\resources\bin`을 PATH에 더해요.

1. **dump 전 해시를 재요.** 두 번 돌려 같은지 확인해요.
   ```powershell
   python scripts/graph_hash.py --content
   ```
2. **컨테이너를 멈춰요.**
   ```powershell
   docker compose stop neo4j
   ```
3. **dump해요.** 덮어쓰지 않도록 `--overwrite-destination=false`를 줘요. 같은 이름의 파일이 이미 있으면 다른 폴더를 쓰거나, 기존 파일을 먼저 옮겨요.
   ```powershell
   $data = (Resolve-Path data\neo4j\data).Path; $bk = (Resolve-Path data\neo4j\backups).Path
   docker run --rm -v "${data}:/data" -v "${bk}:/backups" neo4j:5.26.32-community neo4j-admin database dump neo4j --to-path=/backups --overwrite-destination=false
   ```
4. **dump 파일 해시를 기록해요.** 동결 기록에서 그 dump 파일을 식별하는 용도예요. 그래프 같음은 이 값이 아니라 결과 해시로 판단해요.
   ```powershell
   (Get-FileHash data\neo4j\backups\neo4j.dump -Algorithm SHA256).Hash.ToLower()
   ```
5. **복원해요.** 지금 DB를 dump 내용으로 덮어써요.
   ```powershell
   docker run --rm -v "${data}:/data" -v "${bk}:/backups" neo4j:5.26.32-community neo4j-admin database load neo4j --from-path=/backups --overwrite-destination=true
   ```
6. **다시 기동하고, 접속되면 해시를 다시 재요.** 결과 해시와 내용 해시가 1번과 같아야 해요.
   ```powershell
   docker compose start neo4j
   ```
   ```powershell
   python scripts/graph_hash.py --content
   ```
7. **함께 확인해요.**
   - 제약 조건 수가 `schema/constraints.cypher`와 같은지
   - `python scripts/check_leak.py --graph` 통과
   - `python scripts/check_templates.py --evalset eval/evalset/v1`에서 불일치 0

## 1주차 D5 연습 결과 (2026-11-26)

| 항목 | 값 |
|---|---|
| 그래프 | `--mode eval` 평가용 그래프, 노드 46,975 · 엣지 74,428 |
| dump 전 결과 해시 | `8b91d85d94fcd3234a92add337426b7b03baeaaa2253b1ee3d26cefc82336aa7` (정답 템플릿 슬롯 532, 두 번 같음) |
| dump 전 내용 해시 | `46f8ec08b4da79c9b901e6539cd0397b86777f75e0e4c225f2ac567177e24587` (노드 `afbbd4ab06eb…` · 엣지 `558391bdf40b…`, 두 번 같음) |
| dump | 61파일 · 895.9MiB를 처리해 `neo4j.dump` 144,946,474바이트, 25초 |
| dump 파일 sha256 | `e22a3700bc360fa1d3278971204392bd266a314b676d859629d76021c32721ab` |
| 복원 | `load --overwrite-destination=true` 53초, 기동 뒤 6초 안에 접속 |
| 복원 뒤 | 결과 해시 · 내용 해시 · 의도별 해시가 출력 전체로 dump 전과 같음. 제약 조건 13 = `constraints.cypher` 13, 인덱스 20. 누수 검사 통과, 템플릿 대조 불일치 0 / 532 |

## 다음에 쓰는 곳

- **2주차 D4 저녁 동결**
  1. 위 1~4번을 실행해요.
  2. 결과 해시 · 내용 해시 · dump 파일 해시를 AMENDMENTS에 기록해요.
  3. 기록한 뒤 컨테이너를 다시 기동해요(`docker compose start neo4j`).
  4. 동결 뒤 그래프가 바뀌면 베이스 4B · 8B를 다시 돌려요.
- **3주차 D4 · 4주차 D3:** 결과 해시가 동결 값과 같은지 확인해요. 4주차 D3에는 `--mode full`로 다시 적재하기 전에 dump해서 보관해요.
- **임베딩을 노드 속성으로 넣으면** 그때부터 내용 해시가 바뀌어요. 그래서 동결 기준값은 동결 시점에 다시 재요. 결과 해시는 정답 템플릿 결과만 보기 때문에, 템플릿이 벡터를 읽지 않으면 바뀌지 않아요.
