"""Replay finished experiments rebalance by rebalance, for people to look at.

``genesis/FIAT-http-viewer.md`` asks to *watch* the portfolio: which names a
rebalance ranked, which it kept, trimmed, dropped or bought, and how each
holding's slice of the book grew.  A backtest result only keeps the holdings, so
every experiment is replayed here with the engine itself and three things are
kept that the result files throw away:

* the score and rank of **every** name that ever made the top of the list (or
  the book), at **every** rebalance -- not just the months it was listed;
* the holdings after each rebalance with their weights;
* the value of each holding on every trading day (plus cash), so the total
  equity can be drawn as a stack of its parts.

Experiments are defined from result files that already exist: a walk-forward
run is replayed fold by fold with each fold's own parameters and chained
exactly like :func:`twstable.wfcv.chain_equity`; a single run is replayed over
the whole history.  ``override`` changes a few parameters on top, which is how
the ablations (no weight cap, score weighting, ...) are made without a new
search.

Computing and presenting are separate, as for the sweep: ``twstable explore``
writes ``results/explore/<id>/`` and everything shown to a person
(:mod:`twstable.stacked`, :mod:`twstable.viewer_build`) is built from those
files.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from .config import RESULTS_DIR
from .metrics import compute_metrics, relative_metrics
from .portfolio import Market, StrategyParams, plan_decisions, run_backtest

DEFAULT_DIR = RESULTS_DIR / "explore"

#: Names listed per rebalance in the viewer's ranking.
TOP = 50


@dataclass(frozen=True)
class Experiment:
    id: str
    label: str
    note: str
    source: str
    """Result file under ``results/``: a WFCV run (fold by fold) or a params file."""

    override: dict = field(default_factory=dict)
    start: str = "2000-01-01"
    """Start of a single (non walk-forward) run; the backtest CLI's default."""


MONTHLY = {"rebalance_day": 24, "holiday": "next", "max_m": 12, "cap_weight": True,
           "weighting": "equal"}

EXPERIMENTS: tuple[Experiment, ...] = (
    Experiment(
        "prod", "上線版",
        "每月 24 日、12 檔、權重上限；其餘參數由 WFCV 逐折重新搜尋。2006 起全部是樣本外。",
        "wfcv_monthly.json",
    ),
    Experiment(
        "prod_nocap", "上線版・無權重上限",
        "上線版各折參數不變，只關掉權重上限：候選不足 12 檔時把資金平分給剩下的幾檔。",
        "wfcv_monthly.json", {"cap_weight": False},
    ),
    Experiment(
        "prod_score", "上線版・分數權重",
        "上線版各折參數不變，持股權重改為與分數成正比（weighting = score）。",
        "wfcv_monthly.json", {"weighting": "score"},
    ),
    Experiment(
        "wf_d24", "原 WFCV 參數・每月 24 日",
        "第一次 WFCV 各折挑的參數，只把換股方式改成每月 24 日、12 檔、權重上限（換股日實驗的建議設定）。",
        "wfcv.json", MONTHLY,
    ),
    Experiment(
        "wfcv1", "原 WFCV（每 10–60 交易日）",
        "第一次 WFCV：各折自己挑換股間隔（10–60 個交易日）與檔數（10–30 檔），沒有權重上限。",
        "wfcv.json",
    ),
    Experiment(
        "default20", "預設參數・每 20 交易日",
        "未經優化的預設參數：每 20 個交易日換股、20 檔、等權重，2001 起全期回測。",
        "backtest.json",
    ),
    Experiment(
        "prod_full", "上線版參數・全期",
        "上線版最後一次擬合的參數（production_params.json）從 2001 起全期回測；2020 以後是它的訓練期。",
        "production_params.json",
    ),
)


def get_experiment(exp_id: str) -> Experiment:
    for e in EXPERIMENTS:
        if e.id == exp_id:
            return e
    raise KeyError(f"unknown experiment {exp_id!r}; known: {[e.id for e in EXPERIMENTS]}")


@dataclass(frozen=True)
class Segment:
    """One independently simulated stretch: a WFCV fold, or the whole run."""

    index: int  # fold number, 0 for a single run
    start: pd.Timestamp | None
    end: pd.Timestamp | None
    params: StrategyParams


def segments_for(exp: Experiment, results_dir: Path = RESULTS_DIR) -> list[Segment]:
    raw = json.loads((results_dir / exp.source).read_text("utf-8"))
    if "wfcv" in raw:
        return [
            Segment(f["index"], pd.Timestamp(f["test_start"]), pd.Timestamp(f["test_end"]),
                    StrategyParams.from_dict(f["params"]).replace(**exp.override))
            for f in raw["wfcv"]["folds"]
        ]
    params = raw.get("params", raw)  # a result file, or a bare params file
    return [Segment(0, pd.Timestamp(exp.start), None,
                    StrategyParams.from_dict(params).replace(**exp.override))]


# ---------------------------------------------------------------------------
# replay
# ---------------------------------------------------------------------------


