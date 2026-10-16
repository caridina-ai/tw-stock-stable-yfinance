"""Turn the sweep's ``grid.csv`` into figures, ``summary.json`` and ``SUMMARY.md``.

Nothing here runs a backtest: it only reads what :mod:`twstable.sweep` saved,
so the presentation can be rebuilt (or replaced by a viewer) in seconds.

Three questions, each answered with the walk-forward (``wf``) rows first and
the production (``prod``) rows as the cross-check:

* **Which day of the month?**  Rather than trusting the best cell of a noisy
  heatmap, ask whether a day's advantage *repeats*.  For every calendar year
  the 28 days are ranked by that year's return; Kendall's W measures how much
  the rankings agree across years (0 = unrelated, 1 = identical).  Its null
  distribution comes from shifting each year's day order by an independent
  random offset, which keeps neighbouring days alike (day 10 and day 11 often
  trade on the same date) but breaks any link between years.
* **Before or after a holiday?**  The two rules differ only in months where the
  chosen day is not a trading day, so they are compared pair by pair.
* **How few names?**  For each book size the spread over all 28 days shows how
  much of the result is timing luck; the threshold is the smallest size whose
  typical result stays close to the best size's.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

DAYS = np.arange(1, 29)
SIZES = np.arange(1, 21)

#: A size counts as "as good as the best" when its median Sharpe over the 28
#: days is within this fraction of the best size's median.
NEAR_BEST = 0.95

# sequential blue ramp, light -> dark, and the blue <-> red diverging pair
SEQ = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
DIV = ["#184f95", "#3987e5", "#9ec5f4", "#f0efec", "#f4a3a2", "#e34948", "#a32322"]
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"]
INK, INK2, GRID = "#0b0b0b", "#52514e", "#e4e3df"


def year_columns(grid: pd.DataFrame) -> list[str]:
    return sorted(c for c in grid.columns if c.startswith("y") and c[1:].isdigit())


def select(grid: pd.DataFrame, **kw) -> pd.DataFrame:
    mask = np.ones(len(grid), dtype=bool)
    for k, v in kw.items():
        mask &= (grid[k] == v).to_numpy()
    return grid[mask]


def heat(grid: pd.DataFrame, metric: str, **kw) -> pd.DataFrame:
    """``metric`` as a (max_m x day) table for one protocol/holiday/cap/weighting."""
    rows = select(grid[grid["day"] > 0], **kw)
    return rows.pivot(index="max_m", columns="day", values=metric).reindex(
        index=SIZES, columns=DAYS
    )


def smooth(z: np.ndarray, d_day: int = 2, d_size: int = 1) -> np.ndarray:
    """Neighbourhood mean: days wrap around the month, sizes are clipped."""
    out = np.full_like(z, np.nan, dtype="float64")
    n_size, n_day = z.shape
    for i in range(n_size):
        lo, hi = max(0, i - d_size), min(n_size, i + d_size + 1)
        cols = [(j + k) % n_day for j in range(n_day) for k in range(-d_day, d_day + 1)]
        block = z[lo:hi][:, cols].reshape(hi - lo, n_day, 2 * d_day + 1)
        out[i] = np.nanmean(block, axis=(0, 2))
    return out


# ---------------------------------------------------------------------------
# day of month
# ---------------------------------------------------------------------------


def _kendall_w(mat: np.ndarray) -> np.ndarray:
    """Kendall's W of the rows' rankings; ``mat`` is (..., years, days)."""
    ranks = mat.argsort(axis=-1).argsort(axis=-1) + 1.0
    m, n = ranks.shape[-2:]
    r = ranks.sum(axis=-2)
    s = ((r - r.mean(axis=-1, keepdims=True)) ** 2).sum(axis=-1)
    return 12.0 * s / (m**2 * (n**3 - n))


def day_consistency(
    grid: pd.DataFrame, sizes: list[int], n_perm: int = 20000, seed: int = 0, **kw
) -> dict:
    """Does a day's yearly advantage repeat across years?"""
    years = year_columns(grid)
    rows = select(grid[(grid["day"] > 0) & grid["max_m"].isin(sizes)], **kw)
    by_day = rows.groupby("day")[years].mean().reindex(DAYS)
    mat = by_day.to_numpy().T  # years x days
    mat = mat[~np.isnan(mat).any(axis=1)]
    n_years = len(mat)

    excess = mat - mat.mean(axis=1, keepdims=True)
    profile = excess.mean(axis=0)
    se = excess.std(axis=0, ddof=1) / np.sqrt(n_years)

    w_obs = float(_kendall_w(mat))
    rng = np.random.default_rng(seed)
    shifts = rng.integers(0, len(DAYS), size=(n_perm, n_years))
    idx = (np.arange(len(DAYS))[None, None, :] + shifts[:, :, None]) % len(DAYS)
    perm = mat[np.arange(n_years)[None, :, None], idx]
    w_null = _kendall_w(perm)
    p = float((1 + (w_null >= w_obs).sum()) / (1 + n_perm))

    return {
        "sizes": [int(s) for s in sizes],
        "years": n_years,
        "kendall_w": w_obs,
        "p_value": p,
        "null_w_median": float(np.median(w_null)),
        "profile": [float(x) for x in profile],
        "profile_se": [float(x) for x in se],
        "max_abs_t": float(np.nanmax(np.abs(profile / se))),
    }


