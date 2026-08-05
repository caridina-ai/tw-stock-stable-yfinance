"""The shareable report must be a complete, self-contained page."""

from __future__ import annotations

import json
import re

import numpy as np
import pandas as pd
import pytest

from twstable.report import build, build_payload, render


def make_result(n: int = 500) -> dict:
    idx = pd.bdate_range("2016-01-04", periods=n)
    rng = np.random.default_rng(0)
    eq = 1e6 * np.cumprod(1 + rng.normal(0.0006, 0.01, n))
    bm = 1e6 * np.cumprod(1 + rng.normal(0.0003, 0.012, n))
    return {
        "dates": [d.strftime("%Y-%m-%d") for d in idx],
        "equity": list(map(float, eq)),
        "benchmark": list(map(float, bm)),
        "metrics": {"cagr": 0.19, "sharpe": 1.41, "max_drawdown": -0.24},
        "benchmark_metrics": {"cagr": 0.17, "sharpe": 1.01, "max_drawdown": -0.31},
        "relative": {"beta": 0.54},
        "wfcv": {
            "folds": [
                {
                    "index": 1,
                    "test_start": "2016-01-04",
                    "test_end": "2018-01-04",
                    "partial": False,
                    "oos_metrics": {"cagr": 0.11, "sharpe": 1.3,
                                    "max_drawdown": -0.08, "years": 2.0},
                }
            ]
        },
    }


def test_payload_starts_both_series_at_one() -> None:
    p = build_payload(make_result())
    assert p["strategy"][0] == pytest.approx(1.0, abs=0.05)
    assert p["taiex"][0] == pytest.approx(1.0, abs=0.05)
    assert len(p["dates"]) == len(p["strategy"]) == len(p["dd_strategy"])


def test_downsampling_keeps_the_drawdown_trough() -> None:
    """Weekly sampling must not smooth away a one-day crash."""
    res = make_result()
    res["equity"] = [1e6] * len(res["dates"])
    res["equity"][100] = 5e5  # a single -50% day, recovered the next
    p = build_payload(res, freq="W")
    assert min(p["dd_strategy"]) == pytest.approx(-50.0, abs=0.5)


def test_standalone_is_a_complete_document() -> None:
    html = render(build_payload(make_result()), standalone=True)
    assert html.startswith("<!doctype html>")
    assert '<html lang="zh-Hant">' in html
    assert '<meta charset="utf-8">' in html
    assert html.rstrip().endswith("</html>")
    assert html.count("<title>") == 1
    assert "<head>" in html and "<body>" in html
    # social preview + the standalone-only theme control
    assert 'property="og:title"' in html
    assert 'id="theme-toggle"' in html


def test_fragment_omits_the_document_shell() -> None:
    """A host page supplies the shell and its own theme control."""
    html = render(build_payload(make_result()), standalone=False)
    for tag in ("<!doctype", "<html", "<head>", "<body>"):
        assert tag not in html.lower()
    assert "theme-toggle" not in html
    assert html.count("<title>") == 1


def test_data_is_fully_inlined_and_valid_json() -> None:
    html = render(build_payload(make_result()), standalone=True)
    assert "__DATA__" not in html
    m = re.search(
        r'<script id="chart-data" type="application/json">(.*?)</script>', html, re.S
    )
    assert m, "the data block is missing"
    parsed = json.loads(m.group(1))
    assert {"dates", "strategy", "taiex", "dd_strategy", "dd_taiex", "metrics"} <= parsed.keys()


def test_page_fetches_nothing_at_runtime() -> None:
    """It must work offline and from file:// -- a strict CSP blocks any host."""
    html = render(build_payload(make_result()), standalone=True)
    urls = set(re.findall(r"https?://[^\"'\s)]+", html))
    # the SVG namespace is an identifier, not a request
    assert urls <= {"http://www.w3.org/2000/svg"}, urls
    for bad in ("<link", "@import", "fetch(", "XMLHttpRequest"):
        assert bad not in html


def test_build_writes_the_file(tmp_path) -> None:
    src = tmp_path / "result.json"
    src.write_text(json.dumps(make_result()), encoding="utf-8")
    out = tmp_path / "site" / "index.html"
    written = build(result_path=src, out_path=out)
    assert written == out and out.exists()
    assert out.read_text(encoding="utf-8").startswith("<!doctype html>")


def test_missing_benchmark_is_rejected() -> None:
    res = make_result()
    res["benchmark"] = [None] * len(res["dates"])
    with pytest.raises(ValueError, match="benchmark"):
        build_payload(res)
