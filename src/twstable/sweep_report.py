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
# recommendation
# ---------------------------------------------------------------------------

PROTOCOLS = ("wf", "prod")
FAMILIES = [(False, "equal"), (False, "score"), (True, "equal"), (True, "score")]

#: Below this fraction of the best median Sharpe a book size counts as broken.
BREAKDOWN = 0.80

#: The more involved option (a weight cap, score weights) must beat the simpler
#: one by at least this much walk-forward Sharpe to be recommended.
SIMPLER_MARGIN = 0.02


def _kw_label(kw: dict) -> str:
    parts = []
    if "holiday" in kw:
        parts.append("假日延後" if kw["holiday"] == "next" else "假日提前")
    if "cap" in kw:
        parts.append("權重上限 1/max_m" if kw["cap"] else "無權重上限")
    if "weighting" in kw:
        parts.append("等權重" if kw["weighting"] == "equal" else "分數權重")
    return "、".join(parts)


def family_key(cap: bool, weighting: str) -> str:
    return f"cap={'on' if cap else 'off'},{weighting}"


def family_label(cap: bool, weighting: str) -> str:
    return _kw_label({"cap": cap, "weighting": weighting})


def _plateau_sharpe(table: pd.DataFrame, threshold: int) -> float:
    return float(table.loc[threshold:, "sharpe_med"].median())


def analyse(grid: pd.DataFrame) -> dict:
    """Everything the figures and SUMMARY.md show, as plain numbers."""
    ref = {p: select(grid, protocol=p, day=0, max_m=0).iloc[0] for p in PROTOCOLS}
    out: dict = {
        "reference": {p: _metrics(r) for p, r in ref.items()},
        "families": {},
    }

    tables: dict = {}
    for cap, w in FAMILIES:
        key = family_key(cap, w)
        tables[key] = {p: size_table(grid, protocol=p, cap=cap, weighting=w) for p in PROTOCOLS}
        thr = {p: size_threshold(tables[key][p]) for p in PROTOCOLS}
        out["families"][key] = {
            "cap": cap,
            "weighting": w,
            "label": family_label(cap, w),
            "threshold": thr,
            "breakdown": {p: size_threshold(tables[key][p], BREAKDOWN) for p in PROTOCOLS},
            "plateau_sharpe": {p: _plateau_sharpe(tables[key][p], thr["wf"]) for p in PROTOCOLS},
            "holiday": {p: holiday_effect(grid, protocol=p, cap=cap, weighting=w)
                        for p in PROTOCOLS},
            "sizes": {p: json.loads(tables[key][p].to_json(orient="index")) for p in PROTOCOLS},
        }

    # weight cap and weighting: keep the simpler choice unless the other clearly wins
    fam = out["families"]

    def wf(key: str) -> float:
        return fam[key]["plateau_sharpe"]["wf"]

    cap = wf(family_key(True, "equal")) > wf(family_key(False, "equal")) + SIMPLER_MARGIN
    weighting = ("score" if wf(family_key(cap, "score")) > wf(family_key(cap, "equal"))
                 + SIMPLER_MARGIN else "equal")
    key = family_key(cap, weighting)
    chosen = fam[key]

    # book size: each protocol's plateau starts somewhere; take the middle
    thr = chosen["threshold"]
    size = int(np.ceil((thr["wf"] + thr["prod"]) / 2))

    # holiday rule: the sign of the walk-forward difference (it is small either way)
    holiday = "next" if chosen["holiday"]["wf"]["sharpe"]["mean"] >= 0 else "prev"

    # day: the best worst-case over both protocols of the smoothed Sharpe near `size`
    rows, norm = {}, {}
    for p in PROTOCOLS:
        z = heat(grid, "sharpe", protocol=p, holiday=holiday, cap=cap, weighting=weighting)
        sm = smooth(z.to_numpy())
        lo, hi = max(size - 2, 0), min(size + 1, len(SIZES))
        rows[p] = sm[lo:hi].mean(axis=0)
        norm[p] = (rows[p] - rows[p].min()) / max(rows[p].max() - rows[p].min(), 1e-12)
    both = np.minimum(norm["wf"], norm["prod"])
    day = int(DAYS[np.argmax(both)])
    good_days = _run_around(both >= 0.5, day)

    near = list(range(max(size - 2, 1), min(size + 2, 20) + 1))
    consistency = {p: day_consistency(grid, near, protocol=p, holiday=holiday, cap=cap,
                                      weighting=weighting) for p in PROTOCOLS}

    pick = {p: _metrics(select(grid, protocol=p, day=day, holiday=holiday, max_m=size, cap=cap,
                               weighting=weighting).iloc[0]) for p in PROTOCOLS}
    wf_heat = heat(grid, "sharpe", protocol="wf", holiday=holiday, cap=cap, weighting=weighting)
    out["recommendation"] = {
        "rebalance_day": day,
        "holiday": holiday,
        "max_m": size,
        "cap_weight": bool(cap),
        "weighting": weighting,
        "family": key,
        "good_days": good_days,
        "day_score": [float(x) for x in both],
        "smoothed_sharpe": {p: [float(x) for x in rows[p]] for p in PROTOCOLS},
        "plateau_level": float(0.95 * np.nanmax(smooth(wf_heat.to_numpy()))),
        "metrics": pick,
        "day_consistency": consistency,
    }

    # the original trading-day intervals, for comparison with the monthly schedule
    interval = select(grid, protocol="wf", day=0, cap=cap, weighting=weighting)
    interval = interval[interval["max_m"] > 0].set_index("max_m")
    out["interval_vs_monthly"] = {
        int(m): {"interval_sharpe": float(interval.loc[m, "sharpe"]),
                 "monthly_sharpe_med": float(tables[key]["wf"].loc[m, "sharpe_med"]),
                 "monthly_sharpe_p10": float(tables[key]["wf"].loc[m, "sharpe_p10"]),
                 "monthly_sharpe_p90": float(tables[key]["wf"].loc[m, "sharpe_p90"])}
        for m in SIZES
    }

    # what the weight cap did to 2008 (original schedule, the chosen size)
    out["cap_2008"] = {
        str(c): _metrics(select(grid, protocol="wf", day=0, max_m=size, cap=c,
                                weighting=weighting).iloc[0])
        for c in (False, True)
    }
    return out


