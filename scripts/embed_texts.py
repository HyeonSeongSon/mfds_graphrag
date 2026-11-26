"""평가용 그래프 → 임베딩 텍스트(PREREG 4-5 "임베딩 텍스트(비교 조건)", 부록 B2, PLAN 1주차 D5 7단계).

잠그지 않는 도구지만 텍스트 규칙은 PREREG에 잠겨 있다(잠근 뒤 바꾸지 않음). Neo4j에서 읽어 `data/cache/embed/`(git 제외)에
`<이름>_texts.jsonl`을 쓴다. 건수 · 해시만 출력한다(본문은 출력하지 않음).

    python scripts/embed_texts.py [--name eval]

규칙(PREREG 4-5 그대로, 법령명은 그 노드를 가진 `Law.name` · `AdminRule.name`):
- 조문 `[법령명] 제N조(의M)(제목) 본문`: `Article.text`가 이미 `제N조(의M)(제목)`로 시작하므로 앞에 `[법령명] `만 붙인다.
- 항 `[법령명] 제N조(의M) 제M항 본문`: 본문 맨 앞의 원문자 항 번호(①~⑳)는 뺀다(`제M항`과 겹침).
- 별표 청크 `[법령명] 별표 N 제목 · 청크`: `Annex.text`를 1,000자씩 겹침 없이 자른다. 번호 `7-2`는 `7의2`로 쓴다. 본문 없는 별표는 뺀다.
- 고시 조문 `[고시명] 본문`(`AdminArticle.text`가 `제N조(제목)`로 시작).
- DurRule `text` 그대로. 처분 `violation_text`, 판례 판결요지(`summary`, 없으면 판시사항 `holding`), 해석례 `question` · `answer`,
  심판 `order` · `claim`(둘은 줄바꿈으로 잇는다). 정답 출처 필드(`law_ref_text` · `ref_text`)는 쓰지 않는다.
- 호(`Item`) · 법령 · 고시 · 성분 노드는 넣지 않는다(PREREG 목록 밖). 텍스트가 비면 빼고 종류별 수를 보고한다.

행: `{"id": 벡터 id(노드 uid, 별표 청크는 uid#순번), "uid": 노드 uid, "label": 라벨, "text": ..., "sha": sha256(text)}`, id 순.
"""

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mfds.load import write  # noqa: E402

OUT = ROOT / "data" / "cache" / "embed"
CHUNK = 1000
CIRCLED = re.compile(r"^\s*[①-⑳]\s*")

Q = {
    "Article": "MATCH (l:Law)-[:HAS_ARTICLE]->(a:Article) RETURN a.uid AS uid, l.name AS law, a.text AS text",
    "Paragraph": "MATCH (l:Law)-[:HAS_ARTICLE]->(a:Article)-[:HAS_PARAGRAPH]->(p:Paragraph) "
                 "RETURN p.uid AS uid, l.name AS law, a.number AS n, a.branch AS b, p.no AS no, p.text AS text",
    "Annex": "MATCH (o)-[:HAS_ANNEX]->(x:Annex) WHERE o:Law OR o:AdminRule "
             "RETURN x.uid AS uid, o.name AS law, x.no AS no, x.title AS title, x.text AS text",
    "AdminArticle": "MATCH (r:AdminRule)-[:HAS_ARTICLE]->(a:AdminArticle) RETURN a.uid AS uid, r.name AS law, a.text AS text",
    "DurRule": "MATCH (n:DurRule) RETURN n.uid AS uid, n.text AS text",
    "Disposition": "MATCH (n:Disposition) RETURN n.uid AS uid, n.violation_text AS text",
    "Case": "MATCH (n:Case) RETURN n.uid AS uid, n.summary AS summary, n.holding AS holding",
    "Interpretation": "MATCH (n:Interpretation) RETURN n.uid AS uid, n.question AS a, n.answer AS b",
    "AdminAppeal": "MATCH (n:AdminAppeal) RETURN n.uid AS uid, n.order AS a, n.claim AS b",
}


def s(x) -> str:
    return (x or "").strip()


def article_no(n, b) -> str:
    return f"제{n}조" + (f"의{b}" if b else "")


def texts_for(label: str, r: dict) -> list[tuple[str, str]]:
    """노드 한 행 → [(벡터 id, 텍스트)]. 텍스트가 비면 []."""
    if label in ("Article", "AdminArticle"):
        body = s(r["text"])
        return [(r["uid"], f"[{r['law']}] {body}")] if body else []
    if label == "Paragraph":
        body = CIRCLED.sub("", s(r["text"]))
        return [(r["uid"], f"[{r['law']}] {article_no(r['n'], r['b'])} 제{r['no']}항 {body}")] if body else []
    if label == "Annex":
        body = s(r["text"])
        no = str(r["no"]).replace("-", "의")
        head = f"[{r['law']}] 별표 {no} {s(r['title'])} · "
        return [(f"{r['uid']}#{i // CHUNK}", head + body[i:i + CHUNK]) for i in range(0, len(body), CHUNK)]
    if label == "Case":
        body = s(r["summary"]) or s(r["holding"])
        return [(r["uid"], body)] if body else []
    if label in ("Interpretation", "AdminAppeal"):
        body = "\n".join(x for x in (s(r["a"]), s(r["b"])) if x)
        return [(r["uid"], body)] if body else []
    body = s(r["text"])  # DurRule · Disposition
    return [(r["uid"], body)] if body else []


def build(run) -> tuple[list[dict], Counter, Counter]:
    rows, nodes, empty = [], Counter(), Counter()
    for label, q in Q.items():
        for r in run(q):
            nodes[label] += 1
            got = texts_for(label, r)
            empty[label] += not got
            rows += [{"id": i, "uid": r["uid"], "label": label, "text": t,
                      "sha": hashlib.sha256(t.encode("utf-8")).hexdigest()} for i, t in got]
    rows.sort(key=lambda x: x["id"])
    ids = [x["id"] for x in rows]
    if len(ids) != len(set(ids)):
        raise SystemExit("벡터 id가 겹친다")
    return rows, nodes, empty


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="eval", help="출력 이름(eval: 평가용 그래프, full: 시연용 전체 그래프)")
    args = ap.parse_args()
    with write.driver() as drv, drv.session() as sess:
        drv.verify_connectivity()
        rows, nodes, empty = build(lambda q: sess.run(q).data())
        total = sess.run("MATCH (n) RETURN count(n) AS n").single()["n"]
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{args.name}_texts.jsonl"
    data = "".join(json.dumps(x, ensure_ascii=False, sort_keys=True) + "\n" for x in rows).encode("utf-8")
    path.write_bytes(data)
    per = Counter(x["label"] for x in rows)
    print(f"# 임베딩 텍스트 {path.relative_to(ROOT).as_posix()} (그래프 노드 {total:,})")
    for label in Q:
        print(f"  {label}\t노드 {nodes[label]:,}\t텍스트 {per[label]:,}\t빈 텍스트로 뺌 {empty[label]:,}")
    print(f"## 텍스트 {len(rows):,}, 고유 텍스트 {len({x['sha'] for x in rows}):,}, 파일 sha256 {hashlib.sha256(data).hexdigest()}")


if __name__ == "__main__":
    main()
