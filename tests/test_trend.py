"""Check the prefix-sum fits against a plain np.polyfit reference."""

from __future__ import annotations

import numpy as np

from twstable.trend import (
    build_prefix,
    fit_segment,
    log_slope_from_annual,
    rolling_stats,
    scan_length,
    window_stats,
)


def reference_fit(y: np.ndarray) -> tuple[float, float, float]:
    """(beta, sigma, r2) via numpy least squares on log prices."""
    n = len(y)
    x = np.arange(n, dtype="float64")
    b, a = np.polyfit(x, y, 1)
    resid = y - (a + b * x)
    sse = float(resid @ resid)
    sigma = np.sqrt(sse / max(n - 2, 1))
    sst = float(((y - y.mean()) ** 2).sum())
    r2 = 1.0 - sse / sst if sst > 0 else 0.0
    return float(b), float(sigma), float(r2)


def test_matches_polyfit_random_windows() -> None:
    """Prefix sums trade a little precision for O(1) windows.

    Tolerances are set well inside what any threshold in this project cares
    about (sigma feeds exp(sigma) cut-offs around 1.1-1.3), not at bit level.
    """
    rng = np.random.default_rng(0)
    t_len = 600
    y = np.log(100 * np.exp(np.cumsum(rng.normal(0.0004, 0.015, t_len))))
    pref = build_prefix(y)

    for length in (10, 37, 60, 250, 599):
        starts = np.arange(0, t_len - length + 1, 13)
        st = window_stats(pref, starts, length)
        for k, s in enumerate(starts):
            b_ref, sg_ref, r2_ref = reference_fit(y[s : s + length])
            assert np.isclose(st.beta[k, 0], b_ref, rtol=1e-8, atol=1e-14)
            assert np.isclose(st.sigma[k, 0], sg_ref, rtol=1e-8, atol=1e-14)
            assert np.isclose(st.r2[k, 0], r2_ref, rtol=1e-7, atol=1e-12)
            assert st.valid[k, 0]


def test_intercept_reconstructs_line() -> None:
    rng = np.random.default_rng(1)
    y = np.log(50 * np.exp(np.cumsum(rng.normal(0.001, 0.01, 300))))
    pref = build_prefix(y)
    st = window_stats(pref, np.array([40]), 120)
    seg = y[40:160]
    x = np.arange(120, dtype="float64")
    b, a = np.polyfit(x, seg, 1)
    assert np.isclose(st.intercept[0, 0], a, rtol=1e-9, atol=1e-12)


def test_exact_exponential_has_zero_sigma() -> None:
    """A pure exponential is a perfect log-linear fit."""
    growth = 1.25  # +25% per year
    beta = float(log_slope_from_annual(growth))
    n = 250
    prices = 10.0 * np.exp(beta * np.arange(n))
    fit = fit_segment(prices)
    assert np.isclose(fit.slope, growth, rtol=1e-9)
    # SSE is a difference of large prefix sums, so a perfect fit lands on the
    # float64 noise floor rather than exactly 0 -- parts per billion of price.
    assert fit.sigma < 1e-7
    assert np.isclose(fit.stdev, 1.0, atol=1e-6)
    assert fit.r2 > 1 - 1e-12
    assert np.allclose(fit.fitted_price(), prices, rtol=1e-9)


def test_nan_windows_are_invalid() -> None:
    y = np.log(np.linspace(10, 20, 200))
    y[50] = np.nan
    pref = build_prefix(y)
    starts, st = scan_length(pref, 30)
    # any window covering index 50 must be rejected
    covering = (starts <= 50) & (starts + 30 > 50)
    assert not st.valid[covering].any()
    assert st.valid[~covering].all()


def test_multi_column_matches_single() -> None:
    rng = np.random.default_rng(2)
    mat = np.log(np.cumprod(1 + rng.normal(0.0005, 0.012, (400, 5)), axis=0) * 30)
    pref_all = build_prefix(mat)
    st_all = window_stats(pref_all, np.array([10, 100]), 90)
    for col in range(5):
        pref_one = build_prefix(mat[:, col])
        st_one = window_stats(pref_one, np.array([10, 100]), 90)
        assert np.allclose(st_all.beta[:, col], st_one.beta[:, 0], rtol=1e-10, atol=1e-14)
        assert np.allclose(st_all.sigma[:, col], st_one.sigma[:, 0], rtol=1e-10, atol=1e-14)


def test_rolling_stats_alignment() -> None:
    rng = np.random.default_rng(3)
    y = np.log(np.cumprod(1 + rng.normal(0.0005, 0.012, 300)) * 30)
    pref = build_prefix(y)
    ends = np.array([59, 120, 299])
    st = rolling_stats(pref, ends, 60)
    for k, e in enumerate(ends):
        b_ref, _, _ = reference_fit(y[e - 59 : e + 1])
        assert np.isclose(st.beta[k, 0], b_ref, rtol=1e-8, atol=1e-14)
        assert st.valid[k, 0]
    # a window that would start before row 0 is invalid
    st_short = rolling_stats(pref, np.array([10]), 60)
    assert not st_short.valid[0, 0]
