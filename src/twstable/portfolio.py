"""Portfolio construction and the backtest engine.

Strategy
--------
Every ``rebalance`` trading days -- or on a fixed day of every month, see
``rebalance_day`` -- score the whole market with :mod:`twstable.score`, keep
the eligible names with the highest scores, and hold them equally weighted.

Two details matter more than they look:

*No look-ahead.*  Scores at bar ``t`` use only closes up to and including ``t``,
and the resulting trades execute at the close of ``t+1``.

*Hysteresis.*  A name already held is retained while it stays inside a widened
rank band (``max_m * hold_mult``) rather than being dropped the moment it falls
out of the top ``max_m``.  Without it the portfolio churns on score noise and
the transaction tax eats the edge -- which matters a lot in Taiwan, where the
sell side alone costs 0.3% in tax on top of fees.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from typing import Any

import numpy as np
import pandas as pd

from .config import FEE_RATE, SELL_TAX_RATE
from .data import Panel
from .score import ScoreParams, ScoreResult, compute_scores
from .trend import build_prefix


@dataclass(frozen=True)
class StrategyParams:
    """Everything the strategy needs, including the score's own hyperparameters."""

    score: ScoreParams = field(default_factory=ScoreParams)

    max_m: int = 20
    """Maximum simultaneous holdings."""

    rebalance: int = 20
    """Trading days between re-selections (20 ~ one month)."""

    rebalance_day: int = 0
    """Trade on this calendar day of every month (1-28) instead.

    0 keeps the fixed ``rebalance`` interval.  The day is the *trade* date: the
    selection uses the close of the trading day before it, exactly like the
    interval schedule, so "day 11" means pick on the evening of the 10th and
    trade on the 11th.
    """

    holiday: str = "next"
    """What to do when ``rebalance_day`` is not a trading day.

    ``"next"`` trades on the first trading day after it (延後), ``"prev"`` on
    the last one before it (提前).
    """

    min_score: float = 0.15
    """A stock must score at least this to be bought."""

    hold_mult: float = 2.0
    """Hysteresis band: keep a holding while its rank <= max_m * hold_mult."""

    keep_frac: float = 0.6
    """Keep a holding while its score >= min_score * keep_frac."""

    weight_band: float = 0.35
    """No-trade band around the equal weight, as a fraction of it.

    Re-equalising every position at every rebalance would churn the book even on
    dates when the selection did not change at all -- pure cost.  A position is
    only trimmed or topped up once it drifts outside +-``weight_band`` of its
    target, which also lets winners run a little.
    """

    min_dollar_volume: float = 2.0e7
    """Average daily turnover floor over the lookback, in TWD."""

    min_price: float = 5.0
    """Penny-stock floor, in TWD."""

    cap_weight: bool = False
    """Cap every position at ``1 / max_m`` of the book and keep the rest in cash.

    Off, the book is split evenly over whatever qualified, so a month in which
    only one name clears ``min_score`` puts the whole account in that name --
    which the 2008 fold of the walk-forward run did for four rebalances in a row.
    """

    weighting: str = "equal"
    """How the invested part of the book is split over the holdings.

    ``"equal"`` gives every name the same share, so an unchanged selection
    trades nothing.  ``"score"`` makes each share proportional to the name's
    score, so a holding whose score fades is trimmed gradually instead of being
    held at full size until the hysteresis band finally drops it.
    """

    def replace(self, **kw: Any) -> StrategyParams:
        return replace(self, **kw)

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    @staticmethod
    def from_dict(d: dict) -> StrategyParams:
        d = dict(d)
        sp = d.pop("score", {})
        return StrategyParams(score=ScoreParams(**sp), **d)


# ---------------------------------------------------------------------------
# Backtest
# ---------------------------------------------------------------------------


@dataclass
class Market:
    """Panel data pre-converted to the arrays the engine actually uses.

    The log-price prefix sums and the dollar-volume cumsum span the whole panel
    and do not depend on the strategy parameters.  Rebuilding them per run used
    to dominate a backtest (about 0.9s of every 1.1s), so they are built once
    here and shared across every evaluation in a hyperparameter search.
    """

    dates: pd.DatetimeIndex
    close: np.ndarray
    log_px: np.ndarray
    pref: Any  # trend.Prefix
    dvol_cum: np.ndarray  # (T+1, N) cumulative dollar volume, NaN counted as 0
    stock_ids: list[str]
    benchmark: pd.Series


