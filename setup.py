#!/usr/bin/env python3
"""Bootstrap a fresh session for caridina-ai/tw-stock-stable-yfinance.

Run from the main checkout (the ``main`` branch working tree)::

    python3 ../sidecar/setup.py            # install deps, restore data/ if missing
    python3 ../sidecar/setup.py publish    # snapshot data/ to a GitHub Release

``data/`` (prices downloaded from Yahoo / the TWSE universe) is git-ignored and
takes a long time to re-download, so snapshots live as GitHub Release assets
tagged ``data-YYYYMMDD``.  ``restore`` (the default) picks the newest one.

Safe to re-run: every step is skipped when its result is already in place.
Uses only the standard library plus ``curl``, ``uv`` and (for publish) ``gh``.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

REPO = "caridina-ai/tw-stock-stable-yfinance"
TAG_PREFIX = "data-"
ASSET_SUFFIX = ".tar.gz"
MARKER = "panel.parquet"  # present once data/ is usable


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    print("+", " ".join(cmd), flush=True)
    return subprocess.run(cmd, check=True, **kw)


def find_checkout(arg: str | None) -> Path:
    root = Path(arg or ".").resolve()
    pyproject = root / "pyproject.toml"
    if not pyproject.exists() or 'name = "tw-stable"' not in pyproject.read_text("utf-8"):
        sys.exit(f"{root} is not the tw-stable checkout; cd there or pass --repo")
    return root


# --------------------------------------------------------------------------
# restore
# --------------------------------------------------------------------------


def install_deps(root: Path) -> None:
    if shutil.which("uv") is None:
        print("uv not found; skipping dependency install")
        return
    run(["uv", "sync"], cwd=root)


def latest_snapshot() -> tuple[str, str] | None:
    """(tag, asset download URL) of the newest data snapshot, or None."""
    out = subprocess.run(
        ["curl", "-sSfL", f"https://api.github.com/repos/{REPO}/releases?per_page=100"],
        check=True, capture_output=True, text=True,
    ).stdout
    releases = [r for r in json.loads(out) if r["tag_name"].startswith(TAG_PREFIX)]
    for rel in sorted(releases, key=lambda r: r["tag_name"], reverse=True):
        for asset in rel["assets"]:
            if asset["name"].endswith(ASSET_SUFFIX):
                return rel["tag_name"], asset["browser_download_url"]
    return None


def restore_data(root: Path) -> None:
    data = root / "data"
    if (data / MARKER).exists():
        print(f"data/ already present ({data / MARKER}); skipping restore")
        return
    snap = latest_snapshot()
    if snap is None:
        print("no data snapshot release found; run `python -m twstable fetch` instead")
        return
    tag, url = snap
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / "data.tar.gz"
        run(["curl", "-sSfL", "-o", str(archive), url])
        with tarfile.open(archive) as tar:
            tar.extractall(root, filter="data")
    info = data / "SNAPSHOT.json"
    print(f"restored data/ from {tag}")
    if info.exists():
        print(info.read_text("utf-8"))


# --------------------------------------------------------------------------
# publish
# --------------------------------------------------------------------------


def publish_data(root: Path) -> None:
    data = root / "data"
    if not (data / MARKER).exists():
        sys.exit("data/ is not populated; run `python -m twstable fetch` first")

    import pandas as pd  # only needed here, from the project venv

    close = pd.read_parquet(data / MARKER)
    today = dt.date.today().strftime("%Y%m%d")
    tag = f"{TAG_PREFIX}{today}"
    info = {
        "tag": tag,
        "created": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "dates": [str(close.index[0].date()), str(close.index[-1].date())],
        "symbols": int(close.shape[1]),
    }
    (data / "SNAPSHOT.json").write_text(json.dumps(info, indent=2) + "\n", "utf-8")

    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / f"data-{today}{ASSET_SUFFIX}"
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(data, arcname="data")
        size = archive.stat().st_size / 2**20
        print(f"packed {archive.name}: {size:.1f} MiB")

        body = (
            f"Snapshot of data/ ({info['symbols']} symbols, "
            f"{info['dates'][0]} .. {info['dates'][1]}). "
            "Restore with `python3 ../sidecar/setup.py` from the sidecar branch."
        )
        rel = json.loads(run(
            ["gh", "api", "--method", "POST", f"repos/{REPO}/releases",
             "-f", f"tag_name={tag}", "-f", "target_commitish=main",
             "-f", f"name=Data snapshot {today}", "-f", f"body={body}"],
            capture_output=True, text=True,
        ).stdout)
        run(
            ["gh", "api", "--method", "POST",
             "-H", "Content-Type: application/gzip",
             f"https://uploads.github.com/repos/{REPO}/releases/{rel['id']}/assets"
             f"?name={archive.name}",
             "--input", str(archive)],
            stdout=subprocess.DEVNULL,
        )
    print(f"published {tag}: {rel['html_url']}")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("command", nargs="?", default="restore", choices=["restore", "publish"])
    p.add_argument("--repo", help="path to the main checkout (default: cwd)")
    args = p.parse_args()

    root = find_checkout(args.repo)
    if args.command == "restore":
        install_deps(root)
        restore_data(root)
    else:
        publish_data(root)
    return 0


if __name__ == "__main__":
    sys.exit(main())
