"""Stacked equity curve: the book drawn as the sum of its holdings.

The total equity of the portfolio is the cash plus the market value of every
holding.  Drawing each holding as its own coloured band, stacked on the others,
turns the equity curve into a picture of *who* earned it.  Each rebalance drops
some bands and starts new ones; a name that survives keeps going.

The whole difficulty is the stacking order.  A band only *looks* continuous if
the bands below it do not change at a rebalance: when a lower band ends (or a
new one appears below it) everything above it jumps.  So:

**Layers.**  A held name occupies one horizontal layer for its whole run of
consecutive rebalances.  When a name leaves, a newcomer takes over its layer,
so the total below every surviving band barely moves -- equal weights make the
newcomer about as tall as the name it replaced.  Two assignments are tried:

* ``sweep`` walks forward in time and hands each vacated layer to a newcomer,
  longest-held newcomers first, lowest layer first;
* ``firstfit`` places the longest runs first, each in the lowest layer that is
  free for its whole run -- the "longest at the bottom" rule taken literally.

**Order.**  Layers are then reordered to minimise the total jump of the bands
that continue through a rebalance (measured as a fraction of equity), starting
from the layers that change hands least often at the bottom.  The assignment
with the smaller total jump wins.

**Colour.**  One colour per name for its whole history, chosen so that bands
that touch (adjacent layers at the same time, or one name handing a layer to
the next) differ.

Reads only what :mod:`twstable.explore` saved.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from .explore import DEFAULT_DIR, EXPERIMENTS, SavedTrace, get_experiment, load_trace

# Tableau-20 style categorical set, reordered so neighbours in the list differ
# in both hue and lightness.
PALETTE = [
    "#4e79a7", "#f28e2b", "#59a14f", "#e15759", "#b07aa1", "#edc948", "#76b7b2",
    "#ff9da7", "#9c755f", "#a0cbe8", "#ffbe7d", "#8cd17d", "#d37295", "#b6992d",
    "#499894", "#fabfd2", "#86bcb6", "#d4a6c8", "#f1ce63", "#bab0ac",
]
CASH = "#e4e3df"
INK, INK2, GRID = "#0b0b0b", "#52514e", "#d9d8d2"


@dataclass(frozen=True)
class Run:
    stock: str
    a: int  # first rebalance held (inclusive)
    b: int  # last rebalance held (inclusive)

    @property
    def length(self) -> int:
        return self.b - self.a + 1


def build_runs(months: list[dict]) -> list[Run]:
    """Maximal stretches of consecutive rebalances in which a name is held."""
    runs: list[Run] = []
    open_: dict[str, int] = {}
    for m, month in enumerate(months):
        now = set(month["holdings"])
        for sid in [s for s in open_ if s not in now]:
            runs.append(Run(sid, open_.pop(sid), m - 1))
        for sid in now:
            open_.setdefault(sid, m)
    runs.extend(Run(sid, a, len(months) - 1) for sid, a in open_.items())
    return sorted(runs, key=lambda r: (r.a, r.stock))


def assign_sweep(runs: list[Run], n_months: int) -> np.ndarray:
    """Hand each vacated layer to a newcomer; new layers only when none is free."""
    layer = np.full(len(runs), -1)
    starts: dict[int, list[int]] = {}
    for k, r in enumerate(runs):
        starts.setdefault(r.a, []).append(k)
    occupant: list[int | None] = []
    for m in range(n_months):
        for j, k in enumerate(occupant):
            if k is not None and runs[k].b < m:
                occupant[j] = None
        for k in sorted(starts.get(m, []), key=lambda k: (-runs[k].length, runs[k].stock)):
            free = [j for j, o in enumerate(occupant) if o is None]
            if free:
                j = free[0]
                occupant[j] = k
            else:
                j = len(occupant)
                occupant.append(k)
            layer[k] = j
    return layer


def assign_firstfit(runs: list[Run], n_months: int) -> np.ndarray:
    """Longest runs first, each in the lowest layer free for its whole run."""
    layer = np.full(len(runs), -1)
    busy: list[np.ndarray] = []
    for k in sorted(range(len(runs)), key=lambda k: (-runs[k].length, runs[k].a, runs[k].stock)):
        r = runs[k]
        free = [j for j, b in enumerate(busy) if not b[r.a : r.b + 1].any()]
        if free:
            j = free[0]
        else:
            j = len(busy)
            busy.append(np.zeros(n_months, dtype=bool))
        busy[j][r.a : r.b + 1] = True
        layer[k] = j
    return layer


@dataclass
class Layout:
    runs: list[Run]
    layer: np.ndarray  # layer of each run
    order: np.ndarray  # layers bottom -> top
    values: np.ndarray  # (n_layers, n_bars) market value held in each layer
    occupant: np.ndarray  # (n_layers, n_bars) run index, -1 when empty
    cost: float
    method: str


def _layer_values(
    saved: SavedTrace, runs: list[Run], layer: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    dates = saved.curve.index
    # a target the engine could not afford to buy holds nothing and has no rows
    wide = saved.positions.pivot_table(index="date", columns="id", values="value",
                                       aggfunc="sum")
    wide = wide.reindex(index=dates, columns=sorted({r.stock for r in runs})).fillna(0.0)
    trade_bar = dates.get_indexer(pd.DatetimeIndex([m["trade"] for m in saved.months]))
    if (trade_bar < 0).any():
        raise ValueError("a rebalance trade date is missing from the equity curve")
    n_bars = len(dates)
    n_layers = int(layer.max()) + 1 if len(layer) else 0
    values = np.zeros((n_layers, n_bars))
    occupant = np.full((n_layers, n_bars), -1)
    for k, r in enumerate(runs):
        t0 = trade_bar[r.a]
        t1 = trade_bar[r.b + 1] if r.b + 1 < len(trade_bar) else n_bars
        values[layer[k], t0:t1] = wide[r.stock].to_numpy()[t0:t1]
        occupant[layer[k], t0:t1] = k
    return values, occupant


def _jump_inputs(saved: SavedTrace, values: np.ndarray, occupant: np.ndarray):
    """Per rebalance: each layer's change, which layers continue, and the equity."""
    dates = saved.curve.index
    bars = dates.get_indexer(pd.DatetimeIndex([m["trade"] for m in saved.months[1:]]))
    bars = bars[bars > 0]
    delta = (values[:, bars] - values[:, bars - 1]).T  # (n_boundaries, n_layers)
    cont = ((occupant[:, bars] == occupant[:, bars - 1]) & (occupant[:, bars] >= 0)).T
    equity = saved.curve["equity"].to_numpy()[bars]
    return delta, cont, equity