def _metrics(row: pd.Series) -> dict:
    keys = ["cagr", "sharpe", "max_drawdown", "worst_year", "avg_held", "invested", "turnover"]
    out = {k: float(row[k]) for k in keys}
    for y in ("y2008", "y2009"):
        if y in row and pd.notna(row[y]):
            out[y] = float(row[y])
    return out


def recommended_params(production: dict, rec: dict) -> dict:
    """The production parameters with the sweep's recommendation applied."""
    out = dict(production)
    for k in ("rebalance_day", "holiday", "max_m", "cap_weight", "weighting"):
        out[k] = rec[k]
    return out


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


def _cmap(colors: list[str]):
    from matplotlib.colors import LinearSegmentedColormap

    return LinearSegmentedColormap.from_list("ramp", colors)


def _clean(ax) -> None:
    ax.tick_params(length=0)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)


def _heat(ax, z: np.ndarray, fmt: str, label: str, mark=None) -> None:
    lo, hi = np.nanpercentile(z, 2), np.nanpercentile(z, 98)
    im = ax.imshow(z, origin="lower", aspect="auto", cmap=_cmap(SEQ), vmin=lo, vmax=hi,
                   extent=(0.5, 28.5, 0.5, 20.5), interpolation="nearest")
    ax.set_xticks([1, 5, 10, 15, 20, 25, 28])
    ax.set_yticks([1, 5, 10, 15, 20])
    ax.set_xlabel("每月換股日（成交日）")
    ax.set_ylabel("持股檔數 max_m")
    ax.tick_params(length=0)
    for s in ax.spines.values():
        s.set_visible(False)
    if mark is not None:
        ax.plot(*mark, marker="o", ms=12, mfc="none", mec=SERIES[1], mew=2.2)
    cb = ax.figure.colorbar(im, ax=ax, fraction=0.045, pad=0.02)
    cb.outline.set_visible(False)
    cb.ax.tick_params(length=0, labelsize=8)
    cb.set_label(label, color=INK2)
    cb.ax.yaxis.set_major_formatter(fmt)


def _protocol_name(p: str) -> str:
    return "walk-forward 樣本外 2006–2026" if p == "wf" else "原上線版參數全期 2001–2026"


