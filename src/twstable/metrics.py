"""Performance statistics for an equity curve."""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from .config import TRADING_DAYS_PER_YEAR


def drawdown(equity: pd.Series) -> pd.Series:
    """Fractional drawdown from the running peak (0 at a new high, negative below)."""
    eq = equity.astype("float64")
    return eq / eq.cummax() - 1.0


def max_drawdown(equity: pd.Series) -> float:
    return float(drawdown(equity).min())


def cagr(equity: pd.Series) -> float:
    eq = equity.dropna()
    if len(eq) < 2 or eq.iloc[0] <= 0:
        return float("nan")
    years = (eq.index[-1] - eq.index[0]).days / 365.25
    if years <= 0:
        return float("nan")
    return float((eq.iloc[-1] / eq.iloc[0]) ** (1.0 / years) - 1.0)


def _daily_returns(equity: pd.Series) -> pd.Series:
    return equity.astype("float64").pct_change().dropna()


def volatility(equity: pd.Series) -> float:
    r = _daily_returns(equity)
    return float(r.std(ddof=1) * np.sqrt(TRADING_DAYS_PER_YEAR)) if len(r) > 1 else float("nan")


def sharpe(equity: pd.Series, rf: float = 0.0) -> float:
    r = _daily_returns(equity) - rf / TRADING_DAYS_PER_YEAR
    if len(r) < 2 or r.std(ddof=1) == 0:
        return float("nan")
    return float(r.mean() / r.std(ddof=1) * np.sqrt(TRADING_DAYS_PER_YEAR))


def sortino(equity: pd.Series, rf: float = 0.0) -> float:
    r = _daily_returns(equity) - rf / TRADING_DAYS_PER_YEAR
    downside = r[r < 0]
    if len(r) < 2 or len(downside) < 2 or downside.std(ddof=1) == 0:
        return float("nan")
    return float(r.mean() / downside.std(ddof=1) * np.sqrt(TRADING_DAYS_PER_YEAR))


def calmar(equity: pd.Series) -> float:
    mdd = max_drawdown(equity)
    return float("nan") if mdd == 0 else float(cagr(equity) / abs(mdd))


def ulcer_index(equity: pd.Series) -> float:
    """RMS drawdown -- penalises long, deep underwater stretches."""
    dd = drawdown(equity)
    return float(np.sqrt(np.mean(np.square(dd)))) if len(dd) else float("nan")


def longest_drawdown_days(equity: pd.Series) -> int:
    """Longest stretch (calendar days) between a peak and its recovery."""
    eq = equity.dropna()
    if eq.empty:
        return 0
    peak_idx = eq.index[0]
    peak_val = eq.iloc[0]
    worst = 0
    for ts, val in eq.items():
        if val >= peak_val:
            worst = max(worst, (ts - peak_idx).days)
            peak_val, peak_idx = val, ts
    return max(worst, (eq.index[-1] - peak_idx).days)


@dataclass
class Metrics:
    start: str
    end: str
    years: float
    total_return: float
    cagr: float
    volatility: float
    sharpe: float
    sortino: float
    max_drawdown: float
    calmar: float
    ulcer_index: float
    longest_drawdown_days: int
    best_year: float
    worst_year: float
    positive_months: float

    def to_dict(self) -> dict:
        return asdict(self)


def _yearly(equity: pd.Series) -> pd.Series:
    return equity.resample("YE").last().pct_change().dropna()


def _monthly(equity: pd.Series) -> pd.Series:
    return equity.resample("ME").last().pct_change().dropna()


def compute_metrics(equity: pd.Series) -> Metrics:
    eq = equity.dropna().astype("float64")
    if len(eq) < 2:
        raise ValueError("equity curve too short for metrics")
    years = (eq.index[-1] - eq.index[0]).days / 365.25
    yr, mo = _yearly(eq), _monthly(eq)
    return Metrics(
        start=str(eq.index[0].date()),
        end=str(eq.index[-1].date()),
        years=round(years, 2),
        total_return=float(eq.iloc[-1] / eq.iloc[0] - 1.0),
        cagr=cagr(eq),
        volatility=volatility(eq),
        sharpe=sharpe(eq),
        sortino=sortino(eq),
        max_drawdown=max_drawdown(eq),
        calmar=calmar(eq),
        ulcer_index=ulcer_index(eq),
        longest_drawdown_days=longest_drawdown_days(eq),
        best_year=float(yr.max()) if len(yr) else float("nan"),
        worst_year=float(yr.min()) if len(yr) else float("nan"),
        positive_months=float((mo > 0).mean()) if len(mo) else float("nan"),
    )


def relative_metrics(equity: pd.Series, benchmark: pd.Series) -> dict:
    """Beta, alpha, tracking error and information ratio against a benchmark."""
    both = pd.concat([equity, benchmark], axis=1).dropna()
    if len(both) < 30:
        return {}
    r_p = both.iloc[:, 0].pct_change().dropna()
    r_b = both.iloc[:, 1].pct_change().dropna()
    aligned = pd.concat([r_p, r_b], axis=1).dropna()
    if len(aligned) < 30:
        return {}
    r_p, r_b = aligned.iloc[:, 0], aligned.iloc[:, 1]

    var_b = float(r_b.var(ddof=1))
    beta = float(r_p.cov(r_b) / var_b) if var_b > 0 else float("nan")
    alpha_daily = float(r_p.mean() - beta * r_b.mean())
    active = r_p - r_b
    te = float(active.std(ddof=1) * np.sqrt(TRADING_DAYS_PER_YEAR))
    return {
        "beta": beta,
        "alpha_annual": float((1 + alpha_daily) ** TRADING_DAYS_PER_YEAR - 1),
        "correlation": float(r_p.corr(r_b)),
        "tracking_error": te,
        "information_ratio": (
            float(active.mean() * TRADING_DAYS_PER_YEAR / te) if te > 0 else float("nan")
        ),
        "excess_cagr": cagr(both.iloc[:, 0]) - cagr(both.iloc[:, 1]),
    }


def format_metrics(m: Metrics, name: str = "strategy") -> str:
    return "\n".join(
        [
            f"--- {name} ---",
            f"  period            {m.start} .. {m.end}  ({m.years:.1f}y)",
            f"  total return      {m.total_return:+.1%}",
            f"  CAGR              {m.cagr:+.2%}",
            f"  volatility        {m.volatility:.2%}",
            f"  Sharpe            {m.sharpe:.2f}",
            f"  Sortino           {m.sortino:.2f}",
            f"  max drawdown      {m.max_drawdown:.2%}",
            f"  Calmar            {m.calmar:.2f}",
            f"  Ulcer index       {m.ulcer_index:.3f}",
            f"  longest DD        {m.longest_drawdown_days} days",
            f"  best / worst year {m.best_year:+.1%} / {m.worst_year:+.1%}",
            f"  positive months   {m.positive_months:.1%}",
        ]
    )
