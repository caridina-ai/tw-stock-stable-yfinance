"""The long-term stable-uptrend score.

The spec asks for a score that avoids three specific failure modes:

1. **Performance chasing** -- picking whatever just went up a lot.
2. **Unsustainably steep trends** -- a huge slope hints it cannot last.
3. **Dead stocks** -- tiny dispersion but essentially zero slope.

So the score is *not* monotone in return.  It is a product of four terms, each
in (0, 1], so the total is in (0, 1] and every term can veto a stock on its own:

``score = trend x stability x consistency x restraint``

trend
    An inverted U in log growth, peaking at ``g_opt`` per year.  Both "barely
    moving" and "up 300% annualised" score near zero, which is what separates
    this from a momentum score.  This is the term that answers (2) and (3).

stability
    Decreasing in the residual dispersion of the log-linear fit: the tighter the
    price hugs its own trend line, the better.  This is the "stable" in stable
    uptrend.

consistency
    The lookback is cut into ``n_sub`` consecutive sub-periods, each fitted
    separately.  A stock that rose in one burst and drifted the rest of the time
    scores badly here even if its full-window slope looks fine.  This is the
    main structural defence against (1).

restraint
    Penalises price sitting far *above* its own fitted line right now, measured
    in residual sigmas.  Buying a name that has just spiked away from its trend
    is precisely the chase we want to avoid, so a stretched stock is marked down
    even when its trend is otherwise excellent.  This is the timing defence
    against (1).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

import numpy as np

from .trend import Prefix, rolling_stats


@dataclass(frozen=True)
class ScoreParams:
    """Hyperparameters of the score.  All are optimised by walk-forward CV."""

    window: int = 250
    """Lookback in trading days used for the main log-linear fit."""

    g_opt: float = 1.30
    """Annualised growth factor the trend term peaks at (1.30 = +30%/yr)."""

    g_width: float = 1.15
    """Width of the inverted U, as a growth-factor ratio.

    This has to be narrow to do its job.  At ``g_opt=1.30`` a width of 1.15
    scores a flat stock (+0%/yr) at ~0.17 and a +400%/yr rocket at ~0, while
    still giving anything between roughly +12% and +55% a year a decent mark.
    Widen it and the term stops discriminating at all.
    """

    sigma0: float = 0.11
    """Residual-dispersion scale (log units).  0.11 ~ prices within about +-11%."""

    n_sub: int = 3
    """Number of sub-periods used by the consistency term."""

    sub_scale: float = 0.06
    """Softness of the per-sub-period "is it rising" test, in log growth."""

    z0: float = 0.8
    """Stretch (in residual sigmas) tolerated before the restraint term bites."""

    z_width: float = 1.6
    """How fast restraint decays once stretch exceeds ``z0``."""

    def replace(self, **kw: Any) -> ScoreParams:
        return replace(self, **kw)


@dataclass
class ScoreResult:
    """Score plus the components, kept for diagnostics and reporting."""

    score: np.ndarray  # (n_dates, n_stocks)
    trend: np.ndarray
    stability: np.ndarray
    consistency: np.ndarray
    restraint: np.ndarray
    slope: np.ndarray  # annualised growth factor of the main fit
    stdev: np.ndarray  # multiplicative residual dispersion
    r2: np.ndarray
    stretch: np.ndarray  # (price - fit) / sigma, in sigmas
    valid: np.ndarray


def _gaussian(x: np.ndarray, width: float) -> np.ndarray:
    return np.exp(-0.5 * np.square(x / max(width, 1e-9)))


def compute_scores(
    pref: Prefix,
    log_px: np.ndarray,
    ends: np.ndarray,
    params: ScoreParams,
) -> ScoreResult:
    """Score every stock at every row index in ``ends``.

    ``pref`` must be the prefix of ``log_px`` (dates x stocks).  Returns arrays
    shaped ``(len(ends), n_stocks)``.
    """
    ends = np.asarray(ends, dtype="int64")
    win = int(params.window)

    main = rolling_stats(pref, ends, win)
    slope, stdev, sigma = main.slope, main.stdev, main.sigma

    # --- trend: inverted U in log growth -----------------------------------
    with np.errstate(divide="ignore", invalid="ignore"):
        log_g = np.log(np.maximum(slope, 1e-12))
    trend = _gaussian(log_g - np.log(params.g_opt), np.log(max(params.g_width, 1.0001)))

    # --- stability: tight around the fitted line ---------------------------
    stability = 1.0 / (1.0 + np.square(sigma / max(params.sigma0, 1e-6)))

    # --- consistency: each sub-period must rise on its own -----------------
    n_sub = max(int(params.n_sub), 1)
    sub_len = max(win // n_sub, 5)
    consistency = np.ones_like(slope)
    if n_sub > 1:
        acc = np.zeros_like(slope)
        for k in range(n_sub):
            # sub-period k ends this many bars before the evaluation date
            offset = (n_sub - 1 - k) * sub_len
            sub = rolling_stats(pref, ends - offset, sub_len)
            with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
                sub_log_g = np.log(np.maximum(sub.slope, 1e-12))
                # a collapsing sub-period overflows the exponent; that saturates
                # to 0.0, which is exactly the verdict we want
                soft = 1.0 / (1.0 + np.exp(-sub_log_g / max(params.sub_scale, 1e-6)))
            acc += np.where(sub.valid, soft, 0.0)
        consistency = acc / n_sub

    # --- restraint: how stretched above its own trend is it right now? -----
    fitted_last = main.intercept + main.beta * (win - 1)
    latest = log_px[ends, :]
    with np.errstate(divide="ignore", invalid="ignore"):
        stretch = (latest - fitted_last) / np.maximum(sigma, 1e-9)
    restraint = _gaussian(np.maximum(stretch - params.z0, 0.0), params.z_width)

    valid = main.valid & np.isfinite(slope) & np.isfinite(sigma) & np.isfinite(stretch)

    score = trend * stability * consistency * restraint
    score = np.where(valid, np.nan_to_num(score, nan=0.0), 0.0)

    return ScoreResult(
        score=score,
        trend=np.nan_to_num(trend),
        stability=np.nan_to_num(stability),
        consistency=np.nan_to_num(consistency),
        restraint=np.nan_to_num(restraint),
        slope=slope,
        stdev=stdev,
        r2=main.r2,
        stretch=np.nan_to_num(stretch),
        valid=valid,
    )
