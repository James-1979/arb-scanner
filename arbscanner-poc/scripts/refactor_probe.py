#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

_REAL_DATETIME = datetime

AUTHORITY_TABLES = {
    "settings", "opportunities", "scenario_runs", "settlements", "monitor_wallets", "monitor_stream_wallets",
    "monitor_positions", "execution_runs", "sim_account_adjustments", "live_accounts", "live_order_attempts", "live_orders",
    "live_fills", "live_positions", "live_settlements", "live_account_movements", "live_reconciliations",
    "venue_controls", "engine_configs", "engine_instances"
}
AUDIT_TABLES = {
    "live_account_audit", "live_account_snapshots", "settlement_audits", "account_snapshots", "balance_reconciliations"
}
DERIVED_TABLES = {
    "snapshot_rollups", "market_hourly_rollups", "market_hourly_seen", "market_hourly_rollup_state",
    "market_financial_hourly_rollups", "market_financial_hourly_state", "exchange_market_discovery_hours",
    "exchange_market_discovery_state", "liquidity_depth_hourly", "liquidity_opportunity_hourly",
    "matched_market_reason_hourly", "racing_funnel_hourly"
}
WRITE_OPS = {"INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "DROP", "ALTER", "VACUUM"}
READ_OPS = {"SELECT", "WITH", "PRAGMA"}
DYNAMIC_NUMBER_KEYS = {
    "latency_ms", "elapsed_ms", "duration_ms", "age_ms", "age_seconds", "quote_age_seconds",
    "last_age_seconds", "seconds_ago"
}


def parse_anchor(value: str | None) -> datetime:
    if value:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    return datetime.now(timezone.utc)


def resolve_value(value, anchor: datetime):
    if isinstance(value, dict):
        return {k: resolve_value(v, anchor) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_value(v, anchor) for v in value]
    if value == "__TO_ANCHOR__":
        return anchor.isoformat()
    if value == "__FROM_7D__":
        return (anchor - timedelta(days=7)).isoformat()
    return value


def near_anchor_timestamp(value: str, anchor: datetime) -> bool:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return abs((dt.astimezone(timezone.utc) - anchor).total_seconds()) <= 900
    except Exception:
        return False


def runtime_number_key(key: str | None) -> bool:
    if not key:
        return False
    if key in DYNAMIC_NUMBER_KEYS:
        return True
    return key.endswith("_ms") and any(token in key for token in ("scenario_", "latency", "elapsed", "duration", "benchmark", "query_time", "load_time"))


def normalize_output(value, anchor: datetime, key: str | None = None):
    if isinstance(value, dict):
        return {str(k): normalize_output(v, anchor, str(k)) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, list):
        return [normalize_output(v, anchor, key) for v in value]
    if isinstance(value, tuple):
        return [normalize_output(v, anchor, key) for v in value]
    if runtime_number_key(key) and isinstance(value, (int, float)):
        return "<DYNAMIC_NUMBER>"
    if isinstance(value, str) and near_anchor_timestamp(value, anchor):
        return "<DYNAMIC_TIMESTAMP>"
    if isinstance(value, float):
        return round(value, 10)
    return value


def canonical_json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)


