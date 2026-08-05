"""The score must reward stable uptrends and reject the spec's failure modes."""

from __future__ import annotations

import numpy as np

from twstable.score import ScoreParams, compute_scores
from twstable.trend import build_prefix, log_slope_from_annual

N = 400
PARAMS = ScoreParams(window=250)


def series(annual_growth: float, noise: float, seed: int = 0, n: int = N) -> np.ndarray:
    """A log-linear price path with i.i.d. noise around the trend."""
    rng = np.random.default_rng(seed)
    beta = float(log_slope_from_annual(annual_growth))
    trend = beta * np.arange(n)
    return 20.0 * np.exp(trend + rng.normal(0, noise, n))


def score_of(prices: np.ndarray, params: ScoreParams = PARAMS):
    log_px = np.log(prices).reshape(-1, 1)
    pref = build_prefix(log_px)
    res = compute_scores(pref, log_px, np.array([len(prices) - 1]), params)
    return res


def test_ideal_stable_uptrend_scores_high() -> None:
    res = score_of(series(PARAMS.g_opt, 0.02, seed=1))
    assert res.valid[0, 0]
    assert res.score[0, 0] > 0.5, res.score[0, 0]


def test_flat_stock_is_rejected() -> None:
    """Tiny dispersion but no slope -- the '不動股' case.

    The stability term alone would happily wave this through; the trend term is
    the only thing standing between the strategy and a portfolio of dead money.
    """
    ideal = score_of(series(PARAMS.g_opt, 0.02, seed=1)).score[0, 0]
    flat = score_of(series(1.001, 0.02, seed=1))
    assert flat.stability[0, 0] > 0.5, "a flat stock is still 'stable'"
    assert flat.trend[0, 0] < 0.25, "but the trend term must discount it hard"
    assert flat.score[0, 0] < 0.15 * ideal


def test_unsustainably_steep_trend_is_rejected() -> None:
    """+400%/yr should score far below the target growth rate."""
    ideal = score_of(series(PARAMS.g_opt, 0.02, seed=1)).score[0, 0]
    steep = score_of(series(5.0, 0.02, seed=1))
    assert steep.trend[0, 0] < 0.05
    assert steep.score[0, 0] < 0.05 * ideal


def test_trend_term_is_single_peaked_at_g_opt() -> None:
    """The defining shape: rising then falling, maximised at g_opt."""
    grid = np.array([1.0, 1.05, 1.1, 1.2, 1.3, 1.45, 1.6, 2.0, 3.0, 6.0])
    vals = np.array([score_of(series(g, 0.02, seed=11)).trend[0, 0] for g in grid])
    peak = int(np.argmax(vals))
    assert grid[peak] == PARAMS.g_opt
    assert np.all(np.diff(vals[: peak + 1]) > 0), "must rise up to g_opt"
    assert np.all(np.diff(vals[peak:]) < 0), "must fall after g_opt"


def test_volatile_uptrend_scores_below_smooth_one() -> None:
    smooth = score_of(series(PARAMS.g_opt, 0.02, seed=2)).score[0, 0]
    choppy = score_of(series(PARAMS.g_opt, 0.25, seed=2)).score[0, 0]
    assert choppy < 0.5 * smooth


def test_recent_spike_is_penalised() -> None:
    """Same trend history, but one has just gapped up 40% -- the chase case."""
    base = series(PARAMS.g_opt, 0.03, seed=3)
    spiked = base.copy()
    spiked[-15:] *= 1.40

    calm_res = score_of(base)
    spike_res = score_of(spiked)

    assert spike_res.stretch[0, 0] > calm_res.stretch[0, 0] + 1.0
    assert spike_res.restraint[0, 0] < 0.6 * calm_res.restraint[0, 0]
    assert spike_res.score[0, 0] < calm_res.score[0, 0]


def test_one_burst_then_flat_loses_to_steady() -> None:
    """Identical total return: one steady climb, one single jump then nothing."""
    n = N
    steady = series(1.30, 0.02, seed=4)

    rng = np.random.default_rng(4)
    burst = 20.0 * np.exp(rng.normal(0, 0.02, n))
    burst[n // 2 :] *= steady[-1] / steady[0]  # one step, same end/begin ratio

    s_steady = score_of(steady)
    s_burst = score_of(burst)
    assert s_burst.consistency[0, 0] < s_steady.consistency[0, 0]
    assert s_burst.score[0, 0] < 0.5 * s_steady.score[0, 0]


def test_score_is_bounded_and_components_in_unit_range() -> None:
    for growth, noise in [(1.0, 0.01), (1.3, 0.05), (4.0, 0.3), (0.6, 0.2)]:
        res = score_of(series(growth, noise, seed=9))
        for arr in (res.score, res.trend, res.stability, res.consistency, res.restraint):
            assert np.all(arr >= 0.0) and np.all(arr <= 1.0 + 1e-12)


def test_insufficient_history_is_invalid() -> None:
    prices = series(1.3, 0.02, seed=5, n=100)  # shorter than the 250d window
    res = score_of(prices)
    assert not res.valid[0, 0]
    assert res.score[0, 0] == 0.0
