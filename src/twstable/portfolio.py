"""Portfolio construction and the backtest engine.

Strategy
--------
Every ``rebalance`` trading days, score the whole market with
:mod:`twstable.score`, keep the eligible names with the highest scores, and hold
them equally weighted.

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
    log_px = market.log_px
    pref = market.pref
    dvol_cum = market.dvol_cum
    stock_ids = market.stock_ids

    lo = 0 if start is None else int(dates.searchsorted(pd.Timestamp(start)))
    hi = len(dates) - 1 if end is None else int(dates.searchsorted(pd.Timestamp(end)))
    hi = min(hi, len(dates) - 1)

    # Need a full lookback before the first decision, and one spare bar to trade on.
    first = max(lo, params.score.window + 1)
    if first >= hi:
        raise ValueError("not enough history in the requested range")

    signal_days = np.arange(first, hi, params.rebalance, dtype="int64")
    if not len(signal_days):
        raise ValueError("no rebalance dates in the requested range")

    res: ScoreResult = compute_scores(pref, log_px, signal_days, params.score)
    liquidity = _trailing_mean_dollar_volume(dvol_cum, signal_days, params.score.window)

    price_ok = close[signal_days] >= params.min_price
    eligible_all = res.valid & price_ok & (liquidity >= params.min_dollar_volume)
    eligible_all &= np.isfinite(close[signal_days])

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

            # value everything, then rebuild equal-weighted
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
                target_value = equity_now / len(targets)
                band = params.weight_band
                upper, lower = target_value * (1 + band), target_value * (1 - band)

                # trim overweights first so the cash is available for top-ups
                for i in targets:
                    cur = shares[i] * last_price[i]
                    if cur > upper and last_price[i] > 0:
                        delta = cur - target_value
                        cost = delta * (FEE_RATE + SELL_TAX_RATE)
                        cash += delta - cost
                        cost_paid += cost
                        traded_value += delta
                        n_trades += 1
                        shares[i] = target_value / last_price[i]

                # top up anything below the band; brand-new names are at 0 and
                # therefore always bought
                for i in targets:
                    cur = shares[i] * last_price[i]
                    if cur >= lower or last_price[i] <= 0:
                        continue
                    delta = min(target_value - cur, cash / (1 + FEE_RATE))
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