@dataclass
class Trace:
    """An experiment replayed rebalance by rebalance.

    Money is in units of the starting capital (the curve starts at 1.0).
    """

    exp: Experiment
    segments: list[Segment]
    months: list[dict]
    """One dict per rebalance: dates, segment, equity, holdings ``{id: weight}``, ..."""

    scores: pd.DataFrame
    """Long table ``month, id, score, rank`` for every name in :attr:`union`.

    ``rank`` is 0-based among the names eligible that month, -1 when not eligible.
    """

    curve: pd.DataFrame
    """Daily ``equity``, ``benchmark`` and ``cash``."""

    positions: pd.DataFrame
    """Daily value of every holding, long form ``date, id, value``."""

    @property
    def union(self) -> list[str]:
        return list(dict.fromkeys(self.scores["id"]))

    def metrics(self) -> dict:
        eq = self.curve["equity"]
        bm = self.curve["benchmark"]
        return {
            "strategy": compute_metrics(eq).to_dict(),
            "benchmark": compute_metrics(bm.dropna()).to_dict(),
            "relative": relative_metrics(eq, bm),
        }


def _replay_segment(market: Market, seg: Segment) -> dict:
    """Run one segment and keep what the result file would throw away."""
    dec = plan_decisions(market, seg.params, seg.start, seg.end)
    res = run_backtest(market, seg.params, start=seg.start, end=seg.end, record=True)
    if len(res.rebalances) != len(dec.signal_days):
        raise RuntimeError("engine and decision plan disagree on the rebalance dates")

    dates, close = market.dates, market.close
    first, hi = dec.first, dec.hi
    ids = market.stock_ids
    col = {sid: i for i, sid in enumerate(ids)}

    # the engine's last_price: last finite close since the first bar, else 0
    last = pd.DataFrame(close[first : hi + 1]).ffill().fillna(0.0).to_numpy()
    n_bars = hi - first + 1

    trade_rows = dates.get_indexer([rb.trade_date for rb in res.rebalances]) - first
    held_ids = sorted({h["id"] for rb in res.rebalances for h in rb.holdings})
    hcol = {sid: j for j, sid in enumerate(held_ids)}
    values = np.zeros((n_bars, len(held_ids)))
    cash = np.full(n_bars, float(res.equity.iloc[0]))

    eligible = dec.eligible.copy()
    for k, rb in enumerate(res.rebalances):
        eligible[k] &= np.isfinite(close[int(dec.signal_days[k]) + 1])
        r0 = int(trade_rows[k])
        r1 = int(trade_rows[k + 1]) if k + 1 < len(trade_rows) else n_bars
        invested = 0.0
        for h in rb.holdings:
            i = col[h["id"]]
            shares = h["weight"] * rb.equity / last[r0, i]
            values[r0:r1, hcol[h["id"]]] = shares * last[r0:r1, i]
            invested += h["weight"] * rb.equity
        cash[r0:r1] = rb.equity - invested

    equity = res.equity.to_numpy()
    rebuilt = cash + values.sum(axis=1)
    if not np.allclose(rebuilt, equity, rtol=1e-9, atol=1e-6):
        worst = float(np.max(np.abs(rebuilt / equity - 1)))
        raise RuntimeError(f"position replay does not add up to the equity (off by {worst:.2e})")

    return {
        "dec": dec,
        "res": res,
        "eligible": eligible,
        "index": dates[first : hi + 1],
        "values": pd.DataFrame(values, index=dates[first : hi + 1], columns=held_ids),
        "cash": pd.Series(cash, index=dates[first : hi + 1]),
    }


def _ranks(scores: np.ndarray, eligible: np.ndarray) -> np.ndarray:
    order = np.argsort(-np.where(eligible, scores, -np.inf), kind="stable")
    rank = np.empty(len(scores), dtype="int64")
    rank[order] = np.arange(len(scores))
    return np.where(eligible, rank, -1)


