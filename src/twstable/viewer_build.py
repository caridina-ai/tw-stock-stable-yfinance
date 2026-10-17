"""Export the explored experiments as a static single-page viewer (``docs/viewer``).

The page (``templates/viewer.html``) is copied verbatim; everything it shows is
pre-computed here, because GitHub Pages only serves files:

``data/meta.json``
    the trading calendar, names of every stock any experiment listed, and the
    list of experiments.
``data/exp/<id>.json``
    one experiment: its folds and parameters, metrics, a weekly equity curve,
    every rebalance, and for every name it ever listed the score and rank at
    *every* rebalance plus the holdings and weights, and the round trips each
    rebalance closed (buy and sell price, return).
``data/px/<stock>.json``
    daily adjusted (還原) OHLC of one stock, fetched when it is first shown.

Prices are stored as integer log-price ticks of ``1/2000`` (0.05%), the close
as day-to-day differences and open/high/low as offsets from the same day's
close, which keeps a 26-year daily history to a few dozen kilobytes.  The close
is the engine's own input (:meth:`Panel.filled_close`), so the page can refit
the score's regression lines itself.  The rounding moves a refitted score by
about 0.03% of its value (median over the production run; the restraint term,
which reads one day's price against a tight residual band, can move it by a few
percent), so the page always shows the exported, exact scores and ranks and
uses its own refit only to draw the lines and break a score into its terms.
"""

from __future__ import annotations

import datetime as dt
import json
import shutil
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pandas as pd

from .config import ROOT
from .explore import DEFAULT_DIR, EXPERIMENTS, SavedTrace, load_trace
from .metrics import drawdown

TEMPLATE = Path(__file__).parent / "templates" / "viewer.html"
DEFAULT_OUT = ROOT / "docs" / "viewer"

#: log-price tick: prices are rounded to multiples of exp(1/2000)
TICKS_PER_LOG = 2000


