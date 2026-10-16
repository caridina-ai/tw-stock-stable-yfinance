"""Command line entry point: ``python -m twstable <command>``."""

from __future__ import annotations

import argparse
import sys

from .config import BENCHMARK, DEFAULT_START, RESULTS_DIR

POOL_NPZ = RESULTS_DIR / "segment_pool.npz"
SEGMENTS_CSV = RESULTS_DIR / "segments.csv"


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------


def cmd_fetch(args: argparse.Namespace) -> int:
    from .data import build_panel, fetch_prices
    from .universe import fetch_universe, load_universe, save_universe

    if args.refresh_universe:
        save_universe(fetch_universe(include_otc=not args.twse_only))
    uni = load_universe()
    tickers = list(uni["ticker"]) + [BENCHMARK]
    print(f"fetching {len(tickers)} symbols from {args.start}")
    have = fetch_prices(tickers, start=args.start, refresh=args.refresh)
    print(f"cached {len(have)} symbols")
    panel = build_panel()
    print(f"panel: {panel.close.shape[0]} dates x {panel.close.shape[1]} stocks")
    print(f"range: {panel.dates[0].date()} .. {panel.dates[-1].date()}")
    return 0


def cmd_mine(args: argparse.Namespace) -> int:
    """Mine up-trend segments and inverse-solve the filter for a target (n, m)."""
    from .data import load_panel
    from .mining import (
        DEFAULT_BASE_MAX_STDEV,
        DEFAULT_BASE_MIN_SLOPE,
        build_pool,
        load_pool,
        pool_meta,
        save_pool,
        solve_nm,
    )

    panel = load_panel()

    wanted = pool_meta(
        min_len=args.min_len,
        max_len=args.max_len,
        base_max_stdev=DEFAULT_BASE_MAX_STDEV,
        base_min_slope=DEFAULT_BASE_MIN_SLOPE,
        max_iou=args.max_iou,
        keep_per_band=args.keep_per_band,
        n_dates=len(panel.dates),
        n_stocks=len(panel.stock_ids),
    )

    pool = None
    if POOL_NPZ.exists() and not args.rebuild:
        cached = load_pool(POOL_NPZ)
        if cached.meta == wanted:
            print(f"loading cached pool from {POOL_NPZ}")
            pool = cached
        else:
            changed = [
                k for k in wanted if cached.meta.get(k) != wanted[k]
            ]
            print(f"cached pool was mined with different settings ({', '.join(changed)}); re-mining")

    if pool is None:
        pool = build_pool(
            panel,
            min_len=args.min_len,
            max_len=args.max_len,
            max_iou=args.max_iou,
            keep_per_band=args.keep_per_band,
        )
        save_pool(pool, POOL_NPZ)
        print(f"pool saved to {POOL_NPZ}")
    print(f"candidate pool: {len(pool)} segments over {len(pool.stock_ids)} symbols")

    sol = solve_nm(
        pool,
        target_n=args.n,
        target_m=args.m,
        weight_m=args.weight_m,
        weight_n=args.weight_n,
    )
    print("\n=== inverse (n, m) solution ===")
    print(sol.describe())

    from .mining import apply_filter

    mask = apply_filter(pool, sol.filt)
    sub = _subset(pool, mask)
    df = sub.to_frame(panel).sort_values(["ID", "begin_date"])
    out = args.out or SEGMENTS_CSV
    df.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\nwrote {len(df)} segments to {out}")
    print(df.head(10).to_string(index=False))
    return 0


def _subset(pool, mask):
    from .mining import SegmentPool

    return SegmentPool(
        stock_idx=pool.stock_idx[mask],
        start=pool.start[mask],
        length=pool.length[mask],
        slope=pool.slope[mask],
        stdev=pool.stdev[mask],
        r2=pool.r2[mask],
        tstat=pool.tstat[mask],
        stock_ids=pool.stock_ids,
        dates=pool.dates,
    )


def cmd_view_segments(args: argparse.Namespace) -> int:
    from .viewers.segment_viewer import run

    run(args.csv or SEGMENTS_CSV)
    return 0


def cmd_backtest(args: argparse.Namespace) -> int:
    from .backtest import run_backtest_cli

    return run_backtest_cli(args)


