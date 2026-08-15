#!/usr/bin/env python3
"""Supported archive administration CLI for ArbScanner.

0.9.14 keeps archive operations consolidated here. Pilot control and the
archive-gated prune switch are explicit configuration writes; prune-plan remains
read-only, while prune run-once requires an exact confirmation token and always
reuses the fail-closed planner before deleting anything.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from arbscanner.archive import (  # noqa: E402
    ARCHIVE_SCHEMA_VERSION,
    RUNTIME_GATE_PROTOCOL_VERSION,
    archive_continuity,
    archive_hour,
    archive_prune_dry_run,
    archive_prune_execute,
    default_archive_root,
    duckdb_runtime_status,
    hour_floor,
    load_runtime_gate_report,
    load_runtime_state,
    manifest_verified,
    newest_closed_hour,
    next_pilot_archive_hour,
    parquet_hour_stats,
    read_archived_rows,
    runtime_blocked_until,
    runtime_gate_passed,
    save_runtime_gate_report,
    save_runtime_state,
)
from arbscanner.db import DB  # noqa: E402


def _sqlite_ro(path: Path) -> sqlite3.Connection:
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _source_hour_stats(db_path: Path, hour: str) -> dict:
    end = (hour_floor(hour) + timedelta(hours=1)).isoformat()
    with _sqlite_ro(db_path) as conn:
        row = conn.execute(
            """SELECT COUNT(*) c,MIN(id) min_id,MAX(id) max_id,
                      MIN(observed_at) min_t,MAX(observed_at) max_t
               FROM matched_markets WHERE observed_at>=? AND observed_at<?""",
            (hour_floor(hour).isoformat(), end),
        ).fetchone()
    return {
        "row_count": int(row["c"] or 0), "min_id": row["min_id"], "max_id": row["max_id"],
        "min_observed_at": row["min_t"], "max_observed_at": row["max_t"],
    }


def _latest_closed_raw_hour(db_path: Path) -> str | None:
    current_hour = hour_floor(datetime.now(timezone.utc)).isoformat()
    with _sqlite_ro(db_path) as conn:
        row = conn.execute(
            """SELECT strftime('%Y-%m-%dT%H:00:00+00:00',observed_at) hour_utc
               FROM matched_markets WHERE observed_at<?
               GROUP BY hour_utc ORDER BY hour_utc DESC LIMIT 1""",
            (current_hour,),
        ).fetchone()
    return str(row[0]) if row and row[0] else None


def _same_source_shape(a: dict, b: dict) -> bool:
    return all(a.get(k) == b.get(k) for k in ("row_count", "min_id", "max_id", "min_observed_at", "max_observed_at"))


def _json(value: dict) -> None:
    print(json.dumps(value, indent=2, sort_keys=True, default=str))


def _status(db_path: Path, root: Path) -> dict:
    db = DB(db_path)
    try:
        cfg = db.get_setting("config", {}) or {}
    finally:
        db.conn.close()
    runtime = load_runtime_state(root)
    pilot_start = runtime.get("pilot_start_hour") or runtime.get("first_success_hour")
    continuity = archive_continuity(root, pilot_start, newest_closed_hour())
    blocked_until, blocked_reason = runtime_blocked_until(root)
    return {
        "ok": True,
        "db": str(db_path),
        "archive_root": str(root),
        "archive_enabled": bool(cfg.get("matched_market_archive_enabled", False)),
        "runtime_gate_required": bool(cfg.get("matched_market_archive_runtime_gate_required", True)),
        "runtime_gate_passed": runtime_gate_passed(root),
        "runtime_gate": load_runtime_gate_report(root),
        "archive_required_before_prune": bool(cfg.get("matched_market_archive_required_before_prune", False)),
        "duckdb": duckdb_runtime_status(),
        "pilot_start_hour": pilot_start,
        "first_success_hour": runtime.get("first_success_hour"),
        "last_success_hour": runtime.get("last_success_hour"),
        "next_target_hour": next_pilot_archive_hour(root, pilot_start, newest_closed_hour()) if cfg.get("matched_market_archive_enabled") else None,
        "continuity": continuity,
        "blocked_until_epoch": blocked_until,
        "blocked_reason": blocked_reason,
        "pruning_invoked": False,
        "live_order_writes": False,
    }


def _runtime_gate(db_path: Path, root: Path, *, hour: str | None, report_path: Path | None) -> int:
    report: dict = {
        "gate": "matched-market-real-parquet-runtime",
        "gate_protocol_version": RUNTIME_GATE_PROTOCOL_VERSION,
        "archive_schema_version": ARCHIVE_SCHEMA_VERSION,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "db": str(db_path), "archive_root": str(root),
        "settings_changed": False, "pruning_invoked": False, "sqlite_open_mode": "read-only/query-only",
        "dependency": duckdb_runtime_status(), "report_published_to_archive": False,
    }
    publish = False
    if not report["dependency"].get("available"):
        report.update({"ok": False, "status": "DEPENDENCY_MISSING", "message": "DuckDB is not available in this Python environment."})
    elif not db_path.exists():
        report.update({"ok": False, "status": "DB_MISSING"})
    else:
        target = hour or _latest_closed_raw_hour(db_path)
        report["hour_utc"] = target
        if not target:
            report.update({"ok": False, "status": "NO_CLOSED_RAW_HOUR"})
        else:
            try:
                before = _source_hour_stats(db_path, target)
                result = archive_hour(db_path, root, target, verify_checksum=True)
                manifest_ok = manifest_verified(root, target, verify_checksum=True)
                parquet_stats = parquet_hour_stats(root, target, verify_checksum=True)
                after = _source_hour_stats(db_path, target)
                stable = _same_source_shape(before, after)
                matches = bool(
                    parquet_stats.get("ok")
                    and int(parquet_stats.get("row_count") or 0) == int(before.get("row_count") or 0)
                    and parquet_stats.get("min_id") == before.get("min_id")
                    and parquet_stats.get("max_id") == before.get("max_id")
                )
                sample = []
                if int(parquet_stats.get("row_count") or 0):
                    end = (hour_floor(target) + timedelta(hours=1)).isoformat()
                    sample = read_archived_rows(root, [target], start_utc=hour_floor(target).isoformat(), end_utc=end, limit=1)
                readback = int(parquet_stats.get("row_count") or 0) == 0 or len(sample) == 1
                ok = bool(result.get("ok") and manifest_ok and stable and matches and readback)
                report.update({
                    "ok": ok, "status": "PASS" if ok else "REVIEW", "source_before": before, "source_after": after,
                    "archive_result": result, "parquet_stats": parquet_stats, "manifest_checksum_verified": manifest_ok,
                    "source_stable_during_gate": stable, "archive_matches_source": matches, "bounded_readback_rows": len(sample),
                    "source_row_count": int(before.get("row_count") or 0), "parquet_row_count": int(parquet_stats.get("row_count") or 0),
                })
                publish = True
            except Exception as exc:
                report.update({"ok": False, "status": "ERROR", "message": str(exc)})
                publish = True
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    report["report_published_to_archive"] = publish
    if publish:
        save_runtime_gate_report(root, report)
    if report_path:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    _json(report)
    return 0 if report.get("ok") else 3


def _pilot(db_path: Path, root: Path, action: str) -> int:
    db = DB(db_path)
    try:
        cfg = db.get_setting("config", {}) or {}
        if action == "enable":
            if not duckdb_runtime_status().get("available"):
                out = _status(db_path, root); out.update({"ok": False, "status": "DEPENDENCY_MISSING"}); _json(out); return 3
            if not runtime_gate_passed(root):
                out = _status(db_path, root); out.update({"ok": False, "status": "GATE_REQUIRED"}); _json(out); return 3
            if bool(cfg.get("matched_market_archive_required_before_prune", False)):
                out = _status(db_path, root); out.update({"ok": False, "status": "PRUNE_GATE_MUST_BE_OFF"}); _json(out); return 3
            cfg["matched_market_archive_enabled"] = True
            cfg["matched_market_archive_runtime_gate_required"] = True
            db.set_setting("config", cfg)
            runtime = load_runtime_state(root)
            patch = {"pilot_armed_at": datetime.now(timezone.utc).isoformat()}
            if not runtime.get("pilot_start_hour"):
                patch["pilot_start_hour"] = newest_closed_hour()
                patch["pilot_started_at"] = datetime.now(timezone.utc).isoformat()
            save_runtime_state(root, patch)
            out = _status(db_path, root); out.update({"status": "ENABLED"}); _json(out); return 0
        if action == "disable":
            cfg["matched_market_archive_enabled"] = False
            db.set_setting("config", cfg)
            save_runtime_state(root, {"pilot_disabled_at": datetime.now(timezone.utc).isoformat()})
            out = _status(db_path, root); out.update({"status": "DISABLED"}); _json(out); return 0
        out = _status(db_path, root); out.update({"status": "STATUS"}); _json(out); return 0
    finally:
        try: db.conn.close()
        except Exception: pass


def _prune(db_path: Path, root: Path, action: str, *, confirm: str | None = None, retention_hours: int = 48, batch_size: int = 5000) -> int:
    db = DB(db_path)
    try:
        cfg = db.get_setting("config", {}) or {}
        archive_enabled = bool(cfg.get("matched_market_archive_enabled", False))
        prune_enabled = bool(cfg.get("matched_market_archive_required_before_prune", False))
        if action == "status":
            out = _status(db_path, root)
            out.update({
                "status": "PRUNING_CAPABLE", "prune_execution_enabled": prune_enabled,
                "prune_plan": archive_prune_dry_run(db_path, root, retention_hours=retention_hours),
            })
            _json(out); return 0
        if action == "disable":
            cfg["matched_market_archive_required_before_prune"] = False
            db.set_setting("config", cfg)
            out = _status(db_path, root); out.update({"status": "PRUNING_DISABLED", "prune_execution_enabled": False}); _json(out); return 0
        if action == "enable":
            if confirm != "ENABLE-ARCHIVE-GATED-PRUNING":
                _json({"ok": False, "status": "CONFIRMATION_REQUIRED", "required": "ENABLE-ARCHIVE-GATED-PRUNING"}); return 3
            if not archive_enabled:
                _json({"ok": False, "status": "ARCHIVE_PILOT_REQUIRED"}); return 3
            if not duckdb_runtime_status().get("available"):
                _json({"ok": False, "status": "DEPENDENCY_MISSING"}); return 3
            if not runtime_gate_passed(root):
                _json({"ok": False, "status": "GATE_REQUIRED"}); return 3
            blocked_until, blocked_reason = runtime_blocked_until(root)
            if blocked_until > datetime.now(timezone.utc).timestamp():
                _json({"ok": False, "status": "SAFETY_BLOCKED", "reason": blocked_reason, "blocked_until_epoch": blocked_until}); return 3
            cfg["matched_market_archive_required_before_prune"] = True
            db.set_setting("config", cfg)
            out = _status(db_path, root); out.update({"status": "PRUNING_ENABLED", "prune_execution_enabled": True}); _json(out); return 0
        if action == "run-once":
            if confirm != "RUN-ONE-ARCHIVE-GATED-PRUNE":
                _json({"ok": False, "status": "CONFIRMATION_REQUIRED", "required": "RUN-ONE-ARCHIVE-GATED-PRUNE"}); return 3
            if not archive_enabled:
                _json({"ok": False, "status": "ARCHIVE_PILOT_REQUIRED"}); return 3
            db.conn.close()
            result = archive_prune_execute(
                db_path, root, retention_hours=retention_hours, batch_size=batch_size,
                scanner_enabled=bool(cfg.get("scanner_enabled", True)),
                price_tick_seconds=float(cfg.get("price_scan_tick_seconds", 2) or 2),
                safety_pause_seconds=int(cfg.get("matched_market_archive_safety_pause_seconds", 3600) or 3600),
            )
            _json(result); return 0 if result.get("ok") else 3
        _json({"ok": False, "status": "UNKNOWN_PRUNE_ACTION"}); return 3
    finally:
        try: db.conn.close()
        except Exception: pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ArbScanner verified archive administration")
    parser.add_argument("--db", required=True, type=Path, help="Path to arbscanner.sqlite3")
    parser.add_argument("--archive-root", type=Path, help="Archive root; defaults beside the database")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    gate = sub.add_parser("runtime-gate"); gate.add_argument("--hour"); gate.add_argument("--report", type=Path)
    pilot = sub.add_parser("pilot"); pilot.add_argument("action", choices=("status", "enable", "disable"))
    one = sub.add_parser("archive-hour"); one.add_argument("--hour"); one.add_argument("--overwrite", action="store_true")
    plan = sub.add_parser("prune-plan"); plan.add_argument("--retention-hours", type=int, default=48); plan.add_argument("--max-hours", type=int, default=500)
    prune = sub.add_parser("prune"); prune.add_argument("action", choices=("status", "enable", "disable", "run-once")); prune.add_argument("--confirm"); prune.add_argument("--retention-hours", type=int, default=48); prune.add_argument("--batch-size", type=int, default=5000)
    args = parser.parse_args(argv)
    db_path = args.db.expanduser().resolve()
    root = args.archive_root.expanduser().resolve() if args.archive_root else default_archive_root(db_path)
    if args.command != "runtime-gate" and not db_path.exists():
        _json({"ok": False, "status": "DB_MISSING", "db": str(db_path)}); return 3
    if args.command == "status": _json(_status(db_path, root)); return 0
    if args.command == "runtime-gate": return _runtime_gate(db_path, root, hour=args.hour, report_path=args.report)
    if args.command == "pilot": return _pilot(db_path, root, args.action)
    if args.command == "archive-hour":
        result = archive_hour(db_path, root, args.hour or newest_closed_hour(), overwrite=args.overwrite); _json(result); return 0 if result.get("ok") else 3
    if args.command == "prune-plan":
        _json(archive_prune_dry_run(db_path, root, retention_hours=args.retention_hours, max_hours=args.max_hours)); return 0
    if args.command == "prune":
        return _prune(db_path, root, args.action, confirm=args.confirm, retention_hours=args.retention_hours, batch_size=args.batch_size)
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
