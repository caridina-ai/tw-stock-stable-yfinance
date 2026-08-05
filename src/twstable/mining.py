"""Mining "long-term stable uptrend" segments, and the inverse (n, m) search.

Two stages:

1. :func:`build_pool` exhaustively fits every window on a length x start grid
   across the whole market, keeps the ones that trend up at all, and thins each
   symbol's near-duplicate windows down to a handful of distinct waves.

2. :func:`solve_nm` answers the inverse question the spec poses -- *given* a
   wanted segment count ``n`` and distinct-symbol count ``m``, which filter
   ``(min_length, max_stdev, min_slope)`` gets closest?  This is solved by exact
   enumeration over a binned grid rather than by hill climbing, so the answer
   does not depend on a starting guess.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from tqdm import tqdm

from .data import Panel
from .trend import build_prefix, window_stats
from .universe import name_map

# ---------------------------------------------------------------------------
# Candidate grid
# ---------------------------------------------------------------------------

#: Permissive pre-filter applied while mining; see :func:`build_pool`.
DEFAULT_BASE_MAX_STDEV = 2.0
DEFAULT_BASE_MIN_SLOPE = 1.0


def length_grid(min_len: int = 40, max_len: int = 1500, ratio: float = 1.15) -> list[int]:
    """Geometric window lengths, so every time scale gets similar coverage."""
    out: list[int] = []
    cur = float(min_len)
    while cur <= max_len:
        val = int(round(cur))
        if not out or val > out[-1]:
            out.append(val)
        cur *= ratio
    return out


#: Segments are de-duplicated inside each band, so every time scale survives
#: instead of long waves swallowing the short ones.
LENGTH_BANDS: tuple[tuple[int, int], ...] = (
    (0, 90),
    (90, 180),
    (180, 365),
    (365, 750),
    (750, 10**9),
)


@dataclass
class SegmentPool:
    """Flat arrays of mined candidate segments, one row per segment."""

    stock_idx: np.ndarray  # int32 index into `stock_ids`
    start: np.ndarray  # int32 row index into the shared date index
    length: np.ndarray  # int32
    slope: np.ndarray  # annualised growth factor
    stdev: np.ndarray  # multiplicative residual dispersion
    r2: np.ndarray
    tstat: np.ndarray  # slope t-statistic, used for ranking
    stock_ids: list[str]
    dates: pd.DatetimeIndex
    meta: dict = field(default_factory=dict)
    """The settings this pool was mined with, so a cached pool built under
    different settings is not silently reused."""

    def __len__(self) -> int:
        return len(self.start)

    def to_frame(self, panel: Panel | None = None) -> pd.DataFrame:
        """Expand to the CSV schema the spec asks for."""
        names = name_map()
        ids = np.asarray(self.stock_ids, dtype=object)[self.stock_idx]
        begin = self.dates[self.start]
        end = self.dates[self.start + self.length - 1]

        df = pd.DataFrame(
            {
                "ID": ids,
                "name": [names.get(str(i), "") for i in ids],
                "begin_date": begin,
                "end_date": end,
                "length": self.length,
                "slope": self.slope,
                "stdev": self.stdev,
                "r2": self.r2,
                "tstat": self.tstat,
            }
        )

        if panel is not None:
            close = panel.filled_close().to_numpy(dtype="float64")
            col = {sid: k for k, sid in enumerate(panel.stock_ids)}
            cols = np.array([col[s] for s in self.stock_ids], dtype="int64")[self.stock_idx]
            df["begin_price"] = close[self.start, cols]
            df["end_price"] = close[self.start + self.length - 1, cols]
            df["total_return"] = df["end_price"] / df["begin_price"] - 1.0

        order = ["ID", "name", "begin_date", "end_date", "length", "begin_price", "end_price",
                 "slope", "stdev", "r2", "tstat", "total_return"]
        return df[[c for c in order if c in df.columns]]


def save_pool(pool: SegmentPool, path) -> None:
    """Persist a mined pool so repeated (n, m) queries skip the mining stage."""
    np.savez_compressed(
        path,
        stock_idx=pool.stock_idx,
        start=pool.start,
        length=pool.length,
        slope=pool.slope,
        stdev=pool.stdev,
        r2=pool.r2,
        tstat=pool.tstat,
        stock_ids=np.asarray(pool.stock_ids, dtype=object),
        dates=pool.dates.values.astype("datetime64[ns]"),
        meta=np.asarray(json.dumps(pool.meta, sort_keys=True)),
    )


def load_pool(path) -> SegmentPool:
    with np.load(path, allow_pickle=True) as z:
        meta = json.loads(str(z["meta"])) if "meta" in z else {}
        return SegmentPool(
            stock_idx=z["stock_idx"],
            start=z["start"],
            length=z["length"],
            slope=z["slope"],
            stdev=z["stdev"],
            r2=z["r2"],
            tstat=z["tstat"],
            stock_ids=[str(s) for s in z["stock_ids"]],
            dates=pd.DatetimeIndex(z["dates"]),
            meta=meta,
        )


def _iou(a_start: int, a_len: int, b_start: np.ndarray, b_len: np.ndarray) -> np.ndarray:
    """Intersection-over-union of one segment against many, on the time axis."""
    a_end, b_end = a_start + a_len, b_start + b_len
    inter = np.minimum(a_end, b_end) - np.maximum(a_start, b_start)
    inter = np.maximum(inter, 0)
    union = a_len + b_len - inter
    return inter / np.maximum(union, 1)


def _suppress(
    start: np.ndarray, length: np.ndarray, rank: np.ndarray, max_iou: float, keep: int
) -> np.ndarray:
    """Greedy non-maximum suppression by time overlap.  Returns kept positions."""
    order = np.argsort(-rank)[: keep * 8]
    chosen: list[int] = []
    c_start = np.empty(keep, dtype="int64")
    c_len = np.empty(keep, dtype="int64")
    n_chosen = 0
    for idx in order:
        if n_chosen and _iou(
            int(start[idx]), int(length[idx]), c_start[:n_chosen], c_len[:n_chosen]
        ).max() > max_iou:
            continue
        chosen.append(int(idx))
        c_start[n_chosen] = start[idx]
        c_len[n_chosen] = length[idx]
        n_chosen += 1
        if n_chosen >= keep:
            break
    return np.asarray(chosen, dtype="int64")


def build_pool(
    panel: Panel,
    min_len: int = 40,
    max_len: int = 1500,
    base_max_stdev: float = DEFAULT_BASE_MAX_STDEV,
    base_min_slope: float = DEFAULT_BASE_MIN_SLOPE,
    max_iou: float = 0.75,
    keep_per_band: int = 60,
    chunk: int = 256,
) -> SegmentPool:
    """Fit the whole length x start grid, then thin to distinct waves per symbol.

    ``base_*`` are deliberately permissive: they only discard windows no sane
    "stable uptrend" filter would ever want, keeping the pool small enough for
    :func:`solve_nm` to enumerate exhaustively.
    """
    log_px = panel.log_filled_close()
    n_dates, n_stocks = log_px.shape
    lengths = [dur for dur in length_grid(min_len, max_len) if dur < n_dates]
    stock_ids = panel.stock_ids

    parts: list[dict[str, np.ndarray]] = []

    for lo in tqdm(range(0, n_stocks, chunk), desc="mine", unit="chunk"):
        hi = min(lo + chunk, n_stocks)
        pref = build_prefix(log_px[:, lo:hi])

        for dur in lengths:
            if dur >= n_dates:
                continue
            stride = max(1, round(dur / 20))
            starts = np.arange(0, n_dates - dur + 1, stride, dtype="int64")
            if not len(starts):
                continue
            st = window_stats(pref, starts, dur)

            slope, stdev = st.slope, st.stdev
            with np.errstate(divide="ignore", invalid="ignore"):
                s_xx = dur * (dur * dur - 1) / 12.0
                tstat = st.beta * np.sqrt(s_xx) / np.maximum(st.sigma, 1e-12)

            ok = (
                st.valid
                & (slope >= base_min_slope)
                & (stdev <= base_max_stdev)
                & np.isfinite(tstat)
            )
            rows, cols = np.nonzero(ok)
            if not len(rows):
                continue
            parts.append(
                {
                    "stock_idx": (cols + lo).astype("int32"),
                    "start": starts[rows].astype("int32"),
                    "length": np.full(len(rows), dur, dtype="int32"),
                    "slope": slope[rows, cols],
                    "stdev": stdev[rows, cols],
                    "r2": st.r2[rows, cols],
                    "tstat": tstat[rows, cols],
                }
            )

    if not parts:
        raise RuntimeError("no candidate segments found")

    cat = {k: np.concatenate([p[k] for p in parts]) for k in parts[0]}
    del parts

    # --- thin near-duplicates, per symbol and per length band ---------------
    band_of = np.zeros(len(cat["length"]), dtype="int8")
    for b, (blo, bhi) in enumerate(LENGTH_BANDS):
        band_of[(cat["length"] >= blo) & (cat["length"] < bhi)] = b

    keys = cat["stock_idx"].astype("int64") * len(LENGTH_BANDS) + band_of
    order = np.argsort(keys, kind="stable")
    keys_sorted = keys[order]
    bounds = np.flatnonzero(np.diff(keys_sorted)) + 1
    groups = np.split(order, bounds)

    kept: list[np.ndarray] = []
    for grp in tqdm(groups, desc="thin", unit="grp"):
        sel = _suppress(
            cat["start"][grp], cat["length"][grp], cat["tstat"][grp], max_iou, keep_per_band
        )
        if len(sel):
            kept.append(grp[sel])

    idx = np.concatenate(kept)
    idx = idx[np.lexsort((cat["start"][idx], cat["stock_idx"][idx]))]

    return SegmentPool(
        stock_idx=cat["stock_idx"][idx],
        start=cat["start"][idx],
        length=cat["length"][idx],
        slope=cat["slope"][idx],
        stdev=cat["stdev"][idx],
        r2=cat["r2"][idx],
        tstat=cat["tstat"][idx],
        stock_ids=stock_ids,
        dates=panel.dates,
        meta=pool_meta(
            min_len=min_len,
            max_len=max_len,
            base_max_stdev=base_max_stdev,
            base_min_slope=base_min_slope,
            max_iou=max_iou,
            keep_per_band=keep_per_band,
            n_dates=int(n_dates),
            n_stocks=int(n_stocks),
        ),
    )


def pool_meta(**kw) -> dict:
    """Normalise mining settings into a comparable fingerprint."""
    return {k: (round(v, 10) if isinstance(v, float) else v) for k, v in sorted(kw.items())}


# ---------------------------------------------------------------------------
# Forward filter and inverse (n, m) search
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Filter:
    """The "long-term stable uptrend" criterion of the spec."""

    min_length: int
    max_stdev: float
    min_slope: float

    def describe(self) -> str:
        return (
            f"min_length={self.min_length}  "
            f"max_stdev={self.max_stdev:.4f}  min_slope={self.min_slope:.4f}"
        )


def apply_filter(pool: SegmentPool, filt: Filter) -> np.ndarray:
    """Boolean mask of segments passing the filter."""
    return (
        (pool.length >= filt.min_length)
        & (pool.stdev <= filt.max_stdev)
        & (pool.slope >= filt.min_slope)
    )


def count_nm(pool: SegmentPool, filt: Filter) -> tuple[int, int]:
    """(n segments, m distinct symbols) for a filter."""
    mask = apply_filter(pool, filt)
    return int(mask.sum()), int(np.unique(pool.stock_idx[mask]).size)


@dataclass
class NMSolution:
    filt: Filter
    n: int
    m: int
    loss: float
    target_n: int
    target_m: int

    def describe(self) -> str:
        return (
            f"{self.filt.describe()}\n"
            f"  -> n={self.n} (target {self.target_n}), "
            f"m={self.m} (target {self.target_m}), loss={self.loss:.5f}"
        )


def _edges(values: np.ndarray, n_bins: int) -> np.ndarray:
    """Quantile bin edges, so resolution follows where the data actually is."""
    qs = np.linspace(0.0, 1.0, n_bins + 1)
    e = np.unique(np.quantile(values, qs))
    if len(e) < 2:
        e = np.array([values.min(), values.min() + 1e-9])
    return e


def solve_nm(
    pool: SegmentPool,
    target_n: int,
    target_m: int,
    weight_m: float = 2.0,
    weight_n: float = 1.0,
    n_bins: int = 160,
) -> NMSolution:
    """Find the filter whose output is closest to ``target_n`` segments / ``target_m`` symbols.

    The search is exhaustive over a grid of candidate thresholds:

    * ``min_length`` -- every distinct length present in the pool.
    * ``max_stdev`` / ``min_slope`` -- quantile bin edges of the pool.

    For a fixed ``min_length``, the segment count over the whole 2-D threshold
    grid is a 2-D cumulative histogram, and the distinct-symbol count comes from
    a per-symbol running minimum of stdev across slope thresholds.  Both are
    O(pool + bins^2), so the entire grid is evaluated exactly and cheaply.

    ``weight_m`` exceeds ``weight_n`` by default because the spec states the
    symbol count as a hard requirement ("only 150 stocks") and the segment count
    as a target ("closest to 1000").
    """
    if len(pool) == 0:
        raise ValueError("empty pool")

    std_edges = _edges(pool.stdev, n_bins)  # thresholds for max_stdev
    slope_edges = _edges(pool.slope, n_bins)  # thresholds for min_slope
    n_a, n_b = len(std_edges), len(slope_edges)
    n_stocks = len(pool.stock_ids)

    # bin index such that stdev <= std_edges[a]  <=>  a_idx <= a
    a_idx = np.searchsorted(std_edges, pool.stdev, side="left")
    # bin index such that slope >= slope_edges[b]  <=>  b_idx >= b
    b_idx = np.searchsorted(slope_edges, pool.slope, side="right") - 1

    valid = (a_idx < n_a) & (b_idx >= 0)
    a_idx, b_idx = a_idx[valid], b_idx[valid]
    lengths = pool.length[valid]
    stocks = pool.stock_idx[valid]

    # Walking min_length downwards makes the surviving subsets nested, so the
    # histogram and the per-symbol minima can be accumulated incrementally and
    # every segment is touched exactly once across the whole search.
    min_lens = sorted({int(v) for v in np.unique(pool.length)}, reverse=True)

    hist = np.zeros(n_a * n_b, dtype="int64")
    # g[s, b] = smallest stdev-bin among symbol s's segments whose slope bin is
    # exactly b; sentinel n_a means "none".
    g = np.full((n_stocks, n_b), n_a, dtype="int32")

    best: NMSolution | None = None
    prev_cut = np.inf
    for min_len in min_lens:
        add = (lengths >= min_len) & (lengths < prev_cut)
        prev_cut = min_len
        if add.any():
            aa, bb, ss = a_idx[add], b_idx[add], stocks[add]
            hist += np.bincount(aa * n_b + bb, minlength=n_a * n_b)
            np.minimum.at(g, (ss, bb), aa.astype("int32"))

        # --- n(a, b): segments with stdev <= edge_a and slope >= edge_b ------
        counts = np.cumsum(hist.reshape(n_a, n_b), axis=0)  # stdev <= edge_a
        counts = np.cumsum(counts[:, ::-1], axis=1)[:, ::-1]  # slope >= edge_b

        # --- m(a, b): distinct symbols --------------------------------------
        # Symbol s counts at (a, b) iff min over b' >= b of g[s, b'] <= a.
        min_a = np.minimum.accumulate(g[:, ::-1], axis=1)[:, ::-1]

        m_hist = np.zeros((n_a + 1, n_b), dtype="int64")
        for b in range(n_b):
            m_hist[:, b] = np.bincount(min_a[:, b], minlength=n_a + 1)
        m_counts = np.cumsum(m_hist[:n_a], axis=0)

        loss = (
            weight_m * np.abs(m_counts - target_m) / target_m
            + weight_n * np.abs(counts - target_n) / target_n
        )
        flat = int(np.argmin(loss))
        a, b = divmod(flat, n_b)
        cand = NMSolution(
            filt=Filter(min_len, float(std_edges[a]), float(slope_edges[b])),
            n=int(counts[a, b]),
            m=int(m_counts[a, b]),
            loss=float(loss[a, b]),
            target_n=target_n,
            target_m=target_m,
        )
        if best is None or cand.loss < best.loss:
            best = cand

    assert best is not None
    return best
