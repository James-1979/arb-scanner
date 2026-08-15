from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

ARCHIVE_SCHEMA_VERSION = 1
RUNTIME_GATE_PROTOCOL_VERSION = 1
STATE_FILE = "_archive_state.json"
RUNTIME_GATE_FILE = "_last_runtime_gate.json"
PRUNE_PROGRESS_FILE = "_prune_progress.json"
PRUNE_AUDIT_FILE = "_prune_audit.jsonl"


def _utc(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def hour_floor(value: str | datetime) -> datetime:
    return _utc(value).replace(minute=0, second=0, microsecond=0)


def hour_iso(value: str | datetime) -> str:
    return hour_floor(value).isoformat()


def next_utc_hour_epoch(now: datetime | None = None, *, grace_seconds: int = 3) -> float:
    dt = _utc(now or datetime.now(timezone.utc))
    return (dt.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1, seconds=max(0, int(grace_seconds)))).timestamp()


def newest_closed_hour(now: datetime | None = None) -> str:
    dt = hour_floor(now or datetime.now(timezone.utc)) - timedelta(hours=1)
    return dt.isoformat()


def default_archive_root(db_path: Path) -> Path:
    return Path(db_path).resolve().parent / "matched-market-archive"


def _hour_dir(root: Path, hour_utc: str | datetime) -> Path:
    dt = hour_floor(hour_utc)
    return Path(root) / f"year={dt:%Y}" / f"month={dt:%m}" / f"day={dt:%d}" / f"hour={dt:%H}"


def parquet_path(root: Path, hour_utc: str | datetime) -> Path:
    return _hour_dir(root, hour_utc) / "matched_markets.parquet"


def manifest_path(root: Path, hour_utc: str | datetime) -> Path:
    return _hour_dir(root, hour_utc) / "manifest.json"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def runtime_gate_report_path(root: Path) -> Path:
    return Path(root) / RUNTIME_GATE_FILE


def load_runtime_gate_report(root: Path) -> dict | None:
    path = runtime_gate_report_path(root)
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return value if isinstance(value, dict) else None


def save_runtime_gate_report(root: Path, payload: dict) -> dict:
    value = dict(payload or {})
    value["published_at"] = datetime.now(timezone.utc).isoformat()
    _atomic_json(runtime_gate_report_path(root), value)
    return value


def runtime_gate_passed(root: Path) -> bool:
    report = load_runtime_gate_report(root)
    return bool(
        report
        and report.get("ok")
        and str(report.get("status") or "").upper() == "PASS"
        and int(report.get("gate_protocol_version") or 0) == RUNTIME_GATE_PROTOCOL_VERSION
        and int(report.get("archive_schema_version") or 0) == ARCHIVE_SCHEMA_VERSION
    )


def read_manifest(root: Path, hour_utc: str | datetime, *, verify_checksum: bool = False) -> dict | None:
    mp = manifest_path(root, hour_utc)
    if not mp.exists():
        return None
    try:
        data = json.loads(mp.read_text(encoding="utf-8"))
    except Exception:
        return None
    if str(data.get("status") or "").upper() != "VERIFIED":
        return data
    pp = parquet_path(root, hour_utc)
    if not pp.exists():
        return {**data, "status": "MISSING_FILE"}
    if verify_checksum and data.get("sha256") and _sha256(pp) != str(data.get("sha256")):
        return {**data, "status": "CHECKSUM_MISMATCH"}
    return data


def manifest_verified(root: Path, hour_utc: str | datetime, *, verify_checksum: bool = False) -> bool:
    row = read_manifest(root, hour_utc, verify_checksum=verify_checksum)
    return bool(row and str(row.get("status") or "").upper() == "VERIFIED")


def duckdb_runtime_status() -> dict:
    """Return dependency readiness without importing DuckDB at application startup."""
    try:
        import duckdb  # type: ignore
        return {"available": True, "version": str(getattr(duckdb, "__version__", "unknown")), "message": None}
    except Exception as exc:  # pragma: no cover - environment-specific dependency
        return {"available": False, "version": None, "message": str(exc)}


def _duckdb():
    try:
        import duckdb  # type: ignore
    except Exception as exc:  # pragma: no cover - environment-specific dependency
        raise RuntimeError("DuckDB is required for matched-market Parquet archival/readback") from exc
    return duckdb


