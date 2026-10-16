"""Sweep the monthly trade day, the holiday rule and the portfolio size.

The question (``genesis/FIAT-hyper-parameters.md``): if the book is re-selected
once a month on a fixed calendar day, which day -- and when that day is a
holiday, trade before or after it?  And how few names can the book hold before
the strategy stops working?

Every configuration is a ``(rebalance_day, holiday, max_m, cap_weight, weighting)``
override on top of parameters that were chosen without it, scored two ways:

walk-forward (``wf``)
    Each fold of ``results/wfcv.json`` keeps the parameters its own training
    window picked; only the overrides change.  The test windows are run and
    chained exactly as in the WFCV run, so the curve is out of sample (2006-).
production (``prod``)
    The shipped production parameters over the whole history (2001-).  They
    were fitted on 2020- data, so this is partly in sample; it is a
    cross-check -- a conclusion worth acting on should hold under both.

Rows with ``rebalance_day == 0`` keep the original trading-day interval, and
``max_m == 0`` keeps the original book size: the reference the monthly
schedules are compared with.

Computing and presenting are separate on purpose.  ``twstable sweep`` writes
``grid.csv`` (one row of metrics and calendar-year returns per configuration)
and ``curves.parquet`` (month-end equity of every configuration, keyed by the
same ``id``); everything shown to a person is built from those two files, so a
new viewer or report never needs the grid re-run.
"""

from __future__ import annotations

import json
import multiprocessing as mp
import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from tqdm import tqdm

from .config import RESULTS_DIR
from .metrics import compute_metrics
from .portfolio import BacktestResult, Market, StrategyParams, prepare_market, run_backtest
from .wfcv import DEFAULT_RESULT as WFCV_RESULT
from .wfcv import chain_equity

DEFAULT_DIR = RESULTS_DIR / "hyper"
GRID_CSV = "grid.csv"
CURVES_PARQUET = "curves.parquet"

DAYS = tuple(range(1, 29))
HOLIDAYS = ("next", "prev")
SIZES = tuple(range(1, 21))
CAPS = (False, True)
WEIGHTINGS = ("equal", "score")
PROTOCOLS = ("wf", "prod")


@dataclass(frozen=True, order=True)
class Task:
    protocol: str
    day: int  # 0 = keep the trading-day interval
    holiday: str
    max_m: int  # 0 = keep the configured size
    cap: bool
    weighting: str = "equal"

    def apply(self, params: StrategyParams) -> StrategyParams:
        over: dict = {"cap_weight": self.cap, "weighting": self.weighting}
        if self.day:
            over.update(rebalance_day=self.day, holiday=self.holiday)
        if self.max_m:
            over["max_m"] = self.max_m
        return params.replace(**over)


def build_tasks() -> list[Task]:
    tasks = []
    for protocol in PROTOCOLS:
        tasks.append(Task(protocol, 0, "next", 0, False))
        for cap in CAPS:
            for w in WEIGHTINGS:
                for m in SIZES:
                    tasks.append(Task(protocol, 0, "next", m, cap, w))
                    for day in DAYS:
                        for holiday in HOLIDAYS:
                            tasks.append(Task(protocol, day, holiday, m, cap, w))
    return tasks


# ---------------------------------------------------------------------------
# evaluation
# ---------------------------------------------------------------------------


@dataclass
class Setup:
    market: Market
    folds: list[tuple[pd.Timestamp, pd.Timestamp, StrategyParams]]
    production: StrategyParams


def load_setup(market: Market, wfcv_json: Path = WFCV_RESULT) -> Setup:
    raw = json.loads(Path(wfcv_json).read_text("utf-8"))["wfcv"]
    folds = [
        (pd.Timestamp(f["test_start"]), pd.Timestamp(f["test_end"]),
         StrategyParams.from_dict(f["params"]))
        for f in raw["folds"]
    ]
    return Setup(market, folds, StrategyParams.from_dict(raw["production_params"]))


def yearly_returns(equity: pd.Series) -> pd.Series:
    """Calendar-year returns, the first (partial) year measured from the first bar."""
    eq = equity.dropna()
    year_end = eq.resample("YE").last()
    prev = year_end.shift(1)
    prev.iloc[0] = eq.iloc[0]
    out = year_end / prev - 1.0
    out.index = out.index.year
    return out


