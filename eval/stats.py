"""PREREG 4-5 "구간 · 검정 방법"의 구현(PLAN B5 `eval/stats.py`). 2주차 D4 저녁 동결 때 해시를 AMENDMENTS에 기록한다.

- 짝 없는 0/1 비율: Wilson 95% 구간(4-2 라벨 검수와 같음)
- 짝 없는 0/1이 아닌 지표(recall@5 · MRR@5 · 인용 정밀도 등): 문항 단위 부트스트랩 백분위 구간
- 짝 차이(같은 문항, 두 구성 · 방식): 문항 단위 짝 부트스트랩 백분위 구간. 차이는 "앞에 적은 쪽 − 뒤에 적은 쪽"
- McNemar: 양측 정확 McNemar(불일치 쌍의 이항 검정, 연속성 보정 · mid-p 없음). 불일치 수와 상관없이 늘 정확 검정
- API 대비 비율(핵심 성과 3, S1 · S2 포함): 로컬 평균 ÷ API 평균, 문항 단위 짝 부트스트랩 백분위 구간

부트스트랩 시드 · 반복 수는 PREREG 측정 정의 값 `BOOTSTRAP_SEED` · `BOOTSTRAP_N`이다(호출마다 같은 시드로 새로 시작하므로
같은 입력은 늘 같은 구간을 낸다). 백분위는 numpy 기본(선형 보간) 2.5 · 97.5다.
"""

import math
from fractions import Fraction

import numpy as np

Z95 = 1.959963984540054
CHUNK = 1000  # 부트스트랩 반복을 이만큼씩 묶어 메모리를 줄인다(결과는 묶음 크기와 무관하게 같음)


def wilson(k: int, n: int, z: float = Z95) -> tuple[float, float]:
    """k/n의 Wilson 구간. n = 0이면 (nan, nan)."""
    if n == 0:
        return math.nan, math.nan
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)


def mcnemar_exact(b: int, c: int) -> float:
    """양측 정확 McNemar p. b · c는 불일치 쌍 수(앞 구성만 맞음 · 뒤 구성만 맞음). 불일치 0이면 1."""
    n = b + c
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, i) for i in range(min(b, c) + 1))
    return float(min(Fraction(1), Fraction(2 * tail, 2 ** n)))


def discordant(a, b) -> tuple[int, int]:
    """짝 0/1 배열 → (a만 맞음, b만 맞음)."""
    a, b = np.asarray(a, dtype=bool), np.asarray(b, dtype=bool)
    return int((a & ~b).sum()), int((~a & b).sum())


def _resample_stats(cols: list[np.ndarray], fn, seed: int, n_boot: int) -> np.ndarray:
    """문항 단위로 같은 인덱스를 다시 뽑아 fn(*열)을 n_boot번 계산."""
    m = len(cols[0])
    rng = np.random.default_rng(seed)
    out = np.empty(n_boot)
    for s in range(0, n_boot, CHUNK):
        k = min(CHUNK, n_boot - s)
        idx = rng.integers(0, m, size=(k, m))
        out[s:s + k] = fn(*[c[idx] for c in cols])
    return out


def _pct(x: np.ndarray) -> tuple[float, float]:
    x = x[~np.isnan(x)]
    if not len(x):
        return math.nan, math.nan
    lo, hi = np.percentile(x, [2.5, 97.5])
    return float(lo), float(hi)


def bootstrap_mean(x, seed: int, n_boot: int) -> tuple[float, float, float]:
    """짝 없는 지표의 평균과 백분위 구간. 빈 입력은 nan."""
    x = np.asarray(x, dtype=float)
    if not len(x):
        return math.nan, math.nan, math.nan
    lo, hi = _pct(_resample_stats([x], lambda v: v.mean(axis=1), seed, n_boot))
    return float(x.mean()), lo, hi


def paired_diff(a, b, seed: int, n_boot: int) -> tuple[float, float, float]:
    """짝 차이 mean(a) − mean(b)와 문항 단위 짝 부트스트랩 백분위 구간."""
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    if len(a) != len(b):
        raise ValueError("짝 길이가 다름")
    if not len(a):
        return math.nan, math.nan, math.nan
    lo, hi = _pct(_resample_stats([a, b], lambda u, v: u.mean(axis=1) - v.mean(axis=1), seed, n_boot))
    return float(a.mean() - b.mean()), lo, hi


def paired_ratio(local, api, seed: int, n_boot: int) -> tuple[float, float, float]:
    """API 대비 비율 mean(local) ÷ mean(api)와 짝 부트스트랩 백분위 구간. 분모가 0인 표본은 구간 계산에서 뺀다."""
    a, b = np.asarray(local, dtype=float), np.asarray(api, dtype=float)
    if len(a) != len(b):
        raise ValueError("짝 길이가 다름")
    if not len(a) or b.mean() == 0:
        return math.nan, math.nan, math.nan

    def ratio(u, v):
        den = v.mean(axis=1)
        with np.errstate(divide="ignore", invalid="ignore"):
            return np.where(den == 0, np.nan, u.mean(axis=1) / den)

    lo, hi = _pct(_resample_stats([a, b], ratio, seed, n_boot))
    return float(a.mean() / b.mean()), lo, hi