def jump_cost(delta: np.ndarray, cont: np.ndarray, equity: np.ndarray, order: np.ndarray) -> float:
    """Total jump of continuing bands, in fractions of equity, for one layer order."""
    d = delta[:, order]
    below = np.cumsum(d, axis=1) - d
    return float((np.abs(below) * cont[:, order]).sum(axis=1).dot(1.0 / equity))


def optimise_order(delta: np.ndarray, cont: np.ndarray, equity: np.ndarray,
                   start: np.ndarray, max_passes: int = 20) -> tuple[np.ndarray, float]:
    """Insertion local search over the layer order."""
    order = list(start)
    best = jump_cost(delta, cont, equity, np.array(order))
    for _ in range(max_passes):
        improved = False
        for p in range(len(order)):
            item = order[p]
            rest = order[:p] + order[p + 1 :]
            for q in range(len(order)):
                if q == p:
                    continue
                cand = rest[:q] + [item] + rest[q:]
                c = jump_cost(delta, cont, equity, np.array(cand))
                if c < best - 1e-12:
                    best, order, improved = c, cand, True
                    break
        if not improved:
            break
    return np.array(order), best


def layout(saved: SavedTrace) -> Layout:
    runs = build_runs(saved.months)
    n_months = len(saved.months)
    best: Layout | None = None
    for method, assign in (("sweep", assign_sweep), ("firstfit", assign_firstfit)):
        layer = assign(runs, n_months)
        values, occupant = _layer_values(saved, runs, layer)
        delta, cont, equity = _jump_inputs(saved, values, occupant)
        # start: layers that change hands least often at the bottom
        changes = (np.diff(occupant, axis=1) != 0).sum(axis=1)
        start = np.argsort(changes, kind="stable")
        order, cost = optimise_order(delta, cont, equity, start)
        cand = Layout(runs, layer, order, values, occupant, cost, method)
        if best is None or cand.cost < best.cost:
            best = cand
    assert best is not None
    return best


