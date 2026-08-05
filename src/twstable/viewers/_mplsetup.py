"""Shared matplotlib setup: backend check, CJK fonts, and keyboard ownership."""

from __future__ import annotations

import matplotlib

# Keys the twstable viewers bind for themselves.  matplotlib ships default
# shortcuts on several of them -- ``left``/``right`` walk the zoom history,
# ``l``/``L`` toggle the y/x axis scales, ``home`` resets the view -- and those
# fire *in addition to* our own handler, so a viewer that does not release them
# ends up fighting the toolbar.  See :func:`claim_keys`.
VIEWER_KEYS = frozenset(
    {
        "up", "down", "left", "right",
        "pageup", "pagedown", "home", "end",
        "l", "L", "b", "q", "escape",
    }
)


def ensure_interactive() -> None:
    """Fail early and helpfully when no GUI backend is available."""
    backend = matplotlib.get_backend()
    if backend.lower() in {"agg", "pdf", "ps", "svg", "template"}:
        for candidate in ("TkAgg", "QtAgg", "Qt5Agg"):
            try:
                matplotlib.use(candidate)
                return
            except Exception:  # noqa: BLE001 - try the next backend
                continue
        raise SystemExit(
            f"matplotlib has no interactive backend available (current: {backend}).\n"
            "Install one, e.g. `python -m pip install pyqt5`, or use tkinter."
        )


def use_cjk_font() -> None:
    """Pick a font that can render Chinese stock names in titles."""
    from matplotlib import font_manager, rcParams

    available = {f.name for f in font_manager.fontManager.ttflist}
    for name in (
        "Microsoft JhengHei",
        "Microsoft YaHei",
        "PingFang TC",
        "Noto Sans CJK TC",
        "SimHei",
        "MingLiU",
    ):
        if name in available:
            rcParams["font.family"] = ["sans-serif"]
            rcParams["font.sans-serif"] = [name, "DejaVu Sans"]
            break
    rcParams["axes.unicode_minus"] = False


def claim_keys(keys: frozenset[str] = VIEWER_KEYS) -> None:
    """Drop ``keys`` from matplotlib's built-in shortcuts so a viewer owns them.

    Anything not listed stays bound, so ``s`` still saves and ``g`` still
    toggles the grid.
    """
    from matplotlib import rcParams

    for param in [name for name in rcParams if name.startswith("keymap.")]:
        kept = [k for k in rcParams[param] if k not in keys]
        if len(kept) != len(rcParams[param]):
            rcParams[param] = kept


def setup() -> None:
    ensure_interactive()
    use_cjk_font()
    claim_keys()