def _sqlite_ro(path: Path) -> sqlite3.Connection:
    uri = f"file:{Path(path).resolve().as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def sqlite_scanner_baseline(db_path: Path) -> dict:
    """Read scanner liveness using an independent read-only WAL connection."""
    try:
        with _sqlite_ro(db_path) as conn:
            price = conn.execute(
                """SELECT id,started_at,finished_at,error,duration_ms FROM scan_runs
                   WHERE COALESCE(scan_kind,'legacy') IN ('price','legacy') ORDER BY id DESC LIMIT 1"""
            ).fetchone()
            matched = conn.execute("SELECT MAX(observed_at) t FROM matched_market_latest").fetchone()
            return {
                "ok": True,
                "price_scan_id": int(price["id"] or 0) if price else 0,
                "price_scan_error": (price["error"] if price else None),
                "price_scan_started_at": (price["started_at"] if price else None),
                "price_scan_finished_at": (price["finished_at"] if price else None),
                "price_scan_duration_ms": int(price["duration_ms"] or 0) if price else 0,
                "matched_market_latest_observed_at": matched["t"] if matched else None,
            }
    except Exception as exc:
        return {"ok": False, "error": str(exc), "price_scan_id": 0}


def archive_impact_guard(before: dict, after: dict, *, elapsed_seconds: float, scanner_enabled: bool,
                         price_tick_seconds: float = 2.0) -> dict:
    """Flag only objective scanner failures; provider timing/quiet markets are not failures."""
    if not scanner_enabled or not before.get("ok") or not after.get("ok"):
        return {"ok": True, "checked_progress": False, "reason": "scanner_guard_not_applicable"}
    before_id = int(before.get("price_scan_id") or 0)
    after_id = int(after.get("price_scan_id") or 0)
    new_error = after_id > before_id and bool(after.get("price_scan_error"))
    if new_error:
        return {"ok": False, "checked_progress": True, "reason": "latest_price_scan_error", "before_id": before_id, "after_id": after_id}
    min_window = max(5.0, float(price_tick_seconds or 2.0) * 2.5)
    if float(elapsed_seconds or 0.0) < min_window:
        return {"ok": True, "checked_progress": False, "reason": "archive_too_short_for_progress_test", "before_id": before_id, "after_id": after_id}
    if before_id > 0 and after_id <= before_id:
        return {"ok": False, "checked_progress": True, "reason": "price_scans_not_progressing", "before_id": before_id, "after_id": after_id}
    return {"ok": True, "checked_progress": True, "reason": "price_scans_progressing", "before_id": before_id, "after_id": after_id}


def load_runtime_state(root: Path) -> dict:
    path = Path(root) / STATE_FILE
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_runtime_state(root: Path, patch: dict) -> dict:
    state = load_runtime_state(root)
    state.update(patch)
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    _atomic_json(Path(root) / STATE_FILE, state)
    return state


def runtime_blocked_until(root: Path) -> tuple[float, str | None]:
    state = load_runtime_state(root)
    now = time.time()
    candidates: list[tuple[float, str]] = []
    for key, reason in (("paused_until_epoch", "scanner_safety_pause"), ("backoff_until_epoch", "archive_failure_backoff")):
        try:
            ts = float(state.get(key) or 0.0)
        except Exception:
            ts = 0.0
        if ts > now:
            candidates.append((ts, reason))
    if not candidates:
        return 0.0, None
    return max(candidates, key=lambda x: x[0])


def _duck_type(sqlite_decl: str | None) -> str:
    d = str(sqlite_decl or "").upper()
    if "INT" in d:
        return "BIGINT"
    if any(x in d for x in ("REAL", "FLOA", "DOUB", "NUM", "DEC")):
        return "DOUBLE"
    if "BLOB" in d:
        return "BLOB"
    return "VARCHAR"


