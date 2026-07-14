#!/usr/bin/env python3
"""
scripts/import_gsak.py — CLI tool to test GSAK direct database import (#469).

By default this imports into a throwaway SCRATCH database under /tmp — NOT
your real OpenSAK database — so you can safely try it against real GSAK
backups without any risk to your actual data. Pass --db to import into a
specific (e.g. a manually copied) database instead.

Usage:
    # Test-import a GSAK backup zip (auto-extracts sqlite.db3) into a scratch DB:
    python scripts/import_gsak.py path/to/GSAK_Database_Backup.zip

    # Or point directly at an already-extracted sqlite.db3:
    python scripts/import_gsak.py path/to/sqlite.db3

    # Import into a specific OpenSAK database instead of the scratch one
    # (e.g. a COPY of your real database — never point this at your live one
    # until you're happy with the result):
    python scripts/import_gsak.py path/to/sqlite.db3 --db /path/to/copy.sqlite
"""

import argparse
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from opensak.db.database import init_db, get_session
from opensak.db.models import Attribute, Cache, UserNote, Waypoint
from opensak.importer.gsak_importer import import_gsak_db, find_gsak_db3_in_zip, scan_gsak_notes_for_embedded_images


def _find_gsak_db3(path: Path) -> Path:
    """Thin wrapper printing progress around the shared extraction helper."""
    if path.suffix.lower() == ".zip":
        print(f"Unzipping {path.name} ...")
    try:
        return find_gsak_db3_in_zip(path)
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Test-import a GSAK database into OpenSAK.")
    parser.add_argument("gsak_path", help="Path to a GSAK backup .zip or an extracted sqlite.db3")
    parser.add_argument(
        "--db", metavar="PATH",
        help="OpenSAK database to import into (default: a fresh scratch DB under /tmp — "
             "never your real database unless you pass this explicitly)",
    )
    args = parser.parse_args()

    gsak_path = Path(args.gsak_path)
    if not gsak_path.exists():
        print(f"Error: file not found: {gsak_path}")
        sys.exit(1)

    db3_path = _find_gsak_db3(gsak_path)

    if args.db:
        target_db = Path(args.db)
    else:
        target_db = Path(tempfile.mkdtemp(prefix="opensak_scratch_")) / "scratch.sqlite"
        print(f"No --db given: using a SCRATCH database (not your real one): {target_db}")

    print("=" * 60)
    print("  OpenSAK — GSAK Direct Database Import (sessions 1+2+3)")
    print("=" * 60)
    print(f"\nSource GSAK database : {db3_path}")
    print(f"Target OpenSAK database: {target_db}\n")

    init_db(db_path=target_db)

    scan = scan_gsak_notes_for_embedded_images(db3_path)
    if scan["affected_notes"]:
        print(f"Note: {scan['affected_notes']} personal notes contain "
              f"{scan['total_images']} embedded image(s) that will be replaced "
              f"with [image: filename] placeholders.\n")

    print(f"Importing {db3_path.name} ...\n")
    with get_session() as session:
        result = import_gsak_db(db3_path, session)

    print("Result:")
    print(result)

    with get_session() as session:
        n_caches = session.query(Cache).count()
        n_waypoints = session.query(Waypoint).count()
        n_attributes = session.query(Attribute).count()
        n_corrected = session.query(UserNote).filter_by(is_corrected=True).count()

        print(f"\nDatabase now contains: {n_caches} caches, {n_waypoints} waypoints, "
              f"{n_attributes} attributes, {n_corrected} corrected-coordinate notes")

        print("\nSample caches:")
        for c in session.query(Cache).order_by(Cache.gc_code).limit(5).all():
            print(f"  {c.gc_code}  {c.cache_type:<22} {c.name!r}  "
                  f"D{c.difficulty}/T{c.terrain}  elevation={c.elevation}")

    print("\n" + "=" * 60)
    print("  Import complete! Inspect the database above, or open it")
    print("  directly in OpenSAK via the database manager dialog.")
    print("=" * 60)


if __name__ == "__main__":
    main()
