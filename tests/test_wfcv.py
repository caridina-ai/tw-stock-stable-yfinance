"""Walk-forward fold construction and the no-look-ahead guarantee."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from twstable.data import Panel
from twstable.portfolio import StrategyParams, prepare_market, run_backtest
from twstable.score import ScoreParams
from twstable.wfcv import make_folds, stitch


def make_panel(n_days: int = 3000, n_stocks: int = 25, seed: int = 0) -> Panel:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2005-01-03", periods=n_days)
    drifts = rng.uniform(-0.0002, 0.0009, n_stocks)
    paths = 50 * np.cumprod(1 + rng.normal(drifts, 0.018, (n_days, n_stocks)), axis=0)
    close = pd.DataFrame(paths, index=idx, columns=[f"{i:04d}" for i in range(n_stocks)])
    dvol = pd.DataFrame(1e9, index=idx, columns=close.columns)
    bench = pd.Series(50 * np.cumprod(1 + rng.normal(0.0003, 0.01, n_days)), index=idx)
    return Panel(close=close, dollar_volume=dvol, benchmark=bench)


# --- folds -----------------------------------------------------------------


def test_folds_are_ordered_and_test_slices_do_not_overlap() -> None:
    dates = pd.bdate_range("2005-01-03", periods=5200)
    folds = make_folds(dates, "2006-01-01", None, train_years=6, test_years=2)
    assert len(folds) >= 3
    for f in folds:
        assert f.train_start < f.train_end <= f.test_start < f.test_end
    for a, b in zip(folds, folds[1:], strict=False):
        assert a.test_end <= b.test_start, "test windows must not overlap"
        assert b.train_start > a.train_start, "training window must roll forward"


def test_training_data_always_precedes_its_test_window() -> None:
    dates = pd.bdate_range("2005-01-03", periods=5200)
    for f in make_folds(dates, "2006-01-01", None, 5, 1):
        assert f.train_end <= f.test_start


# --- the important one: no look-ahead --------------------------------------


def test_future_prices_cannot_change_past_decisions() -> None:
    """Rewrite all prices after a cut date; nothing at or before it may move.

    This is the strongest statement of "no look-ahead" available: if any part of
    the engine peeked at a future bar, scrambling the future would change the
    past, and it must not.
    """
    panel = make_panel(n_days=2200, seed=4)
    params = StrategyParams(
        score=ScoreParams(window=250), max_m=5, rebalance=20, min_dollar_volume=0.0
    )

    base = run_backtest(prepare_market(panel), params, start="2008-01-01")

    cut_pos = 1500
    cut_date = panel.close.index[cut_pos]

    rng = np.random.default_rng(99)
    tampered = panel.close.copy()
    shock = rng.uniform(0.2, 5.0, tampered.shape[1])
    tampered.iloc[cut_pos + 1 :] = tampered.iloc[cut_pos + 1 :].to_numpy() * shock
    panel2 = Panel(
        close=tampered, dollar_volume=panel.dollar_volume, benchmark=panel.benchmark
    )
    other = run_backtest(prepare_market(panel2), params, start="2008-01-01")

    # equity up to the cut must be bit-identical, not merely close: every
    # input to a pre-cut decision has to be causal, down to the arithmetic
    a = base.equity[base.equity.index <= cut_date]
    b = other.equity[other.equity.index <= cut_date]
    assert len(a) > 100
    pd.testing.assert_series_equal(a, b, check_exact=True)

    # and so must every selection that traded on or before the cut
    ra = [rb for rb in base.rebalances if rb.trade_date <= cut_date]
    rb_ = [rb for rb in other.rebalances if rb.trade_date <= cut_date]
    assert len(ra) > 5
    assert len(ra) == len(rb_)
    for x, y in zip(ra, rb_, strict=True):
        assert x.date == y.date
        assert [h["id"] for h in x.holdings] == [h["id"] for h in y.holdings]


def test_scores_do_not_use_the_execution_bar() -> None:
    """Decisions are made on bar t and filled on t+1, never the other way round."""
    panel = make_panel(n_days=1500, seed=6)
    res = run_backtest(
        prepare_market(panel),
        StrategyParams(score=ScoreParams(window=250), max_m=4, min_dollar_volume=0.0),
        start="2008-01-01",
    )
    dates = panel.close.index
    for rb in res.rebalances:
        assert dates.get_loc(rb.trade_date) == dates.get_loc(rb.date) + 1


# --- stitching -------------------------------------------------------------


def test_stitch_chains_returns_multiplicatively() -> None:
    from twstable.wfcv import Fold, FoldResult

    def piece(start: str, periods: int, total_growth: float) -> BacktestResultStub:
        idx = pd.bdate_range(start, periods=periods)
        eq = pd.Series(np.linspace(1.0, total_growth, periods), index=idx)
        return BacktestResultStub(eq)

    market = prepare_market(make_panel(n_days=1200, seed=1))
    parts = [
        piece("2009-01-01", 100, 1.5),
        piece("2009-05-22", 100, 2.0),
    ]
    frs = [
        FoldResult(
            fold=Fold(i + 1, *[pd.Timestamp("2009-01-01")] * 4),
            params=StrategyParams(),
            is_sharpe=1.0,
            is_metrics={},
            oos=p,
            oos_metrics={},
            n_evaluated=1,
        )
        for i, p in enumerate(parts)
    ]
    combined = stitch(frs, market)
    # 1.5x then 2.0x compounds to 3.0x overall
    assert combined.equity.iloc[-1] / combined.equity.iloc[0] == pytest.approx(3.0, rel=1e-6)
    assert combined.equity.index.is_monotonic_increasing
    assert not combined.equity.index.has_duplicates


class BacktestResultStub:
    """Minimal stand-in carrying just what `stitch` reads."""

    def __init__(self, equity: pd.Series) -> None:
        self.equity = equity
        self.rebalances: list = []
        self.cost_paid = 0.0
        self.n_trades = 0


def test_pinned_parameters_leave_the_rest_of_the_search_unchanged() -> None:
    from twstable.wfcv import Fold, sample_params, search_fold

    market = prepare_market(make_panel(n_days=1500, seed=2))
    d = market.dates
    fold = Fold(1, d[0], d[1000], d[1000], d[1400])
    fixed = {"rebalance_day": 24, "max_m": 3, "cap_weight": True}

    best, _, res = search_fold(market, fold, 6, np.random.default_rng(5), progress=False,
                               fixed=fixed)
    assert (best.rebalance_day, best.max_m, best.cap_weight) == (24, 3, True)
    assert res.n_rebalances > 20  # monthly, not every `rebalance` bars

    # the winner is one of the draws an unpinned search with this seed makes, pinned
    rng = np.random.default_rng(5)
    drawn = [sample_params(rng).replace(**fixed) for _ in range(6)]
    assert best in drawn
