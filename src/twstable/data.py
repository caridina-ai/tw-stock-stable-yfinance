"""Price download + on-disk cache.

Layout::

    data/prices/<TICKER>.parquet   one file per symbol: raw OHLCV + ``Adj Close``
    data/panel.parquet             wide close/volume matrices for fast research

Everything downstream reads :func:`load_panel`; the per-symbol files exist so the
K-line viewer can draw real candles without holding the whole market in memory.

Adjusted prices (還原股價)
--------------------------
All analysis runs on dividend- and split-adjusted prices.  Raw prices drop by
the payout on every ex-dividend date, which makes a high-yield ETF that pays
5-10% a year look flat or falling even when its total return climbs steadily.

The cache keeps the *raw* bars plus Yahoo's ``Adj Close`` and applies the
adjustment here (:func:`adjust`) instead of asking yfinance for pre-adjusted
bars.  That keeps both views available -- the segment viewer can flip between
them -- and makes the adjustment visible rather than buried in a download flag.
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
ADJ_CLOSE = "Adj Close"
CACHED = [*OHLCV, ADJ_CLOSE]
PRICE_COLS = ["Open", "High", "Low", "Close"]


def _path_for(ticker: str) -> object:
    return PRICES_DIR / f"{ticker.replace('^', '_')}.parquet"


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    """Drop rows yfinance pads with NaN/zero and enforce dtypes."""
    if df is None or df.empty:
        return pd.DataFrame(columns=CACHED)
    df = df[[c for c in CACHED if c in df.columns]].copy()
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
                auto_adjust=False,  # keep raw bars + Adj Close; see adjust()
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
    """Populate the per-symbol cache.  Returns the tickers that yielded data.

    Files written before the cache carried ``Adj Close`` are re-downloaded:
    they hold pre-adjusted bars with no way back to the raw prices.
    """
    fresh = {t for t in tickers if not refresh and _is_current(_path_for(t))}
    todo = [t for t in tickers if t not in fresh]
    have = [t for t in tickers if t in fresh]

    batches = [todo[i : i + batch_size] for i in range(0, len(todo), batch_size)]
    for batch in tqdm(batches, desc="download", unit="batch"):
        got = download_batch(batch, start=start, end=end)
        for tkr, df in got.items():
            if len(df) >= 60:  # too short to be useful for trend work
                df.to_parquet(_path_for(tkr))
                have.append(tkr)
        time.sleep(0.4)  # be polite to Yahoo
    return sorted(set(have))


def _is_current(path) -> bool:
    """True when ``path`` exists and was written in the raw + Adj Close layout."""
    if not path.exists():
        return False
    import pyarrow.parquet as pq

    return ADJ_CLOSE in pq.read_schema(path).names


def adjust(df: pd.DataFrame) -> pd.DataFrame:
    """Raw bars -> dividend/split-adjusted OHLCV (還原股價).

    Each day's factor is ``Adj Close / Close``; scaling all four prices by it
    keeps every candle's shape while removing the ex-dividend gaps.  Volume is
    left in shares.  A frame without ``Adj Close`` is a legacy cache that was
    downloaded pre-adjusted, so it is returned unchanged.
    """
    if ADJ_CLOSE not in df.columns:
        return df[[c for c in OHLCV if c in df.columns]]
    factor = (df[ADJ_CLOSE] / df["Close"]).where(lambda f: np.isfinite(f) & (f > 0), 1.0)
    out = df[[c for c in OHLCV if c in df.columns]].copy()
    out[PRICE_COLS] = out[PRICE_COLS].mul(factor, axis=0)
    return out


def adjustment_ratio(df: pd.DataFrame) -> float:
    """How much the adjustment rescales the oldest bar relative to the newest.

    1.0 means Yahoo recorded no dividends or splits for the symbol; 0.5 means
    the first adjusted price is half the raw one.  Used by ``fetch`` to flag
    symbols whose adjustment looks missing.
    """
    if ADJ_CLOSE not in df.columns or df.empty:
        return float("nan")
    factor = (df[ADJ_CLOSE] / df["Close"]).to_numpy()
    factor = factor[np.isfinite(factor) & (factor > 0)]
    if factor.size == 0:
        return float("nan")
    return float(factor[0] / factor[-1])


def load_prices(ticker: str, adjusted: bool = True) -> pd.DataFrame | None:
    """OHLCV for one symbol, or None when not cached.

    ``adjusted=True`` (the default, and what all analysis uses) returns 還原股價.
    ``adjusted=False`` returns the raw traded prices, or None for a legacy
    cache file that only ever held adjusted bars.
    """
    path = _path_for(ticker)
    if not path.exists():
        return None
    df = pd.read_parquet(path)
    if adjusted:
        return adjust(df)
    if ADJ_CLOSE not in df.columns:
        return None
    return df[[c for c in OHLCV if c in df.columns]]


# --------------------------------------------------------------------------
# Wide panel
# --------------------------------------------------------------------------


@dataclass
class Panel:
    """Aligned market data.

    ``close`` and ``dollar_volume`` are (dates x stock_id) frames sharing one
    calendar; ``benchmark`` is the TAIEX close on the same index.  ``close`` is
    the adjusted close (還原股價); ``dollar_volume`` is raw close x shares, the
    amount of money that actually changed hands that day.
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
        path = _path_for(tkr)
        if not path.exists():
            continue
        raw = pd.read_parquet(path)
        if raw.empty:
            continue
        sid = id_of.get(tkr, tkr)
        closes[sid] = adjust(raw)["Close"]
        # Turnover is measured in raw prices: adjusted prices shrink old bars,
        # which would understate how liquid a symbol was back then.
        dvols[sid] = raw["Close"] * raw.get("Volume", 0.0)

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
