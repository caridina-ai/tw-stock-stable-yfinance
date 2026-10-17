"""How much of a walk-forward result is the luck of the random search?

``results/PREREGISTRATION.md`` fixed three procedures and five seeds before any
of them ran.  This module only reads the finished result files and tabulates
them -- it never picks one.  The default seed of each procedure is the result
file the rest of the project uses; the other seeds live in ``results/seeds/``.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from .config import RESULTS_DIR
from .metrics import compute_metrics, relative_metrics

SEEDS_DIR = RESULTS_DIR / "seeds"
DEFAULT_SEED = 20260805
SEEDS = (DEFAULT_SEED, 1, 2, 3, 4)

#: procedure -> (description, result file of the default seed)
VARIANTS: dict[str, tuple[str, str]] = {
    "nested": ("每折自己挑換股日、假日、檔數、權重上限、權重方式（上線版）", "wfcv_nested.json"),
    "monthly": ("固定 24 日、延後、12 檔、權重上限、等權重（看過答案）", "wfcv_monthly.json"),
    "interval": ("第一次 WFCV：每 10–60 交易日、10–30 檔", "wfcv.json"),
}

#: fold 2's test window, the 2008 crash
CRISIS = ("2008-01-02", "2010-01-01")


def path_for(variant: str, seed: int, results_dir: Path = RESULTS_DIR) -> Path:
    if seed == DEFAULT_SEED:
        return results_dir / VARIANTS[variant][1]
    return results_dir / "seeds" / f"{variant}_s{seed}.json"


def run_metrics(path: Path) -> dict:
    raw = json.loads(path.read_text("utf-8"))
    idx = pd.DatetimeIndex(raw["dates"])
    eq = pd.Series(raw["equity"], index=idx, dtype="float64")
    bench = pd.Series(raw["benchmark"], index=idx, dtype="float64")
    m = compute_metrics(eq)
    rel = relative_metrics(eq, bench)
    crisis = eq.loc[CRISIS[0]:CRISIS[1]]
    prod = raw["wfcv"]["production_params"]
    return {
        "cagr": m.cagr,
        "sharpe": m.sharpe,
        "max_drawdown": m.max_drawdown,
        "worst_year": m.worst_year,
        "excess_cagr": rel.get("excess_cagr", np.nan),
        "dd_2008": float((crisis / crisis.cummax() - 1).min()),
        "prod_day": prod.get("rebalance_day", 0),
        "prod_m": prod["max_m"],
        "prod_cap": bool(prod.get("cap_weight", False)),
    }


def summarise(results_dir: Path = RESULTS_DIR) -> pd.DataFrame:
    rows = []
    for variant in VARIANTS:
        for seed in SEEDS:
            path = path_for(variant, seed, results_dir)
            if path.exists():
                rows.append({"variant": variant, "seed": seed, **run_metrics(path)})
    return pd.DataFrame(rows)


def fold_choices(path: Path) -> pd.DataFrame:
    """What every fold of one run picked for the settings the sweep used to pin."""
    raw = json.loads(path.read_text("utf-8"))["wfcv"]
    rows = []
    for f in raw["folds"] + [{"index": "上線", "test_start": "", "params": raw["production_params"],
                              "oos_metrics": {}}]:
        p = f["params"]
        rows.append({
            "fold": f["index"],
            "test": f["test_start"],
            "day": p.get("rebalance_day", 0),
            "holiday": p.get("holiday", "next"),
            "m": p["max_m"],
            "cap": bool(p.get("cap_weight", False)),
            "weighting": p.get("weighting", "equal"),
            "min_score": p["min_score"],
            "window": p["score"]["window"],
            "oos_cagr": f["oos_metrics"].get("cagr", np.nan),
            "oos_mdd": f["oos_metrics"].get("max_drawdown", np.nan),
        })
    return pd.DataFrame(rows)


def _pct(x: float) -> str:
    return "" if not np.isfinite(x) else f"{x:+.1%}"


def to_markdown(df: pd.DataFrame, choices: pd.DataFrame | None) -> str:
    out = [
        "# 搜尋雜訊：同一套做法換亂數種子",
        "",
        "由 `twstable seeds` 從結果檔產生，不要手改。規則見 [`../PREREGISTRATION.md`](../PREREGISTRATION.md)：",
        f"種子 {DEFAULT_SEED} 是正式結果，其餘種子只用來看運氣，不從中挑選。",
        "",
        "| 做法 | 種子 | CAGR | Sharpe | 最大回檔 | 最差一年 | 超額 CAGR | 2008–09 回檔 | 上線參數 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for variant, grp in df.groupby("variant", sort=False):
        for r in grp.itertuples():
            tag = "**正式**" if r.seed == DEFAULT_SEED else str(r.seed)
            sched = f"{r.prod_day} 日" if r.prod_day else "間隔"
            prod = f"{sched}、{r.prod_m} 檔{'、上限' if r.prod_cap else ''}"
            out.append(
                f"| {variant} | {tag} | {_pct(r.cagr)} | {r.sharpe:.2f} | {_pct(r.max_drawdown)} "
                f"| {_pct(r.worst_year)} | {_pct(r.excess_cagr)} | {_pct(r.dd_2008)} | {prod} |"
            )
        med = grp.median(numeric_only=True)
        out.append(
            f"| {variant} | 中位數 | {_pct(med.cagr)} | {med.sharpe:.2f} | {_pct(med.max_drawdown)} "
            f"| {_pct(med.worst_year)} | {_pct(med.excess_cagr)} | {_pct(med.dd_2008)} | |"
        )
    out += ["", "做法："]
    out += [f"- `{k}`：{desc}" for k, (desc, _) in VARIANTS.items()]
    if choices is not None:
        out += [
            "",
            "## 正式結果各折挑到的設定",
            "",
            "| 折 | 測試起 | 換股日 | 假日 | 檔數 | 權重上限 | 權重 | min_score | window | OOS CAGR | OOS 最大回檔 |",
            "|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        for r in choices.itertuples():
            out.append(
                f"| {r.fold} | {r.test} | {r.day} | {'延後' if r.holiday == 'next' else '提前'} | {r.m} "
                f"| {'開' if r.cap else '關'} | {'等權重' if r.weighting == 'equal' else '分數'} "
                f"| {r.min_score:.2f} | {r.window} | {_pct(r.oos_cagr)} | {_pct(r.oos_mdd)} |"
            )
    return "\n".join(out) + "\n"


def run_seeds_cli(args) -> int:
    results_dir = Path(args.results_dir) if args.results_dir else RESULTS_DIR
    df = summarise(results_dir)
    if df.empty:
        raise SystemExit(f"no result files under {results_dir}")
    official = path_for("nested", DEFAULT_SEED, results_dir)
    choices = fold_choices(official) if official.exists() else None
    out = results_dir / "seeds"
    out.mkdir(parents=True, exist_ok=True)
    df.to_csv(out / "summary.csv", index=False)
    (out / "SUMMARY.md").write_text(to_markdown(df, choices), "utf-8")
    print(to_markdown(df, choices))
    print(f"written to {out / 'SUMMARY.md'}")
    return 0
