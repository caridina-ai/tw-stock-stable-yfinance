"""Taiwan listed-equity universe, scraped from the TWSE ISIN service.

The ISIN pages are the canonical public list of every security with a Taiwanese
ISIN.  We keep only ordinary shares (CFICode ``ESVUFR``) so that ETFs, warrants,
TDRs, preferred shares and beneficiary certificates stay out of the universe.
"""

from __future__ import annotations

import io
import time

import pandas as pd
import requests

from .config import UNIVERSE_CSV

ISIN_URL = "https://isin.twse.com.tw/isin/C_public.jsp?strMode={mode}"

# strMode -> (market label, yfinance suffix)
MARKETS = {
    2: ("上市", ".TW"),  # TWSE main board
    4: ("上櫃", ".TWO"),  # TPEx / OTC
}

COMMON_STOCK_CFI = "ESVUFR"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}


# The ISIN server rejects a request that follows another too closely with a
# tiny "FOR SECURITY REASONS, THIS PAGE CAN NOT BE ACCESSED" page (HTTP 200, no
# table).  Space the two listings out, and back off when blocked anyway.
_PAUSE_BETWEEN_PAGES = 15.0
_BLOCKED_BACKOFF = (30.0, 60.0, 120.0, 240.0)


def _get_page(mode: int, timeout: int = 60) -> str:
    """Fetch one ISIN listing page, retrying while the server blocks us."""
    for wait in (*_BLOCKED_BACKOFF, None):
        resp = requests.get(ISIN_URL.format(mode=mode), headers=_HEADERS, timeout=timeout)
        resp.raise_for_status()
        # The page is served as Big5/MS950 regardless of what the header claims.
        resp.encoding = "ms950"
        if "<table" in resp.text.lower() and "FOR SECURITY REASONS" not in resp.text:
            return resp.text
        if wait is None:
            break
        time.sleep(wait)
    raise RuntimeError(f"ISIN server keeps blocking strMode={mode}; try again later")


def _fetch_mode(mode: int, timeout: int = 60) -> pd.DataFrame:
    """Download and parse one ISIN listing page."""
    raw = pd.read_html(io.StringIO(_get_page(mode, timeout)), flavor="lxml")[0]

    raw.columns = ["code_name", "isin", "listed_date", "market", "industry", "cfi", "note"]
    raw = raw[raw["cfi"] == COMMON_STOCK_CFI].copy()

    # "1101　台泥" -> ("1101", "台泥")
    split = raw["code_name"].astype(str).str.split(r"[　\s]+", n=1, expand=True)
    raw["stock_id"] = split[0].str.strip()
    raw["name"] = split[1].str.strip() if split.shape[1] > 1 else ""

    market_label, suffix = MARKETS[mode]
    raw["market"] = market_label
    raw["ticker"] = raw["stock_id"] + suffix
    raw["listed_date"] = pd.to_datetime(raw["listed_date"], errors="coerce")

    cols = ["stock_id", "name", "ticker", "market", "industry", "listed_date"]
    return raw[cols].reset_index(drop=True)


def fetch_universe(include_otc: bool = True) -> pd.DataFrame:
    """Return every Taiwanese ordinary share as a tidy DataFrame."""
    modes = [2, 4] if include_otc else [2]
    frames = []
    for i, m in enumerate(modes):
        if i:
            time.sleep(_PAUSE_BETWEEN_PAGES)
        frames.append(_fetch_mode(m))
    out = pd.concat(frames, ignore_index=True)
    # A handful of ids are numeric-with-letter (e.g. "2841A"); keep 4-digit ids only,
    # which is what yfinance can resolve.
    out = out[out["stock_id"].str.fullmatch(r"\d{4}")]
    out = out.drop_duplicates("stock_id").sort_values("stock_id").reset_index(drop=True)
    return out


def save_universe(df: pd.DataFrame) -> None:
    df.to_csv(UNIVERSE_CSV, index=False, encoding="utf-8-sig")


def load_universe() -> pd.DataFrame:
    """Load the cached universe, fetching it first if absent."""
    if not UNIVERSE_CSV.exists():
        df = fetch_universe()
        save_universe(df)
        return df
    df = pd.read_csv(UNIVERSE_CSV, dtype={"stock_id": str})
    df["listed_date"] = pd.to_datetime(df["listed_date"], errors="coerce")
    return df


def name_map() -> dict[str, str]:
    """stock_id -> Chinese short name."""
    uni = load_universe()
    return dict(zip(uni["stock_id"], uni["name"], strict=False))