def assign_colours(lay: Layout) -> dict[str, str]:
    """One colour per name; touching bands get different colours where possible."""
    pos = np.empty(len(lay.order), dtype=int)
    pos[lay.order] = np.arange(len(lay.order))
    stacked = lay.occupant[lay.order]  # rows bottom -> top
    stock_of = np.array([r.stock for r in lay.runs] + [""])  # index -1 -> ""

    weight: dict[tuple[str, str], float] = {}

    def add(a: str, b: str, w: float) -> None:
        if a and b and a != b:
            key = (a, b) if a < b else (b, a)
            weight[key] = weight.get(key, 0.0) + w

    # neighbours in the stack, counted in bars of contact
    for j in range(len(stacked) - 1):
        lo, hi = stock_of[stacked[j]], stock_of[stacked[j + 1]]
        pairs = pd.DataFrame({"a": lo, "b": hi})
        pairs = pairs[(pairs["a"] != "") & (pairs["b"] != "")]
        for (a, b), n in pairs.value_counts().items():
            add(a, b, float(n))
    # a name handing its layer to the next one: a hard edge, weigh it heavily
    for j in range(len(stacked)):
        row = stacked[j]
        change = np.flatnonzero((row[1:] != row[:-1]) & (row[1:] >= 0) & (row[:-1] >= 0))
        for t in change:
            add(stock_of[row[t]], stock_of[row[t + 1]], 250.0)

    held = pd.Series(stock_of[:-1]).value_counts()  # runs per name ~ presence
    length = {}
    for r in lay.runs:
        length[r.stock] = length.get(r.stock, 0) + r.length
    neighbours: dict[str, dict[str, float]] = {}
    for (a, b), w in weight.items():
        neighbours.setdefault(a, {})[b] = w
        neighbours.setdefault(b, {})[a] = w

    colour: dict[str, str] = {}
    used = dict.fromkeys(PALETTE, 0)
    for sid in sorted(length, key=lambda s: (-length[s], -held.get(s, 0), s)):
        clash = dict.fromkeys(PALETTE, 0.0)
        for nb, w in neighbours.get(sid, {}).items():
            if nb in colour:
                clash[colour[nb]] += w
        c = min(PALETTE, key=lambda c: (clash[c], used[c], PALETTE.index(c)))
        colour[sid] = c
        used[c] += 1
    return colour


# ---------------------------------------------------------------------------
# drawing
# ---------------------------------------------------------------------------


def _mpl():
    import logging

    import matplotlib

    matplotlib.use("Agg")
    # WenQuanYi ships only a "medium" weight; matplotlib warns on every label
    logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)
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
        "figure.facecolor": "white", "axes.facecolor": "white", "savefig.dpi": 150,
    })
    return plt


def _ink_on(hex_colour: str) -> str:
    r, g, b = (int(hex_colour[i : i + 2], 16) / 255 for i in (1, 3, 5))
    return INK if 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.55 else "white"


def _segments(row: np.ndarray) -> list[tuple[int, int, int]]:
    """``(run, t0, t1)`` stretches of constant occupant, t1 exclusive."""
    out = []
    t, n = 0, len(row)
    while t < n:
        k = row[t]
        u = t + 1
        while u < n and row[u] == k:
            u += 1
        if k >= 0:
            out.append((int(k), t, u))
        t = u
    return out