def archive_hour(db_path: Path, archive_root: Path, hour_utc: str | datetime, *, overwrite: bool = False,
                 verify_checksum: bool = True) -> dict:
    """Archive one closed UTC hour to verified Parquet without writing to SQLite."""
    started = time.perf_counter()
    hour = hour_iso(hour_utc)
    end = (hour_floor(hour) + timedelta(hours=1)).isoformat()
    if hour_floor(hour) >= hour_floor(datetime.now(timezone.utc)):
        return {"ok": False, "status": "OPEN_HOUR", "hour_utc": hour, "message": "Only closed UTC hours are archive eligible."}
    root = Path(archive_root)
    existing = read_manifest(root, hour, verify_checksum=verify_checksum)
    if existing and str(existing.get("status") or "").upper() == "VERIFIED" and not overwrite:
        return {"ok": True, "status": "ALREADY_VERIFIED", "hour_utc": hour, "manifest": existing, "duration_ms": round((time.perf_counter()-started)*1000, 3)}

    duckdb = _duckdb()
    out = parquet_path(root, hour)
    man = manifest_path(root, hour)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".parquet.tmp")
    if tmp.exists():
        tmp.unlink()

    with _sqlite_ro(Path(db_path)) as conn:
        info = conn.execute("PRAGMA table_info(matched_markets)").fetchall()
        cols = [str(r["name"]) for r in info]
        if not cols:
            raise RuntimeError("matched_markets schema not found")
        stats = conn.execute(
            "SELECT COUNT(*) c,MIN(id) min_id,MAX(id) max_id,MIN(observed_at) min_t,MAX(observed_at) max_t FROM matched_markets WHERE observed_at>=? AND observed_at<?",
            (hour, end),
        ).fetchone()
        row_count = int(stats["c"] or 0)
        dcon = duckdb.connect(database=":memory:")
        try:
            ddl = ",".join(f'"{str(r["name"]).replace(chr(34), chr(34)*2)}" {_duck_type(r["type"])}' for r in info)
            dcon.execute(f"CREATE TABLE archive_rows ({ddl})")
            qcols = ",".join(f'"{c.replace(chr(34), chr(34)*2)}"' for c in cols)
            cur = conn.execute(f"SELECT {qcols} FROM matched_markets WHERE observed_at>=? AND observed_at<? ORDER BY id", (hour, end))
            placeholders = ",".join(["?"] * len(cols))
            while True:
                batch = cur.fetchmany(2000)
                if not batch:
                    break
                dcon.executemany(f"INSERT INTO archive_rows VALUES ({placeholders})", [tuple(r[c] for c in cols) for r in batch])
            escaped = str(tmp).replace("'", "''")
            dcon.execute(f"COPY archive_rows TO '{escaped}' (FORMAT PARQUET, COMPRESSION ZSTD)")
            verify = dcon.execute(f"SELECT COUNT(*) c,MIN(id),MAX(id),MIN(observed_at),MAX(observed_at) FROM read_parquet('{escaped}')").fetchone()
            verified_count = int(verify[0] or 0)
            if verified_count != row_count:
                raise RuntimeError(f"Parquet row-count mismatch: source={row_count} archive={verified_count}")
            if row_count:
                if int(verify[1] or 0) != int(stats["min_id"] or 0) or int(verify[2] or 0) != int(stats["max_id"] or 0):
                    raise RuntimeError("Parquet id-range verification failed")
        finally:
            dcon.close()

    digest = _sha256(tmp)
    os.replace(tmp, out)
    payload = {
        "schema_version": ARCHIVE_SCHEMA_VERSION,
        "status": "VERIFIED",
        "hour_utc": hour,
        "hour_end_utc": end,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "row_count": row_count,
        "verified_row_count": verified_count,
        "min_id": stats["min_id"],
        "max_id": stats["max_id"],
        "min_observed_at": stats["min_t"],
        "max_observed_at": stats["max_t"],
        "parquet_bytes": out.stat().st_size,
        "sha256": digest,
        "sqlite_mode": "ro+query_only",
        "source_table": "matched_markets",
        "parquet_file": out.name,
    }
    _atomic_json(man, payload)
    if verify_checksum and not manifest_verified(root, hour, verify_checksum=True):
        raise RuntimeError("Final archive checksum verification failed")
    return {"ok": True, "status": "VERIFIED", "hour_utc": hour, "manifest": payload,
            "source_row_count": row_count, "verified_row_count": verified_count,
            "duration_ms": round((time.perf_counter()-started)*1000, 3)}


