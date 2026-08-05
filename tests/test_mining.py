"""The inverse (n, m) solver must agree with brute force, exactly."""

from __future__ import annotations

import numpy as np
import pandas as pd

from twstable.mining import Filter, SegmentPool, _iou, apply_filter, count_nm, solve_nm


def make_pool(seed: int, n_seg: int = 4000, n_stocks: int = 120) -> SegmentPool:
    rng = np.random.default_rng(seed)
    return SegmentPool(
        stock_idx=rng.integers(0, n_stocks, n_seg).astype("int32"),
        start=rng.integers(0, 3000, n_seg).astype("int32"),
        length=rng.choice([40, 60, 90, 150, 250, 400, 800], n_seg).astype("int32"),
        slope=np.exp(rng.normal(0.15, 0.4, n_seg)),
        stdev=np.exp(np.abs(rng.normal(0.05, 0.05, n_seg))),
        r2=rng.uniform(0, 1, n_seg),
        tstat=rng.normal(3, 2, n_seg),
        stock_ids=[f"S{i:04d}" for i in range(n_stocks)],
        dates=pd.bdate_range("2005-01-03", periods=3800),
    )


def brute_force_best(pool, target_n, target_m, weight_m=2.0, weight_n=1.0):
    """Evaluate every threshold combination the solver could pick, one by one."""
    lengths = sorted({int(v) for v in np.unique(pool.length)})
    stds = np.unique(pool.stdev)
    slopes = np.unique(pool.slope)
    # Sub-sample the continuous axes; the solver's own grid is quantile based,
    # so we only need a dense-enough common reference.
    stds = stds[:: max(1, len(stds) // 60)]
    slopes = slopes[:: max(1, len(slopes) // 60)]

    best = None
    for min_len in lengths:
        for a in stds:
            for b in slopes:
                filt = Filter(min_len, float(a), float(b))
                n, m = count_nm(pool, filt)
                loss = weight_m * abs(m - target_m) / target_m + weight_n * abs(n - target_n) / target_n
                if best is None or loss < best[0]:
                    best = (loss, filt, n, m)
    return best


def test_solution_counts_are_self_consistent() -> None:
    """Whatever the solver reports as (n, m) must be what the filter really yields."""
    for seed in range(4):
        pool = make_pool(seed)
        for target_n, target_m in [(500, 40), (1200, 90), (200, 20)]:
            sol = solve_nm(pool, target_n, target_m)
            n, m = count_nm(pool, sol.filt)
            assert (n, m) == (sol.n, sol.m), (sol.filt, n, m, sol.n, sol.m)


def test_matches_brute_force() -> None:
    """The exact grid search must not be beaten by exhaustive enumeration."""
    pool = make_pool(7, n_seg=1500, n_stocks=60)
    for target_n, target_m in [(300, 25), (600, 45)]:
        sol = solve_nm(pool, target_n, target_m, n_bins=200)
        bf_loss, bf_filt, bf_n, bf_m = brute_force_best(pool, target_n, target_m)
        # The solver searches a superset-quality grid; allow only float noise.
        assert sol.loss <= bf_loss + 1e-9, (
            f"solver {sol.loss:.6f} {sol.filt} n={sol.n} m={sol.m} worse than "
            f"brute force {bf_loss:.6f} {bf_filt} n={bf_n} m={bf_m}"
        )


def test_filter_semantics() -> None:
    pool = make_pool(3)
    filt = Filter(min_length=150, max_stdev=1.05, min_slope=1.10)
    mask = apply_filter(pool, filt)
    assert (pool.length[mask] >= 150).all()
    assert (pool.stdev[mask] <= 1.05).all()
    assert (pool.slope[mask] >= 1.10).all()
    # nothing that should have passed was dropped
    manual = (pool.length >= 150) & (pool.stdev <= 1.05) & (pool.slope >= 1.10)
    assert np.array_equal(mask, manual)


def test_tighter_filter_is_monotone() -> None:
    """Raising any threshold can never increase n or m."""
    pool = make_pool(5)
    base = Filter(100, 1.08, 1.05)
    n0, m0 = count_nm(pool, base)
    for tighter in (
        Filter(200, 1.08, 1.05),
        Filter(100, 1.04, 1.05),
        Filter(100, 1.08, 1.30),
    ):
        n1, m1 = count_nm(pool, tighter)
        assert n1 <= n0 and m1 <= m0


def test_pool_round_trips_through_disk(tmp_path) -> None:
    from twstable.mining import load_pool, pool_meta, save_pool

    pool = make_pool(11)
    pool.meta = pool_meta(min_len=40, max_len=1500, max_iou=0.75, keep_per_band=60)
    path = tmp_path / "pool.npz"
    save_pool(pool, path)
    back = load_pool(path)

    assert back.meta == pool.meta, "settings must survive so a stale cache is detectable"
    assert back.stock_ids == pool.stock_ids
    assert np.array_equal(back.stock_idx, pool.stock_idx)
    assert np.allclose(back.slope, pool.slope)
    assert np.allclose(back.stdev, pool.stdev)
    assert back.dates.equals(pool.dates)
    # and the solver reaches the same answer either way
    assert solve_nm(back, 500, 40).filt == solve_nm(pool, 500, 40).filt


def test_iou_geometry() -> None:
    # identical segments
    assert _iou(0, 100, np.array([0]), np.array([100]))[0] == 1.0
    # disjoint
    assert _iou(0, 100, np.array([100]), np.array([100]))[0] == 0.0
    # half overlap: intersect 50, union 150
    assert np.isclose(_iou(0, 100, np.array([50]), np.array([100]))[0], 50 / 150)
