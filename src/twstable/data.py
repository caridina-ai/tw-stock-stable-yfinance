"""Price download + on-disk cache.

Layout::

    data/prices/<TICKER>.parquet   one file per symbol, adjusted OHLCV
    data/panel.parquet             wide close/volume matrices for fast research

Everything downstream reads :func:`load_panel`; the per-symbol files exist so the
K-line viewer can draw real candles without holding the whole market in memory.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np
import pandas as pd
import yfinance as yf
from tqdm import tqdm

from .config import BENCHMARK, DEFAULT_START, PANEL_PARQUET, PRICES_DIR
from .universe import load_universe

OHLCV = ["Open", "High", "Low", "Close", "Volume"]


def _path_for(ticker: str) -> object:
    return PRICES_DIR / f"{ticker.replace('^', '_')}.parquet"


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    """Drop rows yfinance pads with NaN/zero and enforce dtypes."""
    if df is None or df.empty:
        return pd.DataFrame(columns=OHLCV)
    df = df[[c for c in OHLCV if c in df.columns]].copy()
    df = df.dropna(subset=["Close"])
    df = df[df["Close"] > 0]
    df.index = pd.DatetimeIndex(df.index).tz_localize(None).normalize()
    df = df[~df.index.duplicated(keep="last")].sort_index()
    return df.astype("float64")


def download_batch(
    tickers: list[str], start: str, end: str | None = None, retries: int = 3
) -> dict[str, pd.DataFrame]:
    """Download a group of symbols in one yfinance call."""
    out: dict[str, pd.DataFrame] = {}
    for attempt in range(retries):
        try:
            raw = yf.download(
                tickers,
                start=start,
                end=end,
                auto_adjust=True,
                group_by="ticker",
                progress=False,
                threads=True,
                timeout=60,
            )
            break
        except Exception:  # noqa: BLE001 - network flakiness, retry with backoff
            if attempt == retries - 1:
                return out
            time.sleep(2 * (attempt + 1))
    else:  # pragma: no cover
        return out

    if raw is None or raw.empty:
        return out

    if isinstance(raw.columns, pd.MultiIndex):
        for tkr in tickers:
            if tkr in raw.columns.get_level_values(0):
                out[tkr] = _clean(raw[tkr])
    elif len(tickers) == 1:
        out[tickers[0]] = _clean(raw)
    return out


def fetch_prices(
    tickers: list[str],
    start: str = DEFAULT_START,
    end: str | None = None,
    batch_size: int = 40,
    refresh: bool = False,
) -> list[str]:
    """Populate the per-symbol cache.  Returns the tickers that yielded data."""
    todo = [t for t in tickers if refresh or not _path_for(t).exists()]
    have = [t for t in tickers if not refresh and _path_for(t).exists()]

    batches = [todo[i : i + batch_size] for i in range(0, len(todo), batch_size)]
    for batch in tqdm(batches, desc="download", unit="batch"):
        got = download_batch(batch, start=start, end=end)
        for tkr, df in got.items():
            if len(df) >= 60:  # too short to be useful for trend work
                df.to_parquet(_path_for(tkr))
                have.append(tkr)
        time.sleep(0.4)  # be polite to Yahoo
    return sorted(set(have))


def load_prices(ticker: str) -> pd.DataFrame | None:
    """Adjusted OHLCV for one symbol, or None when not cached."""
    path = _path_for(ticker)
    if not path.exists():
        return None
    return pd.read_parquet(path)


# --------------------------------------------------------------------------
# Wide panel
# --------------------------------------------------------------------------


@dataclass
class Panel:
    """Aligned market data.

    ``close`` and ``dollar_volume`` are (dates x stock_id) frames sharing one
    calendar; ``benchmark`` is the TAIEX close on the same index.
    """

    close: pd.DataFrame
    dollar_volume: pd.DataFrame
    benchmark: pd.Series

    @property
    def dates(self) -> pd.DatetimeIndex:
        return pd.DatetimeIndex(self.close.index)

    @property
    def stock_ids(self) -> list[str]:
        return list(self.close.columns)

    def log_close(self) -> np.ndarray:
        return np.log(self.close.to_numpy(dtype="float64"))

    def filled_close(self, max_gap: int = 10) -> pd.DataFrame:
        """Close prices with short trading halts carried forward.

        A symbol is NaN in the panel on any day it did not trade while the rest
        of the market did.  Left alone, a single halted day would invalidate
        every window spanning it.  Carrying the last price forward treats a halt
        as a zero-return day, the usual convention.

        Gaps longer than ``max_gap`` sessions stay NaN so that windows are never
        fitted across a long suspension (which would otherwise look implausibly
        "stable"), and nothing is filled outside a symbol's own listed range.
        """
        close = self.close
        filled = close.ffill(limit=max_gap)
        arr = close.to_numpy()
        valid = np.isfinite(arr)
        rows = np.arange(len(close))[:, None]
        first = np.where(valid.any(axis=0), np.argmax(valid, axis=0), len(close))
        last = np.where(valid.any(axis=0), len(close) - 1 - np.argmax(valid[::-1], axis=0), -1)
        inside = (rows >= first) & (rows <= last)
        return filled.where(inside)

    def log_filled_close(self, max_gap: int = 10) -> np.ndarray:
        with np.errstate(divide="ignore", invalid="ignore"):
            return np.log(self.filled_close(max_gap).to_numpy(dtype="float64"))


def build_panel(tickers: list[str] | None = None) -> Panel:
    """Assemble the wide panel from the per-symbol cache and persist it."""
    uni = load_universe()
    if tickers is None:
        tickers = list(uni["ticker"])
    id_of = dict(zip(uni["ticker"], uni["stock_id"], strict=False))

    closes: dict[str, pd.Series] = {}
    dvols: dict[str, pd.Series] = {}
    for tkr in tqdm(tickers, desc="panel", unit="sym"):
        df = load_prices(tkr)
        if df is None or df.empty:
            continue
        sid = id_of.get(tkr, tkr)
        closes[sid] = df["Close"]
        dvols[sid] = df["Close"] * df.get("Volume", 0.0)

    close = pd.DataFrame(closes).sort_index()
    dvol = pd.DataFrame(dvols).reindex_like(close)

    bench_df = load_prices(BENCHMARK)
    bench = (
        bench_df["Close"].reindex(close.index).ffill()
        if bench_df is not None
        else pd.Series(np.nan, index=close.index)
    )

    close.to_parquet(PANEL_PARQUET)
    dvol.to_parquet(PANEL_PARQUET.with_name("panel_dvol.parquet"))
    bench.to_frame("benchmark").to_parquet(PANEL_PARQUET.with_name("panel_bench.parquet"))
    return Panel(close=close, dollar_volume=dvol, benchmark=bench)


def load_panel() -> Panel:
    """Load the cached panel, building it on first use."""
    if not PANEL_PARQUET.exists():
        return build_panel()
    close = pd.read_parquet(PANEL_PARQUET)
    dvol = pd.read_parquet(PANEL_PARQUET.with_name("panel_dvol.parquet"))
    bench = pd.read_parquet(PANEL_PARQUET.with_name("panel_bench.parquet"))["benchmark"]
    return Panel(close=close, dollar_volume=dvol, benchmark=bench)
