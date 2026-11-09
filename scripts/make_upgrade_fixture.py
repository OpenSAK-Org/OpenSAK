#!/usr/bin/env python3
"""
scripts/make_upgrade_fixture.py — generate a "golden" upgrade fixture from an
older OpenSAK release (issue #997).

The fixture is a small set of databases, settings files and filter profiles,
written by the OLD release's own code, plus an ``expected.json`` describing
the data. ``tests/unit-tests/test_upgrade_path.py`` opens it with the current
code and checks that the upgrade keeps everything.

Run once per stable release (see docs/RELEASE_CHECKLIST.md §7) and commit
the result:

    python scripts/make_upgrade_fixture.py v1.20.0

How it works:
  1. checks the tag out into a temporary git worktree (your working tree
     and branch are never touched);
  2. runs scripts/upgrade_fixture_generator.py with that tag's ``src`` on
     PYTHONPATH and HOME pointing into a throw-away folder, so nothing is
     read from or written to your real OpenSAK data (pattern from #829);
  3. checkpoints and compacts the databases, and replaces the throw-away
     folder in all paths and settings keys with ``{ROOT}`` / ``{ROOT_SAFE}``
     so the test can put the fixture anywhere;
  4. writes it to tests/fixtures/upgrade/<tag>/.

It uses the current virtualenv's packages, which must be able to run the old
release (true for v1.20.0).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
GENERATOR = REPO / "scripts" / "upgrade_fixture_generator.py"
FIXTURES = REPO / "tests" / "fixtures" / "upgrade"

ROOT_TOKEN = "{ROOT}"
ROOT_SAFE_TOKEN = "{ROOT_SAFE}"


def safe_path(path: str) -> str:
    """How v1.20.0 turns a path into part of a settings key (db.<safe>.*)."""
    return path.replace("/", "_").replace("\\", "_")


def tokenize(value: Any, root: str) -> Any:
    """Replace *root* in every key and string of a JSON value with tokens."""
    safe = safe_path(root)

    def fix(s: str) -> str:
        return s.replace(root, ROOT_TOKEN).replace(safe, ROOT_SAFE_TOKEN)

    if isinstance(value, dict):
        return {fix(k): tokenize(v, root) for k, v in value.items()}
    if isinstance(value, list):
        return [tokenize(v, root) for v in value]
    if isinstance(value, str):
        return fix(value)
    return value


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO, check=True, capture_output=True, text=True,
    ).stdout.strip()


def ensure_tag(tag: str) -> None:
    try:
        git("rev-parse", "--verify", f"{tag}^{{commit}}")
    except subprocess.CalledProcessError:
        print(f"Fetching tag {tag} …")
        git("fetch", "--depth", "1", "origin", "tag", tag)


def compact(db: Path) -> None:
    """Fold the WAL into the file and shrink it, so only the .db is needed."""
    conn = sqlite3.connect(str(db))
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("VACUUM")
    finally:
        conn.close()
    for suffix in ("-wal", "-shm", "-journal"):
        Path(str(db) + suffix).unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("tag", help="release tag to build the fixture with, e.g. v1.20.0")
    parser.add_argument("--keep", action="store_true",
                        help="keep the temporary folder for inspection")
    args = parser.parse_args()

    ensure_tag(args.tag)
    out = FIXTURES / args.tag
    tmp = Path(tempfile.mkdtemp(prefix="opensak-fixture-")).resolve()
    worktree = tmp / "src"
    root = tmp / "root"
    (root / "home").mkdir(parents=True)

    git("worktree", "add", "--detach", str(worktree), args.tag)
    try:
        env = {k: v for k, v in os.environ.items()
               if k not in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "APPDATA", "PYTHONPATH")}
        env.update({
            "HOME": str(root / "home"),
            "APPDATA": str(root / "home" / "AppData" / "Roaming"),
            "PYTHONPATH": str(worktree / "src"),
            "QT_QPA_PLATFORM": "offscreen",
            "OPENSAK_DISABLE_WEBENGINE": "1",
        })
        print(f"Generating data with {args.tag} …")
        subprocess.run(
            [sys.executable, str(GENERATOR), str(root)],
            cwd=root, env=env, check=True,
        )

        for db in root.rglob("*.db"):
            compact(db)

        bootstraps = list((root / "home").rglob("bootstrap.json"))
        if len(bootstraps) != 1:
            raise SystemExit(f"expected one bootstrap.json, found {bootstraps}")

        if out.exists():
            shutil.rmtree(out)
        out.mkdir(parents=True)
        for folder in ("install", "databases", "backup"):
            if (root / folder).is_dir():   # backup/: v1.21.0+ only (#1193)
                shutil.copytree(root / folder, out / folder)
        shutil.copy2(bootstraps[0], out / "bootstrap.json")
        shutil.copy2(root / "expected.json", out / "expected.json")

        for f in out.rglob("*.json"):
            data = json.loads(f.read_text(encoding="utf-8"))
            f.write_text(
                json.dumps(tokenize(data, str(root)), indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        leftovers = [
            str(f) for f in out.rglob("*.json")
            if str(root) in f.read_text(encoding="utf-8")
        ]
        if leftovers:
            raise SystemExit(f"untokenized paths left in {leftovers}")
    finally:
        git("worktree", "remove", "--force", str(worktree))
        if args.keep:
            print(f"Kept {tmp}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)

    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    print(f"Wrote {out.relative_to(REPO)} ({size // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