# ---------------------------------------------------------------------------
# holiday rule
# ---------------------------------------------------------------------------


def holiday_effect(grid: pd.DataFrame, **kw) -> dict:
    """``next`` minus ``prev`` on the same (day, size), plus a per-year view."""
    keys = ["day", "max_m"]
    nxt = select(grid[grid["day"] > 0], holiday="next", **kw).set_index(keys)
    prv = select(grid[grid["day"] > 0], holiday="prev", **kw).set_index(keys)
    nxt, prv = nxt.align(prv, join="inner", axis=0)
    years = year_columns(grid)
    d_year = (nxt[years] - prv[years]).mean(axis=0).dropna()  # average over cells
    out = {}
    for metric in ("sharpe", "cagr", "max_drawdown"):
        diff = (nxt[metric] - prv[metric]).to_numpy()
        out[metric] = {"mean": float(diff.mean()), "share_next_better": float((diff > 0).mean())}
    out["yearly_mean_diff"] = float(d_year.mean())
    out["yearly_t"] = float(d_year.mean() / (d_year.std(ddof=1) / np.sqrt(len(d_year))))
    out["years"] = len(d_year)
    return out


# ---------------------------------------------------------------------------
# book size
# ---------------------------------------------------------------------------


def size_table(grid: pd.DataFrame, **kw) -> pd.DataFrame:
    """Per size, the spread over every monthly day and both holiday rules."""
    rows = select(grid[grid["day"] > 0], **kw)
    g = rows.groupby("max_m")
    out = pd.DataFrame({
        "sharpe_p10": g["sharpe"].quantile(0.10),
        "sharpe_med": g["sharpe"].median(),
        "sharpe_p90": g["sharpe"].quantile(0.90),
        "cagr_p10": g["cagr"].quantile(0.10),
        "cagr_med": g["cagr"].median(),
        "cagr_p90": g["cagr"].quantile(0.90),
        "mdd_p10": g["max_drawdown"].quantile(0.10),
        "mdd_med": g["max_drawdown"].median(),
        "mdd_p90": g["max_drawdown"].quantile(0.90),
        "worst_year_med": g["worst_year"].median(),
        "avg_held_med": g["avg_held"].median(),
        "invested_med": g["invested"].median(),
    })
    return out.reindex(SIZES)


def size_threshold(table: pd.DataFrame, near: float = NEAR_BEST) -> int:
    """Smallest size from which every larger size stays within ``near`` of the best."""
    med = table["sharpe_med"]
    ok = med >= near * med.max()
    for m in SIZES:
        if ok.loc[m:].all():
            return int(m)
    return int(SIZES[-1])


# ---------------------------------------------------------------------------
# figures
# ---------------------------------------------------------------------------


def _mpl():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    have = {f.name for f in font_manager.fontManager.ttflist}
    for name in ("Noto Sans CJK TC", "WenQuanYi Zen Hei", "Microsoft JhengHei", "PingFang TC"):
        if name in have:
            plt.rcParams["font.sans-serif"] = [name, "DejaVu Sans"]
            break
    plt.rcParams.update({
        "font.family": "sans-serif", "axes.unicode_minus": False,
        "axes.edgecolor": GRID, "axes.labelcolor": INK2, "xtick.color": INK2,
        "ytick.color": INK2, "text.color": INK, "axes.titlecolor": INK,
        "figure.facecolor": "white", "axes.facecolor": "white", "savefig.dpi": 130,
    })
    return plt


def _cmap(colors: list[str], name: str):
    from matplotlib.colors import LinearSegmentedColormap

    return LinearSegmentedColormap.from_list(name, colors)