def prepare_market(panel: Panel) -> Market:
    close_df = panel.filled_close()
    close = close_df.to_numpy(dtype="float64")
    with np.errstate(divide="ignore", invalid="ignore"):
        log_px = np.log(close)

    dvol = np.nan_to_num(panel.dollar_volume.to_numpy(dtype="float64"), nan=0.0)
    dvol_cum = np.zeros((dvol.shape[0] + 1, dvol.shape[1]), dtype="float64")
    np.cumsum(dvol, axis=0, out=dvol_cum[1:])

    return Market(
        dates=pd.DatetimeIndex(close_df.index),
        close=close,
        log_px=log_px,
        pref=build_prefix(log_px),
        dvol_cum=dvol_cum,
        stock_ids=list(panel.stock_ids),
        benchmark=panel.benchmark,
    )


@dataclass
class Rebalance:
    """One re-selection event, kept for the viewer."""

    date: pd.Timestamp
    trade_date: pd.Timestamp
    equity: float
    holdings: list[dict]
    bought: list[str]
    sold: list[str]
    turnover: float
    n_candidates: int


@dataclass
class BacktestResult:
    equity: pd.Series
    benchmark: pd.Series
    rebalances: list[Rebalance]
    params: StrategyParams
    cost_paid: float
    n_trades: int
    n_rebalances: int = 0
    """Number of rebalance events.

    Always populated, unlike ``rebalances``, which is only filled when the run
    was made with ``record=True``.  Hyperparameter searches run with recording
    off, so anything inspecting how often the strategy traded must use this.
    """

    @property
    def dates(self) -> pd.DatetimeIndex:
        return pd.DatetimeIndex(self.equity.index)


def _trailing_mean_dollar_volume(
    dvol_cum: np.ndarray, ends: np.ndarray, window: int
) -> np.ndarray:
    """Average daily turnover over the trailing window, from a prepared cumsum.

    The cumsum spans the whole panel and does not depend on the strategy, so it
    lives on :class:`Market`; rebuilding it per backtest dominated the runtime of
    hyperparameter searches.
    """
    starts = np.clip(ends - window + 1, 0, None)
    span = (ends - starts + 1).astype("float64")[:, None]
    return (dvol_cum[ends + 1] - dvol_cum[starts]) / np.maximum(span, 1.0)


def signal_schedule(
    dates: pd.DatetimeIndex, first: int, hi: int, params: StrategyParams
) -> np.ndarray:
    """Row indices of the decision bars; each one trades on the bar after it.

    Every index is in ``[first, hi)`` so the trade bar is never past ``hi``.
    """
    if not params.rebalance_day:
        return np.arange(first, hi, params.rebalance, dtype="int64")

    day = int(params.rebalance_day)
    if not 1 <= day <= 28:
        raise ValueError(f"rebalance_day must be 1..28, got {day}")
    if params.holiday not in ("next", "prev"):
        raise ValueError(f"holiday must be 'next' or 'prev', got {params.holiday!r}")

    # one month past the end: with "prev", next month's day 1 can fall back into range
    months = pd.period_range(dates[first], dates[hi] + pd.DateOffset(months=1), freq="M")
    targets = pd.DatetimeIndex([pd.Timestamp(m.year, m.month, day) for m in months])
    if params.holiday == "next":
        trade = dates.searchsorted(targets, side="left")  # first bar on or after
    else:
        # past the last bar there is no telling which day was the last one before it
        targets = targets[targets <= dates[-1]]
        trade = dates.searchsorted(targets, side="right") - 1  # last bar on or before
    signal = np.unique(np.asarray(trade, dtype="int64")) - 1
    return signal[(signal >= first) & (signal < hi)]


