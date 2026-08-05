"""Headless smoke tests: both GUIs must build and navigate without a display."""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")

import gc  # noqa: E402
import weakref  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.backend_bases import KeyEvent  # noqa: E402

from twstable.viewers import backtest_viewer as bv  # noqa: E402
from twstable.viewers import segment_viewer as sv  # noqa: E402
from twstable.viewers._mplsetup import VIEWER_KEYS, claim_keys, use_cjk_font  # noqa: E402

# the real entry points do this too; without it every Chinese stock name in a
# title emits a "glyph missing from font" warning
use_cjk_font()


class FakeEvent:
    def __init__(self, key: str) -> None:
        self.key = key


def press(fig, key: str) -> None:
    """Send a key through the real canvas callback path, as a GUI would.

    Calling ``viewer.on_key`` directly cannot catch a handler that matplotlib
    has dropped, which is exactly how the viewers once shipped deaf.
    """
    KeyEvent("key_press_event", fig.canvas, key)._process()


# --- segment viewer --------------------------------------------------------


def _ohlc(n: int, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2015-01-05", periods=n)
    close = 30 * np.cumprod(1 + rng.normal(0.0008, 0.012, n))
    high = close * (1 + np.abs(rng.normal(0, 0.006, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.006, n)))
    open_ = close * (1 + rng.normal(0, 0.004, n))
    return pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": 1e6}, index=idx
    )


def _patch_segment_sources(monkeypatch) -> pd.DataFrame:
    """Point the segment viewer at synthetic bars and return a 2-row segment set."""
    bars = _ohlc(400)
    monkeypatch.setattr(sv, "load_prices", lambda ticker: bars)
    monkeypatch.setattr(
        sv,
        "load_universe",
        lambda: pd.DataFrame({"stock_id": ["1101", "2330"], "ticker": ["1101.TW", "2330.TW"]}),
    )
    return pd.DataFrame(
        {
            "ID": ["1101", "2330"],
            "name": ["台泥", "台積電"],
            "begin_date": [bars.index[0], bars.index[50]],
            "end_date": [bars.index[200], bars.index[399]],
            "length": [201, 350],
            "slope": [1.2, 1.3],
            "stdev": [1.05, 1.04],
        }
    )


def test_segment_viewer_navigates_and_toggles(monkeypatch) -> None:
    seg = _patch_segment_sources(monkeypatch)
    viewer = sv.SegmentViewer(seg)
    assert viewer.idx == 0

    viewer.on_key(FakeEvent("down"))
    assert viewer.idx == 1
    viewer.on_key(FakeEvent("down"))  # clamps at the end
    assert viewer.idx == 1
    viewer.on_key(FakeEvent("up"))
    assert viewer.idx == 0
    viewer.on_key(FakeEvent("up"))  # clamps at the start
    assert viewer.idx == 0

    assert viewer.log_scale
    viewer.on_key(FakeEvent("l"))
    assert not viewer.log_scale
    assert viewer.ax.get_yscale() == "linear"
    viewer.on_key(FakeEvent("l"))
    assert viewer.ax.get_yscale() == "log"

    viewer.on_key(FakeEvent("b"))
    assert not viewer.show_band
    viewer.on_key(FakeEvent("end"))
    assert viewer.idx == 1
    viewer.on_key(FakeEvent("home"))
    assert viewer.idx == 0


def test_segment_viewer_keeps_listening_after_caller_drops_it(monkeypatch) -> None:
    """Regression: run() discards the viewer, and mpl_connect holds it weakly.

    Without a strong reference the handler is collected the moment run()
    returns, so the window answers only matplotlib's own shortcuts.
    """
    seg = _patch_segment_sources(monkeypatch)

    def build():
        viewer = sv.SegmentViewer(seg)  # exactly what run() does: value dropped
        return weakref.ref(viewer), viewer.fig

    ref, fig = build()
    gc.collect()
    assert ref() is not None, "viewer was garbage collected; its key handler is dead"

    press(fig, "down")
    assert ref().idx == 1, "key press did not reach the viewer"
    press(fig, "home")
    assert ref().idx == 0


def test_segment_viewer_hint_is_not_restamped_per_redraw(monkeypatch) -> None:
    seg = _patch_segment_sources(monkeypatch)
    viewer = sv.SegmentViewer(seg)
    before = len(viewer.fig.texts)
    for _ in range(5):
        viewer.on_key(FakeEvent("down"))
        viewer.on_key(FakeEvent("up"))
    assert len(viewer.fig.texts) == before