def _iter_hours(start: datetime, end: datetime) -> Iterable[datetime]:
    cur = hour_floor(start)
    stop = _utc(end)
    while cur < stop:
        yield cur
        cur += timedelta(hours=1)


def archived_hours(root: Path, start: str | datetime, end: str | datetime) -> set[str]:
    return {dt.isoformat() for dt in _iter_hours(_utc(start), _utc(end)) if manifest_verified(root, dt)}


def parquet_hour_stats(root: Path, hour_utc: str | datetime, *, verify_checksum: bool = True) -> dict:
    """Return independent DuckDB stats for one verified archive hour."""
    hour = hour_iso(hour_utc)
    if not manifest_verified(Path(root), hour, verify_checksum=verify_checksum):
        return {"ok": False, "hour_utc": hour, "status": "MANIFEST_NOT_VERIFIED"}
    path = parquet_path(Path(root), hour)
    duckdb = _duckdb()
    con = duckdb.connect(database=":memory:")
    try:
        escaped = str(path).replace("'", "''")
        row = con.execute(
            f"SELECT COUNT(*) c,MIN(id),MAX(id),MIN(observed_at),MAX(observed_at) FROM read_parquet('{escaped}')"
        ).fetchone()
    finally:
        con.close()
    return {
        "ok": True,
        "hour_utc": hour,
        "status": "VERIFIED",
        "row_count": int(row[0] or 0),
        "min_id": row[1],
        "max_id": row[2],
        "min_observed_at": row[3],
        "max_observed_at": row[4],
        "parquet_file": str(path),
        "parquet_bytes": path.stat().st_size,
    }


def read_archived_rows(root: Path, hours: Iterable[str], *, start_utc: str, end_utc: str, limit: int,
                       section: str | None = None, sport: str | None = None, market: str | None = None,
                       search: str | None = None, event_key: str | None = None) -> list[dict]:
    """Bounded detailed read across verified Parquet hours."""
    if limit <= 0:
        return []
    duckdb = _duckdb()
    out: list[dict] = []
    con = duckdb.connect(database=":memory:")
    try:
        for hour in hours:
            if len(out) >= limit:
                break
            path = parquet_path(Path(root), hour)
            if not manifest_verified(Path(root), hour) or not path.exists():
                continue
            clauses = ["observed_at>=?", "observed_at<?"]
            args: list = [str(start_utc), str(end_utc)]
            if section and str(section).lower() not in {"", "all"}:
                clauses.append("LOWER(COALESCE(section,'sports'))=LOWER(?)"); args.append(str(section))
            if sport and str(sport).lower() not in {"", "all"}:
                clauses.append("LOWER(COALESCE(sport,'Unknown'))=LOWER(?)"); args.append(str(sport))
            if market:
                clauses.append("LOWER(COALESCE(market_name,'')) LIKE ?"); args.append('%'+str(market).lower()+'%')
            if event_key:
                clauses.append("COALESCE(event_key,'')=?"); args.append(str(event_key))
            if search:
                clauses.append("LOWER(COALESCE(event_name,'') || ' ' || COALESCE(event_key,'') || ' ' || COALESCE(market_name,'') || ' ' || COALESCE(sport,'')) LIKE ?")
                args.append('%'+str(search).lower()+'%')
            remaining = max(1, int(limit) - len(out))
            args.append(remaining)
            sql = f"SELECT * FROM read_parquet(?) WHERE {' AND '.join(clauses)} ORDER BY observed_at,id LIMIT ?"
            rows = con.execute(sql, [str(path), *args]).fetchall()
            names = [d[0] for d in con.description]
            out.extend(dict(zip(names, row)) for row in rows)
    finally:
        con.close()
    return out


def next_pilot_archive_hour(root: Path, pilot_start_hour: str | datetime | None,
                            through_hour: str | datetime | None = None) -> str | None:
    """Return the oldest unverified hour in the active pilot window.

    Continuous archival must close gaps oldest-first. Jumping straight to the
    newest closed hour after a pause/backoff would leave a permanent hole in
    the detailed-history archive even though the worker appeared to be running.
    """
    if not pilot_start_hour:
        return newest_closed_hour()
    start = hour_floor(pilot_start_hour)
    through = hour_floor(through_hour or newest_closed_hour())
    if through < start:
        return None
    for dt in _iter_hours(start, through + timedelta(hours=1)):
        if not manifest_verified(Path(root), dt):
            return dt.isoformat()
    return None


