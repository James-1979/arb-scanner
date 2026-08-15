from __future__ import annotations

import hashlib
import json
from pathlib import Path

import arbscanner.api as api_module
import arbscanner.config as config_module
import arbscanner.db as db_module
import arbscanner.db_schema as schema_module
import arbscanner.racing_projection as racing_projection
from arbscanner.api import API

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = json.loads((ROOT / "validation" / "refactor_structure_boundaries.json").read_text(encoding="utf-8"))


def _sha_json(value) -> str:
    text = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_config_and_modes_moved_but_api_reexports_are_identity_preserving():
    hashes = MANIFEST["content_hashes"]
    assert api_module.DEFAULT_CONFIG is config_module.DEFAULT_CONFIG
    assert api_module.OPERATING_MODES is config_module.OPERATING_MODES
    assert _sha_json(config_module.DEFAULT_CONFIG) == hashes["default_config_canonical_json_sha256"]
    assert _sha_json(config_module.OPERATING_MODES) == hashes["operating_modes_canonical_json_sha256"]


def test_db_schema_moved_without_byte_change_and_fresh_database_initialises(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    expected = MANIFEST["content_hashes"]["schema_utf8_sha256"]
    assert db_module.SCHEMA is schema_module.SCHEMA
    assert hashlib.sha256(schema_module.SCHEMA.encode("utf-8")).hexdigest() == expected
    api = API(tmp_path / "stage11-schema.sqlite3")
    tables = {row[0] for row in api.db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert api.db._CURRENT_TABLES <= tables


def test_racing_projection_moved_with_historical_api_alias_preserved():
    assert api_module._racing_book_analysis_from_sources is racing_projection.racing_book_analysis_from_sources
    api_text = (ROOT / "arbscanner" / "api.py").read_text(encoding="utf-8")
    assert "def _racing_book_analysis_from_sources(" not in api_text
    assert "_racing_book_analysis_from_sources = racing_book_analysis_from_sources" in api_text


def test_oversized_runtime_modules_shrink_with_frontend_out_of_scope():
    limits = MANIFEST["concentration"]
    api_lines = len((ROOT / "arbscanner" / "api.py").read_text(encoding="utf-8").splitlines())
    db_lines = len((ROOT / "arbscanner" / "db.py").read_text(encoding="utf-8").splitlines())
    assert api_lines <= limits["stage11_api_max_lines"] < limits["stage10_api_lines"]
    assert db_lines <= limits["stage11_db_max_lines"] < limits["stage10_db_lines"]


def test_extracted_static_modules_do_not_gain_runtime_service_dependencies():
    config_text = (ROOT / "arbscanner" / "config.py").read_text(encoding="utf-8")
    schema_text = (ROOT / "arbscanner" / "db_schema.py").read_text(encoding="utf-8")
    assert "from ." not in config_text
    assert "from ." not in schema_text
    racing_text = (ROOT / "arbscanner" / "racing_projection.py").read_text(encoding="utf-8")
    for forbidden in ("from .db", "from .api", "from .scanner", "from .live_providers", "from .provider_runtime"):
        assert forbidden not in racing_text


def test_stage11_static_architecture_gate_passes(tmp_path):
    import subprocess, sys
    output = tmp_path / "architecture.json"
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check_refactor_architecture.py"), "--root", str(ROOT), "--output", str(output)],
        cwd=ROOT, text=True, capture_output=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["ok"] is True
    assert report["failures"] == []
