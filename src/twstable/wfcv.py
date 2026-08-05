"""Walk-forward cross-validation of the strategy hyperparameters.

The history is cut into consecutive folds.  In each fold the hyperparameters are
chosen by random search on the *training* slice only, then applied unchanged to
the *test* slice that follows it.  The test slices never overlap and are always
in the future relative to the data that picked the parameters, so chaining them
gives one continuous out-of-sample equity curve -- the honest estimate of what
this strategy would have done live.

Two things are deliberately conservative:

* Each fold restarts from cash instead of inheriting the previous fold's book,
  so every parameter change pays full entry costs.
* Scores at the very start of a test slice legitimately read prices from the
  training slice -- that is past data at decision time, not look-ahead -- but no
  price at or after a decision date is ever used to make it.

The parameters shipped as the "production" configuration are then re-fitted on
the most recent training window, since that is what one would actually deploy.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from tqdm import tqdm

from .backtest import result_to_dict
from .config import RESULTS_DIR
from .data import load_panel
from .metrics import compute_metrics, format_metrics, relative_metrics
from .portfolio import (
    BacktestResult,
    Market,
    StrategyParams,
    prepare_market,
    run_backtest,
)
from .score import ScoreParams

DEFAULT_RESULT = RESULTS_DIR / "wfcv.json"


# ---------------------------------------------------------------------------
# search space
# ---------------------------------------------------------------------------


def sample_params(rng: np.random.Generator) -> StrategyParams:
    """Draw one configuration from the search space."""
    score = ScoreParams(
        window=int(rng.choice([120, 160, 200, 250, 320, 400, 500])),
        g_opt=float(rng.uniform(1.10, 1.60)),
        g_width=float(rng.uniform(1.08, 1.40)),
        sigma0=float(rng.uniform(0.04, 0.20)),
        n_sub=int(rng.choice([2, 3, 4])),
        sub_scale=float(rng.uniform(0.03, 0.12)),
        z0=float(rng.uniform(0.0, 2.0)),
        z_width=float(rng.uniform(0.8, 3.0)),
    )
    return StrategyParams(
        score=score,
        max_m=int(rng.choice([10, 15, 20, 25, 30])),
        rebalance=int(rng.choice([10, 20, 30, 40, 60])),
        min_score=float(rng.uniform(0.05, 0.40)),
        hold_mult=float(rng.uniform(1.0, 6.0)),
        keep_frac=float(rng.uniform(0.3, 0.9)),
        weight_band=float(rng.uniform(0.10, 0.60)),
        min_dollar_volume=float(rng.choice([1e7, 2e7, 5e7])),
    )


def objective(res: BacktestResult) -> float:
    """In-sample selection criterion.

    Sharpe rather than raw return: the whole premise is *stable* growth, and a
    pure-return objective would hand the search straight back to the
    performance chasing the score is built to avoid.  Configurations that
    barely trade or lose money are rejected outright.
    """
    eq = res.equity.dropna()
    if len(eq) < 60 or eq.iloc[-1] <= 0 or res.n_rebalances < 4:
        return -np.inf
    m = compute_metrics(eq)
    if not np.isfinite(m.sharpe) or m.cagr <= 0:
        return -np.inf
    return float(m.sharpe)


# ---------------------------------------------------------------------------
# folds
# ---------------------------------------------------------------------------


@dataclass
class Fold:
    index: int
    train_start: pd.Timestamp
    train_end: pd.Timestamp
    test_start: pd.Timestamp
    test_end: pd.Timestamp

    def describe(self) -> str:
        return (
            f"fold {self.index}: train {self.train_start.date()}..{self.train_end.date()}"
            f"  test {self.test_start.date()}..{self.test_end.date()}"
        )


def make_folds(
    dates: pd.DatetimeIndex,
    start: str | pd.Timestamp,
    end: str | pd.Timestamp | None,
    train_years: float,
    test_years: float,
) -> list[Fold]:
    first = max(pd.Timestamp(start), dates[0])
    last = min(pd.Timestamp(end), dates[-1]) if end else dates[-1]

    folds: list[Fold] = []
    train_start = first
    while True:
        train_end = train_start + pd.DateOffset(days=int(train_years * 365.25))
        test_end = train_end + pd.DateOffset(days=int(test_years * 365.25))
        if train_end >= last:
            break
        folds.append(
            Fold(
                index=len(folds) + 1,
                train_start=train_start,
                train_end=train_end,
                test_start=train_end,
                test_end=min(test_end, last),
            )
        )
        if test_end >= last:
            break
        train_start = train_start + pd.DateOffset(days=int(test_years * 365.25))
    return folds


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


@dataclass
class FoldResult:
    fold: Fold
    params: StrategyParams
    is_sharpe: float
    is_metrics: dict
    oos: BacktestResult
    oos_metrics: dict
    n_evaluated: int
    partial: bool = False
    """True when the test window was cut short by the end of the data.

    The last fold is usually a stub.  Annualising a few months produces wild
    CAGR and Sharpe figures, so partial folds are shown but kept out of the
    averages -- otherwise a lucky final quarter would flatter the whole table.
    """


def search_fold(
    market: Market,
    fold: Fold,
    trials: int,
    rng: np.random.Generator,
    progress: bool = True,
) -> tuple[StrategyParams, float, BacktestResult]:
    """Random-search the training slice; return the best configuration."""
    best_params: StrategyParams | None = None
    best_obj = -np.inf
    best_res: BacktestResult | None = None
    evaluated = 0

    it = range(trials)
    if progress:
        it = tqdm(it, desc=f"fold {fold.index} search", unit="trial", leave=False)
    for _ in it:
        params = sample_params(rng)
        try:
            res = run_backtest(
                market, params, start=fold.train_start, end=fold.train_end, record=False
            )
        except (ValueError, IndexError):
            continue
        evaluated += 1
        obj = objective(res)
        if obj > best_obj:
            best_obj, best_params, best_res = obj, params, res

    if best_params is None or best_res is None:
        raise RuntimeError(f"no viable configuration found for {fold.describe()}")
    return best_params, best_obj, best_res


def stitch(fold_results: list[FoldResult], market: Market) -> BacktestResult:
    """Chain the per-fold out-of-sample runs into one continuous curve."""
    pieces: list[pd.Series] = []
    rebalances: list = []
    level = 1.0
    cost = 0.0
    trades = 0

    for fr in fold_results:
        eq = fr.oos.equity.dropna()
        if eq.empty:
            continue
        scaled = eq / float(eq.iloc[0]) * level
        if pieces:
            scaled = scaled.iloc[1:]  # avoid duplicating the seam date
        pieces.append(scaled)
        if len(scaled):
            level = float(scaled.iloc[-1])
        rebalances.extend(fr.oos.rebalances)
        cost += fr.oos.cost_paid
        trades += fr.oos.n_trades

    equity = pd.concat(pieces)
    equity = equity[~equity.index.duplicated(keep="first")].sort_index()
    equity.name = "equity"

    bench = market.benchmark.reindex(equity.index).ffill()
    if bench.notna().any():
        bench = bench / float(bench.dropna().iloc[0]) * float(equity.iloc[0])

    return BacktestResult(
        equity=equity,
        benchmark=bench,
        rebalances=rebalances,
        params=fold_results[-1].params,
        cost_paid=cost,
        n_trades=trades,
    )


def run_wfcv(
    market: Market,
    start: str,
    end: str | None,
    train_years: float,
    test_years: float,
    trials: int,
    seed: int,
) -> tuple[list[FoldResult], BacktestResult, StrategyParams]:
    folds = make_folds(market.dates, start, end, train_years, test_years)
    if not folds:
        raise SystemExit("no folds fit in the requested range; shorten --train-years")

    print(f"{len(folds)} walk-forward folds, {trials} trials each")
    for f in folds:
        print("  " + f.describe())
    print()

    rng = np.random.default_rng(seed)
    results: list[FoldResult] = []
    for fold in folds:
        params, is_obj, is_res = search_fold(market, fold, trials, rng)
        oos = run_backtest(
            market, params, start=fold.test_start, end=fold.test_end, record=True
        )
        is_m = compute_metrics(is_res.equity)
        oos_m = compute_metrics(oos.equity)
        span_years = (fold.test_end - fold.test_start).days / 365.25
        partial = span_years < 0.8 * test_years
        results.append(
            FoldResult(
                fold=fold,
                params=params,
                is_sharpe=is_obj,
                is_metrics=is_m.to_dict(),
                oos=oos,
                oos_metrics=oos_m.to_dict(),
                n_evaluated=trials,
                partial=partial,
            )
        )
        tag = "  [partial fold, excluded from averages]" if partial else ""
        print(
            f"  {fold.describe()}{tag}\n"
            f"    IS  Sharpe {is_m.sharpe:5.2f}  CAGR {is_m.cagr:+7.2%}  MDD {is_m.max_drawdown:7.2%}\n"
            f"    OOS Sharpe {oos_m.sharpe:5.2f}  CAGR {oos_m.cagr:+7.2%}  MDD {oos_m.max_drawdown:7.2%}"
        )

    combined = stitch(results, market)

    # The configuration to deploy: re-fitted on the most recent training window.
    last = folds[-1]
    prod_fold = Fold(
        index=0,
        train_start=last.train_start + pd.DateOffset(days=int(test_years * 365.25)),
        train_end=market.dates[-1],
        test_start=market.dates[-1],
        test_end=market.dates[-1],
    )
    print("\nre-fitting production parameters on the most recent training window")
    print("  " + f"{prod_fold.train_start.date()}..{prod_fold.train_end.date()}")
    prod_params, _, _ = search_fold(market, prod_fold, trials, rng)

    return results, combined, prod_params


# ---------------------------------------------------------------------------
# reporting
# ---------------------------------------------------------------------------


def _fold_table(results: list[FoldResult]) -> str:
    head = (
        f"{'fold':>4} {'test period':<23} {'yrs':>4} "
        f"{'OOS CAGR':>9} {'Sharpe':>7} {'MDD':>8} {'Calmar':>7} "
        f"{'IS Sharpe':>10} {'win':>5} {'reb':>4} {'m':>3}"
    )
    lines = [head, "-" * len(head)]
    for fr in results:
        o = fr.oos_metrics
        period = f"{fr.fold.test_start.date()}..{fr.fold.test_end.date()}"
        mark = "*" if fr.partial else " "
        lines.append(
            f"{fr.fold.index:>3}{mark} {period:<23} {o['years']:>4.1f} "
            f"{o['cagr']:>+8.2%} {o['sharpe']:>7.2f} {o['max_drawdown']:>8.2%} "
            f"{o['calmar']:>7.2f} {fr.is_sharpe:>10.2f} "
            f"{o['positive_months']:>5.0%} {fr.params.rebalance:>4} {fr.params.max_m:>3}"
        )
    if any(fr.partial for fr in results):
        lines.append("  * partial fold (test window truncated by end of data)")
    return "\n".join(lines)


def print_wfcv_report(
    results: list[FoldResult], combined: BacktestResult, prod: StrategyParams
) -> None:
    print("\n" + "=" * 84)
    print("WALK-FORWARD OUT-OF-SAMPLE RESULTS (per fold)")
    print("=" * 84)
    print(_fold_table(results))

    full = [fr for fr in results if not fr.partial]
    if full:
        oos_sharpes = [fr.oos_metrics["sharpe"] for fr in full]
        is_sharpes = [fr.is_sharpe for fr in full]
        print(
            f"\n  over {len(full)} full folds:  "
            f"mean IS Sharpe {np.mean(is_sharpes):.2f}   "
            f"mean OOS Sharpe {np.mean(oos_sharpes):.2f}   "
            f"degradation {np.mean(is_sharpes) - np.mean(oos_sharpes):+.2f}"
        )
        print(
            f"  median OOS CAGR {np.median([fr.oos_metrics['cagr'] for fr in full]):+.2%}   "
            f"worst fold {min(fr.oos_metrics['cagr'] for fr in full):+.2%}"
        )
    beat = sum(1 for fr in results if fr.oos_metrics["cagr"] > 0)
    print(f"  folds with positive OOS CAGR: {beat}/{len(results)}")

    print("\n" + "=" * 78)
    print("STITCHED OUT-OF-SAMPLE EQUITY (the production strategy)")
    print("=" * 78)
    print(format_metrics(compute_metrics(combined.equity), "walk-forward OOS"))
    if combined.benchmark.notna().any():
        print(format_metrics(compute_metrics(combined.benchmark.dropna()), "TAIEX (same period)"))
        rel = relative_metrics(combined.equity, combined.benchmark)
        if rel:
            print("--- relative ---")
            print(f"  excess CAGR       {rel['excess_cagr']:+.2%}")
            print(f"  beta              {rel['beta']:.2f}")
            print(f"  annual alpha      {rel['alpha_annual']:+.2%}")
            print(f"  information ratio {rel['information_ratio']:.2f}")

    print("\n" + "=" * 78)
    print("PRODUCTION PARAMETERS (re-fitted on the most recent training window)")
    print("=" * 78)
    print(json.dumps(prod.to_dict(), indent=2))


def run_wfcv_cli(args) -> int:
    panel = load_panel()
    market = prepare_market(panel)

    results, combined, prod = run_wfcv(
        market,
        start=args.start,
        end=args.end,
        train_years=args.train_years,
        test_years=args.test_years,
        trials=args.trials,
        seed=args.seed,
    )
    print_wfcv_report(results, combined, prod)

    out = Path(args.out) if args.out else DEFAULT_RESULT
    payload = result_to_dict(combined, label="walk-forward out-of-sample")
    payload["wfcv"] = {
        "train_years": args.train_years,
        "test_years": args.test_years,
        "trials": args.trials,
        "seed": args.seed,
        "production_params": prod.to_dict(),
        "folds": [
            {
                "index": fr.fold.index,
                "train_start": fr.fold.train_start.strftime("%Y-%m-%d"),
                "train_end": fr.fold.train_end.strftime("%Y-%m-%d"),
                "test_start": fr.fold.test_start.strftime("%Y-%m-%d"),
                "test_end": fr.fold.test_end.strftime("%Y-%m-%d"),
                "params": fr.params.to_dict(),
                "is_sharpe": fr.is_sharpe,
                "is_metrics": fr.is_metrics,
                "oos_metrics": fr.oos_metrics,
                "partial": fr.partial,
            }
            for fr in results
        ],
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, default=str)
    print(f"\nresult written to {out}")
    print(f"view it with:  python -m twstable view-backtest --result {out}")
    return 0