def archive_continuity(root: Path, first_hour: str | datetime | None, through_hour: str | datetime | None = None) -> dict:
    """Report verified-hour continuity for the continuous archive pilot.

    The continuity window begins at the first successfully archived pilot hour,
    not at the beginning of all historical data. This makes the readiness signal
    honest when archival is enabled after the application already has older
    compact-only history.
    """
    if not first_hour:
        return {
            "started": False, "first_hour": None, "through_hour": None,
            "expected_hours": 0, "verified_hours": 0, "gaps": [],
            "complete": False, "latest_verified_hour": None,
        }
    start = hour_floor(first_hour)
    through = hour_floor(through_hour or newest_closed_hour())
    if through < start:
        return {
            "started": True, "first_hour": start.isoformat(), "through_hour": through.isoformat(),
            "expected_hours": 0, "verified_hours": 0, "gaps": [],
            "complete": True, "latest_verified_hour": None,
        }
    end = through + timedelta(hours=1)
    expected = [dt.isoformat() for dt in _iter_hours(start, end)]
    verified = archived_hours(Path(root), start, end)
    gaps = [hour for hour in expected if hour not in verified]
    latest = max(verified) if verified else None
    return {
        "started": True,
        "first_hour": start.isoformat(),
        "through_hour": through.isoformat(),
        "expected_hours": len(expected),
        "verified_hours": len(verified.intersection(expected)),
        "gaps": gaps,
        "complete": not gaps,
        "latest_verified_hour": latest,
    }


def _prune_progress_path(root: Path) -> Path:
    return Path(root) / PRUNE_PROGRESS_FILE


def _load_prune_progress_all(root: Path) -> dict:
    path = _prune_progress_path(root)
    if not path.exists():
        return {"hours": {}}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"hours": {}}
    if not isinstance(value, dict):
        return {"hours": {}}
    hours = value.get("hours")
    if not isinstance(hours, dict):
        value["hours"] = {}
    return value


def load_prune_progress(root: Path, hour_utc: str | datetime) -> dict | None:
    hour = hour_iso(hour_utc)
    value = _load_prune_progress_all(Path(root)).get("hours", {}).get(hour)
    return dict(value) if isinstance(value, dict) else None


def save_prune_progress(root: Path, hour_utc: str | datetime, patch: dict) -> dict:
    hour = hour_iso(hour_utc)
    value = _load_prune_progress_all(Path(root))
    hours = value.setdefault("hours", {})
    current = dict(hours.get(hour) or {})
    current.update(dict(patch or {}))
    current["hour_utc"] = hour
    current["updated_at"] = datetime.now(timezone.utc).isoformat()
    hours[hour] = current
    _atomic_json(_prune_progress_path(Path(root)), value)
    return current


def _append_prune_audit(root: Path, payload: dict) -> None:
    path = Path(root) / PRUNE_AUDIT_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(payload, sort_keys=True, default=str) + "\n"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line)
        fh.flush()
        os.fsync(fh.fileno())