def select_targets(
    scores: np.ndarray,
    eligible: np.ndarray,
    held: list[int],
    params: StrategyParams,
) -> list[int]:
    """Pick the holdings for one rebalance, applying the hysteresis band."""
    n_stocks = len(scores)
    order = np.argsort(-np.where(eligible, scores, -np.inf))
    rank = np.empty(n_stocks, dtype="int64")
    rank[order] = np.arange(n_stocks)

    keep_band = int(round(params.max_m * params.hold_mult))
    keep_floor = params.min_score * params.keep_frac

    kept = [
        i
        for i in held
        if eligible[i] and scores[i] >= keep_floor and rank[i] < keep_band
    ]
    kept.sort(key=lambda i: -scores[i])
    kept = kept[: params.max_m]

    chosen = list(kept)
    held_set = set(chosen)
    for i in order:
        if len(chosen) >= params.max_m:
            break
        if not eligible[i] or scores[i] < params.min_score or i in held_set:
            continue
        chosen.append(int(i))
        held_set.add(int(i))
    return chosen


def target_values(
    equity: float, targets: list[int], scores: np.ndarray, params: StrategyParams
) -> dict[int, float]:
    """Money each selected name should hold after this rebalance."""
    slots = max(len(targets), params.max_m) if params.cap_weight else len(targets)
    if params.weighting == "equal":
        return {i: equity / slots for i in targets}
    if params.weighting != "score":
        raise ValueError(f"weighting must be 'equal' or 'score', got {params.weighting!r}")
    s = np.array([max(float(scores[i]), 0.0) for i in targets])
    share = s / s.sum() if s.sum() > 0 else np.full(len(targets), 1.0 / len(targets))
    invested = equity * len(targets) / slots
    return {i: invested * float(w) for i, w in zip(targets, share, strict=True)}


@dataclass
class Decisions:
    """Everything the strategy decides from, before any money moves.

    ``eligible`` is the liquidity / price / valid-window screen at each signal
    bar; the engine additionally requires a price on the trade bar itself.
    """

    lo: int
    hi: int
    first: int
    signal_days: np.ndarray
    scores: ScoreResult
    eligible: np.ndarray  # (n_signals, n_stocks)


def plan_decisions(
    market: Market,
    params: StrategyParams,
    start: str | pd.Timestamp | None = None,
    end: str | pd.Timestamp | None = None,
) -> Decisions:
    """Signal bars, scores and eligibility of a run, exactly as the engine sees them."""
    dates = market.dates
    close = market.close

    lo = 0 if start is None else int(dates.searchsorted(pd.Timestamp(start)))
    hi = len(dates) - 1 if end is None else int(dates.searchsorted(pd.Timestamp(end)))
    hi = min(hi, len(dates) - 1)

    # Need a full lookback before the first decision, and one spare bar to trade on.
    first = max(lo, params.score.window + 1)
    if first >= hi:
        raise ValueError("not enough history in the requested range")

    signal_days = signal_schedule(dates, first, hi, params)
    if not len(signal_days):
        raise ValueError("no rebalance dates in the requested range")

    res: ScoreResult = compute_scores(market.pref, market.log_px, signal_days, params.score)
    liquidity = _trailing_mean_dollar_volume(market.dvol_cum, signal_days, params.score.window)

    price_ok = close[signal_days] >= params.min_price
    eligible = res.valid & price_ok & (liquidity >= params.min_dollar_volume)
    eligible &= np.isfinite(close[signal_days])
    return Decisions(lo, hi, first, signal_days, res, eligible)


