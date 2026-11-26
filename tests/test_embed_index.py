"""scripts/embed_index.py: 토큰 예산 묶음, 질의 접두어, 모델별 캐시 폴더(모델 없이 검사)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import embed_index as B  # noqa: E402


def test_batches_cover_all_once_within_budget() -> None:
    lengths = [5, 300, 40, 9000, 120, 120, 8, 2000]
    bs = B.batches(lengths, 1000)
    assert sorted(i for b in bs for i in b) == list(range(len(lengths)))
    for b in bs:
        assert len(b) == 1 or max(lengths[i] for i in b) * len(b) <= 1000
    assert [3] in bs  # 예산보다 긴 텍스트는 혼자


def test_query_prefix_only_on_queries() -> None:
    assert B.query_text("식품위생법 제44조") == "query: 식품위생법 제44조"


def test_cache_dir_per_model_keeps_bge_cache_name() -> None:
    assert B.cache_dir().name == "snowflake-arctic-embed-l-v2.0-ko-55ec6e9358a5"
    assert B.cache_dir("BAAI/bge-m3", "5617a9f61b028005a4858fdac845db406aefb181").name == "bge-m3-5617a9f61b02"
