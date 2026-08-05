"""GUI viewer for a portfolio backtest result.

Layout
------
* equity curve, strategy vs TAIEX, with a cursor on the selected rebalance
* drawdown, strategy filled and the benchmark's drawdown dashed on top
* the holdings held at that rebalance, with the score components that picked them
* a metrics panel

Keys
----
Down / Right    next rebalance
Up / Left       previous rebalance
PageDown/PageUp jump 10
Home / End      first / last
L               toggle the equity axis between log and linear
Q / Esc         quit
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from ..metrics import drawdown
from ._mplsetup import setup

STRAT_COLOR = "#1f77b4"
BENCH_COLOR = "#888888"
DD_COLOR = "#d62728"


class BacktestViewer:
    def __init__(self, data: dict) -> None:
        import matplotlib.pyplot as plt
        from matplotlib.gridspec import GridSpec

        self.data = data
        self.idx = 0
        self.log_scale = True
        self._cursors: list = []

        idx = pd.DatetimeIndex(pd.to_datetime(data["dates"]))
        self.equity = pd.Series(data["equity"], index=idx, dtype="float64")
        bench_raw = [np.nan if v is None else v for v in data.get("benchmark", [])]
        self.bench = (
            pd.Series(bench_raw, index=idx, dtype="float64")
            if len(bench_raw) == len(idx)
            else pd.Series(np.nan, index=idx)
        )
        self.has_bench = bool(self.bench.notna().any())

        self.dd = drawdown(self.equity)
        self.bench_dd = drawdown(self.bench.ffill()) if self.has_bench else None
        self.rebalances = data.get("rebalances", [])

        self.fig = plt.figure(figsize=(15.5, 9.5))
        gs = GridSpec(
            3, 2, figure=self.fig,
            height_ratios=[3.0, 1.6, 3.2], width_ratios=[3.1, 1.0],
            hspace=0.42, wspace=0.12,
            left=0.06, right=0.985, top=0.93, bottom=0.06,
        )
        self.ax_eq = self.fig.add_subplot(gs[0, :])
        self.ax_dd = self.fig.add_subplot(gs[1, :], sharex=self.ax_eq)
        self.ax_tbl = self.fig.add_subplot(gs[2, 0])
        self.ax_txt = self.fig.add_subplot(gs[2, 1])

        self.fig.canvas.mpl_connect("key_press_event", self.on_key)
        self.draw_static()
        self.draw_dynamic()

    # -- static panels ------------------------------------------------------

    def draw_static(self) -> None:
        # ax.clear() below detaches the rebalance cursors, so drop our handles to
        # them here -- otherwise the next redraw would try to remove them twice.
        self._cursors = []

        ax = self.ax_eq
        ax.clear()
        base = float(self.equity.iloc[0])
        ax.plot(
            self.equity.index, self.equity / base,
            color=STRAT_COLOR, linewidth=1.7, label="stable-uptrend strategy",
        )
        if self.has_bench:
            ax.plot(
                self.bench.index, self.bench / base,
                color=BENCH_COLOR, linewidth=1.3, linestyle="-", label="TAIEX",
            )
        ax.set_yscale("log" if self.log_scale else "linear")
        if self.log_scale:
            from matplotlib.ticker import FuncFormatter, LogLocator

            ax.yaxis.set_major_locator(LogLocator(base=10, subs=(1.0, 2.0, 3.0, 5.0, 7.0)))
            ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:g}x"))
            ax.yaxis.set_minor_formatter(FuncFormatter(lambda v, _: ""))
        ax.set_ylabel(f"growth of 1 ({'log' if self.log_scale else 'linear'})")
        ax.grid(True, alpha=0.25)
        ax.legend(loc="upper left", fontsize=9)
        ax.set_title(
            f"{self.data.get('label', 'backtest')} — equity curve", fontsize=12, loc="left"
        )

        ax = self.ax_dd
        ax.clear()
        ax.fill_between(
            self.dd.index, self.dd * 100, 0, color=DD_COLOR, alpha=0.30, linewidth=0
        )
        ax.plot(self.dd.index, self.dd * 100, color=DD_COLOR, linewidth=1.1, label="strategy DD")
        if self.bench_dd is not None:
            # spec: show the benchmark's drawdown dashed for comparison
            ax.plot(
                self.bench_dd.index, self.bench_dd * 100,
                color="#333333", linewidth=1.2, linestyle="--", label="TAIEX DD",
            )
        ax.set_ylabel("drawdown (%)")
        ax.grid(True, alpha=0.25)
        ax.legend(loc="lower left", fontsize=8, ncols=2)
        ax.axhline(0, color="black", linewidth=0.6)

    # -- dynamic panels -----------------------------------------------------

    def draw_dynamic(self) -> None:
        for artist in self._cursors:
            artist.remove()
        self._cursors = []

        rb = self.rebalances[self.idx] if self.rebalances else None
        if rb is not None:
            when = pd.Timestamp(rb["date"])
            for ax in (self.ax_eq, self.ax_dd):
                self._cursors.append(
                    ax.axvline(when, color="#ff7f0e", linewidth=1.4, alpha=0.9, zorder=5)
                )

        self._draw_holdings(rb)
        self._draw_metrics(rb)
        self.fig.canvas.draw_idle()

    def _draw_holdings(self, rb: dict | None) -> None:
        ax = self.ax_tbl
        ax.clear()
        ax.axis("off")
        if rb is None:
            ax.set_title("no rebalances recorded", fontsize=10)
            return

        holdings = sorted(rb["holdings"], key=lambda h: -h.get("weight", 0.0))
        cols = ["ID", "name", "wt%", "score", "trend", "stab", "cons", "restr",
                "slope/yr", "stdev", "R²"]
        rows = [
            [
                h["id"],
                (h.get("name") or "")[:6],
                f"{h.get('weight', 0) * 100:.1f}",
                f"{h['score']:.3f}",
                f"{h['trend']:.2f}",
                f"{h['stability']:.2f}",
                f"{h['consistency']:.2f}",
                f"{h['restraint']:.2f}",
                f"{h['slope']:.3f}",
                f"{h['stdev']:.3f}",
                f"{h['r2']:.2f}",
            ]
            for h in holdings
        ]
        if not rows:
            ax.text(0.5, 0.5, "portfolio in cash", ha="center", va="center", fontsize=11)
            ax.set_title(f"holdings @ {rb['date']}", fontsize=10, loc="left")
            return

        # bbox pins the table to the axes so a full book still fits on screen
        table = ax.table(
            cellText=rows, colLabels=cols, cellLoc="right", bbox=[0.0, 0.0, 1.0, 1.0]
        )
        table.auto_set_font_size(False)
        table.set_fontsize(min(8.0, 150.0 / max(len(rows) + 1, 1)))
        for (r, c), cell in table.get_celld().items():
            cell.set_linewidth(0.3)
            if r == 0:
                cell.set_facecolor("#e8eef5")
                cell.set_text_props(weight="bold")
            elif r % 2 == 0:
                cell.set_facecolor("#f7f7f7")
            if c in (0, 1):
                cell.set_text_props(ha="left")

        traded = f"+{len(rb['bought'])} / -{len(rb['sold'])}"
        ax.set_title(
            f"holdings @ {rb['date']}  (traded {rb['trade_date']})   "
            f"{len(holdings)} names   turnover {rb['turnover']:.0%}   "
            f"changes {traded}   candidates {rb['n_candidates']}",
            fontsize=9.5, loc="left",
        )

    def _draw_metrics(self, rb: dict | None) -> None:
        ax = self.ax_txt
        ax.clear()
        ax.axis("off")
        m = self.data.get("metrics", {})
        bm = self.data.get("benchmark_metrics", {})
        rel = self.data.get("relative", {})

        def row(label: str, key: str, fmt: str) -> str:
            sv = m.get(key)
            bv = bm.get(key)
            s = format(sv, fmt) if isinstance(sv, (int, float)) else "-"
            b = format(bv, fmt) if isinstance(bv, (int, float)) else "-"
            return f"{label:<12}{s:>9}{b:>9}"

        lines = [
            f"{'':<12}{'strat':>9}{'TAIEX':>9}",
            "─" * 30,
            row("CAGR", "cagr", "+.2%"),
            row("total", "total_return", "+.0%"),
            row("vol", "volatility", ".1%"),
            row("Sharpe", "sharpe", ".2f"),
            row("Sortino", "sortino", ".2f"),
            row("max DD", "max_drawdown", ".1%"),
            row("Calmar", "calmar", ".2f"),
            row("Ulcer", "ulcer_index", ".3f"),
            row("+months", "positive_months", ".0%"),
            row("best yr", "best_year", "+.0%"),
            row("worst yr", "worst_year", "+.0%"),
        ]
        if rel:
            lines += [
                "",
                "relative to TAIEX",
                "─" * 30,
                f"{'excess CAGR':<18}{rel.get('excess_cagr', float('nan')):>+11.2%}",
                f"{'alpha (ann)':<18}{rel.get('alpha_annual', float('nan')):>+11.2%}",
                f"{'beta':<18}{rel.get('beta', float('nan')):>11.2f}",
                f"{'info ratio':<18}{rel.get('information_ratio', float('nan')):>11.2f}",
            ]
        if rb is not None:
            eq0 = float(self.equity.iloc[0])
            lines += [
                "",
                f"at {rb['date']}",
                "─" * 30,
                f"{'equity':<18}{rb['equity'] / eq0:>10.2f}x",
                f"{'drawdown':<18}{self._dd_at(rb['date']):>11.1%}",
                f"{'rebalance':<18}{self.idx + 1:>7} /{len(self.rebalances):>4}",
            ]

        ax.text(
            0.0, 1.0, "\n".join(lines), va="top", ha="left",
            family="monospace", fontsize=8.4, transform=ax.transAxes,
        )
        self.fig.suptitle(
            "↑/↓ rebalance   PgUp/PgDn ±10   Home/End   L log/linear   Q quit",
            y=0.975, fontsize=9, color="#555555",
        )

    def _dd_at(self, date_str: str) -> float:
        ts = pd.Timestamp(date_str)
        pos = self.dd.index.searchsorted(ts)
        pos = min(pos, len(self.dd) - 1)
        return float(self.dd.iloc[pos])

    # -- interaction --------------------------------------------------------

    def on_key(self, event) -> None:
        import matplotlib.pyplot as plt

        key = (event.key or "").lower()
        last = max(len(self.rebalances) - 1, 0)
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
            self.draw_static()
        elif key in ("q", "escape"):
            plt.close(self.fig)
            return
        else:
            return
        self.draw_dynamic()


def run(result_path: str | Path) -> None:
    setup()
    import matplotlib.pyplot as plt

    path = Path(result_path)
    if not path.exists():
        raise SystemExit(
            f"backtest result not found: {path}\nRun `python -m twstable backtest` first."
        )
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)

    print(f"loaded {path}: {len(data.get('rebalances', []))} rebalances")
    BacktestViewer(data)
    plt.show()