def _dump(obj, path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    path.write_text(text, "utf-8")
    return len(text.encode("utf-8"))


# ---------------------------------------------------------------------------
# prices
# ---------------------------------------------------------------------------


def encode_prices(close: np.ndarray, ohlc: np.ndarray | None) -> dict:
    """Pack one stock's history (on the shared calendar) into tick integers.

    ``close`` is the engine's filled close, NaN where there is none; ``ohlc`` is
    ``(n, 3)`` adjusted open/high/low, NaN on days without a bar of its own.
    The result covers the first to the last finite close; ``s`` is the
    calendar index of its first day.
    """
    finite = np.isfinite(close)
    if not finite.any():
        raise ValueError("no prices")
    i0 = int(np.argmax(finite))
    i1 = len(close) - int(np.argmax(finite[::-1]))
    c = close[i0:i1]
    q = np.where(np.isfinite(c), np.round(np.log(c) * TICKS_PER_LOG), np.nan)

    out_c: list[int | None] = []
    prev = None
    for v in q:
        if np.isnan(v):
            out_c.append(None)
            continue
        v = int(v)
        out_c.append(v if prev is None else v - prev)
        prev = v
    payload: dict = {"s": i0, "c": out_c}
    if ohlc is not None:
        for j, key in enumerate(("o", "h", "l")):
            x = ohlc[i0:i1, j]
            with np.errstate(divide="ignore", invalid="ignore"):
                off = np.round(np.log(x) * TICKS_PER_LOG) - q
            payload[key] = [None if not np.isfinite(v) else int(v) for v in off]
    return payload


def decode_close(payload: dict, n_calendar: int) -> np.ndarray:
    """Inverse of the close part of :func:`encode_prices`, on the full calendar."""
    out = np.full(n_calendar, np.nan)
    level = None
    for k, d in enumerate(payload["c"]):
        if d is None:
            continue
        level = d if level is None else level + d
        out[payload["s"] + k] = np.exp(level / TICKS_PER_LOG)
    return out


def _stock_ohlc(ticker: str, dates: pd.DatetimeIndex) -> np.ndarray | None:
    from .data import _path_for, adjust

    path = _path_for(ticker)
    if not path.exists():
        return None
    bars = adjust(pd.read_parquet(path))
    return bars[["Open", "High", "Low"]].reindex(dates).to_numpy(dtype="float64")


def _raw_close(ticker: str, dates: pd.DatetimeIndex) -> np.ndarray | None:
    """Unadjusted close on the calendar, carried over days without a bar."""
    from .data import _path_for

    path = _path_for(ticker)
    if not path.exists():
        return None
    raw = pd.read_parquet(path, columns=["Close"])["Close"]
    return raw.reindex(dates).ffill().to_numpy(dtype="float64")


# ---------------------------------------------------------------------------
# experiments
# ---------------------------------------------------------------------------


def exits(saved: SavedTrace, cal_index: dict[pd.Timestamp, int],
          prices: Callable[[str], tuple[np.ndarray, np.ndarray]]) -> list[list]:
    """Round trips closed at each rebalance: ``[[(id, buy, sell, return), ...], ...]``.

    A name held at rebalance ``m-1`` and not at ``m`` was bought at the trade
    of the first rebalance of that run and sold at the trade of ``m``.
    ``buy`` and ``sell`` are the raw (unadjusted) closes on those days -- the
    prices one would actually have seen -- and ``return`` is the adjusted
    (還原) ratio, so it includes the dividends in between.  Trims and top-ups
    along the way and trading costs are left out: this is the stock's own
    result over the stretch the strategy held it.

    ``prices(id)`` gives ``(adjusted, raw)`` closes on the calendar.
    """
    months = saved.months
    start: dict[str, int] = {}
    out: list[list] = []
    for m, month in enumerate(months):
        now = set(month["holdings"])
        closed = []
        for sid in sorted(s for s in start if s not in now):
            a = start.pop(sid)
            adj, raw = prices(sid)
            t0 = cal_index[pd.Timestamp(months[a]["trade"])]
            t1 = cal_index[pd.Timestamp(month["trade"])]
            closed.append((sid, float(raw[t0]), float(raw[t1]), float(adj[t1] / adj[t0] - 1)))
        for sid in now:
            start.setdefault(sid, m)
        out.append(closed)
    return out


def _sig(x: float, digits: int = 4) -> float | None:
    """Round to ``digits`` significant figures, the precision a quote is read at."""
    return float(f"{x:.{digits}g}") if np.isfinite(x) else None


def experiment_payload(
    saved: SavedTrace,
    cal_index: dict[pd.Timestamp, int],
    prices: Callable[[str], tuple[np.ndarray, np.ndarray]] | None = None,
) -> dict:
    meta = saved.meta
    union = saved.union
    months = saved.months
    n_months = len(months)
    pos = {sid: u for u, sid in enumerate(union)}

    def ci(day: str) -> int:
        return cal_index[pd.Timestamp(day)]

    sc = saved.scores
    score = sc["score"].to_numpy().reshape(n_months, len(union)).T
    rank = sc["rank"].to_numpy().reshape(n_months, len(union)).T
    if list(sc["id"].iloc[: len(union)]) != union:
        raise ValueError("scores table is not month-major in union order")

    curve = saved.curve
    # weekly samples sit on the last trading day of each week; drawdown keeps
    # the week's worst point so no trough is smoothed away
    week = curve.index.to_period("W-FRI")
    weekly = curve.groupby(week).tail(1)
    dd = drawdown(curve["equity"]).groupby(week).min().to_numpy()
    bdd = drawdown(curve["benchmark"].ffill()).groupby(week).min().to_numpy()
    week_days = [cal_index[d] for d in weekly.index]

    yearly = {}
    for name in ("equity", "benchmark"):
        s = curve[name].dropna()
        ye = s.resample("YE").last()
        prev = ye.shift(1)
        prev.iloc[0] = s.iloc[0]
        yearly[name] = {str(k.year): round(float(v), 4) for k, v in (ye / prev - 1).items()}

    def rnd(x: float, n: int = 4) -> float:
        return round(float(x), n)

    closed = exits(saved, cal_index, prices) if prices else [[] for _ in months]

    return {
        "id": meta["id"],
        "label": meta["label"],
        "note": meta["note"],
        "source": meta["source"],
        "override": meta["override"],
        "segments": meta["segments"],
        "metrics": meta["metrics"],
        "yearly": yearly,
        "curve": {
            "d": week_days,
            "e": [rnd(v, 5) for v in weekly["equity"]],
            "b": [rnd(v, 5) for v in weekly["benchmark"]],
            "dd": [rnd(v, 4) for v in dd],
            "bdd": [rnd(v, 4) for v in bdd],
            "cash": [rnd(c / e, 3) for c, e in zip(weekly["cash"], weekly["equity"], strict=True)],
        },
        "months": [
            {
                "s": ci(m["signal"]),
                "t": ci(m["trade"]),
                "g": next(k for k, s in enumerate(meta["segments"]) if s["index"] == m["segment"]),
                "e": rnd(m["equity"], 5),
                "ne": m["n_eligible"],
                "nc": m["n_candidates"],
                "to": rnd(m["turnover"], 3),
                "h": [[pos[sid], round(w * 10000)] for sid, w in
                      sorted(m["holdings"].items(), key=lambda kv: -kv[1])],
                "x": [[pos[sid], _sig(buy), _sig(sell), rnd(ret, 4)]
                      for sid, buy, sell, ret in closed[k]],
            }
            for k, m in enumerate(months)
        ],
        "union": union,
        "score": [[int(round(v * 1000)) for v in row] for row in score],
        "rank": [[int(v) for v in row] for row in rank],
    }


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------


def build(explore_dir: Path = DEFAULT_DIR, out_dir: Path = DEFAULT_OUT,
          with_prices: bool = True) -> dict:
    from .data import load_panel
    from .universe import load_universe

    panel = load_panel()
    filled = panel.filled_close()
    dates = pd.DatetimeIndex(filled.index)
    cal_index = {d: i for i, d in enumerate(dates)}

    traces = {e.id: load_trace(e.id, explore_dir) for e in EXPERIMENTS}
    union = sorted({sid for t in traces.values() for sid in t.union})

    uni = load_universe().set_index("stock_id")
    stocks = {
        sid: [str(uni.at[sid, "name"]) if sid in uni.index else sid,
              str(uni.at[sid, "market"]) if sid in uni.index else "",
              str(uni.at[sid, "industry"]) if sid in uni.index else ""]
        for sid in union
    }

    data = out_dir / "data"
    sizes: dict[str, int] = {}
    ticker = uni["ticker"].to_dict()
    cache: dict[str, tuple[np.ndarray, np.ndarray]] = {}

    def prices(sid: str) -> tuple[np.ndarray, np.ndarray]:
        if sid not in cache:
            adj = filled[sid].ffill().to_numpy(dtype="float64")
            raw = _raw_close(ticker[sid], dates) if sid in ticker else None
            cache[sid] = (adj, adj if raw is None else raw)
        return cache[sid]

    exp_list = []
    for exp in EXPERIMENTS:
        payload = experiment_payload(traces[exp.id], cal_index, prices)
        sizes[f"exp/{exp.id}.json"] = _dump(payload, data / "exp" / f"{exp.id}.json")
        exp_list.append({"id": exp.id, "label": exp.label, "note": exp.note})

    offsets = (dates - dates[0]).days.to_numpy()
    meta = {
        "built": dt.datetime.now(dt.UTC).strftime("%Y-%m-%d %H:%M UTC"),
        "ticks": TICKS_PER_LOG,
        "calendar": {"start": dates[0].strftime("%Y-%m-%d"),
                     "steps": np.diff(offsets).astype(int).tolist()},
        "stocks": stocks,
        "experiments": exp_list,
    }
    sizes["meta.json"] = _dump(meta, data / "meta.json")

    if with_prices:
        px_dir = data / "px"
        if px_dir.exists():
            shutil.rmtree(px_dir)
        total = 0
        for sid in union:
            close = filled[sid].to_numpy(dtype="float64")
            ohlc = _stock_ohlc(ticker[sid], dates) if sid in ticker else None
            total += _dump(encode_prices(close, ohlc), px_dir / f"{sid}.json")
        sizes["px/*.json"] = total

    out_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(TEMPLATE, out_dir / "index.html")
    return {"stocks": len(union), "experiments": len(exp_list), "bytes": sizes}


def run_viewer_cli(args) -> int:
    out = Path(args.out) if args.out else DEFAULT_OUT
    explore_dir = Path(args.explore_dir) if args.explore_dir else DEFAULT_DIR
    info = build(explore_dir, out, with_prices=not args.no_prices)
    for name, size in info["bytes"].items():
        print(f"  {name:<24} {size / 1e6:7.2f} MB")
    print(f"viewer written to {out} ({info['experiments']} experiments, {info['stocks']} stocks)")
    print(f"try it locally:  python -m http.server -d {out.parent}")
    return 0