def evaluate(setup: Setup, task: Task) -> tuple[dict, pd.Series]:
    """Metrics row and month-end equity curve of one configuration."""
    if task.protocol == "wf":
        runs = [
            run_backtest(setup.market, task.apply(p), start=s, end=e, record=True)
            for s, e, p in setup.folds
        ]
        equity = chain_equity([r.equity for r in runs])
    else:
        runs = [run_backtest(setup.market, task.apply(setup.production), record=True)]
        equity = runs[0].equity
    month_end = equity.resample("ME").last().dropna()
    return summarise(task, equity, runs), month_end


def summarise(task: Task, equity: pd.Series, runs: list[BacktestResult]) -> dict:
    m = compute_metrics(equity)
    rebs = [rb for r in runs for rb in r.rebalances]
    row = {
        "protocol": task.protocol,
        "day": task.day,
        "holiday": task.holiday,
        "max_m": task.max_m,
        "cap": task.cap,
        "weighting": task.weighting,
        "start": m.start,
        "end": m.end,
        "cagr": m.cagr,
        "sharpe": m.sharpe,
        "max_drawdown": m.max_drawdown,
        "volatility": m.volatility,
        "calmar": m.calmar,
        "ulcer_index": m.ulcer_index,
        "worst_year": m.worst_year,
        "n_rebalances": len(rebs),
        "avg_held": float(np.mean([len(rb.holdings) for rb in rebs])) if rebs else 0.0,
        "invested": float(np.mean([sum(h["weight"] for h in rb.holdings) for rb in rebs]))
        if rebs else 0.0,
        "turnover": float(np.mean([rb.turnover for rb in rebs])) if rebs else 0.0,
        "cost_frac": float(sum(r.cost_paid for r in runs) / 1e6),
    }
    for year, ret in yearly_returns(equity).items():
        row[f"y{year}"] = float(ret)
    return row


_SETUP: Setup | None = None


def _init(setup: Setup) -> None:
    global _SETUP
    _SETUP = setup


def _work(task: Task) -> tuple[dict, pd.Series]:
    assert _SETUP is not None
    return evaluate(_SETUP, task)


def run_grid(
    setup: Setup, tasks: list[Task], workers: int
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Evaluate every task, each worker process holding its own copy of the market.

    Returns ``(grid, curves)``: one metrics row per task, and the month-end
    equity of every task in long form (``id``, ``date``, ``equity``).  Tasks are
    sorted first, so ``id`` is the row number and stable across re-runs.

    ``forkserver`` rather than ``fork``: the parent runs numpy/pyarrow thread
    pools, and forking a multi-threaded process can deadlock the children.
    """
    tasks = sorted(tasks)
    if workers <= 1:
        out = [evaluate(setup, t) for t in tqdm(tasks, unit="cfg")]
    else:
        ctx = mp.get_context("forkserver")
        with ctx.Pool(workers, initializer=_init, initargs=(setup,)) as pool:
            out = list(tqdm(pool.imap(_work, tasks, chunksize=4), total=len(tasks), unit="cfg"))

    grid = pd.DataFrame([row for row, _ in out])
    grid.insert(0, "id", np.arange(len(grid), dtype="int32"))
    curves = pd.concat(
        [pd.DataFrame({"id": np.int32(k), "date": c.index, "equity": c.to_numpy("float32")})
         for k, (_, c) in enumerate(out)],
        ignore_index=True,
    )
    return grid, curves


def run_sweep_cli(args) -> int:
    from .data import load_panel

    out_dir = Path(args.out_dir) if args.out_dir else DEFAULT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    csv = out_dir / GRID_CSV

    market = prepare_market(load_panel())
    setup = load_setup(market, Path(args.wfcv) if args.wfcv else WFCV_RESULT)
    tasks = build_tasks()
    workers = args.workers or os.cpu_count() or 1
    print(f"{len(tasks)} configurations on {workers} workers")
    grid, curves = run_grid(setup, tasks, workers)
    grid.to_csv(csv, index=False, float_format="%.6g")
    curves.to_parquet(out_dir / CURVES_PARQUET, index=False, compression="zstd")
    print(f"grid written to {csv}, curves to {out_dir / CURVES_PARQUET}")
    return 0
