"""GUI viewer for mined up-trend segments.

Keys
----
Down / Right    next segment
Up / Left       previous segment
PageDown/PageUp jump 10 segments
Home / End      first / last segment
L               toggle the price axis between linear and log
B               toggle the +-1 sigma dispersion band
Q / Esc         quit

On a log price axis the fitted trend is a straight line; on a linear axis the
same fit shows up as an exponential curve.  That is the whole point of fitting
in log space, so the toggle is the quickest way to sanity-check a segment.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from ..data import load_prices
from ..trend import fit_segment
from ..universe import load_universe
from ._mplsetup import setup

UP_COLOR = "#d62728"  # Taiwan convention: red = up
DOWN_COLOR = "#2ca02c"  # green = down


class SegmentViewer:
    def __init__(self, segments: pd.DataFrame) -> None:
        import matplotlib.pyplot as plt

        self.seg = segments.reset_index(drop=True)
        self.idx = 0
        self.log_scale = True
        self.show_band = True

        uni = load_universe()
        self.ticker_of = dict(zip(uni["stock_id"].astype(str), uni["ticker"], strict=False))
        self._cache: dict[str, pd.DataFrame] = {}

        self.fig, self.ax = plt.subplots(figsize=(13, 7.5))
        self.fig.canvas.mpl_connect("key_press_event", self.on_key)
        self.draw()

    # -- data ---------------------------------------------------------------

    def _ohlc(self, stock_id: str) -> pd.DataFrame | None:
        if stock_id not in self._cache:
            ticker = self.ticker_of.get(stock_id)
            df = load_prices(ticker) if ticker else None
            self._cache[stock_id] = df if df is not None else pd.DataFrame()
        df = self._cache[stock_id]
        return None if df.empty else df

    # -- drawing ------------------------------------------------------------

    def draw(self) -> None:
        self.ax.clear()
        row = self.seg.iloc[self.idx]
        stock_id = str(row["ID"])
        begin, end = pd.Timestamp(row["begin_date"]), pd.Timestamp(row["end_date"])

        df = self._ohlc(stock_id)
        if df is None:
            self.ax.set_title(f"{stock_id}: no cached price data")
            self.fig.canvas.draw_idle()
            return

        win = df.loc[(df.index >= begin) & (df.index <= end)]
        if len(win) < 3:
            self.ax.set_title(f"{stock_id}: segment too short in cache")
            self.fig.canvas.draw_idle()
            return

        x = np.arange(len(win))
        o, h, low, c = (win[k].to_numpy() for k in ("Open", "High", "Low", "Close"))

        # --- candles: thin wick + fat body, drawn as two bar series --------
        up = c >= o
        self.ax.vlines(x[up], low[up], h[up], color=UP_COLOR, linewidth=0.7, zorder=2)
        self.ax.vlines(x[~up], low[~up], h[~up], color=DOWN_COLOR, linewidth=0.7, zorder=2)
        body_lo = np.minimum(o, c)
        body_hi = np.maximum(o, c)
        # keep doji visible on a log axis by giving them a hairline height
        span = np.maximum(body_hi - body_lo, np.maximum(c, 1e-9) * 1e-4)
        self.ax.bar(
            x[up], span[up], bottom=body_lo[up], width=0.68,
            color=UP_COLOR, edgecolor=UP_COLOR, linewidth=0.4, zorder=3,
        )
        self.ax.bar(
            x[~up], span[~up], bottom=body_lo[~up], width=0.68,
            color="white", edgecolor=DOWN_COLOR, linewidth=0.7, zorder=3,
        )

        # --- regression line recomputed from the closes we are drawing -----
        fit = fit_segment(c)
        trend = fit.fitted_price(x)
        self.ax.plot(
            x, trend, color="#1f77b4", linewidth=2.0, zorder=4,
            label=f"log-linear fit  slope={fit.slope:.4f}/yr  stdev={fit.stdev:.4f}",
        )
        if self.show_band:
            band = np.exp(fit.sigma)
            self.ax.fill_between(
                x, trend / band, trend * band, color="#1f77b4", alpha=0.12, zorder=1,
                label="+-1 sigma",
            )

        # --- axes ----------------------------------------------------------
        self.ax.set_yscale("log" if self.log_scale else "linear")
        if self.log_scale:
            from matplotlib.ticker import FuncFormatter, LogLocator

            self.ax.yaxis.set_major_locator(LogLocator(base=10, subs=(1.0, 2.0, 3.0, 5.0, 7.0)))
            self.ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:g}"))
            self.ax.yaxis.set_minor_formatter(FuncFormatter(lambda v, _: ""))

        n_ticks = min(10, len(win))
        pos = np.linspace(0, len(win) - 1, n_ticks).round().astype(int)
        self.ax.set_xticks(pos)
        self.ax.set_xticklabels(
            [win.index[p].strftime("%Y-%m-%d") for p in pos], rotation=30, ha="right", fontsize=9
        )
        self.ax.set_xlim(-1, len(win))
        self.ax.set_xlabel("date")
        self.ax.set_ylabel(f"price (TWD, {'log' if self.log_scale else 'linear'} scale)")
        self.ax.grid(True, which="major", alpha=0.25)

        name = str(row.get("name", "") or "")
        total_ret = float(c[-1] / c[0] - 1.0)
        self.ax.set_title(
            f"{stock_id} {name}   [{self.idx + 1}/{len(self.seg)}]\n"
            f"{begin.date()} → {end.date()}   length={int(row['length'])}d   "
            f"slope={fit.slope:.4f}/yr   stdev={fit.stdev:.4f}   "
            f"R²={fit.r2:.3f}   total={total_ret:+.1%}",
            fontsize=12,
        )
        self.ax.legend(loc="upper left", fontsize=9, framealpha=0.9)
        self.fig.text(
            0.995, 0.01,
            "↑/↓ segment   PgUp/PgDn ±10   Home/End   L log/linear   B band   Q quit",
            ha="right", va="bottom", fontsize=8, color="#555555",
        )
        self.fig.tight_layout()
        self.fig.canvas.draw_idle()

    # -- interaction --------------------------------------------------------

    def on_key(self, event) -> None:
        import matplotlib.pyplot as plt

        key = (event.key or "").lower()
        last = len(self.seg) - 1
        moved = True
        if key in ("down", "right"):
            self.idx = min(self.idx + 1, last)
        elif key in ("up", "left"):
            self.idx = max(self.idx - 1, 0)
        elif key == "pagedown":
            self.idx = min(self.idx + 10, last)
        elif key == "pageup":
            self.idx = max(self.idx - 10, 0)
        elif key == "home":
            self.idx = 0
        elif key == "end":
            self.idx = last
        elif key == "l":
            self.log_scale = not self.log_scale
        elif key == "b":
            self.show_band = not self.show_band
        elif key in ("q", "escape"):
            plt.close(self.fig)
            return
        else:
            moved = False
        if moved:
            self.draw()


def run(csv_path: str | Path) -> None:
    setup()
    import matplotlib.pyplot as plt

    path = Path(csv_path)
    if not path.exists():
        raise SystemExit(f"segments CSV not found: {path}\nRun `python -m twstable mine` first.")

    seg = pd.read_csv(path, dtype={"ID": str})
    seg["begin_date"] = pd.to_datetime(seg["begin_date"])
    seg["end_date"] = pd.to_datetime(seg["end_date"])
    if seg.empty:
        raise SystemExit(f"no segments in {path}")

    print(f"loaded {len(seg)} segments from {path}")
    SegmentViewer(seg)
    plt.show()