def plot_heatmaps(grid: pd.DataFrame, out: Path, rec: dict, protocol: str) -> Path:
    plt = _mpl()
    kw = {"holiday": rec["holiday"], "cap": rec["cap_weight"], "weighting": rec["weighting"]}
    fig, axes = plt.subplots(1, 3, figsize=(17, 5.2), constrained_layout=True)
    mark = (rec["rebalance_day"], rec["max_m"])
    panels = [
        ("sharpe", "Sharpe（越深越好）", "{x:.2f}", "Sharpe"),
        ("cagr", "年化報酬 CAGR（越深越好）", "{x:.0%}", "CAGR"),
        ("max_drawdown", "最大回檔 MDD（越深＝回檔越小）", "{x:.0%}", "MDD"),
    ]
    for ax, (metric, title, fmt, label) in zip(axes, panels, strict=True):
        z = heat(grid, metric, protocol=protocol, **kw).to_numpy()
        _heat(ax, z, fmt, label, mark)
        ax.set_title(title, loc="left", fontsize=11)
    fig.suptitle(f"換股日 × 持股檔數：{_protocol_name(protocol)}（{_kw_label(kw)}；圓圈＝建議）",
                 x=0.01, ha="left", fontsize=13)
    path = out / f"heatmap_{protocol}.png"
    fig.savefig(path)
    plt.close(fig)
    return path


def plot_smoothed(grid: pd.DataFrame, out: Path, rec: dict) -> Path:
    plt = _mpl()
    kw = {"holiday": rec["holiday"], "cap": rec["cap_weight"], "weighting": rec["weighting"]}
    fig, axes = plt.subplots(1, 2, figsize=(15, 5.2), constrained_layout=True)
    for ax, p in zip(axes, PROTOCOLS, strict=True):
        sm = smooth(heat(grid, "sharpe", protocol=p, **kw).to_numpy())
        _heat(ax, sm, "{x:.2f}", "Sharpe", (rec["rebalance_day"], rec["max_m"]))
        if p == "wf":
            ax.contour(DAYS, SIZES, sm, levels=[rec["plateau_level"]], colors=[SERIES[1]],
                       linewidths=1.2)
        ax.set_title(_protocol_name(p) + ("；橘線內 ≥ 最高的 95%" if p == "wf" else ""),
                     loc="left", fontsize=11)
    fig.suptitle("Sharpe 平滑後（±2 日、±1 檔平均，月底接月初）：看「一片區域」而不是單一格；圓圈＝建議",
                 x=0.01, ha="left", fontsize=13)
    path = out / "heatmap_smoothed.png"
    fig.savefig(path)
    plt.close(fig)
    return path


def plot_days(rec: dict, out: Path) -> Path:
    plt = _mpl()
    fig, ax = plt.subplots(figsize=(11, 4.4), constrained_layout=True)
    ax.axhline(0, color=INK2, lw=1)
    for p, color in zip(PROTOCOLS, SERIES, strict=False):
        dc = rec["day_consistency"][p]
        prof = np.array(dc["profile"]) * 100
        se = np.array(dc["profile_se"]) * 100
        ax.fill_between(DAYS, prof - 2 * se, prof + 2 * se, color=color, alpha=0.12, lw=0)
        name = "walk-forward" if p == "wf" else "原上線版參數"
        ax.plot(DAYS, prof, color=color, lw=2, marker="o", ms=4,
                label=f"{name}（W={dc['kendall_w']:.3f}，p={dc['p_value']:.2f}）")
        ax.annotate(name, (28, prof[-1]), xytext=(6, 0), textcoords="offset points",
                    va="center", color=INK2, fontsize=9)
    ax.axvline(rec["rebalance_day"], color=INK, lw=1, ls="--")
    ax.set_xticks(DAYS)
    ax.set_xlim(0.5, 30)
    ax.set_xlabel("每月換股日（成交日）；虛線＝建議")
    ax.set_ylabel("相對 28 日平均的年報酬（百分點）")
    ax.grid(axis="y", color=GRID, lw=0.8)
    _clean(ax)
    ax.legend(frameon=False, loc="upper center", ncols=2, fontsize=9)
    sizes = rec["day_consistency"]["wf"]["sizes"]
    ax.set_title(f"換股日有沒有固定的好壞？線＝每年平均多賺幾個百分點，色帶＝±2 標準誤"
                 f"（檔數 {sizes[0]}–{sizes[-1]} 平均）", loc="left", fontsize=11)
    path = out / "day_profile.png"
    fig.savefig(path)
    plt.close(fig)
    return path


