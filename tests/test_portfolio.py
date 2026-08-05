"""Backtest engine correctness on hand-built panels."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from twstable.config import FEE_RATE, SELL_TAX_RATE
from twstable.data import Panel
from twstable.metrics import cagr, compute_metrics, drawdown, max_drawdown
from twstable.portfolio import StrategyParams, run_backtest, select_targets
from twstable.score import ScoreParams


def make_panel(prices: dict[str, np.ndarray], n_days: int) -> Panel:
    idx = pd.bdate_range("2005-01-03", periods=n_days)
    close = pd.DataFrame(prices, index=idx)
    dvol = pd.DataFrame(1e9, index=idx, columns=close.columns)
    bench = pd.Series(np.linspace(100, 200, n_days), index=idx)
    return Panel(close=close, dollar_volume=dvol, benchmark=bench)


# --- selection -------------------------------------------------------------


def test_select_targets_takes_top_scores() -> None:
    scores = np.array([0.9, 0.8, 0.7, 0.6, 0.5])
    eligible = np.ones(5, dtype=bool)
    p = StrategyParams(max_m=3, min_score=0.1)
    assert select_targets(scores, eligible, [], p) == [0, 1, 2]


def test_select_targets_respects_min_score() -> None:
    scores = np.array([0.9, 0.05, 0.04, 0.03])
    eligible = np.ones(4, dtype=bool)
    p = StrategyParams(max_m=3, min_score=0.1)
    assert select_targets(scores, eligible, [], p) == [0]


def test_select_targets_skips_ineligible() -> None:
    scores = np.array([0.9, 0.8, 0.7])
    eligible = np.array([False, True, True])
    p = StrategyParams(max_m=2, min_score=0.1)
    assert select_targets(scores, eligible, [], p) == [1, 2]


def test_hysteresis_retains_a_slipping_holding() -> None:
    """Rank 3 of 5 with max_m=2: dropped without hysteresis, kept with it."""
    scores = np.array([0.9, 0.8, 0.7, 0.2, 0.1])
    eligible = np.ones(5, dtype=bool)

    strict = StrategyParams(max_m=2, min_score=0.1, hold_mult=1.0, keep_frac=0.6)
    assert 2 not in select_targets(scores, eligible, [2], strict)

    loose = StrategyParams(max_m=2, min_score=0.1, hold_mult=3.0, keep_frac=0.6)
    assert 2 in select_targets(scores, eligible, [2], loose)


def test_hysteresis_drops_a_collapsed_holding() -> None:
    """Hysteresis must not hold on to something whose score has cratered."""
    scores = np.array([0.9, 0.8, 0.001])
    eligible = np.ones(3, dtype=bool)
    p = StrategyParams(max_m=2, min_score=0.1, hold_mult=5.0, keep_frac=0.6)
    assert 2 not in select_targets(scores, eligible, [2], p)


def test_never_exceeds_max_m() -> None:
    rng = np.random.default_rng(0)
    scores = rng.uniform(0.2, 1.0, 50)
    eligible = np.ones(50, dtype=bool)
    p = StrategyParams(max_m=7, min_score=0.1, hold_mult=4.0)
    held = list(range(20))
    assert len(select_targets(scores, eligible, held, p)) == 7


# --- engine ----------------------------------------------------------------


def test_single_stock_buy_and_hold_matches_price_less_costs() -> None:
    """One eligible stock, so the engine must track it up to entry cost."""
    n = 900
    growth = np.exp(np.linspace(0, 0.5, n))
    panel = make_panel({"0001": 100 * growth}, n)

    p = StrategyParams(
        score=ScoreParams(window=250),
        max_m=1,
        rebalance=20,
        min_score=0.0,
        min_dollar_volume=0.0,
        weight_band=0.35,
    )
    res = run_backtest(panel, p, initial_capital=1_000_000.0)

    first, last = res.equity.index[0], res.equity.index[-1]
    px = panel.close["0001"]
    ideal = px.loc[last] / px.loc[first]
    actual = res.equity.iloc[-1] / res.equity.iloc[0]
    # entry costs one fee; with a single name there is nothing to re-weight
    assert actual == pytest.approx(ideal, rel=0.02)
    assert actual < ideal, "costs must make it strictly worse than the raw stock"


def test_costs_are_charged_on_both_sides() -> None:
    """A forced switch between two names must cost sell tax + both fees."""
    n = 700
    # A rises then stalls; B stalls then rises, so the pick flips mid-way.
    t = np.arange(n)
    a = 100 * np.exp(np.where(t < 450, 0.0012 * t, 0.0012 * 450))
    b = 100 * np.exp(np.where(t < 450, 0.0, 0.0012 * (t - 450)))
    panel = make_panel({"0001": a, "0002": b}, n)

    p = StrategyParams(
        score=ScoreParams(window=250), max_m=1, rebalance=20,
        min_score=0.0, min_dollar_volume=0.0, hold_mult=1.0,
    )
    res = run_backtest(panel, p, initial_capital=1_000_000.0)
    assert res.n_trades >= 2
    assert res.cost_paid > 0
    # every sell pays fee + tax, every buy pays fee: cost must exceed the
    # cheapest possible per-trade rate
    assert res.cost_paid > res.n_trades * 1_000 * FEE_RATE


def test_no_lookahead_trades_execute_after_the_signal() -> None:
    for rb in run_backtest(
        make_panel({"0001": 100 * np.exp(np.linspace(0, 0.4, 800))}, 800),
        StrategyParams(score=ScoreParams(window=250), max_m=1, min_score=0.0,
                       min_dollar_volume=0.0),
        initial_capital=1e6,
    ).rebalances:
        assert rb.trade_date > rb.date


def test_equity_is_positive_and_continuous() -> None:
    rng = np.random.default_rng(3)
    n = 900
    prices = {
        f"{i:04d}": 50 * np.cumprod(1 + rng.normal(0.0004, 0.02, n)) for i in range(12)
    }
    panel = make_panel(prices, n)
    res = run_backtest(panel, StrategyParams(max_m=5, min_dollar_volume=0.0), initial_capital=1e6)
    assert (res.equity > 0).all()
    assert res.equity.notna().all()
    # a 5-name equal-weight book cannot move more than the widest single name
    assert res.equity.pct_change().abs().max() < 0.35


def test_illiquid_and_penny_stocks_are_excluded() -> None:
    n = 900
    good = 100 * np.exp(np.linspace(0, 0.5, n))
    panel = make_panel({"0001": good, "0002": good * 0.01}, n)  # 0002 is a penny stock
    p = StrategyParams(max_m=2, min_score=0.0, min_dollar_volume=0.0, min_price=5.0)
    res = run_backtest(panel, p, initial_capital=1e6)
    held = {h["id"] for rb in res.rebalances for h in rb.holdings}
    assert "0002" not in held

    panel2 = make_panel({"0001": good, "0002": good}, n)
    panel2.dollar_volume["0002"] = 1.0  # illiquid
    res2 = run_backtest(panel2, StrategyParams(max_m=2, min_score=0.0,
                                               min_dollar_volume=1e6), initial_capital=1e6)
    held2 = {h["id"] for rb in res2.rebalances for h in rb.holdings}
    assert "0002" not in held2


def test_rebalance_count_survives_record_false() -> None:
    """Searches run with record=False; anything counting trades must still work.

    `rebalances` is intentionally empty in that mode, so a rebalance count read
    from it would silently be zero for every candidate configuration.
    """
    n = 900
    panel = make_panel({"0001": 100 * np.exp(np.linspace(0, 0.5, n))}, n)
    p = StrategyParams(max_m=1, min_score=0.0, min_dollar_volume=0.0)

    rec = run_backtest(panel, p, record=True, initial_capital=1e6)
    quiet = run_backtest(panel, p, record=False, initial_capital=1e6)

    assert rec.n_rebalances == len(rec.rebalances) > 4
    assert quiet.rebalances == []
    assert quiet.n_rebalances == rec.n_rebalances


def test_weight_band_reduces_trading() -> None:
    rng = np.random.default_rng(7)
    n = 1200
    prices = {f"{i:04d}": 50 * np.cumprod(1 + rng.normal(0.0006, 0.015, n)) for i in range(8)}
    panel = make_panel(prices, n)
    tight = run_backtest(panel, StrategyParams(max_m=4, min_dollar_volume=0.0,
                                               weight_band=0.0), initial_capital=1e6)
    loose = run_backtest(panel, StrategyParams(max_m=4, min_dollar_volume=0.0,
                                               weight_band=0.5), initial_capital=1e6)
    assert loose.n_trades < tight.n_trades


# --- metrics ---------------------------------------------------------------


def test_metrics_on_a_known_curve() -> None:
    idx = pd.bdate_range("2010-01-01", periods=252 * 4)
    eq = pd.Series(np.exp(np.log(1.10) * np.arange(len(idx)) / 252), index=idx)
    assert cagr(eq) == pytest.approx(0.10, abs=0.01)
    assert max_drawdown(eq) == pytest.approx(0.0, abs=1e-12)
    m = compute_metrics(eq)
    assert m.total_return == pytest.approx(1.10**4 - 1, rel=0.02)
    assert m.volatility == pytest.approx(0.0, abs=1e-9)


def test_drawdown_shape() -> None:
    idx = pd.bdate_range("2020-01-01", periods=5)
    eq = pd.Series([100.0, 120.0, 60.0, 90.0, 150.0], index=idx)
    dd = drawdown(eq)
    assert dd.iloc[0] == 0.0
    assert dd.iloc[1] == 0.0
    assert dd.iloc[2] == pytest.approx(-0.5)
    assert dd.iloc[3] == pytest.approx(-0.25)
    assert dd.iloc[4] == 0.0
    assert max_drawdown(eq) == pytest.approx(-0.5)


def test_sell_cost_exceeds_buy_cost() -> None:
    """Taiwan charges a 0.3% transaction tax on the sell side only."""
    assert SELL_TAX_RATE > 0
    assert FEE_RATE + SELL_TAX_RATE > FEE_RATE
