"""파서가 내는 노드 · 엣지 모음. Neo4j 없이 만들고 세고 거른다(쓰기는 `write.py`).

- 노드: uid → (라벨, 속성). 라벨은 uid 접두사로 정해진다(`schema/schema.yaml`)
- 엣지: (유형, 시작 uid, 끝 uid, 키) → 속성. 키는 같은 두 노드 사이에 여러 엣지를 두는 유형만 쓴다
  (`CONTRAINDICATED_WITH`의 rule_id). 같은 엣지가 두 번 나오면 처음 속성을 두고, historical은 둘 다 참일 때만 참
- cite_units: `cites.py`가 refs로 읽을 조문 하나((단위 uid, 자기 본문) 목록, 맥락 법령, 맥락 조문). 적재하지 않는다
- stats: 파싱 중 버리거나 해석하지 못한 수(적재 보고용)
"""

from collections import Counter
from dataclasses import dataclass, field

PREFIX_LABEL = {
    "law": "Law", "art": "Article", "par": "Paragraph", "item": "Item", "anx": "Annex",
    "adm": "AdminRule", "admart": "AdminArticle", "case": "Case", "expc": "Interpretation", "decc": "AdminAppeal",
    "disp": "Disposition", "ing": "Ingredient", "dur": "DurRule",
}
EDGE_KEY = {"CONTRAINDICATED_WITH": "rule_id"}


def label_of(uid: str) -> str:
    return PREFIX_LABEL[uid.split(":", 1)[0]]


def article_or_paragraph(uid: str, known: set[str] | dict) -> str | None:
    """refs 결과(조 · 항 · 호)를 그래프에 있는 조 · 항으로 올린다. 호 → 항(항 자리 0이면 조), 없는 항 → 조. 별표 · 없는 조는 None."""
    kind, rest = uid.split(":", 1)
    if kind == "item":
        law, art, par, _ = rest.split(":")
        uid = f"par:{law}:{art}:{par}" if par != "0" else f"art:{law}:{art}"
    if uid.startswith("par:") and uid not in known:
        law, art, _ = uid[4:].split(":")
        uid = f"art:{law}:{art}"
    return uid if uid.startswith(("art:", "par:")) and uid in known else None


@dataclass
class Graph:
    nodes: dict[str, tuple[str, dict]] = field(default_factory=dict)
    edges: dict[tuple[str, str, str, str], dict] = field(default_factory=dict)
    cite_units: list[tuple[list[tuple[str, str]], str | None, str | None]] = field(default_factory=list)
    stats: Counter = field(default_factory=Counter)

    def node(self, uid: str, props: dict) -> bool:
        """노드를 더한다. 이미 있으면 버리고 False(파서가 중복 수를 센다)."""
        if uid in self.nodes:
            return False
        self.nodes[uid] = (label_of(uid), props)
        return True

    def edge(self, type_: str, src: str, dst: str, props: dict | None = None) -> None:
        props = dict(props or {})
        key = str(props.get(EDGE_KEY[type_], "")) if type_ in EDGE_KEY else ""
        k = (type_, src, dst, key)
        old = self.edges.get(k)
        if old is None:
            self.edges[k] = props
        elif "historical" in old and "historical" in props:
            old["historical"] = old["historical"] and props["historical"]

    def update(self, other: "Graph") -> None:
        for uid, (label, props) in other.nodes.items():
            if uid in self.nodes:
                raise ValueError(f"두 파서가 같은 노드를 냈다: {uid}")
            self.nodes[uid] = (label, props)
        for (t, s, d, _), props in other.edges.items():
            self.edge(t, s, d, props)
        self.cite_units += other.cite_units
        self.stats.update(other.stats)

    def prune(self) -> Counter:
        """끝점 노드가 없는 엣지를 뺀다. 유형별 뺀 수."""
        dropped: Counter = Counter()
        for k in [k for k in self.edges if k[1] not in self.nodes or k[2] not in self.nodes]:
            dropped[k[0]] += 1
            del self.edges[k]
        return dropped

    def exclude(self, uids: set[str]) -> tuple[Counter, Counter]:
        """노드와 그 노드에 닿는 엣지를 뺀다(eval 모드 제외 목록). (라벨별 뺀 노드 수, 유형별 뺀 엣지 수)."""
        nodes: Counter = Counter()
        for uid in uids & self.nodes.keys():
            nodes[self.nodes.pop(uid)[0]] += 1
        edges: Counter = Counter()
        for k in [k for k in self.edges if k[1] in uids or k[2] in uids]:
            edges[k[0]] += 1
            del self.edges[k]
        return nodes, edges

    def node_counts(self) -> Counter:
        return Counter(label for label, _ in self.nodes.values())

    def edge_counts(self) -> Counter:
        return Counter(k[0] for k in self.edges)
