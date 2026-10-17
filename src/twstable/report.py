"""Build the shareable HTML report from a backtest result.

The page is a single self-contained file -- no CDN, no build step, no runtime
dependency -- so ``docs/index.html`` can be committed and served straight from
GitHub Pages.

Two output shapes come from one template:

``standalone``
    a complete document with ``<head>``, social preview tags and a theme
    toggle.  This is what goes in ``docs/``.
``fragment``
    body content only, for a host that supplies the document shell and its own
    theme toggle.

Regenerating is part of the pipeline on purpose: the numbers in the page are
baked in at build time, so re-running the backtest and forgetting to rebuild
would leave a stale page claiming current results.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from .config import RESULTS_DIR, ROOT

TEMPLATE = Path(__file__).parent / "templates" / "report.html"
# the production strategy: the walk-forward run whose folds pick their own monthly settings
DEFAULT_RESULT = RESULTS_DIR / "wfcv_nested.json"
DEFAULT_OUT = ROOT / "docs" / "index.html"

PAGE_TITLE = "長期穩定上漲股策略 — 二十年 Walk-Forward 實測"
PAGE_DESC = (
    "台股「長期穩定上漲」選股策略的 20.8 年 walk-forward 樣本外實測（事先登記、不看答案）："
    "年化 15.3%、最大回檔 -34.6%，對照大盤 10.0% / -58.3%；換亂數種子重跑，年化 9.7%–16.9%。"
)


# ---------------------------------------------------------------------------
# data payload
# ---------------------------------------------------------------------------


def build_payload(result: dict, freq: str = "W") -> dict:
    """Reduce a backtest result to just what the page plots.

    Equity is sampled at the close of each period; drawdown takes the *minimum*
    over the period so a trough between two samples is never smoothed away.
    """
    idx = pd.DatetimeIndex(pd.to_datetime(result["dates"]))
    eq = pd.Series(result["equity"], index=idx, dtype="float64")
    raw_bm = [np.nan if v is None else v for v in result.get("benchmark", [])]
    bm = (
        pd.Series(raw_bm, index=idx, dtype="float64").ffill()
        if len(raw_bm) == len(idx)
        else pd.Series(np.nan, index=idx)
    )
    if not bm.notna().any():
        raise ValueError("result has no benchmark series to compare against")

    eq = eq / eq.iloc[0]
    bm = bm / bm.dropna().iloc[0]
    dd_e = eq / eq.cummax() - 1.0
    dd_b = bm / bm.cummax() - 1.0

    grouper = pd.Grouper(freq=freq)
    level = pd.DataFrame({"e": eq, "b": bm}).groupby(grouper).last()
    trough = pd.DataFrame({"de": dd_e, "db": dd_b}).groupby(grouper).min()
    w = level.join(trough).dropna()

    payload = {
        "dates": [t.strftime("%Y-%m-%d") for t in w.index],
        "strategy": [round(float(v), 4) for v in w["e"]],
        "taiex": [round(float(v), 4) for v in w["b"]],
        "dd_strategy": [round(float(v) * 100, 2) for v in w["de"]],
        "dd_taiex": [round(float(v) * 100, 2) for v in w["db"]],
        "metrics": {
            "strategy": result["metrics"],
            "taiex": result["benchmark_metrics"],
            "relative": result.get("relative", {}),
        },
    }

    folds = result.get("wfcv", {}).get("folds", [])
    payload["folds"] = [
        {
            "index": f["index"],
            "test_start": f["test_start"],
            "test_end": f["test_end"],
            "partial": bool(f.get("partial", False)),
            "cagr": f["oos_metrics"]["cagr"],
            "sharpe": f["oos_metrics"]["sharpe"],
            "mdd": f["oos_metrics"]["max_drawdown"],
            "years": f["oos_metrics"]["years"],
            "day": f.get("params", {}).get("rebalance_day", 0),
            "m": f.get("params", {}).get("max_m", 0),
        }
        for f in folds
    ]
    return payload


# ---------------------------------------------------------------------------
# assembly
# ---------------------------------------------------------------------------


@dataclass
class Sections:
    head: str
    body: str
    script: str


def _split_template(text: str) -> Sections:
    def grab(name: str) -> str:
        open_, close = f"<!--{name}-->", f"<!--/{name}-->"
        i, j = text.find(open_), text.find(close)
        if i < 0 or j < 0:
            raise ValueError(f"template is missing the {name} markers")
        return text[i + len(open_) : j].strip()

    return Sections(head=grab("HEAD"), body=grab("BODY"), script=grab("SCRIPT"))


#: Sets the stored theme before first paint so a dark-mode reader never sees a
#: white flash, then wires the toggle. Only needed standalone -- a host page
#: supplies its own control.
THEME_SCRIPT = """<script>
  (() => {
    const KEY = "twstable-theme";
    try {
      const saved = localStorage.getItem(KEY);
      if (saved === "dark" || saved === "light") {
        document.documentElement.setAttribute("data-theme", saved);
      }
    } catch (e) { /* private mode: fall back to the OS preference */ }
    window.__toggleTheme = () => {
      const de = document.documentElement;
      const dark = de.getAttribute("data-theme")
        ? de.getAttribute("data-theme") === "dark"
        : window.matchMedia("(prefers-color-scheme: dark)").matches;
      const next = dark ? "light" : "dark";
      de.setAttribute("data-theme", next);
      try { localStorage.setItem(KEY, next); } catch (e) { /* ignore */ }
      const btn = document.getElementById("theme-toggle");
      if (btn) btn.setAttribute("aria-pressed", String(next === "dark"));
    };
  })();