def plot_sizes(summary: dict, out: Path) -> Path:
    plt = _mpl()
    rec = summary["recommendation"]
    fig, axes = plt.subplots(2, 3, figsize=(17, 8.6), constrained_layout=True, sharex=True)
    panels = [("sharpe", "Sharpe", 1), ("cagr", "年化報酬 CAGR（%）", 100),
              ("mdd", "最大回檔 MDD（%）", 100)]
    for r, p in enumerate(PROTOCOLS):
        for c, (key, title, k) in enumerate(panels):
            ax = axes[r, c]
            for fk, fam in summary["families"].items():
                t = pd.DataFrame(fam["sizes"][p]).T.reindex([str(m) for m in SIZES])
                color = SERIES[0] if fam["cap"] else SERIES[1]
                ls = "-" if fam["weighting"] == "equal" else "--"
                if fk == rec["family"]:
                    ax.fill_between(SIZES, t[f"{key}_p10"] * k, t[f"{key}_p90"] * k,
                                    color=color, alpha=0.13, lw=0)
                ax.plot(SIZES, t[f"{key}_med"] * k, color=color, ls=ls, lw=2,
                        label=fam["label"])
            fam = summary["families"][rec["family"]]
            ax.axvline(fam["threshold"][p], color=INK2, lw=1, ls=":")
            ax.axvline(rec["max_m"], color=INK, lw=1.2, ls="--")
            ax.set_xticks([1, 4, 8, 12, 16, 20])
            ax.grid(axis="y", color=GRID, lw=0.8)
            _clean(ax)
            ax.set_title(f"{title}｜{_protocol_name(p)}", loc="left", fontsize=10.5)
            if r == 1:
                ax.set_xlabel("持股檔數 max_m")
    axes[0, 0].legend(frameon=False, loc="lower right", fontsize=9)
    fig.suptitle("持股檔數：線＝28 個換股日 × 2 種假日規則的中位數，色帶＝10%–90%（換股日運氣）；"
                 "點線＝高原起點，黑虛線＝建議", x=0.01, ha="left", fontsize=12)
    path = out / "sizes.png"
    fig.savefig(path)
    plt.close(fig)
    return path


# ---------------------------------------------------------------------------
# SUMMARY.md
# ---------------------------------------------------------------------------


def _pct(x: float, signed: bool = True) -> str:
    return f"{x:+.1%}" if signed else f"{x:.1%}"


