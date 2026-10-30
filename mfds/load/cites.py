"""조문 · 항 · 호 · 행정규칙 조문 본문 → refs → `CITES {raw, historical}`(대상은 조 · 항).

파서가 조문마다 (단위 uid, 자기 본문) 목록을 `Graph.cite_units`로 낸다. 단위 본문을 정규화해 공백 하나로 이어 붙인 조문 전체를
**한 번에** refs로 읽고(`같은 법` · `같은 항`이 앞 단위의 법령 · 조항을 가리킬 수 있다), 참조 원문(`Ref.raw`)이 시작하는
위치가 든 단위를 엣지의 시작으로 삼는다. refs는 한 번 읽을 때 같은 uid를 한 번만 내므로, 같은 대상을 다시 인용한 단위는
단위 본문만 따로 읽어 **조문 전체 결과에 있는 uid만** 엣지로 더한다(앞 단위 맥락 없이 잘못 읽은 참조가 끼지 않게).
그래서 조문 단위로 올린 인용 집합은 조문 전체를 한 번 읽은 결과와 같다(d3_report · 평가셋 생성기와 같은 읽기).

법령 조문은 그 법령 · 조를 맥락으로, 행정규칙 조문은 맥락 없이 읽는다. 호를 가리키면 항으로, 그래프에 없는 항이면 조로
올린다. 별표 참조와 자기 자신은 엣지가 아니다.

행정규칙 안의 자기 조문 참조(`제3조에 따라`)는 법령 맥락이 없어 refs에서 no_context로 떨어진다. 이 수를 적재 보고에 남긴다
(행정규칙 → 행정규칙 엣지는 스키마 밖, 한계).
"""

from bisect import bisect_right

from mfds.load.graph import Graph, article_or_paragraph, label_of
from mfds.refs import Resolver, norm_text


def build(units: list[tuple[list[tuple[str, str]], str | None, str | None]], resolver: Resolver,
          known: set[str]) -> Graph:
    g = Graph()
    for segments, law, art in units:
        side = "admrul" if label_of(segments[0][0]) == "AdminArticle" else "law"
        parts = [(uid, t) for uid, t in ((u, norm_text(x)) for u, x in segments) if t]
        if not parts:
            continue
        starts, n = [], 0
        for _, t in parts:
            starts.append(n)
            n += len(t) + 1
        whole = " ".join(t for _, t in parts)
        res = resolver.resolve(whole, law, art)
        pos = 0
        for ref in res.refs:
            at = whole.find(ref.raw, pos)
            if at < 0:  # 원문 위치를 못 찾으면 조문 자체를 시작으로
                g.stats[f"cites.{side} 참조 위치 못 찾음(조문에 붙임)"] += 1
                src = segments[0][0]
            else:
                pos = at
                src = parts[bisect_right(starts, at) - 1][0]
            if ref.uid.startswith("anx:"):
                g.stats[f"cites.{side} 별표 참조(엣지 아님)"] += 1
                continue
            dst = article_or_paragraph(ref.uid, known)
            if dst is None:
                g.stats[f"cites.{side} 대상 노드 없음"] += 1
            elif dst == src:
                g.stats[f"cites.{side} 자기 참조(엣지 아님)"] += 1
            else:
                g.edge("CITES", src, dst, {"raw": ref.raw, "historical": ref.historical})
        for f in res.failures:
            g.stats[f"cites.{side} 해석 실패 {f.reason}"] += 1
        if len(parts) > 1:  # 같은 대상을 다시 인용한 단위: 단위만 읽어 조문 전체 결과에 있는 uid만 더한다
            whole_ref = {r.uid: r for r in res.refs}
            for src, t in parts:
                for r in resolver.resolve(t, law, art).refs:
                    dst = article_or_paragraph(r.uid, known) if r.uid in whole_ref else None
                    if dst and dst != src and ("CITES", src, dst, "") not in g.edges:
                        g.stats[f"cites.{side} 다시 인용한 단위(엣지 더함)"] += 1
                        g.edge("CITES", src, dst, {"raw": r.raw, "historical": whole_ref[r.uid].historical})
    return g