def replay(market: Market, exp: Experiment, results_dir: Path = RESULTS_DIR) -> Trace:
    segments = segments_for(exp, results_dir)
    runs = [_replay_segment(market, seg) for seg in segments]

    # --- chain the segments exactly like wfcv.chain_equity ------------------
    eq_parts, cash_parts, val_parts, factors = [], [], [], []
    level = 1.0
    for j, run in enumerate(runs):
        eq = run["res"].equity
        f = level / float(eq.iloc[0])
        sl = slice(1, None) if j else slice(None)
        eq_parts.append(eq.iloc[sl] * f)
        cash_parts.append(run["cash"].iloc[sl] * f)
        val_parts.append(run["values"].iloc[sl] * f)
        factors.append(f)
        level = float(eq_parts[-1].iloc[-1])

    equity = pd.concat(eq_parts)
    keep = ~equity.index.duplicated(keep="first")
    equity = equity[keep]
    cash = pd.concat(cash_parts)[keep]
    values = pd.concat(val_parts).fillna(0.0)[keep]

    bench = market.benchmark.reindex(equity.index).ffill()
    bench = bench / float(bench.dropna().iloc[0]) * float(equity.iloc[0])
    curve = pd.DataFrame({"equity": equity, "benchmark": bench, "cash": cash})
    curve.index.name = "date"

    pos = values.stack()
    pos = pos[pos > 0].rename("value").reset_index()
    pos.columns = ["date", "id", "value"]

    # --- rebalances, scores and ranks ----------------------------------------
    months: list[dict] = []
    score_rows: list[np.ndarray] = []
    rank_rows: list[np.ndarray] = []
    for seg, run, f in zip(segments, runs, factors, strict=True):
        dec, res = run["dec"], run["res"]
        for k, rb in enumerate(res.rebalances):
            sc = dec.scores.score[k]
            rk = _ranks(sc, run["eligible"][k])
            score_rows.append(sc)
            rank_rows.append(rk)
            months.append({
                "signal": rb.date.strftime("%Y-%m-%d"),
                "trade": rb.trade_date.strftime("%Y-%m-%d"),
                "segment": seg.index,
                "equity": rb.equity * f,
                "n_eligible": int(run["eligible"][k].sum()),
                "n_candidates": rb.n_candidates,
                "turnover": rb.turnover,
                "holdings": {h["id"]: h["weight"] for h in rb.holdings},
            })

    ids = np.array(market.stock_ids)
    score_m = np.vstack(score_rows)
    rank_m = np.vstack(rank_rows)
    listed = ((rank_m >= 0) & (rank_m < TOP)).any(axis=0)
    held = np.isin(ids, [sid for m in months for sid in m["holdings"]])
    cols = np.flatnonzero(listed | held)
    # order the union by first appearance, then best rank: stable and readable
    first_seen = np.where((rank_m[:, cols] >= 0) & (rank_m[:, cols] < TOP), 1, 0).argmax(axis=0)
    cols = cols[np.lexsort((ids[cols], first_seen))]

    n_months = len(months)
    scores = pd.DataFrame({
        "month": np.repeat(np.arange(n_months, dtype="int32"), len(cols)),
        "id": np.tile(ids[cols], n_months),
        "score": score_m[:, cols].reshape(-1).astype("float32"),
        "rank": rank_m[:, cols].reshape(-1).astype("int32"),
    })

    return Trace(exp, segments, months, scores, curve, pos)


# ---------------------------------------------------------------------------
# persistence
# ---------------------------------------------------------------------------


def save_trace(trace: Trace, out_dir: Path = DEFAULT_DIR) -> Path:
    d = out_dir / trace.exp.id
    d.mkdir(parents=True, exist_ok=True)
    meta = {
        "id": trace.exp.id,
        "label": trace.exp.label,
        "note": trace.exp.note,
        "source": trace.exp.source,
        "override": trace.exp.override,
        "segments": [
            {
                "index": s.index,
                "start": s.start.strftime("%Y-%m-%d") if s.start is not None else None,
                "end": s.end.strftime("%Y-%m-%d") if s.end is not None else None,
                "params": s.params.to_dict(),
            }
            for s in trace.segments
        ],
        "metrics": trace.metrics(),
        "months": trace.months,
    }
    (d / "trace.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=1, default=float) + "\n", "utf-8"
    )
    trace.scores.to_parquet(d / "scores.parquet", index=False, compression="zstd")
    trace.curve.astype("float64").to_parquet(d / "curve.parquet", compression="zstd")
    pos = trace.positions.assign(value=trace.positions["value"].astype("float32"))
    pos.to_parquet(d / "positions.parquet", index=False, compression="zstd")
    return d


@dataclass
class SavedTrace:
    """What :func:`save_trace` wrote, read back without the market data."""

    meta: dict
    scores: pd.DataFrame
    curve: pd.DataFrame
    positions: pd.DataFrame

    @property
    def months(self) -> list[dict]:
        return self.meta["months"]

    @property
    def union(self) -> list[str]:
        return list(dict.fromkeys(self.scores["id"]))


def load_trace(exp_id: str, out_dir: Path = DEFAULT_DIR) -> SavedTrace:
    d = out_dir / exp_id
    if not (d / "trace.json").exists():
        raise SystemExit(f"{d} has no trace; run `python -m twstable explore` first")
    return SavedTrace(
        meta=json.loads((d / "trace.json").read_text("utf-8")),
        scores=pd.read_parquet(d / "scores.parquet"),
        curve=pd.read_parquet(d / "curve.parquet"),
        positions=pd.read_parquet(d / "positions.parquet"),
    )


def run_explore_cli(args) -> int:
    from .data import load_panel
    from .portfolio import prepare_market

    out_dir = Path(args.out_dir) if args.out_dir else DEFAULT_DIR
    chosen = [get_experiment(x) for x in args.only] if args.only else list(EXPERIMENTS)
    market = prepare_market(load_panel())
    for exp in chosen:
        trace = replay(market, exp)
        d = save_trace(trace, out_dir)
        m = trace.metrics()["strategy"]
        print(
            f"{exp.id:<11} {len(trace.months):>4} rebalances  {len(trace.union):>4} names  "
            f"CAGR {m['cagr']:+.2%}  Sharpe {m['sharpe']:.2f}  MDD {m['max_drawdown']:.1%}  -> {d}"
        )
    return 0