def archive_prune_dry_run(db_path: Path, archive_root: Path, *, retention_hours: int = 48, max_hours: int = 500) -> dict:
    """Plan archive-gated raw-history pruning without modifying SQLite or archive files.

    0.9.14 deliberately keeps this read-only. An hour is eligible only when the
    compact-history ledger is finalized, the protocol/schema-bound runtime gate
    is still valid, DuckDB is available, no archive safety pause/backoff is
    active, and the VERIFIED manifest/checksum exactly covers the raw rows that
    would be removed. Unknown or partial state always fails closed.
    """
    retention = max(1, int(retention_hours or 48))
    limit = max(1, min(5000, int(max_hours or 500)))
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=retention)).replace(minute=0, second=0, microsecond=0)
    cutoff_iso = cutoff.isoformat()
    root = Path(archive_root)
    dependency = duckdb_runtime_status()
    gate_ok = runtime_gate_passed(root)
    blocked_until, blocked_reason = runtime_blocked_until(root)
    blocked = bool(blocked_until and blocked_until > time.time())

    rows: list[dict] = []
    with _sqlite_ro(Path(db_path)) as conn:
        raw = conn.execute(
            """SELECT strftime('%Y-%m-%dT%H:00:00+00:00',observed_at) hour_utc,
                      COUNT(*) row_count, MIN(id) min_id, MAX(id) max_id,
                      MIN(observed_at) min_observed_at, MAX(observed_at) max_observed_at
               FROM matched_markets
               WHERE observed_at<?
               GROUP BY hour_utc ORDER BY hour_utc LIMIT ?""",
            (cutoff_iso, limit + 1),
        ).fetchall()
        truncated = len(raw) > limit
        raw = raw[:limit]
        for item in raw:
            hour = str(item["hour_utc"] or "")
            finalized = bool(conn.execute(
                "SELECT 1 FROM matched_market_history_state WHERE hour_utc=?", (hour,)
            ).fetchone())
            reasons: list[str] = []
            if not dependency.get("available"):
                reasons.append("duckdb_dependency_missing")
            if not gate_ok:
                reasons.append("runtime_gate_not_passed")
            if blocked:
                reasons.append(str(blocked_reason or "archive_safety_block"))
            if not finalized:
                reasons.append("history_not_finalized")

            manifest = read_manifest(root, hour, verify_checksum=True)
            manifest_status = str((manifest or {}).get("status") or "MISSING").upper()
            progress = load_prune_progress(root, hour)
            resume_prune = False
            if not manifest:
                reasons.append("archive_manifest_missing")
            elif manifest_status == "CHECKSUM_MISMATCH":
                reasons.append("archive_checksum_failed")
            elif manifest_status != "VERIFIED":
                reasons.append(f"archive_{manifest_status.lower()}")
            else:
                if int(manifest.get("schema_version") or 0) != ARCHIVE_SCHEMA_VERSION:
                    reasons.append("archive_schema_incompatible")
                source_count = int(item["row_count"] or 0)
                archive_count = int(manifest.get("row_count") or -1)
                if archive_count == source_count:
                    if source_count and (manifest.get("min_id") != item["min_id"] or manifest.get("max_id") != item["max_id"]):
                        reasons.append("archive_id_range_mismatch")
                elif 0 <= source_count < archive_count:
                    deleted_expected = archive_count - source_count
                    progress_ok = bool(
                        progress
                        and str(progress.get("archive_sha256") or "") == str(manifest.get("sha256") or "")
                        and int(progress.get("archive_row_count") or -1) == archive_count
                        and int(progress.get("deleted_rows") or -1) == deleted_expected
                        and str(progress.get("status") or "").upper() in {"IN_PROGRESS", "PAUSED", "FAILED"}
                    )
                    if not progress_ok:
                        reasons.append("partial_prune_state_missing")
                    elif source_count and (
                        (manifest.get("min_id") is not None and int(item["min_id"] or 0) < int(manifest.get("min_id")))
                        or (manifest.get("max_id") is not None and int(item["max_id"] or 0) > int(manifest.get("max_id")))
                    ):
                        reasons.append("archive_id_range_mismatch")
                    else:
                        resume_prune = True
                else:
                    reasons.append("archive_row_count_mismatch")

            eligible = not reasons
            rows.append({
                "hour_utc": hour,
                "row_count": int(item["row_count"] or 0),
                "min_id": item["min_id"],
                "max_id": item["max_id"],
                "min_observed_at": item["min_observed_at"],
                "max_observed_at": item["max_observed_at"],
                "history_finalized": finalized,
                "archive_status": manifest_status,
                "archive_row_count": int((manifest or {}).get("row_count") or 0),
                "archive_sha256": (manifest or {}).get("sha256"),
                "resume_prune": resume_prune,
                "prune_progress": progress,
                "eligible": eligible,
                "reasons": reasons,
            })

    eligible_rows = sum(int(x["row_count"]) for x in rows if x["eligible"])
    blocked_rows = sum(int(x["row_count"]) for x in rows if not x["eligible"])
    reason_counts: dict[str, int] = {}
    for row in rows:
        for reason in row["reasons"]:
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
    return {
        "ok": True,
        "mode": "DRY_RUN",
        "destructive_action": False,
        "pruning_invoked": False,
        "retention_hours": retention,
        "cutoff_utc": cutoff_iso,
        "candidate_hours": len(rows),
        "eligible_hours": sum(1 for x in rows if x["eligible"]),
        "blocked_hours": sum(1 for x in rows if not x["eligible"]),
        "eligible_rows": eligible_rows,
        "blocked_rows": blocked_rows,
        "truncated": truncated,
        "dependency": dependency,
        "runtime_gate_passed": gate_ok,
        "archive_safety_blocked": blocked,
        "archive_safety_reason": blocked_reason if blocked else None,
        "blocked_reason_counts": reason_counts,
        "hours": rows,
    }


