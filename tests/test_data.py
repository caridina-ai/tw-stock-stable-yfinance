"""Adjusted-price handling: raw bars + Adj Close in the cache, 還原股價 out."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from twstable import data


def _raw_with_dividend() -> pd.DataFrame:
    """Five flat days at 20, then a 1-TWD ex-dividend drop to 19."""
    idx = pd.bdate_range("2024-01-01", periods=10)
    close = np.array([20.0] * 5 + [19.0] * 5)
    # Yahoo back-adjusts everything before the ex-date by (1 - 1/20)
    adj = np.where(np.arange(10) < 5, close * 0.95, close)
    return pd.DataFrame(
        {
            "Open": close, "High": close + 0.5, "Low": close - 0.5, "Close": close,
            "Adj Close": adj, "Volume": 1000.0,
        },
        index=idx,
    )


def test_adjust_removes_ex_dividend_gap() -> None:
    out = data.adjust(_raw_with_dividend())
    assert "Adj Close" not in out.columns
    np.testing.assert_allclose(out["Close"], 19.0)  # flat total return, no drop
    np.testing.assert_allclose(out["High"].iloc[0], 20.5 * 0.95)  # whole candle scaled
    assert (out["Volume"] == 1000.0).all()  # shares untouched


def test_adjust_passes_legacy_cache_through() -> None:
    legacy = _raw_with_dividend().drop(columns="Adj Close")
    pd.testing.assert_frame_equal(data.adjust(legacy), legacy)


def test_adjustment_ratio() -> None:
    assert data.adjustment_ratio(_raw_with_dividend()) == pytest.approx(0.95)


def test_load_prices_raw_and_adjusted(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(data, "PRICES_DIR", tmp_path)
    _raw_with_dividend().to_parquet(tmp_path / "0056.TW.parquet")

    adj = data.load_prices("0056.TW")
    raw = data.load_prices("0056.TW", adjusted=False)
    np.testing.assert_allclose(adj["Close"], 19.0)
    assert raw["Close"].iloc[0] == 20.0
    assert data._is_current(tmp_path / "0056.TW.parquet")


def test_legacy_cache_is_stale_and_has_no_raw(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(data, "PRICES_DIR", tmp_path)
    _raw_with_dividend().drop(columns="Adj Close").to_parquet(tmp_path / "1101.TW.parquet")

    assert not data._is_current(tmp_path / "1101.TW.parquet")
    assert data.load_prices("1101.TW", adjusted=False) is None
    assert data.load_prices("1101.TW") is not None