def fingerprint(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def sql_op(statement: str) -> str:
    text = statement.lstrip()
    return text.split(None, 1)[0].upper() if text else ""


def target_table(statement: str) -> str | None:
    text = " ".join(statement.strip().split())
    patterns = [
        r"(?i)^INSERT(?:\s+OR\s+\w+)?\s+INTO\s+[`\"\[]?([A-Za-z0-9_]+)",
        r"(?i)^REPLACE\s+INTO\s+[`\"\[]?([A-Za-z0-9_]+)",
        r"(?i)^UPDATE\s+[`\"\[]?([A-Za-z0-9_]+)",
        r"(?i)^DELETE\s+FROM\s+[`\"\[]?([A-Za-z0-9_]+)",
        r"(?i)^(?:CREATE|DROP|ALTER)\s+(?:TABLE|INDEX)\s+(?:IF\s+(?:NOT\s+)?EXISTS\s+)?[`\"\[]?([A-Za-z0-9_]+)"
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1).lower()
    return None


def classify_table(table: str | None) -> str:
    if not table:
        return "other"
    if table in AUTHORITY_TABLES:
        return "authority"
    if table in AUDIT_TABLES:
        return "audit"
    if table in DERIVED_TABLES or table.endswith("_rollups") or table.endswith("_rollup_state"):
        return "derived"
    return "other"


def freeze_arbscanner_datetime(anchor: datetime) -> None:
    class FrozenDateTime(_REAL_DATETIME):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return anchor.replace(tzinfo=None)
            return anchor.astimezone(tz)

        @classmethod
        def utcnow(cls):
            return anchor.astimezone(timezone.utc).replace(tzinfo=None)

    for name, module in list(sys.modules.items()):
        if not name.startswith("arbscanner") or module is None:
            continue
        if getattr(module, "datetime", None) is _REAL_DATETIME:
            setattr(module, "datetime", FrozenDateTime)


def authority_integrity(db_path: Path) -> dict:
    uri = f"file:{db_path.resolve()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        checks = []
        def add(name: str, sql: str):
            if all(token in tables for token in re.findall(r"__TABLE_([A-Za-z0-9_]+)__", sql)):
                pass
            clean = re.sub(r"__TABLE_([A-Za-z0-9_]+)__", lambda m: m.group(1), sql)
            try:
                count = int(conn.execute(clean).fetchone()[0])
                checks.append({"name": name, "count": count, "ok": count == 0})
            except sqlite3.Error as exc:
                checks.append({"name": name, "count": None, "ok": False, "error": str(exc)})
        required = {"opportunities", "monitor_positions", "settlements", "live_positions", "live_settlements", "live_order_attempts"}
        if required.issubset(tables):
            add("monitor_position_orphans", "SELECT COUNT(*) FROM monitor_positions p LEFT JOIN opportunities o ON o.id=p.opportunity_id WHERE o.id IS NULL")
            add("settlement_orphans", "SELECT COUNT(*) FROM settlements s LEFT JOIN opportunities o ON o.id=s.opportunity_id WHERE o.id IS NULL")
            add("settled_opportunity_missing_result", "SELECT COUNT(*) FROM opportunities o LEFT JOIN settlements s ON s.opportunity_id=o.id WHERE LOWER(COALESCE(o.status,''))='settled' AND s.opportunity_id IS NULL")
            add("result_status_mismatch", "SELECT COUNT(*) FROM settlements s JOIN opportunities o ON o.id=s.opportunity_id WHERE LOWER(COALESCE(o.status,''))<>'settled'")
            add("settled_monitor_missing_result", "SELECT COUNT(*) FROM monitor_positions p LEFT JOIN settlements s ON s.opportunity_id=p.opportunity_id WHERE p.status='SETTLED' AND s.opportunity_id IS NULL")
            add("settled_monitor_result_mismatch", "SELECT COUNT(*) FROM monitor_positions p JOIN settlements s ON s.opportunity_id=p.opportunity_id WHERE p.status='SETTLED' AND LOWER(TRIM(COALESCE(p.outcome,'')))<>LOWER(TRIM(COALESCE(s.outcome,'')))")
            add("monitor_non_sim_mode", "SELECT COUNT(*) FROM monitor_positions WHERE COALESCE(mode,'sim')<>'sim'")
            add("live_order_non_live_mode", "SELECT COUNT(*) FROM live_order_attempts WHERE COALESCE(mode,'')<>'live'")
            add("live_settlement_orphans", "SELECT COUNT(*) FROM live_settlements s LEFT JOIN live_positions p ON p.position_id=s.position_id WHERE p.position_id IS NULL")
            add("settled_monitor_missing_timestamp", "SELECT COUNT(*) FROM monitor_positions WHERE status='SETTLED' AND settled_at IS NULL")
        return {"ok": all(x.get("ok") for x in checks), "checks": checks}
    finally:
        conn.close()


def run_projection(api_cls, source_db: Path, item: dict, anchor: datetime, work_dir: Path) -> dict:
    projection_id = str(item["id"])
    method = str(item["method"])
    db_path = work_dir / f"{projection_id}.sqlite3"
    shutil.copy2(source_db, db_path)
    api = api_cls(db_path)
    data = resolve_value(item.get("data") or {}, anchor)
    statements: list[str] = []
    api.db.conn.set_trace_callback(statements.append)
    before_changes = int(api.db.conn.total_changes)
    start = time.perf_counter()
    error = None
    output = None
    try:
        output = getattr(api, method)(data)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    elapsed_ms = round((time.perf_counter() - start) * 1000.0, 3)
    after_changes = int(api.db.conn.total_changes)
    api.db.conn.set_trace_callback(None)
    reads = [s for s in statements if sql_op(s) in READ_OPS]
    writes = [s for s in statements if sql_op(s) in WRITE_OPS]
    table_counts = Counter()
    class_counts = Counter()
    for statement in writes:
        table = target_table(statement)
        table_counts[table or "<unknown>"] += 1
        class_counts[classify_table(table)] += 1
    normalized = normalize_output(output, anchor)
    try:
        api.db.conn.close()
    except Exception:
        pass
    return {
        "id": projection_id,
        "method": method,
        "data": data,
        "error": error,
        "ok": bool(error is None and (not isinstance(output, dict) or output.get("ok", True))),
        "query_count": len(reads),
        "write_statement_count": len(writes),
        "total_changes": after_changes - before_changes,
        "write_tables": dict(sorted(table_counts.items())),
        "write_classes": {k: int(class_counts.get(k, 0)) for k in ("authority", "audit", "derived", "other")},
        "elapsed_ms": elapsed_ms,
        "output_fingerprint": fingerprint(normalized),
        "normalized_output": normalized
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure ArbScanner read projections against a copied SQLite snapshot.")
    parser.add_argument("--code-root", required=True)
    parser.add_argument("--db", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--home", required=True)
    parser.add_argument("--anchor")
    args = parser.parse_args()

    code_root = Path(args.code_root).resolve()
    source_db = Path(args.db).resolve()
    home = Path(args.home).resolve()
    home.mkdir(parents=True, exist_ok=True)
    os.environ["HOME"] = str(home)
    sys.path.insert(0, str(code_root))
    from arbscanner.api import API

    anchor = parse_anchor(args.anchor)
    freeze_arbscanner_datetime(anchor)
    manifest = json.loads(Path(args.manifest).read_text())
    with tempfile.TemporaryDirectory(prefix="arbscanner-probe-") as tmp:
        work_dir = Path(tmp)
        results = [run_projection(API, source_db, item, anchor, work_dir) for item in manifest.get("projections") or []]
    payload = {
        "schema_version": 1,
        "code_root": str(code_root),
        "source_db": str(source_db),
        "anchor": anchor.isoformat(),
        "integrity_before": authority_integrity(source_db),
        "projections": results
    }
    Path(args.output).write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n")
    blockers = [x for x in results if x.get("error") or int((x.get("write_classes") or {}).get("authority", 0)) > 0]
    return 1 if blockers else 0


if __name__ == "__main__":
    raise SystemExit(main())