def test_claim_keys_releases_viewer_shortcuts_only() -> None:
    from matplotlib import rcParams

    with matplotlib.rc_context():
        claim_keys()
        for param, keys in rcParams.items():
            if param.startswith("keymap."):
                assert not (set(keys) & VIEWER_KEYS), f"{param} still steals {keys}"
        # untouched shortcuts must survive
        assert "s" in rcParams["keymap.save"]
        assert "g" in rcParams["keymap.grid"]


def test_segment_viewer_survives_missing_prices(monkeypatch) -> None:
    monkeypatch.setattr(sv, "load_prices", lambda ticker: None)
    monkeypatch.setattr(
        sv, "load_universe", lambda: pd.DataFrame({"stock_id": ["9999"], "ticker": ["9999.TW"]})
    )
    seg = pd.DataFrame(
        {
            "ID": ["9999"],
            "name": ["nope"],
            "begin_date": [pd.Timestamp("2020-01-02")],
            "end_date": [pd.Timestamp("2020-06-01")],
            "length": [100],
            "slope": [1.1],
            "stdev": [1.02],
        }
    )
    sv.SegmentViewer(seg)  # must not raise


# --- backtest viewer -------------------------------------------------------


def _result(n: int = 800, n_rb: int = 12) -> dict:
    idx = pd.bdate_range("2015-01-05", periods=n)
    rng = np.random.default_rng(1)
    eq = 1e6 * np.cumprod(1 + rng.normal(0.0006, 0.009, n))
    bench = 1e6 * np.cumprod(1 + rng.normal(0.0003, 0.011, n))
    rb_pos = np.linspace(0, n - 1, n_rb).astype(int)
    return {
        "label": "test",
        "dates": [d.strftime("%Y-%m-%d") for d in idx],
        "equity": list(map(float, eq)),
        "benchmark": list(map(float, bench)),
        "metrics": {
            "cagr": 0.15, "total_return": 1.2, "volatility": 0.14, "sharpe": 1.0,
            "sortino": 1.2, "max_drawdown": -0.2, "calmar": 0.7, "ulcer_index": 0.05,
            "positive_months": 0.65, "best_year": 0.4, "worst_year": -0.1,
        },
        "benchmark_metrics": {"cagr": 0.08, "sharpe": 0.5, "max_drawdown": -0.3},
        "relative": {
            "excess_cagr": 0.07, "alpha_annual": 0.06, "beta": 0.7,
            "information_ratio": 0.4,
        },
        "rebalances": [
            {
                "date": idx[p].strftime("%Y-%m-%d"),
                "trade_date": idx[min(p + 1, n - 1)].strftime("%Y-%m-%d"),
                "equity": float(eq[p]),
                "turnover": 0.3,
                "n_candidates": 120,
                "bought": ["1101"],
                "sold": ["2330"],
                "holdings": [
                    {
                        "id": f"{1000 + j}", "name": f"股{j}", "weight": 0.2,
                        "score": 0.6, "trend": 0.9, "stability": 0.8,
                        "consistency": 0.9, "restraint": 1.0, "slope": 1.3,
                        "stdev": 1.04, "r2": 0.8, "stretch": 0.2,
                    }
                    for j in range(5)
                ],
            }
            for p in rb_pos
        ],
    }


def test_backtest_viewer_navigates_and_toggles() -> None:
    viewer = bv.BacktestViewer(_result())
    assert viewer.has_bench
    assert viewer.bench_dd is not None

    viewer.on_key(FakeEvent("down"))
    assert viewer.idx == 1
    viewer.on_key(FakeEvent("pagedown"))
    assert viewer.idx == 11
    viewer.on_key(FakeEvent("pagedown"))  # clamps
    assert viewer.idx == 11
    viewer.on_key(FakeEvent("home"))
    assert viewer.idx == 0

    assert viewer.log_scale
    viewer.on_key(FakeEvent("l"))
    assert viewer.ax_eq.get_yscale() == "linear"


def test_backtest_viewer_keeps_listening_after_caller_drops_it() -> None:
    def build():
        viewer = bv.BacktestViewer(_result())
        return weakref.ref(viewer), viewer.fig

    ref, fig = build()
    gc.collect()
    assert ref() is not None, "viewer was garbage collected; its key handler is dead"

    press(fig, "pagedown")
    assert ref().idx == 10


def test_backtest_viewer_without_benchmark() -> None:
    data = _result()
    data["benchmark"] = [None] * len(data["dates"])
    data.pop("benchmark_metrics")
    data.pop("relative")
    viewer = bv.BacktestViewer(data)
    assert not viewer.has_bench
    viewer.on_key(FakeEvent("down"))  # must not raise


def test_backtest_viewer_handles_empty_book() -> None:
    data = _result()
    data["rebalances"][0]["holdings"] = []
    bv.BacktestViewer(data)  # must not raise
