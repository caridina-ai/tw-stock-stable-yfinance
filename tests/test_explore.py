"""Experiment replay, the stacked layout and the viewer's data export."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from test_wfcv import make_panel

from twstable.explore import Experiment, load_trace, replay, save_trace
from twstable.portfolio import StrategyParams, prepare_market, run_backtest
from twstable.score import ScoreParams
from twstable.stacked import (
    Run,
    assign_firstfit,
    assign_sweep,
    build_runs,
    draw,
    jump_cost,
    layout,
)
from twstable.viewer_build import (
    TEMPLATE,
    TICKS_PER_LOG,
    decode_close,
    encode_prices,
    experiment_payload,
)
from twstable.wfcv import chain_equity


@pytest.fixture(scope="module")
def market():
    return prepare_market(make_panel(n_days=1500, n_stocks=24, seed=7))


@pytest.fixture(scope="module")
def folds(market):
    base = StrategyParams(score=ScoreParams(window=200), max_m=5, rebalance=20,
                          min_score=0.0, min_dollar_volume=0.0)
    d = market.dates
    return [(d[400], d[800], base), (d[800], d[1200], base.replace(max_m=4, rebalance_day=11)),
            (d[1200], d[1499], base.replace(cap_weight=True))]


@pytest.fixture(scope="module")
def exp_dir(tmp_path_factory, folds) -> Path:
    d = tmp_path_factory.mktemp("results")
    payload = {"wfcv": {"folds": [
        {"index": k + 1, "test_start": s.strftime("%Y-%m-%d"), "test_end": e.strftime("%Y-%m-%d"),
         "params": p.to_dict()}
        for k, (s, e, p) in enumerate(folds)
    ]}}
    (d / "wf.json").write_text(json.dumps(payload), "utf-8")
    (d / "params.json").write_text(json.dumps(folds[0][2].to_dict()), "utf-8")
    return d


@pytest.fixture(scope="module")
def trace(market, exp_dir):
    return replay(market, Experiment("wf", "wf", "", "wf.json"), exp_dir)


# --- replay ----------------------------------------------------------------


def test_walk_forward_replay_matches_the_chained_engine(market, folds, trace) -> None:
    runs = [run_backtest(market, p, start=s, end=e) for s, e, p in folds]
    eq = chain_equity([r.equity for r in runs])
    assert trace.curve.index.equals(eq.index)
    np.testing.assert_allclose(trace.curve["equity"].to_numpy(), eq.to_numpy(), rtol=1e-12)
    rebs = [rb for r in runs for rb in r.rebalances]
    assert len(trace.months) == len(rebs)
    for month, rb in zip(trace.months, rebs, strict=True):
        assert month["trade"] == rb.trade_date.strftime("%Y-%m-%d")
        assert month["holdings"] == {h["id"]: h["weight"] for h in rb.holdings}


def test_positions_and_cash_add_up_to_the_equity(trace) -> None:
    held = trace.positions.groupby("date")["value"].sum()
    total = trace.curve["cash"].add(held, fill_value=0.0)
    np.testing.assert_allclose(total.to_numpy(), trace.curve["equity"].to_numpy(), rtol=1e-9)


def test_ranks_order_the_eligible_names_by_score(trace) -> None:
    for m, grp in trace.scores.groupby("month"):
        ok = grp[grp["rank"] >= 0].sort_values("rank")
        assert list(ok["rank"]) == list(range(len(ok))), f"month {m}: ranks not contiguous"
        assert (np.diff(ok["score"].to_numpy()) <= 1e-7).all()
    held = {sid for m in trace.months for sid in m["holdings"]}
    assert held <= set(trace.union), "every held name must be in the exported union"


def test_single_run_experiment_and_round_trip(market, exp_dir, tmp_path) -> None:
    t = replay(market, Experiment("one", "one", "", "params.json", start="2005-01-01"), exp_dir)
    assert {m["segment"] for m in t.months} == {0}
    save_trace(t, tmp_path)
    back = load_trace("one", tmp_path)
    assert back.months == json.loads(json.dumps(t.months))
    assert back.union == t.union
    np.testing.assert_allclose(back.curve["equity"], t.curve["equity"])


# --- stacked layout ----------------------------------------------------------


def _runs_overlap(a: Run, b: Run) -> bool:
    return not (a.b < b.a or b.b < a.a)


@pytest.mark.parametrize("assign", [assign_sweep, assign_firstfit])
def test_a_layer_never_holds_two_names_at_once(trace, assign) -> None:
    runs = build_runs(trace.months)
    layer = assign(runs, len(trace.months))
    for j in set(layer):
        mine = [r for r, lay in zip(runs, layer, strict=True) if lay == j]
        for i, a in enumerate(mine):
            for b in mine[i + 1 :]:
                assert not _runs_overlap(a, b)


def test_sweep_needs_no_more_layers_than_the_largest_book(trace) -> None:
    runs = build_runs(trace.months)
    layer = assign_sweep(runs, len(trace.months))
    assert layer.max() + 1 == max(len(m["holdings"]) for m in trace.months)


def test_runs_cover_every_holding_exactly_once(trace) -> None:
    runs = build_runs(trace.months)
    for m, month in enumerate(trace.months):
        active = sorted(r.stock for r in runs if r.a <= m <= r.b)
        assert active == sorted(month["holdings"])


def test_a_handoff_of_equal_value_costs_nothing() -> None:
    # layer 0: A then B (same value), layer 1 continues on top of it
    delta = np.array([[0.0, 0.0]])
    cont = np.array([[False, True]])
    assert jump_cost(delta, cont, np.array([1.0]), np.array([0, 1])) == 0.0
    # a vacated layer below a continuing band is a jump of its value
    delta = np.array([[-0.25, 0.0]])
    assert jump_cost(delta, cont, np.array([1.0]), np.array([0, 1])) == pytest.approx(0.25)
    assert jump_cost(delta, cont, np.array([1.0]), np.array([1, 0])) == 0.0


def test_layout_and_drawing(trace, tmp_path) -> None:
    save_trace(trace, tmp_path)
    saved = load_trace("wf", tmp_path)
    lay = layout(saved)
    assert sorted(lay.order) == list(range(len(lay.order)))
    path = draw(saved, tmp_path / "stacked.png") and tmp_path / "stacked.png"
    assert path.stat().st_size > 10_000


# --- viewer export -----------------------------------------------------------


def test_prices_round_trip_within_half_a_tick() -> None:
    rng = np.random.default_rng(3)
    close = 30 * np.exp(np.cumsum(rng.normal(0, 0.02, 400)))
    close[:5] = np.nan  # not listed yet
    close[100:104] = np.nan  # long halt
    ohlc = np.column_stack([close * 0.99, close * 1.02, close * 0.97])
    ohlc[50] = np.nan  # a filled day without its own bar
    p = encode_prices(close, ohlc)
    assert p["s"] == 5
    back = decode_close(p, len(close))
    ok = np.isfinite(close)
    assert (np.isfinite(back) == ok).all()
    assert np.max(np.abs(np.log(back[ok] / close[ok]))) <= 0.5 / TICKS_PER_LOG + 1e-12
    assert p["h"][50 - 5] is None
    # offsets are differences of two rounded ticks, so within one tick of exact
    assert abs(p["h"][60 - 5] - np.log(1.02) * TICKS_PER_LOG) <= 1.0


def test_refitting_rounded_prices_barely_moves_the_score(market) -> None:
    from twstable.score import compute_scores
    from twstable.trend import build_prefix

    params = ScoreParams(window=200)
    ends = np.arange(300, 1500, 21)
    exact = compute_scores(market.pref, market.log_px, ends, params).score
    rounded = np.column_stack([
        decode_close(encode_prices(market.close[:, i], None), len(market.dates))
        for i in range(market.close.shape[1])
    ])
    log_r = np.log(rounded)
    approx = compute_scores(build_prefix(log_r), log_r, ends, params).score
    ok = exact > 0.05
    rel = np.abs(approx[ok] / exact[ok] - 1)
    assert np.median(rel) < 1e-3
    assert np.abs(approx - exact).max() < 0.02


def test_experiment_payload_shapes(market, trace, tmp_path) -> None:
    save_trace(trace, tmp_path)
    saved = load_trace("wf", tmp_path)
    cal = {d: i for i, d in enumerate(market.dates)}
    p = experiment_payload(saved, cal)
    n_m, n_u = len(p["months"]), len(p["union"])
    assert len(p["score"]) == n_u and all(len(r) == n_m for r in p["score"])
    assert len(p["rank"]) == n_u and all(len(r) == n_m for r in p["rank"])
    for m, mo in enumerate(p["months"]):
        assert mo["s"] < mo["t"]
        assert market.dates[mo["t"]].strftime("%Y-%m-%d") == saved.months[m]["trade"]
        weights = {p["union"][u]: w / 1e4 for u, w in mo["h"]}
        assert weights == pytest.approx(saved.months[m]["holdings"], abs=1e-4)
    assert p["curve"]["d"] == sorted(p["curve"]["d"])


def test_exits_are_the_round_trips_each_rebalance_closed(market, trace, tmp_path) -> None:
    from twstable.viewer_build import exits

    save_trace(trace, tmp_path)
    saved = load_trace("wf", tmp_path)
    cal = {d: i for i, d in enumerate(market.dates)}
    adj = pd.DataFrame(market.close, columns=market.stock_ids).ffill()
    raw = adj * 1.5  # stands in for unadjusted prices: must be passed through untouched
    out = exits(saved, cal, lambda sid: (adj[sid].to_numpy(), raw[sid].to_numpy()))
    assert len(out) == len(saved.months) and out[0] == []
    n_closed = 0
    for m, closed in enumerate(out[1:], start=1):
        prev, now = set(saved.months[m - 1]["holdings"]), set(saved.months[m]["holdings"])
        assert [c[0] for c in closed] == sorted(prev - now)
        for sid, buy, sell, ret in closed:
            a = m - 1
            while a > 0 and sid in saved.months[a - 1]["holdings"]:
                a -= 1
            t0 = cal[pd.Timestamp(saved.months[a]["trade"])]
            t1 = cal[pd.Timestamp(saved.months[m]["trade"])]
            assert buy == pytest.approx(raw[sid].iloc[t0])
            assert sell == pytest.approx(raw[sid].iloc[t1])
            assert ret == pytest.approx(adj[sid].iloc[t1] / adj[sid].iloc[t0] - 1)
            n_closed += 1
    assert n_closed > 0
    p = experiment_payload(saved, cal, lambda sid: (adj[sid].to_numpy(), raw[sid].to_numpy()))
    assert sum(len(mo["x"]) for mo in p["months"]) == n_closed


def test_viewer_page_is_self_contained() -> None:
    html = TEMPLATE.read_text("utf-8")
    assert "<script src" not in html and "<link" not in html
    assert "http://" not in html and "https://" not in html
    for key in ("ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Enter", "Home", "End"):
        assert f'"{key}"' in html


def test_engine_wrapper_is_unchanged_by_the_decision_split(market) -> None:
    """plan_decisions is the first half of run_backtest; the split must not move a bar."""
    p = StrategyParams(score=ScoreParams(window=150), max_m=6, rebalance_day=5,
                       min_score=0.0, min_dollar_volume=0.0)
    res = run_backtest(market, p, start=market.dates[300])
    from twstable.portfolio import plan_decisions

    dec = plan_decisions(market, p, market.dates[300])
    assert [rb.date for rb in res.rebalances] == list(market.dates[dec.signal_days])
    assert pd.Timestamp(res.equity.index[0]) == market.dates[dec.first]
