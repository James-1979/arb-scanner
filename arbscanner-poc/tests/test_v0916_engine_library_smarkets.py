import base64
import io
import json
import os
import zipfile
from pathlib import Path

import pytest

from arbscanner.api import API
from arbscanner.db import DB
from arbscanner.engine_packages import (
    ENGINE_API_VERSION,
    FORMAT_VERSION,
    build_export_package,
    inspect_package_bytes,
    validate_restricted_source,
)
from arbscanner.provider_runtime import default_provider_runtime_registry
from arbscanner.strategy_engines import EngineRegistry


def make_api(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    return API(tmp_path / "arbscanner.sqlite3")


def package_bytes(manifest, source=None):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, sort_keys=True))
        if source is not None:
            zf.writestr("engine.py", source)
    return buf.getvalue()


def test_0916_schema_adds_engine_library_metadata_and_builtin_descriptions(tmp_path):
    db = DB(tmp_path / "arbscanner.sqlite3")
    db.ensure_default_engines()
    cols = db._columns("engine_instances")
    assert {"description", "notes", "package_source", "package_sha256", "package_author"}.issubset(cols)
    rows = {x["engine_instance_id"]: x for x in db.engine_instances()}
    assert rows["SPORTS_BASELINE_ARB_PRIMARY"]["description"]
    assert rows["SPORTS_SUPERBET_ARB_PRIMARY"]["description"]
    assert rows["GREYHOUNDS_BASELINE_ARB_PRIMARY"]["description"]


def test_engine_library_hides_reference_and_test_engines_by_default(tmp_path, monkeypatch):
    api = make_api(tmp_path, monkeypatch)
    sports = api.engines({"section": "sports"})
    ids = {x["engine_instance_id"] for x in sports["rows"]}
    assert "SPORTS_BASELINE_ARB_PRIMARY" in ids
    assert "SPORTS_SUPERBET_ARB_PRIMARY" in ids
    assert "SPORTS_DEPTH_ARB_REFERENCE" not in ids
    assert "NOOP_FRAMEWORK_TEST" not in ids
    assert sports["hidden_count"] >= 2
    all_rows = api.engines({"section": "sports", "include_reference": True})
    all_ids = {x["engine_instance_id"] for x in all_rows["rows"]}
    assert "SPORTS_DEPTH_ARB_REFERENCE" in all_ids
    assert "NOOP_FRAMEWORK_TEST" in all_ids


def test_description_and_notes_are_metadata_not_config_versions(tmp_path, monkeypatch):
    api = make_api(tmp_path, monkeypatch)
    iid = "SPORTS_BASELINE_ARB_PRIMARY"
    before = api.db.engine_active_config(iid)
    result = api.engine_update_metadata({"engine_instance_id": iid, "description": "Baseline description", "notes": "Operator note"})
    assert result["ok"] is True
    row = api.db.engine_instance(iid)
    after = api.db.engine_active_config(iid)
    assert row["description"] == "Baseline description"
    assert row["notes"] == "Operator note"
    assert after["config_version"] == before["config_version"]
    assert after["config_hash"] == before["config_hash"]


def test_builtin_engine_export_is_portable_and_same_instance_enters_upgrade_review(tmp_path, monkeypatch):
    api = make_api(tmp_path, monkeypatch)
    exported = api.engine_export_package({"engine_instance_id": "SPORTS_BASELINE_ARB_PRIMARY"})
    assert exported["ok"] is True
    path = Path(exported["path"])
    assert path.suffix == ".arbengine" and path.exists()
    info = inspect_package_bytes(path.read_bytes())
    assert info["manifest"]["engine_type"] == "SPORTS_BASELINE_ARB"
    assert info["manifest"]["engine_api_version"] == ENGINE_API_VERSION
    review = api.engine_import_package({"filename": path.name, "package_base64": base64.b64encode(path.read_bytes()).decode("ascii")})
    assert review["ok"] is True and review["code_executed"] is False
    assert review["install_kind"] == "UPGRADE_REVIEW"
    assert review["installed_version"] == "1.0.0"
    result = api.engine_install_quarantined_package({"quarantine_token": review["quarantine_token"], "confirm": True})
    assert result["ok"] is True
    row = result["engine"]
    assert row["engine_type"] == "SPORTS_BASELINE_ARB"
    assert row["engine_grade"] == "STANDARD"
    assert row["sim_enabled"] is True
    assert row["effective_lifecycle"] == "SIM"
    assert result["install_kind"] == "UPGRADE_REVIEW"
    assert row["package_sha256"]


def test_restricted_python_engine_package_registers_but_stays_disabled(tmp_path, monkeypatch):
    api = make_api(tmp_path, monkeypatch)
    manifest = {
        "format_version": FORMAT_VERSION,
        "engine_api_version": ENGINE_API_VERSION,
        "engine_type": "SPORTS_OPERATOR_TEST",
        "display_name": "Sports Operator Test",
        "engine_version": "1.0.0",
        "engine_grade": "EXTREME",
        "capabilities": ["DIRECTIONAL", "SINGLE_LEG"],
        "config_schema": {},
        "default_config": {},
        "implementation_kind": "restricted_python",
        "engine_class": "OperatorTestEngine",
        "platform_min_version": "0.9.16",
        "section": "sports",
        "description": "Reviewed local test engine",
    }
    source = '''class OperatorTestEngine(StrategyEngine):\n    engine_type = "SPORTS_OPERATOR_TEST"\n    engine_version = "1.0.0"\n    def evaluate(self, context):\n        return EngineEvaluation(context, (), (), None, 0.0)\n'''
    raw = package_bytes(manifest, source)
    review = api.engine_import_package({"filename": "operator-test.arbengine", "package_base64": base64.b64encode(raw).decode("ascii")})
    assert review["ok"] is True and review["code_executed"] is False
    result = api.engine_install_quarantined_package({"quarantine_token": review["quarantine_token"], "confirm": True})
    assert result["ok"] is True
    assert result["engine"]["engine_grade"] == "RESEARCH"
    assert result["engine"]["effective_lifecycle"] == "DISABLED"
    types = {x["engine_type"]: x for x in api.scanner.engine_runtime.registry.types()}
    assert "SPORTS_OPERATOR_TEST" in types
    assert types["SPORTS_OPERATOR_TEST"]["package_origin"]