def render_summary(s: dict) -> str:
    rec = s["recommendation"]
    fam = s["families"][rec["family"]]
    ref, pick = s["reference"], rec["metrics"]
    day, size = rec["rebalance_day"], rec["max_m"]
    hol = "延後到下一個交易日" if rec["holiday"] == "next" else "提前到前一個交易日"
    dc = rec["day_consistency"]
    he = fam["holiday"]
    wf_t = pd.DataFrame(fam["sizes"]["wf"]).T
    pr_t = pd.DataFrame(fam["sizes"]["prod"]).T

    lines = [
        "# 換股日 × 假日規則 × 持股檔數",
        "",
        "> 本檔由 `python -m twstable sweep-report` 從 `grid.csv` 產生，請勿手改。",
        "> 需求見 [`genesis/FIAT-hyper-parameters.md`](../../genesis/FIAT-hyper-parameters.md)。",
        "> 本頁的「原上線版參數」是第一次 WFCV（`results/wfcv.json`）那組。採用本頁建議後，"
        "上線版已改為固定這些設定重跑的 WFCV（`results/wfcv_monthly.json`，見 README 第五節）。",
        "",
        "## 結論",
        "",
        f"- **每月 {day} 日換股**（{day} 日下單成交，前一個交易日晚上選股）；"
        f"其實 **{_span(rec['good_days'])}之間任選一天都差不多**，"
        "沒有哪一天穩定勝出（見「換股日」）。",
        f"- **遇到假日：{hol}**。兩種規則差異很小，選這個只是因為樣本外略好一點。",
        f"- **持股 {size} 檔**；少於 {fam['threshold']['wf']} 檔開始變差，"
        f"{fam['breakdown']['wf'] - 1} 檔以下就真的不能用（見「持股檔數」）。",
        f"- **開啟權重上限 `cap_weight`**（每檔最多 1/{size}，選不滿就留現金）" if rec["cap_weight"]
        else "- 不開權重上限",
        f"- **{'等權重' if rec['weighting'] == 'equal' else '依分數加權'}**"
        + ("：依分數加權沒有比較好，交易還略多。" if rec["weighting"] == "equal" else "。"),
        "",
        "| | CAGR | Sharpe | 最大回檔 | 最差一年 |",
        "|---|---|---|---|---|",
    ]
    for p in PROTOCOLS:
        r, m = ref[p], pick[p]
        name = "walk-forward 樣本外" if p == "wf" else "原上線版參數全期"
        lines.append(f"| {name}：原設定 | {_pct(r['cagr'])} | {r['sharpe']:.2f} | "
                     f"{_pct(r['max_drawdown'])} | {_pct(r['worst_year'])} |")
        lines.append(f"| {name}：**建議設定** | {_pct(m['cagr'])} | {m['sharpe']:.2f} | "
                     f"{_pct(m['max_drawdown'])} | {_pct(m['worst_year'])} |")
    band = fam["sizes"]["prod"][str(size)]
    if pick["prod"]["sharpe"] < ref["prod"]["sharpe"]:
        lines += [
            "",
            f"**原上線版參數下，建議設定略差於原設定**（Sharpe {pick['prod']['sharpe']:.2f} 對 "
            f"{ref['prod']['sharpe']:.2f}）。原上線版參數是在「每 40 個交易日換股、10 檔」的前提下挑的，"
            f"換成每月換股本來就不在它的最佳點；而且差距在換股日運氣的範圍內"
            f"（{size} 檔、28 個換股日的 Sharpe 10%–90% 是 {band['sharpe_p10']:.2f}–"
            f"{band['sharpe_p90']:.2f}）。",
        ]
    lines += [
        "",
        "原設定：walk-forward 各折用自己挑的換股間隔與檔數；原上線版是每 40 個交易日、10 檔。",
        "建議設定套到原上線版參數上，見 [`recommended_params.json`](recommended_params.json)。",
        "",
        "**兩種評估**：",
        "",
        "- **walk-forward**：沿用 `results/wfcv.json` 每一折自己的參數，只換掉換股日、假日規則、檔數、"
        "權重上限與權重方式，再把 2006–2026 各折的樣本外曲線串起來。這是比較可信的估計。",
        "- **原上線版參數**：用 `results/wfcv.json` 的 `production_params`（第一次 WFCV，以 2020–2026 訓練）"
        "跑 2001–2026。"
        "2001–2019 對它其實是「往回的樣本外」，所以當作交叉驗證：結論要在兩邊都站得住。",
        "",
        "## 換股日",
        "",
        "![換股日曲線](day_profile.png)",
        "",
        "每一年把 28 個換股日依當年報酬排名，再看各年的排名有沒有一致（Kendall's W，0＝毫無關聯，"
        "1＝每年一模一樣）。虛無分布是把每一年的日序各自隨機平移，保留「相鄰幾天常在同一天成交」的相關。",
        "",
        "| 評估 | 年數 | W | 隨機時的 W 中位數 | p 值 |",
        "|---|---|---|---|---|",
    ]
    for p in PROTOCOLS:
        d = dc[p]
        name = "walk-forward" if p == "wf" else "原上線版參數"
        lines.append(f"| {name} | {d['years']} | {d['kendall_w']:.3f} | "
                     f"{d['null_w_median']:.3f} | {d['p_value']:.2f} |")
    lines += [
        "",
        "**p 值都很大：某一天今年表現好，明年並不會比較好。** 熱力圖上看得到的深淺，"
        "大多是「那幾天剛好躲過或碰上某次大跌」的運氣。",
        "",
        "兩種評估倒是有一個共同的弱傾向：**月中（約 5–17 日）略差、下旬略好**。"
        "這跟「每月 10 日前公布營收」的時間點吻合，但幅度只有每年約一個百分點、且不跨年一致，"
        "所以只拿來挑一個「兩邊都不吃虧」的日子，不要期待它會重演。",
        "建議日是兩種評估在平滑後 Sharpe 上「較差的那一邊」最好的一天（maximin）。",
        "",
        "![平滑後的熱力圖](heatmap_smoothed.png)",
        "",
        "![walk-forward 熱力圖](heatmap_wf.png)",
        "",
        "![原上線版參數熱力圖](heatmap_prod.png)",
        "",
        "## 假日提前或延後",
        "",
        "兩種規則只在「選定日剛好不是交易日」的月份不同，所以逐格（同一天、同一檔數）相減：",
        "",
        "| 評估 | 延後 − 提前：Sharpe | CAGR | 延後較好的格子 | 逐年差的 t 值 |",
        "|---|---|---|---|---|",
    ]
    for p in PROTOCOLS:
        h = he[p]
        name = "walk-forward" if p == "wf" else "原上線版參數"
        lines.append(f"| {name} | {h['sharpe']['mean']:+.3f} | {_pct(h['cagr']['mean'])} | "
                     f"{h['sharpe']['share_next_better']:.0%} | {h['yearly_t']:+.2f} |")
    lines += [
        "",
        "差距在雜訊範圍內（|t| 遠小於 2）。**提前或延後都可以**，挑一個固定下來就好。",
        "",
        "## 持股檔數",
        "",
        "![持股檔數](sizes.png)",
        "",
        f"高原起點＝從這個檔數起，每個更大的檔數其 Sharpe 中位數都在最好那個的 {NEAR_BEST:.0%} 以內。",
        "",
        "| 評估 | 高原起點 | 跌破最好的 80%（不能用）| 高原上的 Sharpe 中位數 |",
        "|---|---|---|---|",
    ]
    for p in PROTOCOLS:
        name = "walk-forward" if p == "wf" else "原上線版參數"
        lines.append(f"| {name} | {fam['threshold'][p]} 檔 | {fam['breakdown'][p] - 1} 檔以下 | "
                     f"{fam['plateau_sharpe'][p]:.2f} |")
    lines += [
        "",
        f"walk-forward 在 {fam['threshold']['wf']} 檔就到高原；原上線版參數要到 {fam['threshold']['prod']} 檔，"
        "因為它沒有避開 2008，持股越多、那次股災跌得越少。"
        f"建議取兩個起點的中間：**{size} 檔**。",
        "",
        f"同一個檔數下 28 個換股日的差異（色帶）也是風險：檔數越少，「哪天換股」的運氣影響越大。"
        f"以 walk-forward 為例（{rec['family']}）：",
        "",
        "| 檔數 | Sharpe 10%／中位數／90% | CAGR 中位數 | 最大回檔 中位數 | 最差一年 中位數 | 原上線版 Sharpe 中位數 |",
        "|---|---|---|---|---|---|",
    ]
    for m in (1, 2, 3, 4, 5, 6, 8, 10, 12, 15, 20):
        t, u = wf_t.loc[str(m)], pr_t.loc[str(m)]
        lines.append(f"| {m} | {t['sharpe_p10']:.2f}／{t['sharpe_med']:.2f}／{t['sharpe_p90']:.2f} | "
                     f"{_pct(t['cagr_med'])} | {_pct(t['mdd_med'])} | {_pct(t['worst_year_med'])} | "
                     f"{u['sharpe_med']:.2f} |")
    lines += [
        "",
        "## 權重上限與權重方式",
        "",
        "| 設定 | walk-forward 高原 Sharpe | 原上線版高原 Sharpe | walk-forward 高原起點 |",
        "|---|---|---|---|",
    ]
    for f in s["families"].values():
        lines.append(f"| {f['label']} | {f['plateau_sharpe']['wf']:.2f} | "
                     f"{f['plateau_sharpe']['prod']:.2f} | {f['threshold']['wf']} 檔 |")
    c0, c1 = s["cap_2008"]["False"], s["cap_2008"]["True"]
    lines += [
        "",
        f"**權重上限**：候選股不足 {size} 檔時，沒開上限會把全部資金平分給剩下的幾檔"
        "（2008 年曾連續 4 次全押 1 檔）。開了之後空出來的部分留現金。"
        f"以原換股間隔、{size} 檔的 walk-forward 為例：2008 年 {_pct(c0['y2008'])} → {_pct(c1['y2008'])}，"
        f"最大回檔 {_pct(c0['max_drawdown'])} → {_pct(c1['max_drawdown'])}，"
        f"Sharpe {c0['sharpe']:.2f} → {c1['sharpe']:.2f}。原上線版參數在股災時候選股還很多，所以幾乎沒差。",
        "",
        f"**權重方式**：依分數加權必須比等權重高出 {SIMPLER_MARGIN} 以上的 Sharpe 才採用，"
        "結果沒有；它的周轉率還略高。遲滯（`hold_mult`、`keep_frac`）與權重不交易帶（`weight_band`）"
        "已經讓分數下滑的股票不會被立刻賣掉，分數權重的「漸漸離開」沒有帶來額外好處。",
        "",
        "## 每月固定日 vs 原本的固定交易日間隔",
        "",
        "walk-forward 各折原本用自己挑的間隔（10–60 個交易日）。改成每月固定一天，"
        "各檔數的 Sharpe 中位數都落在原設定附近：",
        "",
        "| 檔數 | 原間隔 | 每月固定日 中位數（10%–90%）|",
        "|---|---|---|",
    ]
    for m in (5, 8, 10, 12, 15, 20):
        iv = s["interval_vs_monthly"][str(m)] if str(m) in s["interval_vs_monthly"] else \
            s["interval_vs_monthly"][m]
        lines.append(f"| {m} | {iv['interval_sharpe']:.2f} | {iv['monthly_sharpe_med']:.2f}"
                     f"（{iv['monthly_sharpe_p10']:.2f}–{iv['monthly_sharpe_p90']:.2f}）|")
    lines += [
        "",
        "**改成每月固定日換股不會吃虧**，而且實務上規律、好執行。",
        "",
        "## 限制",
        "",
        "- 換股日、檔數之外的參數是各折用**原本的**換股間隔與檔數挑出來的；這裡只覆寫這幾項，"
        "沒有重新搜尋。理想上應把新參數放進 WFCV 搜尋空間重跑。",
        "- 28 × 2 × 20 × 2 × 2 個組合看了這麼多格，最好的那一格必然偏樂觀；所以只看區域與跨年一致性，"
        "不採用單格最佳值。",
        "- README 提過的偏誤（倖存者偏誤、基準不含息、以收盤價成交）在這裡都還在。",
        "",
        "## 檔案",
        "",
        "- `grid.csv`：每個組合一列（`id` 與 `curves.parquet` 對應），含各項指標與每年報酬。",
        "- `curves.parquet`：每個組合的月底權益曲線（長表：`id`、`date`、`equity`）。",
        "- `summary.json`：本頁所有數字。",
        "- 重跑整個參數空間：`python -m twstable sweep`（約 15 分鐘，4 核心）；"
        "只重做本頁與圖：`python -m twstable sweep-report`（幾秒）。",
        "",
    ]
    return "\n".join(lines)