def run_backtest(
    market: Market | Panel,
    params: StrategyParams,
    start: str | pd.Timestamp | None = None,
    end: str | pd.Timestamp | None = None,
    initial_capital: float = 1_000_000.0,
    record: bool = True,
) -> BacktestResult:
    """Simulate the strategy day by day.

    Accepts either a :class:`Panel` or an already-prepared :class:`Market`; pass
    the latter when running many backtests so the prefix sums are built once.
    """
    if isinstance(market, Panel):
        market = prepare_market(market)
    dates = market.dates
    close = market.close
    stock_ids = market.stock_ids

    dec = plan_decisions(market, params, start, end)
    lo, hi, first = dec.lo, dec.hi, dec.first
    signal_days, res, eligible_all = dec.signal_days, dec.scores, dec.eligible

    # --- simulation ------------------------------------------------------
    cash = float(initial_capital)
    shares = np.zeros(len(stock_ids), dtype="float64")
    held: list[int] = []

    equity_vals = np.full(hi - lo + 1, np.nan, dtype="float64")
    rebalances: list[Rebalance] = []
    cost_paid = 0.0
    n_trades = 0
    n_rebalances = 0

    last_price = np.where(np.isfinite(close[first]), close[first], 0.0)
    trade_at = {int(d) + 1: k for k, d in enumerate(signal_days)}

    for t in range(first, hi + 1):
        px = close[t]
        good = np.isfinite(px)
        last_price = np.where(good, px, last_price)

        if t in trade_at:
            k = trade_at[t]
            sig = int(signal_days[k])
            scores = res.score[k]
            eligible = eligible_all[k] & np.isfinite(px)

            equity_before = cash + float(shares @ last_price)
            targets = select_targets(scores, eligible, held, params)

            # value everything, then rebuild to the target weights
            target_set = set(targets)
            sold_ids, bought_ids = [], []
            traded_value = 0.0

            for i in np.flatnonzero(shares > 0):
                if int(i) not in target_set:
                    proceeds = shares[i] * last_price[i]
                    cost = proceeds * (FEE_RATE + SELL_TAX_RATE)
                    cash += proceeds - cost
                    cost_paid += cost
                    traded_value += proceeds
                    n_trades += 1
                    sold_ids.append(stock_ids[i])
                    shares[i] = 0.0

            equity_now = cash + float(shares @ last_price)
            if targets:
                target_value = target_values(equity_now, targets, scores, params)
                band = params.weight_band

                # trim overweights first so the cash is available for top-ups
                for i in targets:
                    cur = shares[i] * last_price[i]
                    if cur > target_value[i] * (1 + band) and last_price[i] > 0:
                        delta = cur - target_value[i]
                        cost = delta * (FEE_RATE + SELL_TAX_RATE)
                        cash += delta - cost
                        cost_paid += cost
                        traded_value += delta
                        n_trades += 1
                        shares[i] = target_value[i] / last_price[i]

                # top up anything below the band; brand-new names are at 0 and
                # therefore always bought
                for i in targets:
                    cur = shares[i] * last_price[i]
                    if cur >= target_value[i] * (1 - band) or last_price[i] <= 0:
                        continue
                    delta = min(target_value[i] - cur, cash / (1 + FEE_RATE))
                    if delta <= 0:
                        continue
                    cost = delta * FEE_RATE
                    cash -= delta + cost
                    cost_paid += cost
                    traded_value += delta
                    n_trades += 1
                    if shares[i] == 0:
                        bought_ids.append(stock_ids[i])
                    shares[i] += delta / last_price[i]

            held = [int(i) for i in targets]
            n_rebalances += 1

            if record:
                equity_after = cash + float(shares @ last_price)
                rebalances.append(
                    Rebalance(
                        date=dates[sig],
                        trade_date=dates[t],
                        equity=equity_after,
                        holdings=[
                            {
                                "id": stock_ids[i],
                                "score": float(res.score[k, i]),
                                "trend": float(res.trend[k, i]),
                                "stability": float(res.stability[k, i]),
                                "consistency": float(res.consistency[k, i]),
                                "restraint": float(res.restraint[k, i]),
                                "slope": float(res.slope[k, i]),
                                "stdev": float(res.stdev[k, i]),
                                "r2": float(res.r2[k, i]),
                                "stretch": float(res.stretch[k, i]),
                                "weight": float(shares[i] * last_price[i] / max(equity_after, 1e-9)),
                            }
                            for i in targets
                        ],
                        bought=bought_ids,
                        sold=sold_ids,
                        turnover=float(traded_value / max(equity_before, 1e-9)),
                        n_candidates=int(
                            (eligible & (scores >= params.min_score)).sum()
                        ),
                    )
                )

        equity_vals[t - lo] = cash + float(shares @ last_price)

    equity = pd.Series(equity_vals, index=dates[lo : hi + 1], name="equity").dropna()
    bench = market.benchmark.reindex(equity.index).ffill()
    bench = bench / bench.iloc[0] * float(equity.iloc[0]) if bench.notna().any() else bench

    return BacktestResult(
        equity=equity,
        benchmark=bench,
        rebalances=rebalances,
        params=params,
        cost_paid=cost_paid,
        n_trades=n_trades,
        n_rebalances=n_rebalances,
    )