def test_package_validation_rejects_imports_and_future_platform_contracts():
    with pytest.raises(ValueError, match="imports are not allowed"):
        validate_restricted_source("import os\nclass X: pass\n")
    manifest = {
        "format_version": FORMAT_VERSION,
        "engine_api_version": ENGINE_API_VERSION,
        "engine_type": "FUTURE_ENGINE",
        "display_name": "Future",
        "engine_version": "1.0.0",
        "engine_grade": "RESEARCH",
        "capabilities": [], "config_schema": {}, "default_config": {},
        "implementation_kind": "builtin", "platform_min_version": "9.9.9",
    }
    with pytest.raises(ValueError, match="requires ArbScanner"):
        inspect_package_bytes(package_bytes(manifest))


def test_smarkets_is_first_class_staged_provider_with_no_io_factory():
    runtime = default_provider_runtime_registry()
    spec = runtime.providers.get("smarkets")
    profile = runtime.profile("smarkets")
    assert spec.venue.venue_name == "Smarkets"
    assert profile.api_state == "awaiting_api_access"
    assert profile.rate_limit_per_minute == 1200
    assert profile.execution_enabled is False
    assert profile.orders_write_capability is False
    assert profile.metadata["session_route"] == "/v3/sessions/"
    assert profile.metadata["quotes_route"] == "/v3/markets/{market_ids}/quotes/"
    assert "smarkets" not in runtime._factories


def test_dashboard_operational_status_exposes_smarkets_awaiting_api(tmp_path, monkeypatch):
    api = make_api(tmp_path, monkeypatch)
    feeds = {x["key"]: x for x in api._operational_status()["feeds"]}
    assert feeds["smarkets"]["state"] == "awaiting_api_access"
    assert feeds["smarkets"]["api_state"] == "awaiting_api_access"
    assert feeds["smarkets"]["enabled"] is False
    assert feeds["smarkets"]["sim_feed_enabled"] is False
    assert feeds["smarkets"]["live_feed_enabled"] is False
    assert "awaiting Smarkets API activation" in feeds["smarkets"]["message"]
    assert feeds["smarkets"]["ok"] is False


def test_0916_ui_is_engine_library_with_scenarios_and_third_exchange_tile():
    html = (Path(__file__).parents[1] / "frontend" / "index.html").read_text()
    for text in (
        "SPORTS · ENGINE LIFECYCLE", "RACING · ENGINE LIBRARY", "Import .arbengine",
        "Show research/test", "Nickname", "Save metadata", "Export .arbengine",
        "Model strategy engines", "Run selected engines", "Smarkets", "AWAITING API ACCESS",
    ):
        assert text in html
    assert "Create experiment" not in html
    assert "Parameter sweep" not in html
    assert "Run Replay compare" not in html
    assert "Greyhounds" in html


def test_0915_to_0916_upgrade_preserves_archive_and_prune_state(tmp_path):
    from arbscanner.archive import default_archive_root, save_runtime_gate_report
    path = tmp_path / "arbscanner.sqlite3"
    db = DB(path)
    db.ensure_default_engines()
    cfg = dict(db.get_setting("config", {}) or {})
    cfg.update({
        "matched_market_archive_enabled": True,
        "matched_market_archive_runtime_gate_required": True,
        "matched_market_archive_required_before_prune": False,
        "sentinel_0916_upgrade": "keep",
    })
    db.set_setting("config", cfg)
    db.set_setting("archive_runtime_pause_until", "2026-08-13T20:00:00+00:00")
    root = default_archive_root(path)
    root.mkdir(parents=True, exist_ok=True)
    sentinel = root / "0916-upgrade-sentinel.bin"
    sentinel.write_bytes(b"archive-survives-engine-library-upgrade")
    save_runtime_gate_report(root, {
        "ok": True, "status": "PASS", "gate_protocol_version": 1, "archive_schema_version": 1,
        "hour_utc": "2026-08-13T17:00:00+00:00",
    })
    db.conn.close()

    upgraded = DB(path)
    upgraded.ensure_default_engines()
    after = upgraded.get_setting("config", {})
    assert after["matched_market_archive_enabled"] is True
    assert after["matched_market_archive_runtime_gate_required"] is True
    assert after["matched_market_archive_required_before_prune"] is False
    assert after["sentinel_0916_upgrade"] == "keep"
    assert upgraded.get_setting("archive_runtime_pause_until") == "2026-08-13T20:00:00+00:00"
    assert sentinel.read_bytes() == b"archive-survives-engine-library-upgrade"
    assert upgraded.engine_instance("SPORTS_BASELINE_ARB_PRIMARY")["description"]