def _heat_axes(ax, z: pd.DataFrame, cmap, vmin, vmax, fmt: str, mark=None, label=""):
    im = ax.imshow(z.to_numpy(), origin="lower", aspect="auto", cmap=cmap, vmin=vmin, vmax=vmax,
                   extent=(0.5, 28.5, 0.5, 20.5), interpolation="nearest")
    ax.set_xticks(range(1, 29, 3))
    ax.set_yticks(range(1, 21, 2))
    ax.set_xlabel("每月換股日（成交日）")
    ax.set_ylabel("持股檔數 max_m")
    ax.tick_params(length=0)
    for s in ax.spines.values():
        s.set_visible(False)
    if mark is not None:
        ax.plot(*mark, marker="o", ms=11, mfc="none", mec="#eb6834", mew=2)
    cb = ax.figure.colorbar(im, ax=ax, fraction=0.04, pad=0.02)
    cb.outline.set_visible(False)
    cb.ax.tick_params(length=0, labelsize=8)
    cb.set_label(label, color=INK2)
    cb.ax.yaxis.set_major_formatter(fmt)
    return im


def plot_heatmaps(grid: pd.DataFrame, out: Path, rec: dict, protocol: str, **kw) -> Path:
    plt = _mpl()
    fig, axes = plt.subplots(1, 3, figsize=(17, 5.2), constrained_layout=True)
    mark = (rec["day"], rec["max_m"]) if protocol == "wf" else None
    for ax, (metric, title, fmt, label) in zip(axes, [
        ("sharpe", "Sharpe（越深越好）", "{x:.2f}", "Sharpe"),
        ("cagr", "年化報酬 CAGR（越深越好）", "{x:.0%}", "CAGR"),
        ("max_drawdown", "最大回檔 MDD（越深越淺＝越好）", "{x:.0%}", "MDD"),
    ], strict=True):
        z = heat(grid, metric, protocol=protocol, **kw)
        lo, hi = np.nanpercentile(z, 2), np.nanpercentile(z, 98)
        _heat_axes(ax, z, _cmap(SEQ, "seq"), lo, hi, fmt, mark, label)
        ax.set_title(title, loc="left", fontsize=11)
    what = "walk-forward 樣本外 2006–2026" if protocol == "wf" else "上線版參數全期 2001–2026"
    fig.suptitle(f"換股日 × 持股檔數（{what}；{_kw_label(kw)}）", x=0.01, ha="left", fontsize=13)
    path = out / f"heatmap_{protocol}.png"
    fig.savefig(path)
    plt.close(fig)
    return path


def plot_smoothed(grid: pd.DataFrame, out: Path, rec: dict, **kw) -> Path:
    plt = _mpl()
    z = heat(grid, "sharpe", protocol="wf", **kw)
    sm = pd.DataFrame(smooth(z.to_numpy()), index=z.index, columns=z.columns)
    fig, ax = plt.subplots(figsize=(8.5, 5.4), constrained_layout=True)
    lo, hi = np.nanpercentile(sm, 2), np.nanpercentile(sm, 98)
    _heat_axes(ax, sm, _cmap(SEQ, "seq"), lo, hi, "{x:.2f}", (rec["day"], rec["max_m"]), "Sharpe")
    ax.contour(np.arange(1, 29), np.arange(1, 21), sm.to_numpy(),
               levels=[rec["plateau_level"]], colors=["#eb6834"], linewidths=1.2)
    ax.set_title(
        f"Sharpe 平滑後（±2 日、±1 檔平均）；橘線內 ≥ {rec['plateau_level']:.2f}，圓圈＝建議",
        loc="left", fontsize=11)
    path = out / "heatmap_smoothed.png"
    fig.savefig(path)
    plt.close(fig)
    return path


