#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a current-schema local SQLite fixture without network credentials.")
    parser.add_argument("--code-root", required=True)
    parser.add_argument("--db", required=True)
    parser.add_argument("--home", required=True)
    args = parser.parse_args()
    code_root = Path(args.code_root).resolve()
    db_path = Path(args.db).resolve()
    home = Path(args.home).resolve()
    home.mkdir(parents=True, exist_ok=True)
    os.environ["HOME"] = str(home)
    sys.path.insert(0, str(code_root))
    from arbscanner.api import API
    if db_path.exists():
        db_path.unlink()
    api = API(db_path)
    api.db.conn.close()
    print(db_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
