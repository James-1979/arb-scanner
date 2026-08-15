from __future__ import annotations
import json
import logging
import os
import sys
import time
import argparse
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path
from arbscanner import __version__
from arbscanner.db import DB
from arbscanner.scanner import Scanner
from arbscanner.secrets import SecretStore
from arbscanner.archive import (
    archive_hour, archive_impact_guard, archive_prune_execute, default_archive_root, duckdb_runtime_status,
    load_runtime_state, newest_closed_hour, next_pilot_archive_hour, next_utc_hour_epoch, runtime_blocked_until, runtime_gate_passed, save_runtime_state,
    sqlite_scanner_baseline,
)

APP_DIR = Path.home() / "Library" / "Application Support" / "ArbScanner" if os.name != "nt" else Path.home() / "AppData" / "Local" / "ArbScanner"


def _configure_logging() -> logging.Logger:
    logger = logging.getLogger("arbscanner.worker")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
    return logger


def _archive_child_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--archive-hour-worker", action="store_true")
    parser.add_argument("--db", required=True)
    parser.add_argument("--archive-root", required=True)
    parser.add_argument("--hour", required=True)
    args, _ = parser.parse_known_args(argv)
    try:
        result = archive_hour(Path(args.db), Path(args.archive_root), args.hour)
    except Exception as exc:
        result = {"ok": False, "status": "ERROR", "hour_utc": args.hour, "message": str(exc)}
    print(json.dumps(result, sort_keys=True, default=str), flush=True)
    return 0 if result.get("ok") else 3


def _archive_process_command(db_path: Path, archive_root: Path, hour: str) -> list[str]:
    args = ["--archive-hour-worker", "--db", str(db_path), "--archive-root", str(archive_root), "--hour", str(hour)]
    if getattr(sys, "frozen", False):
        return [sys.executable, *args]
    return [sys.executable, str(Path(__file__).resolve()), *args]


def _archive_preexec(nice_value: int):
    def apply():
        try:
            os.nice(max(0, int(nice_value)))
        except Exception:
            pass
    return apply


def _run_discovery_background(db_path: Path, logger: logging.Logger) -> None:
    """Run slow discovery independently from the fast price scheduler.

    Discovery can spend minutes walking provider catalogues. Running it inline
    starves the price loop and makes otherwise healthy feeds appear stale. A
    dedicated Scanner/DB connection keeps the price scheduler responsive while
    preserving the same SQLite/WAL source of truth.
    """
    local_db = DB(db_path)
    try:
        local_scanner = Scanner(local_db, SecretStore(), producer="worker-discovery")
        result = local_scanner.discover_once(job_id=None)
        logger.info(
            "discovery ok=%s markets=%s matches=%s cache=%s duration=%sms feeds=%s%s",
            result.get("ok"), result.get("markets", 0), result.get("matches", 0), result.get("cache_entries", 0),
            result.get("duration_ms", 0), json.dumps(result.get("statuses") or [], ensure_ascii=False),
            f" error={result.get('message')}" if result.get("message") else "",
        )
    except Exception:
        try:
            local_db.rollback_if_needed()
        except Exception:
            pass
        logger.exception("uncaught discovery failure")
    finally:
        try:
            local_db.conn.close()
        except Exception:
            pass


def _parse_archive_child_output(output: str) -> dict:
    for line in reversed((output or "").splitlines()):
        try:
            value = json.loads(line)
            if isinstance(value, dict):
                return value
        except Exception:
            continue
    return {"ok": False, "status": "NO_RESULT", "message": (output or "Archive child returned no JSON result")[-2000:]}