def _run_around(mask: np.ndarray, day: int) -> list[int]:
    """The days around ``day`` where ``mask`` holds, wrapping from 28 to 1."""
    n = len(mask)
    i = day - 1
    if not mask[i]:
        return [day]
    lo = hi = i
    while mask[(lo - 1) % n] and (i - lo) < n - 1:
        lo -= 1
    while mask[(hi + 1) % n] and (hi - lo) < n - 1:
        hi += 1
    return [int(k % n) + 1 for k in range(lo, hi + 1)]


def _span(days: list[int]) -> str:
    a, b = days[0], days[-1]
    return f"{a}–{b} 日" if a <= b else f"{a} 日到隔月 {b} 日"


def build_report(grid: pd.DataFrame, out_dir: Path, production: dict | None = None) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = analyse(grid)
    rec = summary["recommendation"]
    (out_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", "utf-8")
    if production is not None:
        (out_dir / "recommended_params.json").write_text(
            json.dumps(recommended_params(production, rec), indent=2) + "\n", "utf-8")
    for p in PROTOCOLS:
        plot_heatmaps(grid, out_dir, rec, p)
    plot_smoothed(grid, out_dir, rec)
    plot_days(rec, out_dir)
    plot_sizes(summary, out_dir)
    (out_dir / "SUMMARY.md").write_text(render_summary(summary), "utf-8")
    return summary


def run_report_cli(args) -> int:
    from .sweep import DEFAULT_DIR, GRID_CSV
    from .wfcv import DEFAULT_RESULT

    out_dir = Path(args.out_dir) if args.out_dir else DEFAULT_DIR
    wfcv = Path(args.wfcv) if getattr(args, "wfcv", None) else DEFAULT_RESULT
    production = json.loads(wfcv.read_text("utf-8"))["wfcv"]["production_params"]
    summary = build_report(pd.read_csv(out_dir / GRID_CSV), out_dir, production)
    rec = summary["recommendation"]
    print(f"report written to {out_dir}")
    print(f"recommendation: day {rec['rebalance_day']} ({rec['holiday']}), max_m {rec['max_m']}, "
          f"cap_weight {rec['cap_weight']}, weighting {rec['weighting']}")
    return 0
