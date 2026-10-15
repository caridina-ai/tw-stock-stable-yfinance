#!/usr/bin/env python3
"""Bootstrap a fresh session for caridina-ai/tw-stock-stable-yfinance.

Run from the main checkout (the ``main`` branch working tree)::

    python3 ../sidecar/setup.py            # uv sync + check out the pinned data/
    python3 ../sidecar/setup.py publish    # commit data/, push it, re-pin data.lock

``data/`` lives in its own **private** repo, ``DATA_REPO``, cloned into the
git-ignored ``data/`` directory of the main checkout.  ``data.lock`` at the
root of ``main`` pins the data commit each version of the code was run on, so
checking out an old version and re-running this script restores the matching
data.  (A hand-rolled submodule: the public repo stays cloneable by people
without access to the data.)

In a cloud session the private repo must be attached to the session first
(``add_repo`` for ``DATA_REPO``) or the clone is refused.

Safe to re-run: every step is skipped when its result is already in place.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import subprocess
import sys
from pathlib import Path

DATA_REPO = "caridina-ai/tw-stock-stable-yfinance-data"
LOCK = "data.lock"


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    print("+", " ".join(cmd), flush=True)
    return subprocess.run(cmd, check=True, **kw)


def out(cmd: list[str], **kw) -> str:
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kw).stdout.strip()


def find_checkout(arg: str | None) -> Path:
    root = Path(arg or ".").resolve()
    pyproject = root / "pyproject.toml"
    if not pyproject.exists() or 'name = "tw-stable"' not in pyproject.read_text("utf-8"):
        sys.exit(f"{root} is not the tw-stable checkout; cd there or pass --repo")
    return root


def read_lock(root: Path) -> dict[str, str]:
    """``data.lock`` is ``key=value`` lines; ``#`` starts a comment."""
    path = root / LOCK
    if not path.exists():
        return {}
    pairs = (ln.split("=", 1) for ln in path.read_text("utf-8").splitlines()
             if "=" in ln and not ln.lstrip().startswith("#"))
    return {k.strip(): v.strip() for k, v in pairs}


def write_lock(root: Path, commit: str, info: dict) -> None:
    (root / LOCK).write_text(
        "# Data snapshot this version of the code was run on.\n"
        "# data/ is a clone of the repo below (private), checked out at `commit`.\n"
        f"repo={DATA_REPO}\n"
        f"commit={commit}\n"
        f"dates={info['dates'][0]}..{info['dates'][1]}\n"
        f"symbols={info['symbols']}\n",
        "utf-8",
    )


# --------------------------------------------------------------------------
# restore
# --------------------------------------------------------------------------


def install_deps(root: Path) -> None:
    if shutil.which("uv") is None:
        print("uv not found; skipping dependency install")
        return
    run(["uv", "sync"], cwd=root)


def _head(repo: Path) -> str:
    """Commit checked out in ``repo``, or "" before the first checkout."""
    r = subprocess.run(["git", "rev-parse", "-q", "--verify", "HEAD"], cwd=repo,
                       capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


def _has_commit(repo: Path, sha: str) -> bool:
    return subprocess.run(["git", "cat-file", "-e", f"{sha}^{{commit}}"], cwd=repo,
                          capture_output=True).returncode == 0


def restore_data(root: Path) -> None:
    lock = read_lock(root)
    if "commit" not in lock:
        print(f"no {LOCK}; run `python -m twstable fetch` instead")
        return
    data, want = root / "data", lock["commit"]
    repo = lock.get("repo", DATA_REPO)
    url = repo if "://" in repo else f"https://github.com/{repo}"

    if not (data / ".git").exists():
        if data.exists() and any(data.iterdir()):
            sys.exit(f"{data} exists but is not a clone of {url}; move it aside first")
        run(["git", "clone", "--no-checkout", url, str(data)])
        # the work tree is empty, so -f only fills it in; nothing local to lose
        run(["git", "-c", "advice.detachedHead=false", "checkout", "-q", "-f", "--detach",
             want], cwd=data)
    else:
        if out(["git", "status", "--porcelain"], cwd=data):
            sys.exit("data/ has changes that are not in any data commit (a fresh fetch?); "
                     "publish them or discard them before switching snapshots")
        if _head(data) == want:
            print(f"data/ already at {want[:7]}")
            return
        if not _has_commit(data, want):
            run(["git", "fetch", "origin"], cwd=data)
        run(["git", "-c", "advice.detachedHead=false", "checkout", "-q", "--detach", want],
            cwd=data)
    print(f"data/ checked out at {want[:7]} ({lock.get('dates', '?')}, {lock.get('symbols', '?')} symbols)")


# --------------------------------------------------------------------------
# publish
# --------------------------------------------------------------------------


def publish_data(root: Path) -> None:
    data = root / "data"
    if not (data / "panel.parquet").exists():
        sys.exit("data/ is not populated; run `python -m twstable fetch` first")
    if not (data / ".git").exists():
        run(["git", "init", "-q", "-b", "main"], cwd=data)
        run(["git", "remote", "add", "origin", f"https://github.com/{DATA_REPO}"], cwd=data)

    import pandas as pd  # from the project venv: run this via `uv run`

    close = pd.read_parquet(data / "panel.parquet")
    info = {
        "created": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "dates": [str(close.index[0].date()), str(close.index[-1].date())],
        "symbols": int(close.shape[1]),
    }
    (data / "SNAPSHOT.json").write_text(json.dumps(info, indent=2) + "\n", "utf-8")

    run(["git", "add", "-A"], cwd=data)
    if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=data).returncode:
        msg = (f"Data snapshot: {info['symbols']} symbols, "
               f"{info['dates'][0]} .. {info['dates'][1]}")
        run(["git", "commit", "-q", "-m", msg], cwd=data)
    run(["git", "push", "origin", "HEAD:refs/heads/main"], cwd=data)

    commit = out(["git", "rev-parse", "HEAD"], cwd=data)
    write_lock(root, commit, info)
    print(f"pinned {commit[:7]} in {LOCK}; commit {LOCK} on main together with the results")


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
