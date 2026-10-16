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
    assert len(wf) == 28 * 2 * 20 * 2


def test_reference_row_reproduces_the_walk_forward_curve(setup: Setup) -> None:
    row = evaluate(setup, Task("wf", 0, "next", 0, False))
    runs = [run_backtest(setup.market, p, start=s, end=e) for s, e, p in setup.folds]
    eq = chain_equity([r.equity for r in runs])
    assert row["cagr"] == pytest.approx((eq.iloc[-1] / eq.iloc[0]) ** (
        365.25 / (eq.index[-1] - eq.index[0]).days) - 1)


def test_overrides_reach_every_fold(setup: Setup) -> None:
    row = evaluate(setup, Task("wf", 11, "prev", 3, False))
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
    tasks = [Task("prod", d, h, m, c) for d in (3, 17) for h in ("next", "prev")
             for m in (2, 6) for c in (False, True)]
    serial = run_grid(setup, tasks, workers=1)
    parallel = run_grid(setup, tasks, workers=2)
    pd.testing.assert_frame_equal(serial, parallel)
