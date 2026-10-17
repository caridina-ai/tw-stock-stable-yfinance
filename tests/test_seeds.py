"""The seed table reads result files as they are and finds every seed's file."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from twstable.portfolio import StrategyParams
from twstable.seeds import DEFAULT_SEED, fold_choices, path_for, summarise, to_markdown


def _write(path: Path, crash: float, m: int) -> None:
    idx = pd.bdate_range("2006-01-02", "2012-12-31")
    eq = np.exp(np.linspace(0, 0.6, len(idx)))
    hit = (idx >= "2008-06-02") & (idx < "2008-12-01")
    eq[hit] *= 1 + crash  # a dip inside fold 2's window, recovered after it
    p = StrategyParams(max_m=m, rebalance_day=11, cap_weight=True).to_dict()
    payload = {
        "dates": [d.strftime("%Y-%m-%d") for d in idx],
        "equity": eq.tolist(),
        "benchmark": np.exp(np.cumsum(np.random.default_rng(1).normal(2e-4, 0.01, len(idx)))).tolist(),
        "wfcv": {"production_params": p, "folds": [
            {"index": 1, "test_start": "2006-01-02", "params": p,
             "oos_metrics": {"cagr": 0.1, "max_drawdown": -0.2}},
        ]},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), "utf-8")


def test_every_seed_file_is_read_and_the_crisis_window_is_measured(tmp_path) -> None:
    _write(path_for("nested", DEFAULT_SEED, tmp_path), -0.30, 7)
    _write(path_for("nested", 3, tmp_path), -0.10, 9)
    _write(path_for("interval", 1, tmp_path), -0.20, 15)
    df = summarise(tmp_path)
    assert sorted(zip(df.variant, df.seed, strict=True)) == [("interval", 1), ("nested", 3), ("nested", DEFAULT_SEED)]
    row = df.set_index(["variant", "seed"]).loc[("nested", DEFAULT_SEED)]
    assert row.dd_2008 == pytest.approx(-0.30, abs=0.01)
    assert row.prod_m == 7 and row.prod_day == 11 and row.prod_cap
    choices = fold_choices(path_for("nested", DEFAULT_SEED, tmp_path))
    assert list(choices["fold"]) == [1, "上線"]
    md = to_markdown(df, choices)
    assert "**正式**" in md and "| nested | 中位數 |" in md
