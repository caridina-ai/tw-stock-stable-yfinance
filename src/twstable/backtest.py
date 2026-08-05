"""Backtest driver: run the strategy, score it, and persist a result file."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from .config import RESULTS_DIR
from .data import load_panel
from .metrics import compute_metrics, format_metrics, relative_metrics
from .portfolio import BacktestResult, StrategyParams, run_backtest
from .universe import name_map

DEFAULT_RESULT = RESULTS_DIR / "backtest.json"


def _jsonable(obj):
    if isinstance(obj, (np.floating, np.integer)):
        return obj.item()
    if isinstance(obj, pd.Timestamp):
        return obj.strftime("%Y-%m-%d")
    raise TypeError(f"not JSON serialisable: {type(obj)}")


def result_to_dict(res: BacktestResult, label: str = "backtest") -> dict:
    """Flatten a backtest into the structure the viewer reads."""
    names = name_map()
    metrics = compute_metrics(res.equity)
    payload = {
        "label": label,
        "params": res.params.to_dict(),
        "dates": [d.strftime("%Y-%m-%d") for d in res.equity.index],
        "equity": [float(v) for v in res.equity.to_numpy()],
        "benchmark": [
            None if not np.isfinite(v) else float(v) for v in res.benchmark.to_numpy()
        ],
        "metrics": metrics.to_dict(),
        "cost_paid": res.cost_paid,
        "n_trades": res.n_trades,
        "rebalances": [
            {
                "date": rb.date.strftime("%Y-%m-%d"),
                "trade_date": rb.trade_date.strftime("%Y-%m-%d"),
                "equity": rb.equity,
                "turnover": rb.turnover,
                "n_candidates": rb.n_candidates,
                "bought": rb.bought,
                "sold": rb.sold,
                "holdings": [
                    {**h, "name": names.get(h["id"], "")} for h in rb.holdings
                ],
            }
            for rb in res.rebalances
        ],
    }
    if res.benchmark.notna().any():
        payload["benchmark_metrics"] = compute_metrics(res.benchmark.dropna()).to_dict()
        payload["relative"] = relative_metrics(res.equity, res.benchmark)
    return payload


def save_result(res: BacktestResult, path: Path, label: str = "backtest") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(result_to_dict(res, label), fh, ensure_ascii=False, default=_jsonable)


def load_result(path: Path) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def print_report(res: BacktestResult) -> None:
    m = compute_metrics(res.equity)
    print(format_metrics(m, "strategy"))
    if res.benchmark.notna().any():
        bm = compute_metrics(res.benchmark.dropna())
        print(format_metrics(bm, "TAIEX benchmark"))
        rel = relative_metrics(res.equity, res.benchmark)
        if rel:
            print("--- relative ---")
            print(f"  excess CAGR       {rel['excess_cagr']:+.2%}")
            print(f"  beta              {rel['beta']:.2f}")
            print(f"  annual alpha      {rel['alpha_annual']:+.2%}")
            print(f"  correlation       {rel['correlation']:.2f}")
            print(f"  information ratio {rel['information_ratio']:.2f}")
    avg_turn = np.mean([rb.turnover for rb in res.rebalances]) if res.rebalances else 0.0
    avg_hold = np.mean([len(rb.holdings) for rb in res.rebalances]) if res.rebalances else 0.0
    print("--- trading ---")
    print(f"  rebalances        {len(res.rebalances)}")
    print(f"  avg holdings      {avg_hold:.1f}")
    print(f"  avg turnover      {avg_turn:.1%} per rebalance")
    print(f"  trades            {res.n_trades}")
    print(f"  costs paid        {res.cost_paid:,.0f} TWD")


def _load_params(spec: str | None) -> StrategyParams:
    if not spec:
        return StrategyParams()
    path = Path(spec)
    raw = json.loads(path.read_text(encoding="utf-8")) if path.exists() else json.loads(spec)
    return StrategyParams.from_dict(raw)


def run_backtest_cli(args) -> int:
    panel = load_panel()
    params = _load_params(getattr(args, "params", None))
    print("strategy parameters:")
    print(json.dumps(params.to_dict(), indent=2))

    res = run_backtest(panel, params, start=args.start, end=args.end)
    print()
    print_report(res)

    out = Path(args.out) if args.out else DEFAULT_RESULT
    save_result(res, out)
    print(f"\nresult written to {out}")
    return 0
