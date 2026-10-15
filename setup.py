#!/usr/bin/env python3
"""Bootstrap a fresh session for caridina-ai/tw-stock-stable-yfinance.

Run from the main checkout (the ``main`` branch working tree)::

    python3 ../sidecar/setup.py            # install deps, restore data/ if missing
    uv run python ../sidecar/setup.py publish   # snapshot data/ to the `data` branch

``data/`` (prices downloaded from Yahoo / the TWSE universe) is git-ignored and
takes a long time to re-download, so a snapshot lives on the orphan branch
``data``.  Cloud sessions cannot create GitHub Releases (the git proxy answers
403), but they can push, so the branch is the transport.

Each publish force-pushes a *single, history-less* commit, so the repository
only ever carries one snapshot.  Only the raw inputs are stored
(``prices/``, ``universe.csv``, ``SNAPSHOT.json``); the ``panel*.parquet``
files are derived and rebuilt on restore in about 20 seconds.

Safe to re-run: every step is skipped when its result is already in place.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

DATA_BRANCH = "data"
DATA_REF = f"refs/remotes/origin/{DATA_BRANCH}"
STORED = ["prices", "universe.csv", "SNAPSHOT.json"]
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


def restore_data(root: Path) -> None:
    data = root / "data"
    if (data / MARKER).exists():
        print(f"data/ already present ({data / MARKER}); skipping restore")
        return
    try:
        run(["git", "fetch", "--depth", "1", "origin", f"{DATA_BRANCH}:{DATA_REF}"], cwd=root)
    except subprocess.CalledProcessError:
        print(f"no `{DATA_BRANCH}` branch on origin; run `python -m twstable fetch` instead")
        return

    data.mkdir(exist_ok=True)
    archive = subprocess.Popen(["git", "archive", DATA_REF], cwd=root, stdout=subprocess.PIPE)
    run(["tar", "-x", "-C", str(data)], stdin=archive.stdout)
    archive.stdout.close()
    if archive.wait() != 0:
        sys.exit("git archive failed")

    print("rebuilding panel from restored prices ...", flush=True)
    run(["uv", "run", "python", "-c", "from twstable.data import build_panel; build_panel()"],
        cwd=root)
    info = data / "SNAPSHOT.json"
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
    info = {
        "created": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "dates": [str(close.index[0].date()), str(close.index[-1].date())],
        "symbols": int(close.shape[1]),
    }
    (data / "SNAPSHOT.json").write_text(json.dumps(info, indent=2) + "\n", "utf-8")

    # Build the commit with a throwaway index whose work tree is data/, so the
    # main checkout's index and .gitignore (which ignores data/) are untouched.
    with tempfile.TemporaryDirectory() as tmp:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(tmp) / "index")}
        git = ["git", f"--work-tree={data}"]
        run([*git, "add", "--", *STORED], cwd=root, env=env)
        tree = run([*git, "write-tree"], cwd=root, env=env,
                   capture_output=True, text=True).stdout.strip()
    msg = (f"data snapshot: {info['symbols']} symbols, "
           f"{info['dates'][0]} .. {info['dates'][1]}")
    commit = run(["git", "commit-tree", tree, "-m", msg], cwd=root,
                 capture_output=True, text=True).stdout.strip()
    run(["git", "push", "--force", "origin", f"{commit}:refs/heads/{DATA_BRANCH}"], cwd=root)
    print(f"published {msg}")


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
