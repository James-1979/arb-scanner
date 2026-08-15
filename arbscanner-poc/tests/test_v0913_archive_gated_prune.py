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
    archive_prune_execute,
    default_archive_root,
    load_prune_progress,
    manifest_path,
    parquet_path,
    save_prune_progress,
    save_runtime_gate_report,
)
from arbscanner.db import DB

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
INSTALLER = (ROOT / "BUILD_AND_INSTALL.command").read_text(encoding="utf-8")
ADMIN = (ROOT / "scripts" / "archive_admin.py").read_text(encoding="utf-8")
WORKER = (ROOT / "worker.py").read_text(encoding="utf-8")


def _rows(db: DB, count: int = 3, hours_ago: int = 72):
    base = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).replace(minute=5, second=0, microsecond=0)
    hour = base.replace(minute=0, second=0, microsecond=0).isoformat()
    ids = []
    scan_id = db.start_scan(scan_kind="price")
    for i in range(count):
        when = base + timedelta(seconds=i * 10)
        with db.lock:
            cur = db.conn.execute(
                """INSERT INTO matched_markets(
                    scan_id,observed_at,event_key,event_name,event_start,market_name,match_score,status,
                    strategy,sport,section,net_roi_pct,legs_json,source_markets_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (scan_id, when.isoformat(), f"event-0913-{i}", "Event", when.isoformat(), "Match Odds", 0.99,
                 "qualified", "two-way", "Tennis", "sports", 1.25, "[]", "[]"),
            )
            ids.append(int(cur.lastrowid))
            db.conn.commit()
    with db.lock:
        db.conn.execute(
            "INSERT OR REPLACE INTO matched_market_history_state(hour_utc,built_at) VALUES(?,?)",
            (hour, datetime.now(timezone.utc).isoformat()),
        )
        db.conn.commit()
    return hour, ids


def _archive(root: Path, hour: str, ids: list[int]):
    pp = parquet_path(root, hour)
    mp = manifest_path(root, hour)
    pp.parent.mkdir(parents=True, exist_ok=True)
    payload = b"verified-parquet-placeholder-0913"
    pp.write_bytes(payload)
    mp.write_text(json.dumps({
        "status": "VERIFIED", "schema_version": ARCHIVE_SCHEMA_VERSION, "hour_utc": hour,
        "row_count": len(ids), "min_id": min(ids), "max_id": max(ids),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }), encoding="utf-8")


def _gate(root: Path, hour: str):
    save_runtime_gate_report(root, {
        "gate": "matched-market-real-parquet-runtime", "ok": True, "status": "PASS",
        "gate_protocol_version": RUNTIME_GATE_PROTOCOL_VERSION,
        "archive_schema_version": ARCHIVE_SCHEMA_VERSION, "hour_utc": hour,
        "settings_changed": False, "pruning_invoked": False,
    })


def _patch_runtime(monkeypatch, count: int):
    monkeypatch.setattr("arbscanner.archive.duckdb_runtime_status", lambda: {"available": True, "version": "test", "message": None})
    monkeypatch.setattr("arbscanner.archive.parquet_hour_stats", lambda *a, **k: {"ok": True, "row_count": count, "status": "VERIFIED"})


def test_0913_release_stamp_and_safety_defaults():
    assert __version__ == "0.9.36"
    assert "ArbScanner PoC 0.9.36" in HTML
    assert 'EXPECTED_VERSION="0.9.36"' in INSTALLER
    assert DEFAULT_CONFIG["matched_market_archive_enabled"] is False
    assert DEFAULT_CONFIG["matched_market_archive_runtime_gate_required"] is True
    assert DEFAULT_CONFIG["matched_market_archive_required_before_prune"] is False
    assert "## 0.9.13" in (ROOT / "RELEASE_NOTES.md").read_text(encoding="utf-8")
    assert not (ROOT / "ui-audit").exists()


def test_archive_pilot_soak_pauses_legacy_raw_deletion(tmp_path):
    db = DB(tmp_path / "soak.sqlite3")
    hour, ids = _rows(db, count=2)
    before = db.conn.execute("SELECT COUNT(*) FROM matched_markets").fetchone()[0]
    result = db.matched_market_finalize_due_hour(retention_hours=48)
    after = db.conn.execute("SELECT COUNT(*) FROM matched_markets").fetchone()[0]
    assert result["mode"] == "FINALIZE_ONLY"
    assert result["deleted"] == 0
    assert before == after == 2
    assert hour


def test_prune_executor_deletes_only_planner_eligible_hour_in_batches(monkeypatch, tmp_path):
    db = DB(tmp_path / "execute.sqlite3")
    hour, ids = _rows(db, count=205)
    root = default_archive_root(db.path)
    _gate(root, hour)
    _archive(root, hour, ids)
    db.conn.close()
    _patch_runtime(monkeypatch, len(ids))
    plan = archive_prune_dry_run(tmp_path / "execute.sqlite3", root, retention_hours=48)
    assert plan["eligible_hours"] == 1
    result = archive_prune_execute(tmp_path / "execute.sqlite3", root, retention_hours=48, batch_size=100, scanner_enabled=False)
    assert result["status"] == "PASS"
    assert result["deleted_rows"] == 205
    assert result["batches"] == 3
    check = DB(tmp_path / "execute.sqlite3")
    assert check.conn.execute("SELECT COUNT(*) FROM matched_markets").fetchone()[0] == 0
    check.conn.close()
    progress = load_prune_progress(root, hour)
    assert progress["status"] == "COMPLETE"
    assert progress["deleted_rows"] == 205
    audit = (root / "_prune_audit.jsonl").read_text(encoding="utf-8")
    assert '"status": "PASS"' in audit
    assert '"deleted_total": 205' in audit


def test_prune_executor_never_skips_a_blocked_older_hour(monkeypatch, tmp_path):
    db = DB(tmp_path / "gap.sqlite3")
    old_hour, old_ids = _rows(db, count=1, hours_ago=74)
    new_hour, new_ids = _rows(db, count=1, hours_ago=72)
    root = default_archive_root(db.path)
    _gate(root, new_hour)
    # Deliberately do not archive the oldest hour; archive only the later one.
    _archive(root, new_hour, new_ids)
    db.conn.close()
    _patch_runtime(monkeypatch, len(new_ids))
    result = archive_prune_execute(tmp_path / "gap.sqlite3", root, retention_hours=48, scanner_enabled=False)
    assert result["status"] == "OLDEST_HOUR_BLOCKED"
    assert result["pruning_invoked"] is False
    check = DB(tmp_path / "gap.sqlite3")
    assert check.conn.execute("SELECT COUNT(*) FROM matched_markets").fetchone()[0] == 2
    check.conn.close()


def test_partial_prune_progress_is_resumable_but_unrecorded_partial_is_blocked(monkeypatch, tmp_path):
    db = DB(tmp_path / "resume.sqlite3")
    hour, ids = _rows(db, count=205)
    root = default_archive_root(db.path)
    _gate(root, hour)
    _archive(root, hour, ids)
    _patch_runtime(monkeypatch, len(ids))
    first = db.matched_market_archive_prune_batch(
        hour_utc=hour,
        cutoff_utc=(datetime.now(timezone.utc) - timedelta(hours=48)).replace(minute=0, second=0, microsecond=0).isoformat(),
        batch_size=100, expected_archive_rows=205, expected_min_id=min(ids), expected_max_id=max(ids), deleted_so_far=0,
    )
    assert first["deleted"] == 100
    save_prune_progress(root, hour, {
        "status": "IN_PROGRESS", "archive_sha256": json.loads(manifest_path(root, hour).read_text())["sha256"],
        "archive_row_count": 205, "deleted_rows": 100,
    })
    plan = archive_prune_dry_run(db.path, root, retention_hours=48)
    assert plan["eligible_hours"] == 1
    assert plan["hours"][0]["resume_prune"] is True
    # Lose the progress proof: a partial source must now fail closed.
    (root / "_prune_progress.json").unlink()
    blocked = archive_prune_dry_run(db.path, root, retention_hours=48)
    assert blocked["eligible_hours"] == 0
    assert "partial_prune_state_missing" in blocked["hours"][0]["reasons"]
    db.conn.close()


def test_admin_reports_pruning_capable_but_off(monkeypatch, tmp_path):
    api = API(db_path=tmp_path / "admin.sqlite3")
    monkeypatch.setattr("arbscanner.api.duckdb_runtime_status", lambda: {"available": True, "version": "test", "message": None})
    monkeypatch.setattr("arbscanner.archive.duckdb_runtime_status", lambda: {"available": True, "version": "test", "message": None})
    status = api.archive_pilot_status({})
    assert status["prune_planner_mode"] == "PRUNING_CAPABLE"
    assert status["prune_execution_enabled"] is False
    assert "PRUNING_CAPABLE" in HTML
    assert "explicitly armed" in HTML


def test_archive_admin_requires_exact_confirmation_for_destructive_controls():
    assert "ENABLE-ARCHIVE-GATED-PRUNING" in ADMIN
    assert "RUN-ONE-ARCHIVE-GATED-PRUNE" in ADMIN
    assert 'choices=("status", "enable", "disable", "run-once")' in ADMIN
    assert 'cfg["matched_market_archive_required_before_prune"] = True' in ADMIN
    assert "archive_prune_execute(" in ADMIN
    assert "archive_prune_execute(" in WORKER
    assert "matched_market_finalize_due_hour" in WORKER


def test_0912_pilot_upgrade_to_0913_preserves_state_and_keeps_prune_off(monkeypatch, tmp_path):
    db_path = tmp_path / "upgrade.sqlite3"
    db = DB(db_path)
    cfg = {
        "matched_market_archive_enabled": True,
        "matched_market_archive_runtime_gate_required": True,
        "matched_market_archive_required_before_prune": False,
        "sentinel_upgrade_setting": "preserve",
    }
    db.set_setting("config", cfg)
    hour, ids = _rows(db, count=1, hours_ago=2)
    root = default_archive_root(db.path)
    _gate(root, hour)
    _archive(root, hour, ids)
    db.conn.close()
    monkeypatch.setattr("arbscanner.api.duckdb_runtime_status", lambda: {"available": True, "version": "test", "message": None})
    monkeypatch.setattr("arbscanner.archive.duckdb_runtime_status", lambda: {"available": True, "version": "test", "message": None})
    api = API(db_path=db_path)
    preserved = api.db.get_setting("config", {})
    assert preserved["matched_market_archive_enabled"] is True
    assert preserved["matched_market_archive_required_before_prune"] is False
    assert preserved["sentinel_upgrade_setting"] == "preserve"
    assert api.archive_pilot_status({})["prune_execution_enabled"] is False
