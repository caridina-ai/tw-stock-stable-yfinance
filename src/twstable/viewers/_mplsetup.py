"""Shared matplotlib setup: interactive backend check and CJK-capable fonts."""

from __future__ import annotations

import matplotlib


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


def setup() -> None:
    ensure_interactive()
    use_cjk_font()
