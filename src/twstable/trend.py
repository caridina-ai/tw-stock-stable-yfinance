"""Log-linear trend measurement.

Every "stable uptrend" statistic in this project comes from one primitive: fit

    log(price_t) = a + b * t + e_t

by ordinary least squares over a window, and report the slope together with the
dispersion of the residuals.

Reported units are **ratios**, so that the filter thresholds read naturally:

* ``slope``  = exp(b * 252)  -- the annualised growth factor.  ``1.05`` means
  the fitted line gains 5% per year.
* ``stdev``  = exp(sd(e))    -- the multiplicative residual dispersion.  ``1.2``
  means prices typically sit within about +-20% of the fitted line.

The implementation is built on prefix sums, so the cost of measuring a window is
O(1) regardless of its length and every start position for a given length is
evaluated in one vectorised pass.  That is what makes exhaustive segment mining
over the whole market tractable.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .config import TRADING_DAYS_PER_YEAR

# Windows shorter than this cannot support a meaningful 2-parameter fit.
MIN_WINDOW = 5


@dataclass(frozen=True)
class Prefix:
    """Cumulative sums of a (T, N) log-price matrix, NaN-aware.

    All arrays have T+1 rows so that a window [i, i+L) is ``arr[i + L] - arr[i]``.

    Prefix sums buy O(1) windows at the cost of computing small residual
    quantities as differences of large running totals.  To keep that
    cancellation harmless the log prices are shifted by a per-column constant
    (``offset``) before accumulation: slope, residuals and R^2 are invariant to
    an additive shift in y, and the shift is added back into ``intercept``.

    The offset is each column's **first observed** log price, never a statistic
    of the whole series.  A full-series mean would work just as well
    numerically -- the offset cancels out of every reported quantity -- but it
    would make a future bar an input to a past window's arithmetic, which is
    exactly the kind of path that makes "no look-ahead" impossible to assert
    exactly.  Using the first bar keeps the computation causal, so
    :func:`tests.test_wfcv.test_future_prices_cannot_change_past_decisions`
    can demand bit-identical results rather than approximate ones.
    """

    sum_y: np.ndarray  # sum of (y - offset)
    sum_jy: np.ndarray  # sum of j * (y - offset)   (j = global row index)
    sum_yy: np.ndarray  # sum of (y - offset)^2
    count: np.ndarray  # number of finite observations
    offset: np.ndarray  # (N,) shift removed from y before accumulating

    @property
    def n_rows(self) -> int:
        return self.sum_y.shape[0] - 1


def build_prefix(log_price: np.ndarray) -> Prefix:
    """Prefix sums for a (T,) or (T, N) matrix of log prices (NaN = missing)."""
    y = np.asarray(log_price, dtype="float64")
    if y.ndim == 1:
        y = y[:, None]
    t_len = y.shape[0]

    finite = np.isfinite(y)
    has_any = finite.any(axis=0)
    first_row = np.argmax(finite, axis=0)  # 0 where a column is entirely NaN
    offset = np.where(has_any, y[first_row, np.arange(y.shape[1])], 0.0)
    offset = np.nan_to_num(offset)

    yz = np.where(finite, y - offset, 0.0)
    idx = np.arange(t_len, dtype="float64")[:, None]

    def _cum(arr: np.ndarray) -> np.ndarray:
        out = np.zeros((t_len + 1, arr.shape[1]), dtype="float64")
        np.cumsum(arr, axis=0, out=out[1:])
        return out

    return Prefix(
        sum_y=_cum(yz),
        sum_jy=_cum(idx * yz),
        sum_yy=_cum(yz * yz),
        count=_cum(finite.astype("float64")),
        offset=offset,
    )


@dataclass(frozen=True)
class WindowStats:
    """Vectorised fit results.  Arrays share the shape of the ``starts`` input."""

    beta: np.ndarray  # daily log slope
    sigma: np.ndarray  # residual standard deviation (log space)
    r2: np.ndarray
    intercept: np.ndarray  # fitted log price at the first bar of the window
    valid: np.ndarray  # bool: window fully observed and long enough

    @property
    def slope(self) -> np.ndarray:
        """Annualised growth factor, exp(252 * beta)."""
        return np.exp(self.beta * TRADING_DAYS_PER_YEAR)

    @property
    def stdev(self) -> np.ndarray:
        """Multiplicative residual dispersion, exp(sigma)."""
        return np.exp(self.sigma)


def window_stats(pref: Prefix, starts: np.ndarray, length: int) -> WindowStats:
    """Fit every window ``[s, s+length)`` for the start positions in ``starts``.

    ``starts`` is an integer array of any shape; the returned arrays always have
    shape ``starts.shape + (N,)`` for an N-column prefix -- including N == 1, so
    that result shapes never depend on how many symbols happen to be in a batch.
    """
    starts = np.asarray(starts, dtype="int64")
    flat = starts.reshape(-1)
    length = int(length)

    lo, hi = pref.sum_y[flat], pref.sum_y[flat + length]
    s_y = hi - lo
    s_jy = pref.sum_jy[flat + length] - pref.sum_jy[flat]
    s_yy = pref.sum_yy[flat + length] - pref.sum_yy[flat]
    n_obs = pref.count[flat + length] - pref.count[flat]

    # Shift the global index j to a window-local x = j - start, then centre it.
    s_xy_raw = s_jy - flat[:, None].astype("float64") * s_y
    x_bar = (length - 1) / 2.0
    s_xy = s_xy_raw - x_bar * s_y

    # sum (x - x_bar)^2 for x = 0..L-1
    s_xx = length * (length * length - 1) / 12.0

    with np.errstate(divide="ignore", invalid="ignore"):
        beta = s_xy / s_xx
        s_yy_c = s_yy - s_y * s_y / length  # total sum of squares
        sse = np.maximum(s_yy_c - s_xy * s_xy / s_xx, 0.0)
        dof = max(length - 2, 1)
        sigma = np.sqrt(sse / dof)
        r2 = np.where(s_yy_c > 0, 1.0 - sse / s_yy_c, 0.0)
        # y_hat at x = 0, restored to the original (un-shifted) log-price scale
        intercept = s_y / length - beta * x_bar + pref.offset

    valid = (n_obs >= length - 0.5) & (length >= MIN_WINDOW) & np.isfinite(beta)

    def _shape(arr: np.ndarray) -> np.ndarray:
        return arr.reshape(starts.shape + (arr.shape[-1],))

    return WindowStats(
        beta=_shape(beta),
        sigma=_shape(sigma),
        r2=_shape(np.nan_to_num(r2)),
        intercept=_shape(intercept),
        valid=_shape(valid),
    )


def scan_length(pref: Prefix, length: int, stride: int = 1) -> tuple[np.ndarray, WindowStats]:
    """Fit every window of ``length`` (subsampled by ``stride``).

    Returns ``(starts, stats)``.
    """
    last_start = pref.n_rows - length
    if last_start < 0:
        return np.empty(0, dtype="int64"), window_stats(pref, np.empty(0, dtype="int64"), max(length, MIN_WINDOW))
    starts = np.arange(0, last_start + 1, stride, dtype="int64")
    return starts, window_stats(pref, starts, length)


def rolling_stats(pref: Prefix, ends: np.ndarray, length: int) -> WindowStats:
    """Fit the trailing ``length``-bar window that *ends* on each index in ``ends``.

    ``ends`` are inclusive row indices; the window is ``[end - length + 1, end]``.
    Windows that would start before row 0 are marked invalid.
    """
    ends = np.asarray(ends, dtype="int64")
    length = int(length)
    starts = ends - length + 1
    max_start = pref.n_rows - length

    if max_start < 0:
        # The window is longer than the whole history: nothing can be fitted.
        shape = starts.shape + (pref.sum_y.shape[1],)
        zeros = np.zeros(shape, dtype="float64")
        return WindowStats(
            beta=zeros,
            sigma=zeros.copy(),
            r2=zeros.copy(),
            intercept=zeros.copy(),
            valid=np.zeros(shape, dtype=bool),
        )

    safe = np.clip(starts, 0, max_start)
    stats = window_stats(pref, safe, length)
    mask = ((starts >= 0) & (starts <= max_start))[..., None]
    return WindowStats(
        beta=stats.beta,
        sigma=stats.sigma,
        r2=stats.r2,
        intercept=stats.intercept,
        valid=stats.valid & mask,
    )


# ---------------------------------------------------------------------------
# Single-segment helpers (used by the viewer and for reporting)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Fit:
    """Scalar fit over one price segment."""

    beta: float
    sigma: float
    r2: float
    intercept: float
    length: int

    @property
    def slope(self) -> float:
        return float(np.exp(self.beta * TRADING_DAYS_PER_YEAR))

    @property
    def stdev(self) -> float:
        return float(np.exp(self.sigma))

    def fitted_log(self, x: np.ndarray | None = None) -> np.ndarray:
        """Fitted log price over window-local positions ``x`` (default 0..L-1)."""
        if x is None:
            x = np.arange(self.length, dtype="float64")
        return self.intercept + self.beta * np.asarray(x, dtype="float64")

    def fitted_price(self, x: np.ndarray | None = None) -> np.ndarray:
        """Fitted price -- a straight line on a log axis, an exponential on linear."""
        return np.exp(self.fitted_log(x))


def fit_segment(prices: np.ndarray) -> Fit:
    """Least-squares log-linear fit of a single price series."""
    p = np.asarray(prices, dtype="float64")
    y = np.log(p)
    n = len(y)
    if n < MIN_WINDOW:
        raise ValueError(f"segment too short: {n}")
    pref = build_prefix(y)
    st = window_stats(pref, np.array([0]), n)
    return Fit(
        beta=float(st.beta[0, 0]),
        sigma=float(st.sigma[0, 0]),
        r2=float(st.r2[0, 0]),
        intercept=float(st.intercept[0, 0]),
        length=n,
    )


def annualised_slope(beta: float | np.ndarray) -> float | np.ndarray:
    return np.exp(np.asarray(beta) * TRADING_DAYS_PER_YEAR)


def log_slope_from_annual(slope: float | np.ndarray) -> float | np.ndarray:
    """Inverse of :func:`annualised_slope`."""
    return np.log(np.asarray(slope, dtype="float64")) / TRADING_DAYS_PER_YEAR
