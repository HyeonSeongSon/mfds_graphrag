"""병용금기 고시 별표 1~3(`mfds/collect/dur.py`) + 성분 정규화(`mfds/ingredients.py`) → Ingredient · DurRule와 DUR 엣지.

- DurRule uid: 별표 1 `dur:pair:{A}:{B}`(두 이름 정렬), 별표 2 `dur:age:{성분}:{연령 기준}`, 별표 3 `dur:preg:{성분}`.
  연령 기준은 공백을 하나로, `:`는 `_`로 바꾼다
- 같은 uid가 여러 행에서 나오면 **행 내용 sha256** 순서로 2번째부터 `#2` · `#3`을 붙인다. 행 내용 = 연번을 뺀 칸들
  (`dur.ANNEX_COLUMNS` 순서)을 탭으로 이은 UTF-8 문자열. 해시가 같으면(내용이 같은 행) 연번 순
- 별표 1 행: INVOLVES 두 성분, CONTRAINDICATED_WITH 정렬 앞 → 뒤 한 방향(rule_id = DurRule uid)
- 복합제 `A + B`: 복합제 노드 하나 + 성분마다 HAS_COMPONENT. INVOLVES는 복합제 노드에만 단다
- DEFINED_IN: DurRule → 고시 별표 노드 `anx:adm{행정규칙ID}:{별표}`(노드는 `parse_admrul`이 만든다)
- `<삭제>` 표기 행은 규칙을 만들지 않는다
"""

import hashlib
import re
from collections import defaultdict
from pathlib import Path

from lxml import etree

from mfds.collect import dur
from mfds.ingredients import Ingredient, normalize
from mfds.load.graph import Graph

DUR_RULE_NAME = "의약품 병용금기 성분 등의 지정에 관한 규정"
TYPES = {"1": "pair", "2": "age", "3": "preg"}


def find_body(admrul_bodies: list[Path]) -> Path | None:
    for p in admrul_bodies:
        if etree.parse(str(p)).getroot().findtext("행정규칙기본정보/행정규칙명") == DUR_RULE_NAME:
            return p
    return None


def row_hash(row: dict, annex_no: str) -> str:
    cells = [str(row[c]) for c in dur.ANNEX_COLUMNS[annex_no] if c != "seq"]
    return hashlib.sha256("\t".join(cells).encode("utf-8")).hexdigest()


def assign_suffixes(rules: list[tuple[str, str, int]]) -> list[str]:
    """(기본 uid, 행 해시, 연번) 목록 → 같은 순서의 최종 uid. 기본 uid가 같은 것끼리 (해시, 연번) 순으로 #2 · #3."""
    groups: dict[str, list[int]] = defaultdict(list)
    for i, (base, _, _) in enumerate(rules):
        groups[base].append(i)
    out = [r[0] for r in rules]
    for base, idx in groups.items():
        for rank, i in enumerate(sorted(idx, key=lambda i: (rules[i][1], rules[i][2]))):
            if rank:
                out[i] = f"{base}#{rank + 1}"
    return out


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def _add_ingredient(g: Graph, ing: Ingredient) -> None:
    for c in ing.components:
        _add_ingredient(g, c)
        g.edge("HAS_COMPONENT", ing.uid, c.uid)
    old = g.nodes.get(ing.uid)
    if old is None:
        g.node(ing.uid, {"name": ing.name, "aliases": sorted(set(ing.aliases)), "base": ing.base,
                         "class_codes": sorted(set(ing.class_codes)), "contains": ing.contains,
                         "is_combination": bool(ing.components)})
        return
    p = old[1]
    p["aliases"] = sorted(set(p["aliases"]) | set(ing.aliases))
    p["class_codes"] = sorted(set(p["class_codes"]) | set(ing.class_codes))
    p["contains"] = p["contains"] or ing.contains


def parse(path: Path) -> Graph:
    g = Graph()
    aid = etree.parse(str(path)).getroot().findtext("행정규칙기본정보/행정규칙ID")
    annexes = dur.annexes_from_xml(path)
    pending = []  # (기본 uid, 행 해시, 연번, 속성, INVOLVES 성분 uid, 병용 쌍 또는 None, 별표)
    for annex_no, rows in sorted(annexes.items()):
        g.stats[f"dur.별표 {annex_no} 행"] += len(rows)
        for row in rows:
            note = _clean(row.get("note", "")) or None
            if annex_no == "1":
                a, b = normalize(row["ingredient_a"]), normalize(row["ingredient_b"])
                if a is None or b is None:
                    g.stats["dur.<삭제> 행(규칙 없음)"] += 1
                    continue
                x, y = sorted((a, b), key=lambda i: i.name)
                base = f"dur:pair:{x.name}:{y.name}"
                form = "; ".join(f"{i.name}: {i.form}" for i in (x, y) if i.form) or None
                text = f"{x.name}와(과) {y.name}는(은) 함께 쓰면 안 되는 병용금기 성분이다."
                props = {"form": form, "condition_note": note}
                ings, pair = (x, y), (x.uid, y.uid)
            else:
                i = normalize(row["ingredient"])
                if i is None:
                    g.stats["dur.<삭제> 행(규칙 없음)"] += 1
                    continue
                ings, pair = (i,), None
                if annex_no == "2":
                    age = _clean(row["age"])
                    base = f"dur:age:{i.name}:{age.replace(':', '_')}"
                    form = ", ".join(dict.fromkeys(f for f in (_clean(row["dosage_form"]), i.form) if f)) or None
                    text = f"{i.name}는(은) {age}에게 쓰면 안 되는 특정연령대 금기 성분이다."
                    props = {"form": form, "age": age}
                else:
                    grade = _clean(row["grade"]) or None
                    base = f"dur:preg:{i.name}"
                    text = f"{i.name}는(은) 임부금기 성분이다" + (f"(등급 {grade})." if grade else ".")
                    props = {"form": i.form, "condition_note": note, "grade": grade}
            if props.get("form"):
                text += f" 제형: {props['form']}."
            if props.get("condition_note"):
                text += f" 비고: {props['condition_note']}."
            props = {"type": TYPES[annex_no], "annex": int(annex_no), "seq": row["seq"], **props, "text": text}
            pending.append((base, row_hash(row, annex_no), row["seq"], props, ings, pair, annex_no))
    uids = assign_suffixes([(p[0], p[1], p[2]) for p in pending])
    for uid, (base, _, _, props, ings, pair, annex_no) in zip(uids, pending):
        if uid != base:
            g.stats["dur.같은 uid 행(#번호 붙임)"] += 1
        g.node(uid, props)
        g.edge("DEFINED_IN", uid, f"anx:adm{aid}:{annex_no}")
        for ing in ings:
            _add_ingredient(g, ing)
            g.edge("INVOLVES", uid, ing.uid)
        if pair:
            g.edge("CONTRAINDICATED_WITH", pair[0], pair[1], {"rule_id": uid})
    return g