def archive_prune_execute(db_path: Path, archive_root: Path, *, retention_hours: int = 48, batch_size: int = 5000,
                          scanner_enabled: bool = True, price_tick_seconds: float = 2.0, safety_pause_seconds: int = 3600) -> dict:
    """Execute one planner-approved archived hour in bounded SQLite batches.

    0.9.14 keeps the dry-run planner as the single eligibility authority. The
    executor never broadens eligibility: it selects the oldest eligible hour,
    re-verifies the manifest checksum, records resumable progress after every
    committed batch, verifies SQLite integrity and Parquet queryability after
    deletion, and appends a durable audit record. Any uncertainty fails closed.
    """
    started_wall = datetime.now(timezone.utc)
    started = time.perf_counter()
    root = Path(archive_root)
    plan = archive_prune_dry_run(Path(db_path), root, retention_hours=retention_hours, max_hours=5000)
    hours = list(plan.get("hours", []) or [])
    if not hours:
        return {
            "ok": True, "status": "NO_ELIGIBLE_HISTORY", "destructive_action": False,
            "pruning_invoked": False, "plan": plan, "deleted_rows": 0,
        }
    candidate = hours[0]
    if not candidate.get("eligible"):
        return {
            "ok": True, "status": "OLDEST_HOUR_BLOCKED", "destructive_action": False,
            "pruning_invoked": False, "hour_utc": candidate.get("hour_utc"),
            "blocked_reasons": candidate.get("reasons") or [], "plan": plan, "deleted_rows": 0,
        }
    hour = str(candidate["hour_utc"])
    manifest = read_manifest(root, hour, verify_checksum=True)
    if not manifest or str(manifest.get("status") or "").upper() != "VERIFIED":
        return {"ok": False, "status": "MANIFEST_REVERIFY_FAILED", "pruning_invoked": False, "hour_utc": hour}
    archive_rows = int(manifest.get("row_count") or 0)
    source_rows = int(candidate.get("row_count") or 0)
    deleted_so_far = max(0, archive_rows - source_rows)
    progress = load_prune_progress(root, hour) or {}
    if deleted_so_far and int(progress.get("deleted_rows") or -1) != deleted_so_far:
        return {"ok": False, "status": "PARTIAL_PROGRESS_MISMATCH", "pruning_invoked": False, "hour_utc": hour}

    before = sqlite_scanner_baseline(Path(db_path))
    save_prune_progress(root, hour, {
        "status": "IN_PROGRESS", "archive_sha256": manifest.get("sha256"),
        "archive_row_count": archive_rows, "archive_min_id": manifest.get("min_id"),
        "archive_max_id": manifest.get("max_id"), "deleted_rows": deleted_so_far,
        "cutoff_utc": plan.get("cutoff_utc"), "started_at": progress.get("started_at") or started_wall.isoformat(),
    })

    deleted_this_run = 0
    batches = 0
    quick_check = None
    db = None
    error = None
    try:
        from .db import DB
        db = DB(Path(db_path))
        while deleted_so_far < archive_rows:
            result = db.matched_market_archive_prune_batch(
                hour_utc=hour, cutoff_utc=str(plan.get("cutoff_utc")), batch_size=batch_size,
                expected_archive_rows=archive_rows, expected_min_id=manifest.get("min_id"),
                expected_max_id=manifest.get("max_id"), deleted_so_far=deleted_so_far,
            )
            if not result.get("ok"):
                error = str(result.get("status") or "DELETE_FAILED")
                break
            deleted = int(result.get("deleted") or 0)
            deleted_this_run += deleted
            deleted_so_far += deleted
            batches += 1
            save_prune_progress(root, hour, {
                "status": "IN_PROGRESS", "deleted_rows": deleted_so_far, "last_batch_rows": deleted,
                "last_batch_at": datetime.now(timezone.utc).isoformat(),
            })
            if deleted <= 0 and int(result.get("hour_remaining") or 0) > 0:
                error = "DELETE_MADE_NO_PROGRESS"
                break
            if int(result.get("hour_remaining") or 0) == 0:
                break
        if db is not None:
            with db.lock:
                row = db.conn.execute("PRAGMA quick_check").fetchone()
                quick_check = str(row[0] if row else "unknown")
    except Exception as exc:
        error = str(exc)
    finally:
        if db is not None:
            try:
                db.conn.close()
            except Exception:
                pass

    elapsed = time.perf_counter() - started
    after = sqlite_scanner_baseline(Path(db_path))
    guard = archive_impact_guard(before, after, elapsed_seconds=elapsed, scanner_enabled=scanner_enabled, price_tick_seconds=price_tick_seconds)
    manifest_ok = manifest_verified(root, hour, verify_checksum=True)
    archive_stats = {}
    archive_queryable = False
    if manifest_ok:
        try:
            archive_stats = parquet_hour_stats(root, hour, verify_checksum=True)
            archive_queryable = bool(archive_stats.get("ok") and int(archive_stats.get("row_count") or -1) == archive_rows)
        except Exception as exc:
            archive_stats = {"ok": False, "error": str(exc)}
    source_remaining = None
    try:
        with _sqlite_ro(Path(db_path)) as conn:
            end = (hour_floor(hour) + timedelta(hours=1)).isoformat()
            source_remaining = int(conn.execute(
                "SELECT COUNT(*) c FROM matched_markets WHERE observed_at>=? AND observed_at<?", (hour, end)
            ).fetchone()["c"] or 0)
    except Exception as exc:
        error = error or str(exc)

    complete = bool(
        not error and deleted_so_far == archive_rows and source_remaining == 0
        and quick_check == "ok" and manifest_ok and archive_queryable and guard.get("ok")
    )
    status = "PASS" if complete else ("PAUSED" if not guard.get("ok") else "REVIEW")
    progress_status = "COMPLETE" if complete else ("PAUSED" if not guard.get("ok") else "FAILED")
    save_prune_progress(root, hour, {
        "status": progress_status, "deleted_rows": deleted_so_far, "finished_at": datetime.now(timezone.utc).isoformat(),
        "error": error, "scanner_guard": guard, "sqlite_quick_check": quick_check,
        "archive_queryable_after_prune": archive_queryable,
    })
    if not guard.get("ok"):
        save_runtime_state(root, {
            "paused_until_epoch": time.time() + max(60, int(safety_pause_seconds or 3600)),
            "last_error": f"prune_guard:{guard.get('reason')}", "last_guard": guard,
        })
    audit = {
        "audit_type": "matched_market_archive_prune", "status": status, "ok": complete,
        "started_at": started_wall.isoformat(), "finished_at": datetime.now(timezone.utc).isoformat(),
        "hour_utc": hour, "cutoff_utc": plan.get("cutoff_utc"), "retention_hours": int(retention_hours),
        "archive_schema_version": int(manifest.get("schema_version") or 0), "archive_sha256": manifest.get("sha256"),
        "archive_row_count": archive_rows, "archive_min_id": manifest.get("min_id"), "archive_max_id": manifest.get("max_id"),
        "deleted_before_run": archive_rows - source_rows, "deleted_this_run": deleted_this_run,
        "deleted_total": deleted_so_far, "source_rows_remaining": source_remaining, "batches": batches,
        "sqlite_quick_check": quick_check, "manifest_checksum_verified": manifest_ok,
        "archive_queryable_after_prune": archive_queryable, "scanner_before": before, "scanner_after": after,
        "scanner_guard": guard, "error": error, "pruning_invoked": True,
    }
    _append_prune_audit(root, audit)
    return {
        "ok": complete, "status": status, "destructive_action": True, "pruning_invoked": True,
        "hour_utc": hour, "deleted_rows": deleted_this_run, "deleted_total": deleted_so_far,
        "source_rows_remaining": source_remaining, "batches": batches, "audit": audit, "plan": plan,
    }