def main():
    logger = _configure_logging()
    logger.info("ArbScannerWorker %s starting; database=%s", __version__, APP_DIR / "arbscanner.sqlite3")
    db = DB(APP_DIR / "arbscanner.sqlite3")
    scanner = Scanner(db, SecretStore(), producer="worker")
    last_keepalive = 0.0
    last_discovery = 0.0
    discovery_thread = None
    last_price_tick = 0.0
    last_settlement = 0.0
    last_storage_maintenance = 0.0
    last_matched_market_maintenance = 0.0
    archive_root = default_archive_root(db.path)
    archive_process = None
    archive_before = None
    archive_started = 0.0
    archive_hour = None
    archive_next_check = 0.0

    while True:
        cfg = db.get_setting("config", {}) or {}
        now = time.time()

        # 0.9.11 archive conversion runs in a separate low-priority process. The
        # scanner loop never waits for Parquet conversion and archive safety state
        # lives in a sidecar JSON file rather than taking SQLite's writer lock.
        if archive_process is not None and archive_process.poll() is not None:
            output, _ = archive_process.communicate()
            result = _parse_archive_child_output(output)
            elapsed = max(0.0, time.time() - archive_started)
            after = sqlite_scanner_baseline(db.path)
            guard = archive_impact_guard(
                archive_before or {}, after, elapsed_seconds=elapsed,
                scanner_enabled=bool(cfg.get("scanner_enabled", True)),
                price_tick_seconds=float(cfg.get("price_scan_tick_seconds", 2) or 2),
            )
            if not result.get("ok"):
                backoff = max(60, int(cfg.get("matched_market_archive_failure_backoff_seconds", 1800) or 1800))
                save_runtime_state(archive_root, {
                    "last_attempt_hour": archive_hour, "last_error": result.get("message") or result.get("status"),
                    "last_result": result, "backoff_until_epoch": time.time() + backoff,
                })
                logger.warning("matched-market archive failed hour=%s status=%s; backing off", archive_hour, result.get("status"))
            elif not guard.get("ok"):
                pause = max(300, int(cfg.get("matched_market_archive_safety_pause_seconds", 3600) or 3600))
                save_runtime_state(archive_root, {
                    "last_attempt_hour": archive_hour, "last_error": guard.get("reason"),
                    "last_result": result, "last_guard": guard, "paused_until_epoch": time.time() + pause,
                })
                logger.warning("matched-market archive safety pause hour=%s reason=%s", archive_hour, guard.get("reason"))
            else:
                current_state = load_runtime_state(archive_root)
                success_patch = {
                    "last_attempt_hour": archive_hour, "last_success_hour": archive_hour, "last_error": None,
                    "last_result": result, "last_guard": guard, "backoff_until_epoch": 0,
                }
                if not current_state.get("first_success_hour"):
                    success_patch["first_success_hour"] = archive_hour
                save_runtime_state(archive_root, success_patch)
                logger.info("matched-market archive hour=%s status=%s rows=%s duration=%sms",
                            archive_hour, result.get("status"), (result.get("manifest") or {}).get("row_count"), result.get("duration_ms"))
            archive_process = None
            archive_before = None
            archive_started = 0.0
            archive_hour = None
            # Preserve continuity after a pause/backoff: if any pilot hour is still
            # unverified, retry/catch up oldest-first instead of jumping to the
            # newest hour and leaving a permanent archive gap.
            state_after = load_runtime_state(archive_root)
            blocked_after, _ = runtime_blocked_until(archive_root)
            now_after = time.time()
            pending_after = next_pilot_archive_hour(
                archive_root, state_after.get("pilot_start_hour"), newest_closed_hour()
            )
            if blocked_after > now_after:
                archive_next_check = blocked_after
            elif pending_after:
                catchup_delay = max(5, int(cfg.get("matched_market_archive_catchup_delay_seconds", 30) or 30))
                archive_next_check = now_after + catchup_delay
            else:
                archive_next_check = next_utc_hour_epoch()

        if bool(cfg.get("matched_market_archive_enabled", False)) and archive_process is None and now >= archive_next_check:
            gate_required = bool(cfg.get("matched_market_archive_runtime_gate_required", True))
            dependency_ready = bool(duckdb_runtime_status().get("available"))
            if not dependency_ready or (gate_required and not runtime_gate_passed(archive_root)):
                # The continuous worker must never become the first real-Parquet
                # experiment for a recovered install, nor repeatedly spawn a child
                # after DuckDB disappears. Recheck without touching SQLite.
                archive_next_check = now + 60.0
            else:
                blocked_until, blocked_reason = runtime_blocked_until(archive_root)
                if blocked_until > now:
                    archive_next_check = blocked_until
                else:
                    current_state = load_runtime_state(archive_root)
                    pilot_start = current_state.get("pilot_start_hour")
                    if not pilot_start:
                        pilot_start = newest_closed_hour()
                        save_runtime_state(archive_root, {
                            "pilot_started_at": datetime.now(timezone.utc).isoformat(),
                            "pilot_start_hour": pilot_start,
                        })
                    archive_hour = next_pilot_archive_hour(archive_root, pilot_start, newest_closed_hour())
                    if archive_hour is None:
                        # Everything through the newest closed hour is verified.
                        # Sleep until the next UTC boundary instead of re-running
                        # an already verified hour.
                        archive_next_check = next_utc_hour_epoch()
                    else:
                        archive_before = sqlite_scanner_baseline(db.path)
                        archive_started = time.time()
                        command = _archive_process_command(db.path, archive_root, archive_hour)
                        preexec = _archive_preexec(int(cfg.get("matched_market_archive_nice", 10) or 10)) if os.name != "nt" else None
                        archive_process = subprocess.Popen(
                            command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, preexec_fn=preexec
                        )
                        logger.info("matched-market archive started hour=%s pid=%s", archive_hour, archive_process.pid)
        elif not bool(cfg.get("matched_market_archive_enabled", False)) and archive_process is None:
            # If the user enables the pilot later, run once immediately instead of
            # inheriting a stale sleep-until-boundary timestamp.
            archive_next_check = 0.0

        # v0.8.39 storage maintenance is independent of scanner mode and venue
        # connectivity. A paused scanner can still safely recover its legacy raw
        # quote footprint without touching trading or financial history.
        maintenance_interval = max(5, int(cfg.get("snapshot_maintenance_seconds", 10) or 10))
        if now - last_storage_maintenance >= maintenance_interval:
            try:
                maintenance = db.snapshot_storage_maintenance(
                    keep_legacy_rows=int(cfg.get("snapshot_legacy_keep_rows", 100000) or 100000),
                    batch_size=int(cfg.get("snapshot_prune_batch_rows", 100000) or 100000),
                )
                if maintenance.get("deleted"):
                    logger.info(
                        "snapshot storage maintenance deleted=%s done=%s oldest_id=%s target_id=%s",
                        maintenance.get("deleted"), maintenance.get("done"), maintenance.get("oldest_id"), maintenance.get("target_id"),
                    )
            except Exception:
                db.rollback_if_needed()
                logger.exception("snapshot storage maintenance failure")
            last_storage_maintenance = time.time()

        # 0.9.14 matched-market lifecycle. Fresh/non-archive installs retain the
        # established 48-hour verbose-row lifecycle. Once the verified archive
        # pilot is enabled, raw deletion pauses by default: compact history is
        # still finalized, but deletion can resume only through the archive-gated
        # planner/executor after explicit arming.
        matched_interval = max(5, int(cfg.get("matched_market_maintenance_seconds", 30) or 30))
        if now - last_matched_market_maintenance >= matched_interval:
            try:
                retention = int(cfg.get("matched_market_retention_hours", 48) or 48)
                batch_rows = int(cfg.get("matched_market_prune_batch_rows", 5000) or 5000)
                archive_enabled = bool(cfg.get("matched_market_archive_enabled", False))
                prune_armed = bool(cfg.get("matched_market_archive_required_before_prune", False))
                if archive_enabled and prune_armed:
                    maintenance = archive_prune_execute(
                        db.path, archive_root, retention_hours=retention, batch_size=batch_rows,
                        scanner_enabled=bool(cfg.get("scanner_enabled", True)),
                        price_tick_seconds=float(cfg.get("price_scan_tick_seconds", 2) or 2),
                        safety_pause_seconds=int(cfg.get("matched_market_archive_safety_pause_seconds", 3600) or 3600),
                    )
                elif archive_enabled:
                    maintenance = db.matched_market_finalize_due_hour(retention_hours=retention)
                else:
                    maintenance = db.matched_market_storage_maintenance(
                        retention_hours=retention, batch_size=batch_rows, archive_required_before_prune=False,
                        archive_root=archive_root,
                    )
                if maintenance.get("deleted") or maintenance.get("deleted_rows"):
                    logger.info(
                        "matched-market maintenance status=%s deleted=%s hour=%s remaining=%s",
                        maintenance.get("status") or maintenance.get("mode"),
                        maintenance.get("deleted_rows", maintenance.get("deleted", 0)),
                        maintenance.get("hour_utc"), maintenance.get("source_rows_remaining", maintenance.get("hour_remaining")),
                    )
            except Exception:
                db.rollback_if_needed()
                logger.exception("matched-market storage maintenance failure")
            last_matched_market_maintenance = time.time()

        if cfg.get("scanner_enabled", True):
            discovery_interval = max(30, int(cfg.get("discovery_interval_seconds", cfg.get("scan_interval_seconds", 60)) or 60))
            price_tick = max(1, int(cfg.get("price_scan_tick_seconds", 2) or 2))
            cache_active = int((db.market_cache_stats() or {}).get("active") or 0)

            if discovery_thread is not None and not discovery_thread.is_alive():
                discovery_thread = None
            if (cache_active == 0 or now - last_discovery >= discovery_interval) and discovery_thread is None:
                # Discovery is intentionally asynchronous. A slow catalogue walk
                # must never prevent the 1-2s price scheduler from refreshing
                # current market state and provider freshness.
                discovery_thread = threading.Thread(
                    target=_run_discovery_background,
                    args=(db.path, logger),
                    name="arbscanner-discovery",
                    daemon=True,
                )
                discovery_thread.start()
                last_discovery = now

            now = time.time()
            if now - last_price_tick >= price_tick:
                try:
                    result = scanner.price_scan_once(job_id=None, force=False)
                    p = result.get("pipeline") or {}
                    if not result.get("skipped"):
                        logger.info(
                            "price ok=%s cache=%s processed=%s opportunities=%s qualified=%s executed=%s duration=%sms%s",
                            result.get("ok"), result.get("matches", 0), p.get("processed", 0), p.get("opportunities", 0),
                            p.get("qualified", 0), p.get("executed", 0), p.get("duration_ms", 0),
                            f" error={result.get('message')}" if not result.get("ok") and result.get("message") else "",
                        )
                except Exception:
                    db.rollback_if_needed()
                    logger.exception("uncaught price-scan failure")
                last_price_tick = time.time()

            now = time.time()
            if now - last_settlement >= max(20, int(cfg.get("settlement_poll_seconds", 30) or 30)):
                try:
                    settle = scanner.settle_once()
                    if not settle.get("ok", True):
                        logger.warning("settlement check reported errors: %s", json.dumps(settle, ensure_ascii=False))
                except Exception:
                    db.rollback_if_needed()
                    logger.exception("uncaught settlement failure")
                last_settlement = time.time()

            now = time.time()
            if now - last_keepalive >= 6 * 60 * 60:
                try:
                    keepalive = scanner.keepalive_betfair()
                    if keepalive.get("ok"):
                        logger.info("Betfair keepAlive OK")
                    else:
                        logger.warning("Betfair keepAlive failed: %s", keepalive.get("message"))
                except Exception:
                    db.rollback_if_needed()
                    logger.exception("uncaught Betfair keepAlive failure")
                last_keepalive = now
        time.sleep(1.0)


if __name__ == "__main__":
    if "--archive-hour-worker" in sys.argv:
        raise SystemExit(_archive_child_main(sys.argv[1:]))
    main()
