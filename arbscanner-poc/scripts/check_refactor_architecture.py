#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import sys
from pathlib import Path


def _sha_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha_json(value) -> str:
    return _sha_text(json.dumps(value, sort_keys=True, separators=(",", ":")))


def _relative_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level and node.module:
            found.add(node.module.split(".", 1)[0])
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage 11 static ArbScanner refactor architecture gate")
    parser.add_argument("--root", default=".")
    parser.add_argument("--output")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    sys.path.insert(0, str(root))

    manifest = json.loads((root / "validation" / "refactor_structure_boundaries.json").read_text(encoding="utf-8"))
    import arbscanner.api as api_module
    import arbscanner.config as config_module
    import arbscanner.db as db_module
    import arbscanner.db_schema as schema_module
    import arbscanner.racing_projection as racing_projection

    checks: list[dict] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    hashes = manifest["content_hashes"]
    check("default_config_hash", _sha_json(config_module.DEFAULT_CONFIG) == hashes["default_config_canonical_json_sha256"])
    check("operating_modes_hash", _sha_json(config_module.OPERATING_MODES) == hashes["operating_modes_canonical_json_sha256"])
    check("schema_hash", _sha_text(schema_module.SCHEMA) == hashes["schema_utf8_sha256"])
    check("api_default_config_reexport", api_module.DEFAULT_CONFIG is config_module.DEFAULT_CONFIG)
    check("api_operating_modes_reexport", api_module.OPERATING_MODES is config_module.OPERATING_MODES)
    check("api_racing_alias", api_module._racing_book_analysis_from_sources is racing_projection.racing_book_analysis_from_sources)
    check("db_schema_reexport", db_module.SCHEMA is schema_module.SCHEMA)

    api_text = (root / "arbscanner" / "api.py").read_text(encoding="utf-8")
    db_text = (root / "arbscanner" / "db.py").read_text(encoding="utf-8")
    concentration = manifest["concentration"]
    check("api_line_budget", len(api_text.splitlines()) <= int(concentration["stage11_api_max_lines"]), str(len(api_text.splitlines())))
    check("db_line_budget", len(db_text.splitlines()) <= int(concentration["stage11_db_max_lines"]), str(len(db_text.splitlines())))
    check("config_definition_moved", "\nDEFAULT_CONFIG = {" not in api_text and "\nOPERATING_MODES = {" not in api_text)
    check("schema_definition_moved", "\nSCHEMA = \"\"\"" not in db_text)
    check("racing_projection_definition_moved", "def _racing_book_analysis_from_sources(" not in api_text)

    for module_name, spec in manifest["module_boundaries"].items():
        path = root / Path(*module_name.split("."))
        path = path.with_suffix(".py")
        actual = _relative_imports(path)
        allowed = set(spec["allowed_relative_imports"])
        check(f"imports:{module_name}", actual <= allowed, f"actual={sorted(actual)} allowed={sorted(allowed)}")

    failed = [item for item in checks if not item["ok"]]
    report = {"ok": not failed, "checks": checks, "failures": failed}
    text = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