def draw(saved: SavedTrace, path: Path, names: dict[str, str] | None = None) -> Layout:
    plt = _mpl()
    names = names or {}
    lay = layout(saved)
    colours = assign_colours(lay)

    curve = saved.curve
    dates = curve.index
    x = dates.to_numpy()
    # one extra point so a step-post fill covers the last bar
    x_ext = np.append(x, x[-1] + np.timedelta64(1, "D"))
    equity = curve["equity"].to_numpy()

    stacked = lay.values[lay.order]
    tops = np.cumsum(stacked, axis=0)
    bottoms = tops - stacked
    occ = lay.occupant[lay.order]
    shares = np.divide(stacked, equity, out=np.zeros_like(stacked), where=equity > 0)
    s_tops = np.cumsum(shares, axis=0)
    s_bottoms = s_tops - shares

    fig, (ax, ax2) = plt.subplots(
        2, 1, figsize=(18, 11), sharex=True, gridspec_kw={"height_ratios": [1.35, 1]}
    )
    fig.subplots_adjust(left=0.05, right=0.985, top=0.885, bottom=0.05, hspace=0.06)

    for j in range(len(stacked)):
        for k, t0, t1 in _segments(occ[j]):
            sid = lay.runs[k].stock
            c = colours[sid]
            sl = slice(t0, t1 + 1)
            for axis, lo, hi in ((ax, bottoms, tops), (ax2, s_bottoms, s_tops)):
                ylo = np.append(lo[j, t0:t1], lo[j, t1 - 1])
                yhi = np.append(hi[j, t0:t1], hi[j, t1 - 1])
                axis.fill_between(x_ext[sl], ylo, yhi, step="post", facecolor=c,
                                  edgecolor="white", linewidth=0.25)

    cash_lo = tops[-1] if len(tops) else np.zeros_like(equity)
    ax.fill_between(x_ext, np.append(cash_lo, cash_lo[-1]), np.append(equity, equity[-1]),
                    step="post", facecolor=CASH, edgecolor="none")
    s_cash = s_tops[-1] if len(s_tops) else np.zeros_like(equity)
    ax2.fill_between(x_ext, np.append(s_cash, s_cash[-1]), 1.0, step="post",
                     facecolor=CASH, edgecolor="none")

    ax.plot(x, equity, color=INK, linewidth=0.9, label="總權益")
    ax.plot(x, curve["benchmark"].to_numpy(), color=INK2, linewidth=0.9, linestyle=(0, (4, 3)),
            label="大盤（同起點）")

    # names on bands that are long and thick enough to carry them
    fig.canvas.draw()
    for axis, lo, hi, ymin_px in ((ax, bottoms, tops, 9.0), (ax2, s_bottoms, s_tops, 9.0)):
        bbox = axis.get_window_extent()
        y0, y1 = axis.get_ylim()
        px_per_unit = bbox.height / (y1 - y0)
        px_per_bar = bbox.width / len(x)
        for j in range(len(stacked)):
            for k, t0, t1 in _segments(occ[j]):
                sid = lay.runs[k].stock
                label = names.get(sid, sid)
                height = (hi[j, t0:t1] - lo[j, t0:t1]) * px_per_unit
                need = 7.5 * (len(label) + 0.5)  # px of width the label needs
                if (t1 - t0) * px_per_bar < need + 6 or height.max() < ymin_px:
                    continue
                # centre of the stretch where the band is thick enough
                ok = np.flatnonzero(height >= ymin_px)
                t = int(ok[len(ok) // 2]) + t0
                w = int(need / px_per_bar / 2)
                if t - w < t0 or t + w >= t1 or height[t - w - t0 : t + w - t0].min() < ymin_px * 0.8:
                    continue
                axis.text(x[t], (lo[j, t] + hi[j, t]) / 2, label, fontsize=7, ha="center",
                          va="center", color=_ink_on(colours[sid]), clip_on=True)

    # fold seams
    meta = saved.meta
    if len(meta["segments"]) > 1:
        for seg in meta["segments"][1:]:
            for axis in (ax, ax2):
                axis.axvline(pd.Timestamp(seg["start"]), color=INK2, linewidth=0.6,
                             linestyle=(0, (1, 2)), alpha=0.8)
        for seg in meta["segments"]:
            ax2.text(pd.Timestamp(seg["start"]) + pd.Timedelta(days=20), 1.012, f"折 {seg['index']}",
                     fontsize=8, color=INK2, va="bottom", ha="left", clip_on=False)

    ax.set_xlim(x[0], x_ext[-1])
    ax.set_ylim(0, max(equity.max(), np.nanmax(curve["benchmark"].to_numpy())) * 1.03)
    ax.set_ylabel("權益（起始資金 = 1）")
    ax.grid(axis="y", color=GRID, linewidth=0.6)
    ax.set_axisbelow(False)
    ax.legend(loc="upper left", frameon=False, fontsize=10)
    ax2.set_ylim(0, 1)
    ax2.set_yticks([0, 0.25, 0.5, 0.75, 1.0], ["0%", "25%", "50%", "75%", "100%"])
    ax2.set_ylabel("占權益比例")
    for axis in (ax, ax2):
        for side in ("top", "right"):
            axis.spines[side].set_visible(False)

    m = meta["metrics"]["strategy"]
    n_names = len({r.stock for r in lay.runs})
    longest = max(lay.runs, key=lambda r: r.length)
    fig.suptitle(
        f"疊加型 Equity Curve｜{meta['label']}　{dates[0]:%Y-%m}–{dates[-1]:%Y-%m}",
        x=0.05, ha="left", fontsize=15, y=0.975,
    )
    fig.text(
        0.05, 0.925,
        f"每個色塊是一檔成分股的市值，同一檔股票連續持有時色塊相連；換股時新進的股票接手被汰除者的那一層。"
        f"下層是換手最少、最穩定的層，淺灰是現金。上圖為金額，下圖為占比。\n"
        f"CAGR {m['cagr']:+.1%}　Sharpe {m['sharpe']:.2f}　最大回檔 {m['max_drawdown']:.1%}　"
        f"{len(saved.months)} 次換股、{n_names} 檔曾入選；連續持有最久："
        f"{names.get(longest.stock, longest.stock)}（{longest.stock}）{longest.length} 次。",
        fontsize=10, color=INK2, va="top",
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path)
    plt.close(fig)
    return lay


def run_stacked_cli(args) -> int:
    from .universe import name_map

    out_dir = Path(args.out_dir) if args.out_dir else DEFAULT_DIR
    chosen = [get_experiment(x) for x in args.only] if args.only else list(EXPERIMENTS)
    names = name_map()
    for exp in chosen:
        saved = load_trace(exp.id, out_dir)
        path = out_dir / exp.id / "stacked.png"
        lay = draw(saved, path, names)
        print(f"{exp.id:<11} {len(lay.order):>3} layers ({lay.method}), "
              f"jump {lay.cost:.3f} x equity over {len(saved.months)} rebalances -> {path}")
    return 0
