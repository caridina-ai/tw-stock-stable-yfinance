"""The trade-day / book-size sweep on a synthetic market."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from test_wfcv import make_panel

from twstable.portfolio import StrategyParams, prepare_market, run_backtest
from twstable.score import ScoreParams
from twstable.sweep import Setup, Task, build_tasks, evaluate, run_grid, yearly_returns
from twstable.wfcv import chain_equity


@pytest.fixture(scope="module")
def setup() -> Setup:
    market = prepare_market(make_panel(n_days=1600, n_stocks=20, seed=4))
    base = StrategyParams(score=ScoreParams(window=200), max_m=5, rebalance=30,
                          min_score=0.0, min_dollar_volume=0.0)
    d = market.dates
    folds = [(d[400], d[900], base), (d[900], d[1400], base.replace(max_m=8))]
    return Setup(market, folds, base.replace(rebalance=40))


def test_grid_covers_every_combination() -> None:
    tasks = build_tasks()
    assert len(tasks) == len(set(tasks))
    wf = [t for t in tasks if t.protocol == "wf" and t.day]
    assert len(wf) == 28 * 2 * 20 * 2 * 2


def test_reference_row_reproduces_the_walk_forward_curve(setup: Setup) -> None:
    row, curve = evaluate(setup, Task("wf", 0, "next", 0, False))
    runs = [run_backtest(setup.market, p, start=s, end=e) for s, e, p in setup.folds]
    eq = chain_equity([r.equity for r in runs])
    assert row["cagr"] == pytest.approx((eq.iloc[-1] / eq.iloc[0]) ** (
        365.25 / (eq.index[-1] - eq.index[0]).days) - 1)
    assert curve.iloc[-1] / curve.iloc[0] == pytest.approx(
        eq.resample("ME").last().iloc[-1] / eq.resample("ME").last().iloc[0])


def test_overrides_reach_every_fold(setup: Setup) -> None:
    row, _ = evaluate(setup, Task("wf", 11, "prev", 3, False, "score"))
    assert row["avg_held"] <= 3
    # ~25 months of tests -> about one rebalance a month
    months = sum((e - s).days for s, e, _ in setup.folds) / 30.44
    assert abs(row["n_rebalances"] - months) <= 3


def test_yearly_returns_compound_to_the_total() -> None:
    idx = pd.bdate_range("2010-03-01", "2012-06-29")
    eq = pd.Series(np.exp(np.linspace(0, 0.3, len(idx))), index=idx)
    yr = yearly_returns(eq)
    assert list(yr.index) == [2010, 2011, 2012]
    assert np.prod(1 + yr.to_numpy()) == pytest.approx(eq.iloc[-1] / eq.iloc[0])


def test_parallel_grid_matches_serial(setup: Setup) -> None:
    tasks = [Task("prod", d, h, m, c, w) for d in (17, 3) for h in ("next", "prev")
             for m in (2, 6) for c in (False, True) for w in ("equal", "score")]
    grid, curves = run_grid(setup, tasks, workers=1)
    pgrid, pcurves = run_grid(setup, tasks, workers=2)
    pd.testing.assert_frame_equal(grid, pgrid)
    pd.testing.assert_frame_equal(curves, pcurves)
    # ids are row numbers of the sorted grid and key the curves
    assert list(grid["id"]) == list(range(len(tasks)))
    assert grid["day"].is_monotonic_increasing
    assert set(curves["id"]) == set(grid["id"])
