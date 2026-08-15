from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from arbscanner import __version__
from arbscanner.api import API, DEFAULT_CONFIG
from arbscanner.archive import (
    ARCHIVE_SCHEMA_VERSION,
    RUNTIME_GATE_PROTOCOL_VERSION,
    archive_prune_dry_run,
    default_archive_root,
    manifest_path,
    parquet_path,
    save_runtime_gate_report,
    save_runtime_state,
)
from arbscanner.db import DB

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
INSTALLER = (ROOT / "BUILD_AND_INSTALL.command").read_text(encoding="utf-8")


def _old_row(db: DB, hours_ago: int = 72) -> tuple[str, int]:
    when = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).replace(minute=10, second=0, microsecond=0)
    hour = when.replace(minute=0, second=0, microsecond=0).isoformat()
    scan_id = db.start_scan(scan_kind="price")
    with db.lock:
        cur = db.conn.execute(
            """INSERT INTO matched_markets(
                scan_id,observed_at,event_key,event_name,event_start,market_name,match_score,status,
                strategy,sport,section,net_roi_pct,legs_json,source_markets_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (scan_id, when.isoformat(), "event-0912", "Event", when.isoformat(), "Match Odds", 0.99,
             "qualified", "two-way", "Tennis", "sports", 1.25, "[]", "[]"),
        )
        row_id = int(cur.lastrowid)
        db.conn.execute(
            "INSERT OR REPLACE INTO matched_market_history_state(hour_utc,built_at) VALUES(?,?)",
            (hour, datetime.now(timezone.utc).isoformat()),
        )
        db.conn.commit()
    return hour, row_id


def _verified_archive(root: Path, hour: str, row_id: int, *, schema_version: int = ARCHIVE_SCHEMA_VERSION) -> None:
    pp = parquet_path(root, hour)
    mp = manifest_path(root, hour)
    pp.parent.mkdir(parents=True, exist_ok=True)
    payload = b"verified-parquet-placeholder-0912"
    pp.write_bytes(payload)
    mp.write_text(json.dumps({
        "status": "VERIFIED",
        "schema_version": schema_version,
        "hour_utc": hour,
        "row_count": 1,
        "min_id": row_id,
        "max_id": row_id,
        "sha256": hashlib.sha256(payload).hexdigest(),
    }), encoding="utf-8")


def _gate(root: Path, hour: str) -> None:
    save_runtime_gate_report(root, {
        "gate": "0.9.11-real-parquet-runtime",
        "ok": True,
        "status": "PASS",
        "gate_protocol_version": RUNTIME_GATE_PROTOCOL_VERSION,
        "archive_schema_version": ARCHIVE_SCHEMA_VERSION,
        "hour_utc": hour,
        "settings_changed": False,
        "pruning_invoked": False,
    })


def test_0912_release_stamp_and_master_notes_only():
    assert __version__ == "0.9.36"
    assert "<title>ArbScanner PoC 0.9.36</title>" in HTML
    assert 'EXPECTED_VERSION="0.9.36"' in INSTALLER
    assert "RELEASE_NOTES.md" in INSTALLER
    assert (ROOT / "RELEASE_NOTES.md").exists()
    assert "## 0.9.13" in (ROOT / "RELEASE_NOTES.md").read_text(encoding="utf-8")


def test_release_tree_has_no_loose_hotfix_patch_or_per_version_notes():
    forbidden_top_level = {
        "ANALYSIS_RUNTIME_HOTFIX.md", "DB_LOCK_HOTFIX.md", "FRONTEND_STARTUP_HOTFIX.md",
        "RACING_MONITOR_SELECTION_HOTFIX.md", "PATCH_NOTES.md", "PATCH_NOTES.txt", "PERFORMANCE_NOTES.md",
        "RUN_CONTINUOUS_ARCHIVE_PILOT.command", "STANDALONE_LIVE_PILOT_README.txt",
    }
    names = {p.name for p in ROOT.iterdir() if p.is_file()}
    assert not (forbidden_top_level & names)
    assert not [p for p in ROOT.glob("RELEASE_*.md") if p.name != "RELEASE_NOTES.md"]
    assert not list((ROOT / "tests").glob("*hotfix*.py"))
    assert (ROOT / "README.md").exists()
    assert (ROOT / "RELEASE_NOTES.md").exists()


def test_archive_scripts_are_consolidated_to_one_operator_cli():
    archive_scripts = sorted(p.name for p in (ROOT / "scripts").glob("archive_*.py"))
    assert archive_scripts == ["archive_admin.py"]
    cli = (ROOT / "scripts" / "archive_admin.py").read_text(encoding="utf-8")
    for command in ("status", "runtime-gate", "pilot", "archive-hour", "prune-plan"):
        assert command in cli
    assert "matched_market_storage_maintenance(" not in cli


def test_prune_dry_run_is_fail_closed_and_never_deletes(monkeypatch, tmp_path):
    db = DB(tmp_path / "plan.sqlite3")
    hour, row_id = _old_row(db)
    root = default_archive_root(db.path)
    _gate(root, hour)
    _verified_archive(root, hour, row_id)
    monkeypatch.setattr("arbscanner.archive.duckdb_runtime_status", lambda: {"available": True, "version": "test", "message": None})
    before = db.conn.execute("SELECT COUNT(*) FROM matched_markets").fetchone()[0]
    plan = archive_prune_dry_run(db.path, root, retention_hours=48)
    after = db.conn.execute("SELECT COUNT(*) FROM matched_markets").fetchone()[0]
    assert plan["mode"] == "DRY_RUN"
    assert plan["destructive_action"] is False
    assert plan["pruning_invoked"] is False
    assert plan["eligible_hours"] == 1
    assert plan["eligible_rows"] == 1
    assert plan["blocked_hours"] == 0
    assert before == after == 1


def test_prune_dry_run_blocks_bad_checksum_schema_and_runtime_pause(monkeypatch, tmp_path):
    db = DB(tmp_path / "blocked.sqlite3")
    hour, row_id = _old_row(db)
    root = default_archive_root(db.path)
    _gate(root, hour)
    _verified_archive(root, hour, row_id, schema_version=ARCHIVE_SCHEMA_VERSION + 1)
    save_runtime_state(root, {"paused_until_epoch": datetime.now(timezone.utc).timestamp() + 300})
    monkeypatch.setattr("arbscanner.archive.duckdb_runtime_status", lambda: {"available": True, "version": "test", "message": None})
    plan = archive_prune_dry_run(db.path, root, retention_hours=48)
    row = plan["hours"][0]
    assert row["eligible"] is False
    assert "archive_schema_incompatible" in row["reasons"]
    assert "scanner_safety_pause" in row["reasons"]
    # Corrupt the archive after the manifest was written: checksum failure must also fail closed.
    parquet_path(root, hour).write_bytes(b"corrupt")
    plan2 = archive_prune_dry_run(db.path, root, retention_hours=48)
    assert "archive_checksum_failed" in plan2["hours"][0]["reasons"]


def test_admin_surfaces_dry_run_and_pruning_stays_off(monkeypatch, tmp_path):
    api = API(db_path=tmp_path / "admin.sqlite3")
    monkeypatch.setattr("arbscanner.api.duckdb_runtime_status", lambda: {"available": True, "version": "test", "message": None})
    monkeypatch.setattr("arbscanner.archive.duckdb_runtime_status", lambda: {"available": True, "version": "test", "message": None})
    result = api.archive_pilot_status({})
    assert result["prune_planner_mode"] == "PRUNING_CAPABLE"
    assert result["prune_dry_run"]["destructive_action"] is False
    assert result["archive_required_before_prune"] is False
    assert "PRUNING_CAPABLE" in HTML
    assert "explicitly armed" in HTML


def test_0911_pilot_state_survives_0912_in_place_upgrade(monkeypatch, tmp_path):
    db_path = tmp_path / "upgrade.sqlite3"
    db = DB(db_path)
    cfg = {"matched_market_archive_enabled": True,
           "matched_market_archive_runtime_gate_required": True,
           "matched_market_archive_required_before_prune": False,
           "sentinel_upgrade_setting": "preserve"}
    db.set_setting("config", cfg)
    root = default_archive_root(db.path)
    gate_hour = (datetime.now(timezone.utc) - timedelta(hours=2)).replace(minute=0, second=0, microsecond=0).isoformat()
    current_hour = (datetime.now(timezone.utc) - timedelta(hours=1)).replace(minute=0, second=0, microsecond=0).isoformat()
    _gate(root, gate_hour)
    _verified_archive(root, gate_hour, 1)
    _verified_archive(root, current_hour, 1)
    save_runtime_state(root, {
        "pilot_start_hour": current_hour,
        "pilot_started_at": datetime.now(timezone.utc).isoformat(),
        "first_success_hour": current_hour,
        "last_success_hour": current_hour,
    })
    db.conn.close()
    monkeypatch.setattr("arbscanner.api.duckdb_runtime_status", lambda: {"available": True, "version": "test", "message": None})
    monkeypatch.setattr("arbscanner.archive.duckdb_runtime_status", lambda: {"available": True, "version": "test", "message": None})
    api = API(db_path=db_path)
    status = api.archive_pilot_status({})
    preserved = api.db.get_setting("config", {})
    assert preserved["matched_market_archive_enabled"] is True
    assert preserved["matched_market_archive_required_before_prune"] is False
    assert preserved["sentinel_upgrade_setting"] == "preserve"
    assert status["enabled"] is True
    assert status["runtime_gate_passed"] is True
    assert status["pilot_start_hour"] == current_hour
    assert status["last_success_hour"] == current_hour
    assert "Application Support/ArbScanner" not in INSTALLER or "rm -rf \"$HOME/Library/Application Support/ArbScanner\"" not in INSTALLER


def test_fresh_install_safety_defaults_remain_conservative():
    assert DEFAULT_CONFIG["matched_market_archive_enabled"] is False
    assert DEFAULT_CONFIG["matched_market_archive_runtime_gate_required"] is True
    assert DEFAULT_CONFIG["matched_market_archive_required_before_prune"] is False
