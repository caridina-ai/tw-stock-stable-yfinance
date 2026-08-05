"""Project-wide paths and market constants."""

from __future__ import annotations

from pathlib import Path

# --- paths -----------------------------------------------------------------

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
PRICES_DIR = DATA_DIR / "prices"
RESULTS_DIR = ROOT / "results"

UNIVERSE_CSV = DATA_DIR / "universe.csv"
PANEL_PARQUET = DATA_DIR / "panel.parquet"

for _d in (DATA_DIR, PRICES_DIR, RESULTS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# --- market conventions ----------------------------------------------------

TRADING_DAYS_PER_YEAR = 252

BENCHMARK = "^TWII"  # 台灣加權股價指數
BENCHMARK_NAME = "TAIEX"

# Taiwan retail transaction costs.
# Brokerage fee 0.1425% each side (a 6-fold discount is common; we keep the
# full rate to stay conservative), securities transaction tax 0.3% on sells.
FEE_RATE = 0.001425
SELL_TAX_RATE = 0.003

# yfinance history window used by `twstable fetch`.
#
# 2000-01-04 is a hard floor, not a preference: Yahoo's Taiwan daily history
# simply begins there. A 100-symbol sample found nothing at all before 2000,
# then ~34% of the universe appearing at once on 2000-01-04. (A stray name like
# 2317 goes back to 1993, but it is the exception.) OTC (.TWO) is a second
# cliff — Yahoo carries no OTC daily bars before 2008 — so anything earlier
# than that is a TWSE-only universe of roughly 600 names.
DEFAULT_START = "2000-01-01"
