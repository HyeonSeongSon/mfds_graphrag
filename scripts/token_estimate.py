"""벤더 API 비정형 추출 입력 크기 추정(1주차 D3 G). 집계만 출력한다.

입력 규칙은 플랜 부록 B1: 판례는 판시사항 + 판결요지(둘 다 없으면 판례내용 앞 6천 자, 참조조문은 항상 뺌),
해석례는 질의요지 · 회답 · 이유, 심판은 주문 · 청구취지 · 이유 앞부분(앞 6천 자로 잡음). 비교용으로 전문도 센다.
토크나이저(`tokenizers` + tokenizer.json)가 있으면 그 토큰 수도 센다(벤더 토크나이저 대용, 참고치).

    python scripts/token_estimate.py data/raw/20261029 [tokenizer.json]
"""

import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

HEAD = 6000
TAG = re.compile(r"<[^>]+>")
SKIP = {"참조조문", "참조판례"}


def clean(s: str | None) -> str:
    return re.sub(r"\s+", " ", TAG.sub(" ", s or "")).strip()


def fields(root: ET.Element) -> dict[str, str]:
    return {c.tag: clean(c.text) for c in root}


def extract_input(kind: str, f: dict[str, str]) -> str:
    if kind == "prec":
        short = " ".join(x for x in (f.get("판시사항"), f.get("판결요지")) if x)
        return short or f.get("판례내용", "")[:HEAD]
    if kind == "expc":
        return " ".join(f.get(k, "") for k in ("질의요지", "회답", "이유"))
    return " ".join((f.get("주문", ""), f.get("청구취지", ""), f.get("이유", "")[:HEAD]))


def full_text(f: dict[str, str]) -> str:
    return " ".join(v for k, v in f.items() if k not in SKIP)


def main() -> None:
    S = Path(sys.argv[1])
    tok = None
    if len(sys.argv) > 2:
        from tokenizers import Tokenizer
        tok = Tokenizer.from_file(sys.argv[2])
        tok.no_truncation()  # 학습용 tokenizer.json에 512 자르기가 켜져 있을 수 있다
    print("| 대상 | 문서 | 추출 입력 자 | 평균 | 토큰(참고) | 전문 자 |")
    print("|---|---|---|---|---|---|")
    tot_c = tot_t = tot_f = 0
    for kind in ("prec", "expc", "decc"):
        n = chars = toks = full = 0
        for p in sorted((S / "decisions" / kind / "body").glob("*.xml")):
            f = fields(ET.parse(p).getroot())
            x = extract_input(kind, f)
            n += 1
            chars += len(x)
            full += len(full_text(f))
            if tok:
                toks += len(tok.encode(x).ids)
        tot_c, tot_t, tot_f = tot_c + chars, tot_t + toks, tot_f + full
        print(f"| {kind} | {n:,} | {chars:,} | {chars // max(n, 1):,} | {toks:,} | {full:,} |")
    print(f"| 합 | | {tot_c:,} | | {tot_t:,} | {tot_f:,} |")
    if tok:
        print(f"\n토큰/자 비율(추출 입력): {tot_t / tot_c:.2f}")


if __name__ == "__main__":
    main()
