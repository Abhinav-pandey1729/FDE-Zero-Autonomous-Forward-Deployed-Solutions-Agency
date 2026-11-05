"""Rebuild the bundled synthetic flagship; never reads the user's runtime database."""

import gzip
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import db  # noqa: E402
from bootstrap import DEFAULT_COMPANIES  # noqa: E402
from service_engine import pipeline  # noqa: E402


def main():
    destination = ROOT / "flagship_seed.sqlite.gz"
    original_db = db.DB
    try:
        with tempfile.TemporaryDirectory() as directory:
            db.DB = str(Path(directory) / "seed.sqlite")
            pipeline(DEFAULT_COMPANIES, mode="demo")
            if not db.verify_ledger()["ok"]:
                raise RuntimeError("Cannot bundle a seed with a broken citation chain")
            with (
                open(db.DB, "rb") as source,
                gzip.open(destination, "wb", compresslevel=9) as target,
            ):
                shutil.copyfileobj(source, target)
    finally:
        db.DB = original_db
    print(f"Rebuilt synthetic seed: {destination.name}")


if __name__ == "__main__":
    main()