def plot_days(dc: dict, out: Path) -> Path:
    plt = _mpl()
    prof = np.array(dc["profile"]) * 100
    se = np.array(dc["profile_se"]) * 100
    fig, ax = plt.subplots(figsize=(10, 4.2), constrained_layout=True)
    ax.axhline(0, color=INK2, lw=1)
    ax.fill_between(DAYS, prof - 2 * se, prof + 2 * se, color=SEQ[1], alpha=0.6, lw=0,
                    label="±2 標準誤（跨年份）")
    ax.plot(DAYS, prof, color=SERIES[0], lw=2, marker="o", ms=4, label="平均每年多賺（百分點）")
    ax.set_xticks(DAYS)
    ax.set_xlim(0.5, 28.5)
    ax.set_xlabel("每月換股日（成交日）")
    ax.set_ylabel("相對 28 日平均的年報酬（百分點）")
    ax.grid(axis="y", color=GRID, lw=0.8)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.legend(frameon=False, loc="upper right")
    ax.set_title(
        f"換股日有沒有固定的好壞？Kendall's W = {dc['kendall_w']:.3f}，"
        f"p = {dc['p_value']:.2f}（{dc['years']} 年；檔數 {dc['sizes'][0]}–{dc['sizes'][-1]} 平均）",
        loc="left", fontsize=11)
    path = out / "day_profile.png"
    fig.savefig(path)
    plt.close(fig)
    return path


def plot_sizes(tables: dict[str, pd.DataFrame], out: Path, threshold: int) -> Path:
    plt = _mpl()
    fig, axes = plt.subplots(1, 3, figsize=(17, 4.8), constrained_layout=True)
    for ax, (key, title, pct) in zip(axes, [
        ("sharpe", "Sharpe", False), ("cagr", "年化報酬 CAGR", True), ("mdd", "最大回檔 MDD", True),
    ], strict=True):
        for (label, t), color in zip(tables.items(), SERIES, strict=False):
            k = 100 if pct else 1
            ax.fill_between(SIZES, t[f"{key}_p10"] * k, t[f"{key}_p90"] * k, color=color,
                            alpha=0.12, lw=0)
            ax.plot(SIZES, t[f"{key}_med"] * k, color=color, lw=2, label=label)
        ax.axvline(threshold, color=INK2, lw=1, ls="--")
        ax.set_xticks(range(1, 21, 1 if ax is axes[0] else 2))
        ax.set_xlabel("持股檔數 max_m")
        ax.set_title(title + ("（%）" if pct else ""), loc="left", fontsize=11)
        ax.grid(axis="y", color=GRID, lw=0.8)
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
    axes[0].legend(frameon=False, loc="lower right", fontsize=9)
    fig.suptitle(
        f"持股檔數：線＝28 個換股日 × 2 假日規則的中位數，色帶＝10%–90%（換股日運氣）；"
        f"虛線＝臨界值 {threshold} 檔（walk-forward）",
        x=0.01, ha="left", fontsize=12)
    path = out / "sizes.png"
    fig.savefig(path)
    plt.close(fig)
    return path


def _kw_label(kw: dict) -> str:
    parts = []
    if "holiday" in kw:
        parts.append("假日延後" if kw["holiday"] == "next" else "假日提前")
    if "cap" in kw:
        parts.append("權重上限 1/max_m" if kw["cap"] else "無權重上限")
    if "weighting" in kw:
        parts.append("等權重" if kw["weighting"] == "equal" else "分數權重")
    return "、".join(parts)


# ---------------------------------------------------------------------------
# putting it together
# ---------------------------------------------------------------------------

FAMILIES = [(c, w) for c in (False, True) for w in ("equal", "score")]


def family_label(cap: bool, weighting: str) -> str:
    return _kw_label({"cap": cap, "weighting": weighting})


def analyse(grid: pd.DataFrame) -> dict:
    """Everything the figures and SUMMARY.md show, as plain numbers."""
    out: dict = {"families": {}}
    ref = {p: select(grid, protocol=p, day=0, max_m=0).iloc[0] for p in ("wf", "prod")}
    out["reference"] = {p: {k: float(r[k]) for k in ("cagr", "sharpe", "max_drawdown")}
                        for p, r in ref.items()}

    for cap, w in FAMILIES:
        fam = {}
        for p in ("wf", "prod"):
            t = size_table(grid, protocol=p, cap=cap, weighting=w)
            fam[p] = {
                "threshold": size_threshold(t),
                "table": json.loads(t.to_json(orient="index")),
            }
        fam["holiday"] = {p: holiday_effect(grid, protocol=p, cap=cap, weighting=w)
                          for p in ("wf", "prod")}
        out["families"][family_label(cap, w)] = fam
    return out


def build_report(grid: pd.DataFrame, out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = analyse(grid)
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
                                          "utf-8")
    return summary


def run_report_cli(args) -> int:
    from .sweep import DEFAULT_DIR, GRID_CSV

    out_dir = Path(args.out_dir) if args.out_dir else DEFAULT_DIR
    build_report(pd.read_csv(out_dir / GRID_CSV), out_dir)
    print(f"report written to {out_dir}")
    return 0
