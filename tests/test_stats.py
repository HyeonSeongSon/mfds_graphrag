"""eval/stats.py: Wilson(PREREG 4-2 참고값), 양측 정확 McNemar, 부트스트랩 재현성 · 차이 부호 · 비율."""

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "eval"))
import stats as S  # noqa: E402


@pytest.mark.parametrize("k,n,upper", [(0, 50, 0.071), (1, 100, 0.054), (2, 100, 0.070), (3, 100, 0.085),
                                       (4, 100, 0.098), (5, 100, 0.112)])
def test_wilson_matches_prereg_reference(k: int, n: int, upper: float) -> None:
    assert round(S.wilson(k, n)[1], 3) == upper


def test_mcnemar_exact_two_sided() -> None:
    assert S.mcnemar_exact(0, 0) == 1.0
    assert S.mcnemar_exact(0, 6) == pytest.approx(2 / 64)
    assert S.mcnemar_exact(5, 1) == pytest.approx(2 * 7 / 64)
    assert S.mcnemar_exact(3, 3) == 1.0  # 1을 넘지 않음
    assert S.mcnemar_exact(30, 30) == 1.0  # 불일치가 많아도 정확 검정
    assert S.discordant([1, 1, 0, 0], [1, 0, 1, 0]) == (1, 1)


def test_paired_bootstrap_reproducible_and_signed() -> None:
    a = [1] * 60 + [0] * 40
    b = [1] * 40 + [0] * 60
    r1 = S.paired_diff(a, b, 20261031, 2000)
    assert r1 == S.paired_diff(a, b, 20261031, 2000)  # 같은 시드 → 같은 구간
    d, lo, hi = r1
    assert d == pytest.approx(0.2) and lo <= d <= hi and lo > 0  # 앞 − 뒤
    assert S.paired_diff(b, a, 20261031, 2000)[0] == pytest.approx(-0.2)


def test_bootstrap_mean_and_ratio() -> None:
    m, lo, hi = S.bootstrap_mean([0.0, 0.5, 1.0, 1.0], 1, 1000)
    assert m == pytest.approx(0.625) and lo <= m <= hi
    r, lo, hi = S.paired_ratio([1, 1, 0, 1], [1, 1, 1, 1], 1, 1000)
    assert r == pytest.approx(0.75) and lo <= r <= hi
    assert math.isnan(S.paired_ratio([1, 0], [0, 0], 1, 100)[0])  # API 평균 0 → nan