</script>"""

THEME_STYLE = """<style>
  .theme-toggle {
    position: fixed; top: 14px; right: 14px; z-index: 20;
    width: 34px; height: 34px; padding: 0;
    display: grid; place-items: center;
    border: 1px solid var(--hairline); border-radius: 8px;
    background: var(--surface); color: var(--ink-2);
    cursor: pointer; line-height: 1;
    font: inherit; font-size: 14px;
  }
  .theme-toggle:hover { color: var(--ink); }
  .theme-toggle:focus-visible { outline: 2px solid var(--s1); outline-offset: 2px; }
  .theme-toggle .moon { display: none; }
  @media (prefers-color-scheme: dark) {
    :root:where(:not([data-theme="light"])) .theme-toggle .moon { display: block; }
    :root:where(:not([data-theme="light"])) .theme-toggle .sun { display: none; }
  }
  :root[data-theme="dark"] .theme-toggle .moon { display: block; }
  :root[data-theme="dark"] .theme-toggle .sun { display: none; }
  :root[data-theme="light"] .theme-toggle .moon { display: none; }
  :root[data-theme="light"] .theme-toggle .sun { display: block; }
  @media print { .theme-toggle { display: none; } }
</style>"""

THEME_BUTTON = """<button type="button" id="theme-toggle" class="theme-toggle"
        aria-pressed="false" title="切換深色 / 淺色" onclick="window.__toggleTheme()">
  <span class="sun" aria-hidden="true">◑</span><span class="moon" aria-hidden="true">◐</span>
  <span style="position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)">切換深色或淺色主題</span>
</button>"""


def render(payload: dict, standalone: bool = True) -> str:
    """Assemble the report HTML with ``payload`` inlined."""
    sec = _split_template(TEMPLATE.read_text(encoding="utf-8"))
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if "__DATA__" not in sec.script:
        raise ValueError("template SCRIPT section has no __DATA__ placeholder")
    script = sec.script.replace("__DATA__", data)

    if not standalone:
        return f"{sec.head}\n\n{sec.body}\n\n{script}\n"

    return "\n".join(
        [
            "<!doctype html>",
            '<html lang="zh-Hant">',
            "<head>",
            '<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            f'<meta name="description" content="{PAGE_DESC}">',
            '<meta property="og:type" content="website">',
            f'<meta property="og:title" content="{PAGE_TITLE}">',
            f'<meta property="og:description" content="{PAGE_DESC}">',
            '<meta name="twitter:card" content="summary">',
            THEME_SCRIPT,
            sec.head,
            THEME_STYLE,
            "</head>",
            "<body>",
            THEME_BUTTON,
            sec.body,
            script,
            "</body>",
            "</html>",
            "",
        ]
    )


def build(
    result_path: Path = DEFAULT_RESULT,
    out_path: Path = DEFAULT_OUT,
    standalone: bool = True,
    freq: str = "W",
) -> Path:
    if not result_path.exists():
        raise SystemExit(
            f"backtest result not found: {result_path}\n"
            "Run `python -m twstable wfcv` (or `backtest`) first."
        )
    with open(result_path, encoding="utf-8") as fh:
        result = json.load(fh)

    html = render(build_payload(result, freq=freq), standalone=standalone)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    return out_path


def run_report_cli(args) -> int:
    result_path = Path(args.result) if args.result else DEFAULT_RESULT
    out_path = Path(args.out) if args.out else DEFAULT_OUT
    written = build(
        result_path=result_path,
        out_path=out_path,
        standalone=not args.fragment,
        freq=args.freq,
    )
    size = written.stat().st_size
    print(f"built {written} ({size / 1024:.0f} KB) from {result_path}")
    if not args.fragment:
        print("open it directly, or serve the folder:")
        print(f"  python -m http.server -d {written.parent}")
    return 0