def cmd_view_backtest(args: argparse.Namespace) -> int:
    from .viewers.backtest_viewer import run

    run(args.result or (RESULTS_DIR / "backtest.json"))
    return 0


def cmd_wfcv(args: argparse.Namespace) -> int:
    from .wfcv import run_wfcv_cli

    return run_wfcv_cli(args)


def cmd_report(args: argparse.Namespace) -> int:
    from .report import run_report_cli

    return run_report_cli(args)


def cmd_sweep(args: argparse.Namespace) -> int:
    from .sweep import run_sweep_cli

    return run_sweep_cli(args)


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="twstable",
        description="Long-term stable-uptrend stock strategy for the Taiwan market",
    )
    sub = p.add_subparsers(dest="command", required=True)

    f = sub.add_parser("fetch", help="download TW price history from yfinance")
    f.add_argument("--start", default=DEFAULT_START)
    f.add_argument("--refresh", action="store_true", help="re-download cached symbols")
    f.add_argument("--refresh-universe", action="store_true")
    f.add_argument("--twse-only", action="store_true", help="exclude OTC (上櫃)")
    f.set_defaults(func=cmd_fetch)

    m = sub.add_parser("mine", help="mine up-trend segments for a target (n, m)")
    m.add_argument("-n", type=int, default=1000, help="target number of segments")
    m.add_argument("-m", type=int, default=150, help="target number of distinct stocks")
    m.add_argument("--weight-m", type=float, default=2.0)
    m.add_argument("--weight-n", type=float, default=1.0)
    m.add_argument("--min-len", type=int, default=40)
    m.add_argument("--max-len", type=int, default=1500)
    m.add_argument("--max-iou", type=float, default=0.75)
    m.add_argument("--keep-per-band", type=int, default=60)
    m.add_argument("--rebuild", action="store_true", help="re-mine the candidate pool")
    m.add_argument("--out", help="output CSV path")
    m.set_defaults(func=cmd_mine)

    v = sub.add_parser("view-segments", help="GUI viewer for a segments CSV")
    v.add_argument("--csv", help="segments CSV (default results/segments.csv)")
    v.set_defaults(func=cmd_view_segments)

    b = sub.add_parser("backtest", help="backtest the portfolio strategy")
    b.add_argument("--start", default="2000-01-01")
    b.add_argument("--end", default=None)
    b.add_argument("--out", help="output JSON path")
    b.add_argument("--params", help="JSON file or inline JSON of strategy params")
    b.set_defaults(func=cmd_backtest)

    vb = sub.add_parser("view-backtest", help="GUI viewer for backtest results")
    vb.add_argument("--result", help="backtest JSON (default results/backtest.json)")
    vb.set_defaults(func=cmd_view_backtest)

    w = sub.add_parser("wfcv", help="walk-forward cross-validated hyperparameter search")
    w.add_argument("--start", default="2000-01-01")
    w.add_argument("--end", default=None)
    w.add_argument("--train-years", type=float, default=6.0)
    w.add_argument("--test-years", type=float, default=2.0)
    w.add_argument("--trials", type=int, default=300)
    w.add_argument("--seed", type=int, default=20260805)
    w.add_argument("--out", help="output JSON path")
    w.set_defaults(func=cmd_wfcv)

    r = sub.add_parser("report", help="build the shareable HTML report (docs/index.html)")
    r.add_argument("--result", help="backtest JSON to render (default results/wfcv.json)")
    r.add_argument("--out", help="output HTML path (default docs/index.html)")
    r.add_argument(
        "--fragment",
        action="store_true",
        help="emit body-only HTML for a host that supplies the page shell",
    )
    r.add_argument("--freq", default="W", help="resampling frequency for the chart (default W)")
    r.set_defaults(func=cmd_report)

    s = sub.add_parser("sweep", help="sweep the monthly trade day, holiday rule and book size")
    s.add_argument("--wfcv", help="WFCV result whose folds to reuse (default results/wfcv.json)")
    s.add_argument("--out-dir", help="output directory (default results/hyper)")
    s.add_argument("--workers", type=int, default=0, help="processes (default: all cores)")
    s.set_defaults(func=cmd_sweep)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
