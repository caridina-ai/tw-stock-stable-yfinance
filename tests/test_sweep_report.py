"""The sweep report: statistics on known inputs, and an end-to-end build."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from twstable.sweep import build_tasks
from twstable.sweep_report import (
    _kendall_w,
    _run_around,
    _span,
    build_report,
    size_threshold,
    smooth,
)


def test_kendall_w_is_one_for_identical_rankings_and_small_for_noise() -> None:
    same = np.tile(np.arange(28.0), (20, 1))
    assert _kendall_w(same) == pytest.approx(1.0)
    noise = np.random.default_rng(0).normal(size=(20, 28))
    assert _kendall_w(noise) < 0.15


def test_smoothing_wraps_days_but_not_sizes() -> None:
    z = np.zeros((20, 28))
    z[:, 0] = 1.0  # day 1 only
    sm = smooth(z, d_day=2, d_size=1)
    assert sm[5, 27] == pytest.approx(1 / 5)  # day 28 sees day 1 across the month end
    assert sm[5, 13] == 0.0
    assert np.allclose(smooth(np.ones((20, 28))), 1.0)  # edges are averaged, not padded


def test_run_around_wraps_the_month_end() -> None:
    mask = np.zeros(28, dtype=bool)
    mask[[0, 1, 2, 21, 22, 23, 24, 25, 26, 27]] = True  # days 1-3 and 22-28
    run = _run_around(mask, 24)
    assert run == [22, 23, 24, 25, 26, 27, 28, 1, 2, 3]
    assert _span(run) == "22 日到隔月 3 日"
    assert _span([5, 6, 7]) == "5–7 日"
    assert _run_around(np.zeros(28, dtype=bool), 9) == [9]


def test_size_threshold_is_where_the_plateau_starts() -> None:
    med = pd.Series([0.2, 0.5, 0.8, 0.96, 1.0, 0.97, 0.99] + [0.98] * 13, index=range(1, 21))
    t = pd.DataFrame({"sharpe_med": med})
    assert size_threshold(t, 0.95) == 4
    assert size_threshold(t, 0.80) == 3


def fake_grid(seed: int = 0) -> pd.DataFrame:
    """Every task of the real grid, with plausible random metrics."""
    rng = np.random.default_rng(seed)
    rows = []
    years = range(2006, 2027)
    for t in sorted(build_tasks()):
        m = t.max_m or 10
        base = 1.2 * (1 - np.exp(-m / 4)) + (0.08 if t.cap else 0.0)
        row = {
            "protocol": t.protocol, "day": t.day, "holiday": t.holiday, "max_m": t.max_m,
            "cap": t.cap, "weighting": t.weighting,
            "cagr": 0.14 * base + rng.normal(0, 0.005), "sharpe": base + rng.normal(0, 0.04),
            "max_drawdown": -0.5 / (1 + m / 5) + rng.normal(0, 0.01),
            "worst_year": -0.1 + rng.normal(0, 0.02), "avg_held": m * 0.9,
            "invested": 0.95, "turnover": 0.5,
        }
        row.update({f"y{y}": rng.normal(0.12, 0.2) for y in years})
        rows.append(row)
    grid = pd.DataFrame(rows)
    grid.insert(0, "id", np.arange(len(grid)))
    return grid


def test_report_builds_from_the_grid_alone(tmp_path) -> None:
    production = {"score": {"window": 400}, "max_m": 10, "rebalance": 40}
    summary = build_report(fake_grid(), tmp_path, production)

    rec = summary["recommendation"]
    assert 1 <= rec["rebalance_day"] <= 28 and 1 <= rec["max_m"] <= 20
    assert rec["holiday"] in ("next", "prev")
    assert rec["cap_weight"] is True  # the fake cap is worth +0.08 Sharpe
    assert rec["weighting"] == "equal"  # score weighting has no edge in the fake grid
    for name in ("SUMMARY.md", "summary.json", "recommended_params.json", "heatmap_wf.png",
                 "heatmap_prod.png", "heatmap_smoothed.png", "day_profile.png", "sizes.png"):
        assert (tmp_path / name).stat().st_size > 0, name
    params = json.loads((tmp_path / "recommended_params.json").read_text("utf-8"))
    assert params["rebalance"] == 40 and params["max_m"] == rec["max_m"]
    assert "## 結論" in (tmp_path / "SUMMARY.md").read_text("utf-8")
    # random yearly returns carry no day effect
    assert summary["recommendation"]["day_consistency"]["wf"]["p_value"] > 0.01
