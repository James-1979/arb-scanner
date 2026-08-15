from __future__ import annotations

import hashlib
import json
from pathlib import Path
from datetime import datetime, timedelta, timezone

import pytest

from arbscanner import __version__
from arbscanner.analytics_store import AnalyticsStore
from arbscanner.archive import (
    ARCHIVE_SCHEMA_VERSION,
    RUNTIME_GATE_PROTOCOL_VERSION,
    archive_impact_guard,
    manifest_path,
    parquet_path,
    runtime_blocked_until,
    save_runtime_gate_report,
    load_runtime_gate_report,
    runtime_gate_passed,
    save_runtime_state,
)
from arbscanner.db import DB
from arbscanner.replay import ReplayHistoryLimitExceeded, prepare_replay_history


def _iso_hour(hours_ago: int = 0) -> datetime:
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).replace(minute=0, second=0, microsecond=0)


def _insert_matched(db: DB, observed_at: datetime, *, event_key: str = "event-1", market: str = "Match Odds") -> int:
    scan_id = db.start_scan(scan_kind="price")
    with db.lock:
        cur = db.conn.execute(
            """INSERT INTO matched_markets(
                scan_id,observed_at,event_key,event_name,event_start,market_name,match_score,status,
                strategy,sport,section,net_roi_pct,legs_json,source_markets_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (scan_id, observed_at.isoformat(), event_key, "Event", observed_at.isoformat(), market, 0.99,
             "qualified", "two-way", "Tennis", "sports", 1.25, "[]", "[]"),
        )
        db.conn.commit()
        return int(cur.lastrowid)


def _verified_manifest(root, hour: datetime, payload: bytes = b"parquet-placeholder"):
    pp = parquet_path(root, hour)
    mp = manifest_path(root, hour)
    pp.parent.mkdir(parents=True, exist_ok=True)
    pp.write_bytes(payload)
    body = {
        "status": "VERIFIED",
        "hour_utc": hour.isoformat(),
        "row_count": 1,
        "sha256": hashlib.sha256(payload).hexdigest(),
    }
    mp.write_text(json.dumps(body), encoding="utf-8")
    return body


def _settled_opportunity(db: DB, when: datetime, *, sig: str, execution_mode: str | None = None) -> int:
    oid = int(db.add_opportunity(
        f"event-{sig}", f"Event {sig}", when.isoformat(), "Match Odds", 1.5, 1.0,
        [], [], 0.99, sig, strategy="two-way", sport="Tennis",
    ))
    with db.lock:
        db.conn.execute("UPDATE opportunities SET detected_at=? WHERE id=?", (when.isoformat(), oid))
        db.conn.execute(
            "INSERT INTO settlements(opportunity_id,settled_at,outcome,simulated_pnl,notes) VALUES(?,?,?,?,?)",
            (oid, (when + timedelta(minutes=30)).isoformat(), "Home", 1.0, ""),
        )
        db.conn.execute("UPDATE opportunities SET status='settled' WHERE id=?", (oid,))
        db.conn.commit()
    if execution_mode:
        db.add_execution_run(oid, mode=execution_mode, execution_type="test", state="SETTLED", details={})
    return oid


def test_recovery_tree_keeps_099_version_and_archive_defaults_off(tmp_path):
    assert __version__ == "0.9.36"
    db = DB(tmp_path / "a.sqlite3")
    cfg = db.get_setting("config", {}) or {}
    # Defaults may be merged by API rather than DB; the operational absence/default must stay false.
    assert bool(cfg.get("matched_market_archive_enabled", False)) is False
    assert bool(cfg.get("matched_market_archive_required_before_prune", False)) is False


def test_archive_guard_ignores_quiet_market_and_short_archive():
    before = {"ok": True, "price_scan_id": 10, "matched_market_latest_observed_at": "2026-01-01T00:00:00+00:00"}
    after = {"ok": True, "price_scan_id": 10, "matched_market_latest_observed_at": "2026-01-01T00:00:00+00:00"}
    result = archive_impact_guard(before, after, elapsed_seconds=2, scanner_enabled=True, price_tick_seconds=2)
    assert result["ok"] is True
    assert result["checked_progress"] is False


def test_archive_guard_detects_stalled_or_error_price_scans():
    before = {"ok": True, "price_scan_id": 10}
    stalled = {"ok": True, "price_scan_id": 10, "price_scan_error": None}
    assert archive_impact_guard(before, stalled, elapsed_seconds=20, scanner_enabled=True, price_tick_seconds=2)["reason"] == "price_scans_not_progressing"
    errored = {"ok": True, "price_scan_id": 11, "price_scan_error": "provider failure"}
    assert archive_impact_guard(before, errored, elapsed_seconds=2, scanner_enabled=True, price_tick_seconds=2)["reason"] == "latest_price_scan_error"


def test_archive_runtime_pause_is_persisted(tmp_path):
    root = tmp_path / "archive"
    until = datetime.now(timezone.utc).timestamp() + 600
    save_runtime_state(root, {"paused_until_epoch": until, "last_error": "scan stalled"})
    blocked, reason = runtime_blocked_until(root)
    assert blocked == pytest.approx(until)
    assert reason == "scanner_safety_pause"


def test_pruning_default_is_unchanged_but_archive_gate_blocks_then_allows(tmp_path):
    db = DB(tmp_path / "retention.sqlite3")
    old = _iso_hour(96) + timedelta(minutes=10)
    _insert_matched(db, old)
    archive_root = tmp_path / "archive"

    blocked = db.matched_market_storage_maintenance(
        retention_hours=48, batch_size=100,
        archive_required_before_prune=True, archive_root=archive_root,
    )
    assert blocked["deleted"] == 0
    assert blocked["archive_required"] is True
    assert db.conn.execute("SELECT COUNT(*) FROM matched_markets").fetchone()[0] == 1

    hour = old.replace(minute=0, second=0, microsecond=0)
    _verified_manifest(archive_root, hour)
    allowed = db.matched_market_storage_maintenance(
        retention_hours=48, batch_size=100,
        archive_required_before_prune=True, archive_root=archive_root,
    )
    assert allowed["deleted"] == 1
    assert db.conn.execute("SELECT COUNT(*) FROM matched_markets").fetchone()[0] == 0


def test_default_pruning_still_deletes_without_archive(tmp_path):
    db = DB(tmp_path / "retention-default.sqlite3")
    _insert_matched(db, _iso_hour(96) + timedelta(minutes=10))
    result = db.matched_market_storage_maintenance(retention_hours=48, batch_size=100)
    assert result["deleted"] == 1


def test_analytics_coverage_uses_hour_ledger_and_reports_real_gap(tmp_path):
    db = DB(tmp_path / "coverage.sqlite3")
    root = tmp_path / "archive"
    start = _iso_hour(6)
    middle = start + timedelta(hours=1)
    tail = start + timedelta(hours=2)
    end = start + timedelta(hours=3)
    with db.lock:
        db.conn.execute("INSERT OR REPLACE INTO matched_market_history_state(hour_utc,built_at) VALUES(?,?)", (start.isoformat(), datetime.now(timezone.utc).isoformat()))
        db.conn.commit()
    _insert_matched(db, tail + timedelta(minutes=5), event_key="tail")
    _verified_manifest(root, start)

    coverage = AnalyticsStore(db, root).coverage(start.isoformat(), end.isoformat())
    assert coverage["summary_history_complete"] is False
    assert coverage["summary_history_gaps"] == [middle.isoformat()]
    assert coverage["detailed_history_complete"] is False
    assert coverage["detailed_history_gaps"] == [middle.isoformat()]


def test_all_history_range_resolves_ledger_plus_hot_tail(tmp_path):
    db = DB(tmp_path / "range.sqlite3")
    ledger_hour = _iso_hour(10)
    hot = _iso_hour(1) + timedelta(minutes=15)
    with db.lock:
        db.conn.execute("INSERT OR REPLACE INTO matched_market_history_state(hour_utc,built_at) VALUES(?,?)", (ledger_hour.isoformat(), datetime.now(timezone.utc).isoformat()))
        db.conn.commit()
    _insert_matched(db, hot)
    start, end, available = AnalyticsStore(db, tmp_path / "archive").resolve_range(None, None)
    assert start == ledger_hour.isoformat()
    assert end is not None and datetime.fromisoformat(end) > hot
    assert available["hot_to_utc"] == hot.isoformat()


def test_detailed_history_requires_explicit_range_and_refuses_partial(tmp_path):
    db = DB(tmp_path / "detail-gap.sqlite3")
    store = AnalyticsStore(db, tmp_path / "archive")
    assert store.detailed_history(None, None)["ok"] is False
    start = _iso_hour(4)
    result = store.detailed_history(start.isoformat(), (start + timedelta(hours=1)).isoformat())
    assert result["ok"] is False
    assert result["detailed_history_complete"] is False


def test_detailed_history_hot_sqlite_path_is_bounded_and_filtered(tmp_path):
    db = DB(tmp_path / "detail-hot.sqlite3")
    hour = _iso_hour(1)
    _insert_matched(db, hour + timedelta(minutes=5), event_key="alpha", market="Match Odds")
    _insert_matched(db, hour + timedelta(minutes=6), event_key="beta", market="Set Betting")
    result = AnalyticsStore(db, tmp_path / "archive").detailed_history(
        hour.isoformat(), (hour + timedelta(hours=1)).isoformat(), search="alpha", limit=10,
    )
    assert result["ok"] is True
    assert result["detailed_history_complete"] is True
    assert result["archive_hours"] == []
    assert result["sqlite_hours"] == [hour.isoformat()]
    assert [r["event_key"] for r in result["rows"]] == ["alpha"]


def test_replay_query_pushes_range_settlement_and_execution_mode_into_sqlite(tmp_path):
    db = DB(tmp_path / "replay.sqlite3")
    base = _iso_hour(20)
    sim_id = _settled_opportunity(db, base + timedelta(hours=1), sig="sim", execution_mode="sim")
    _settled_opportunity(db, base + timedelta(hours=2), sig="live", execution_mode="live")
    _settled_opportunity(db, base + timedelta(hours=5), sig="late", execution_mode="sim")
    rows = db.replay_opportunity_rows(
        date_from=base.isoformat(), date_to=(base + timedelta(hours=4)).isoformat(),
        execution_mode="sim", time_basis="detected_at",
    )
    assert [int(r["id"]) for r in rows] == [sim_id]
    assert all(r["outcome"] for r in rows)


def test_execution_history_is_loaded_only_for_selected_cohort(tmp_path):
    db = DB(tmp_path / "exec.sqlite3")
    base = _iso_hour(20)
    selected = _settled_opportunity(db, base, sig="selected", execution_mode="sim")
    other = _settled_opportunity(db, base + timedelta(hours=1), sig="other", execution_mode="sim")
    rows = db.execution_history_for_opportunities([selected], mode="sim")
    assert {int(r["opportunity_id"]) for r in rows} == {selected}
    assert other not in {int(r["opportunity_id"]) for r in rows}


def test_prepare_replay_history_fails_explicitly_above_250k(monkeypatch, tmp_path):
    db = DB(tmp_path / "sentinel.sqlite3")
    same = {"id": 1, "qualification_status": "qualified", "legs_json": "[]"}
    monkeypatch.setattr(db, "replay_opportunity_rows", lambda **kwargs: [same] * 250001)
    with pytest.raises(ReplayHistoryLimitExceeded, match="250,000-opportunity"):
        prepare_replay_history(db)


def test_detailed_history_transparently_unions_archive_and_hot_tail(monkeypatch, tmp_path):
    db = DB(tmp_path / "union.sqlite3")
    root = tmp_path / "archive"
    archived_hour = _iso_hour(3)
    hot_hour = archived_hour + timedelta(hours=1)
    end = hot_hour + timedelta(hours=1)
    _verified_manifest(root, archived_hour)
    _insert_matched(db, hot_hour + timedelta(minutes=5), event_key="event-union")

    archived_row = {
        "id": 7, "observed_at": (archived_hour + timedelta(minutes=10)).isoformat(),
        "event_key": "event-union", "market_name": "Match Odds", "sport": "Tennis",
    }
    monkeypatch.setattr("arbscanner.analytics_store.read_archived_rows", lambda *args, **kwargs: [archived_row])
    result = AnalyticsStore(db, root).detailed_history(
        archived_hour.isoformat(), end.isoformat(), event_key="event-union", limit=20,
    )
    assert result["ok"] is True
    assert result["archive_hours"] == [archived_hour.isoformat()]
    assert result["sqlite_hours"] == [hot_hour.isoformat()]
    assert [r["event_key"] for r in result["rows"]] == ["event-union", "event-union"]


def test_replay_market_evidence_consumer_uses_hot_history_without_provider_access(tmp_path):
    from arbscanner.api import API

    api = API(tmp_path / "replay-evidence.sqlite3")
    hour = _iso_hour(1)
    oid = _settled_opportunity(api.db, hour + timedelta(minutes=5), sig="evidence", execution_mode="sim")
    _insert_matched(api.db, hour + timedelta(minutes=7), event_key="event-evidence")
    _insert_matched(api.db, hour + timedelta(minutes=8), event_key="different-event")
    result = api.replay_market_evidence({
        "opportunity_id": oid,
        "from_utc": hour.isoformat(),
        "to_utc": (hour + timedelta(hours=1)).isoformat(),
        "limit": 100,
    })
    assert result["ok"] is True
    assert result["provider_acquisition_touched"] is False
    assert result["detailed_history_complete"] is True
    assert result["archive_hours"] == []
    assert result["sqlite_hours"] == [hour.isoformat()]
    assert [row["event_key"] for row in result["rows"]] == ["event-evidence"]


def test_frontend_replay_drilldown_and_coverage_note_are_wired():
    from pathlib import Path
    html = (Path(__file__).resolve().parents[1] / "frontend" / "index.html").read_text(encoding="utf-8")
    assert "loadReplayMarketEvidence0911" in html
    assert "replay_market_evidence" in html
    assert "Summary history incomplete" in html
    assert "Detailed history complete via verified archive + hot sqlite" in html


def test_archive_continuity_reports_only_pilot_window(tmp_path):
    from arbscanner.archive import archive_continuity, newest_closed_hour

    through = datetime.fromisoformat(newest_closed_hour())
    first = through - timedelta(hours=1)
    _verified_manifest(tmp_path, first)
    _verified_manifest(tmp_path, through)
    result = archive_continuity(tmp_path, first.isoformat(), through.isoformat())
    assert result["started"] is True
    assert result["expected_hours"] == 2
    assert result["verified_hours"] == 2
    assert result["gaps"] == []
    assert result["complete"] is True
    assert result["latest_verified_hour"] == through.isoformat()


def test_archive_pilot_status_is_read_only_and_reports_continuity(monkeypatch, tmp_path):
    from arbscanner.api import API
    from arbscanner.archive import default_archive_root, newest_closed_hour

    api = API(tmp_path / "pilot-status.sqlite3")
    root = default_archive_root(api.db.path)
    through = datetime.fromisoformat(newest_closed_hour())
    first = through - timedelta(hours=1)
    _verified_manifest(root, first)
    _verified_manifest(root, through)
    save_runtime_state(root, {
        "pilot_started_at": datetime.now(timezone.utc).isoformat(),
        "pilot_start_hour": first.isoformat(),
        "first_success_hour": first.isoformat(),
        "last_success_hour": through.isoformat(),
    })
    save_runtime_gate_report(root, {
        "gate": "0.9.16-real-parquet-runtime",
        "ok": True,
        "status": "PASS",
        "gate_protocol_version": RUNTIME_GATE_PROTOCOL_VERSION,
        "archive_schema_version": ARCHIVE_SCHEMA_VERSION,
        "hour_utc": first.isoformat(),
        "settings_changed": False,
        "pruning_invoked": False,
    })
    api.db.set_setting("config", {
        "matched_market_archive_enabled": True,
        "matched_market_archive_required_before_prune": False,
        "matched_market_retention_hours": 48,
    })
    monkeypatch.setattr("arbscanner.api.duckdb_runtime_status", lambda: {"available": True, "version": "test", "message": None})
    result = api.archive_pilot_status({})
    assert result["ok"] is True
    assert result["enabled"] is True
    assert result["archive_required_before_prune"] is False
    assert result["prune_policy"] == "pilot-soak-no-delete"
    assert result["readiness"] == "HEALTHY"
    assert result["continuity"]["complete"] is True
    assert result["continuity"]["verified_hours"] == 2
    assert result["latest_checksum_verified"] is True
    assert result["live_order_writes"] is False


def test_archive_admin_status_and_runtime_gate_are_present():
    root = Path(__file__).resolve().parents[1]
    html = (root / "frontend" / "index.html").read_text(encoding="utf-8")
    script = (root / "scripts" / "archive_admin.py").read_text(encoding="utf-8")
    worker = (root / "worker.py").read_text(encoding="utf-8")
    assert "archivePilotStatus0911" in html
    assert "loadArchivePilotStatus0911" in html
    assert "archive_pilot_status" in html
    assert "pruning_invoked" in script and "False" in script
    assert "first_success_hour" in worker
    assert "pilot_started_at" in worker


def test_runtime_gate_report_roundtrip_is_archive_sidecar_only(tmp_path):
    root = tmp_path / "archive"
    assert load_runtime_gate_report(root) is None
    saved = save_runtime_gate_report(root, {
        "gate": "0.9.16-real-parquet-runtime",
        "ok": True,
        "status": "PASS",
        "gate_protocol_version": RUNTIME_GATE_PROTOCOL_VERSION,
        "archive_schema_version": ARCHIVE_SCHEMA_VERSION,
        "hour_utc": _iso_hour(2).isoformat(),
        "settings_changed": False,
        "pruning_invoked": False,
    })
    assert saved["published_at"]
    loaded = load_runtime_gate_report(root)
    assert loaded["status"] == "PASS"
    assert loaded["settings_changed"] is False
    assert loaded["pruning_invoked"] is False


def test_archive_pilot_status_surfaces_last_runtime_gate(monkeypatch, tmp_path):
    from arbscanner.api import API
    from arbscanner.archive import default_archive_root

    api = API(tmp_path / "pilot-gate.sqlite3")
    root = default_archive_root(api.db.path)
    hour = _iso_hour(2).isoformat()
    save_runtime_gate_report(root, {
        "gate": "0.9.16-real-parquet-runtime",
        "ok": True,
        "status": "PASS",
        "gate_protocol_version": RUNTIME_GATE_PROTOCOL_VERSION,
        "archive_schema_version": ARCHIVE_SCHEMA_VERSION,
        "hour_utc": hour,
        "settings_changed": False,
        "pruning_invoked": False,
    })
    monkeypatch.setattr("arbscanner.api.duckdb_runtime_status", lambda: {"available": True, "version": "test", "message": None})
    result = api.archive_pilot_status({})
    assert result["ok"] is True
    assert result["runtime_gate_passed"] is True
    assert result["runtime_gate"]["status"] == "PASS"
    assert result["runtime_gate"]["hour_utc"] == hour


def test_runtime_gate_script_preflights_before_archive_publication():
    root = Path(__file__).resolve().parents[1]
    script = (root / "scripts" / "archive_admin.py").read_text(encoding="utf-8")
    html = (root / "frontend" / "index.html").read_text(encoding="utf-8")
    assert "runtime-gate" in script
    assert "parquet_hour_stats" in script
    assert "source_stable_during_gate" in script
    assert "archive_matches_source" in script
    assert "save_runtime_gate_report" in script
    assert "Runtime gate" in html
    assert "Gate hour" in html


def test_runtime_gate_passed_requires_explicit_pass(tmp_path):
    root = tmp_path / "archive"
    assert runtime_gate_passed(root) is False
    save_runtime_gate_report(root, {"ok": False, "status": "REVIEW"})
    assert runtime_gate_passed(root) is False
    save_runtime_gate_report(root, {
        "ok": True, "status": "PASS",
        "gate_protocol_version": RUNTIME_GATE_PROTOCOL_VERSION,
        "archive_schema_version": ARCHIVE_SCHEMA_VERSION,
    })
    assert runtime_gate_passed(root) is True
    save_runtime_gate_report(root, {
        "ok": True, "status": "PASS",
        "gate_protocol_version": RUNTIME_GATE_PROTOCOL_VERSION + 1,
        "archive_schema_version": ARCHIVE_SCHEMA_VERSION,
    })
    assert runtime_gate_passed(root) is False


def test_archive_pilot_status_requires_gate_before_enabled_continuous_pilot(monkeypatch, tmp_path):
    from arbscanner.api import API

    api = API(tmp_path / "pilot-interlock.sqlite3")
    api.db.set_setting("config", {
        "matched_market_archive_enabled": True,
        "matched_market_archive_runtime_gate_required": True,
        "matched_market_archive_required_before_prune": False,
        "matched_market_retention_hours": 48,
    })
    monkeypatch.setattr("arbscanner.api.duckdb_runtime_status", lambda: {"available": True, "version": "test", "message": None})
    result = api.archive_pilot_status({})
    assert result["enabled"] is True
    assert result["runtime_gate_required"] is True
    assert result["runtime_gate_passed"] is False
    assert result["readiness"] == "GATE_REQUIRED"


def test_worker_continuous_archive_has_runtime_gate_interlock():
    root = Path(__file__).resolve().parents[1]
    worker = (root / "worker.py").read_text(encoding="utf-8")
    html = (root / "frontend" / "index.html").read_text(encoding="utf-8")
    assert 'matched_market_archive_runtime_gate_required' in worker
    assert 'runtime_gate_passed(archive_root)' in worker
    assert 'GATE_REQUIRED' in html


def test_next_pilot_archive_hour_closes_oldest_gap_first(tmp_path):
    from arbscanner.archive import next_pilot_archive_hour

    start = _iso_hour(4)
    middle = start + timedelta(hours=1)
    later = start + timedelta(hours=2)
    _verified_manifest(tmp_path, start)
    _verified_manifest(tmp_path, later)
    target = next_pilot_archive_hour(tmp_path, start.isoformat(), later.isoformat())
    assert target == middle.isoformat()
    _verified_manifest(tmp_path, middle)
    assert next_pilot_archive_hour(tmp_path, start.isoformat(), later.isoformat()) is None


def test_archive_pilot_status_reports_catchup_target(monkeypatch, tmp_path):
    from arbscanner.api import API
    from arbscanner.archive import default_archive_root

    api = API(tmp_path / "pilot-catchup.sqlite3")
    root = default_archive_root(api.db.path)
    start = _iso_hour(3)
    later = start + timedelta(hours=2)
    _verified_manifest(root, start)
    _verified_manifest(root, later)
    save_runtime_state(root, {
        "pilot_started_at": datetime.now(timezone.utc).isoformat(),
        "pilot_start_hour": start.isoformat(),
        "first_success_hour": start.isoformat(),
        "last_success_hour": later.isoformat(),
    })
    save_runtime_gate_report(root, {
        "ok": True, "status": "PASS",
        "gate_protocol_version": RUNTIME_GATE_PROTOCOL_VERSION,
        "archive_schema_version": ARCHIVE_SCHEMA_VERSION,
    })
    api.db.set_setting("config", {
        "matched_market_archive_enabled": True,
        "matched_market_archive_runtime_gate_required": True,
        "matched_market_archive_required_before_prune": False,
        "matched_market_retention_hours": 48,
    })
    monkeypatch.setattr("arbscanner.api.duckdb_runtime_status", lambda: {"available": True, "version": "test", "message": None})
    # Freeze latest closed hour to the end of our synthetic pilot window.
    monkeypatch.setattr("arbscanner.api.newest_closed_hour", lambda: later.isoformat())
    result = api.archive_pilot_status({})
    assert result["readiness"] == "CATCHING_UP"
    assert result["pending_archive_hours"] == 1
    assert result["next_target_hour"] == (start + timedelta(hours=1)).isoformat()
    assert result["continuity"]["complete"] is False


def test_archive_admin_uses_protocol_bound_runtime_gate(monkeypatch, tmp_path):
    from arbscanner.api import API
    from arbscanner.archive import default_archive_root

    api = API(tmp_path / "pilot-old-gate.sqlite3")
    root = default_archive_root(api.db.path)
    save_runtime_gate_report(root, {
        "ok": True, "status": "PASS",
        "gate_protocol_version": RUNTIME_GATE_PROTOCOL_VERSION + 1,
        "archive_schema_version": ARCHIVE_SCHEMA_VERSION,
    })
    api.db.set_setting("config", {
        "matched_market_archive_enabled": True,
        "matched_market_archive_runtime_gate_required": True,
        "matched_market_archive_required_before_prune": False,
    })
    monkeypatch.setattr("arbscanner.api.duckdb_runtime_status", lambda: {"available": True, "version": "test", "message": None})
    result = api.archive_pilot_status({})
    assert result["runtime_gate"]["status"] == "PASS"
    assert result["runtime_gate_passed"] is False
    assert result["readiness"] == "GATE_REQUIRED"


def test_continuous_pilot_control_is_explicit_and_pruning_safe():
    root = Path(__file__).resolve().parents[1]
    script = (root / "scripts" / "archive_admin.py").read_text(encoding="utf-8")
    worker = (root / "worker.py").read_text(encoding="utf-8")
    html = (root / "frontend" / "index.html").read_text(encoding="utf-8")
    assert "runtime_gate_passed(root)" in script
    assert "PRUNE_GATE_MUST_BE_OFF" in script
    assert 'cfg["matched_market_archive_enabled"] = True' in script
    assert 'cfg["matched_market_archive_runtime_gate_required"] = True' in script
    assert "ENABLE-ARCHIVE-GATED-PRUNING" in script
    assert 'cfg["matched_market_archive_required_before_prune"] = True' in script
    assert "next_pilot_archive_hour" in worker
    assert "matched_market_archive_catchup_delay_seconds" in worker
    assert "CATCHING_UP" in html
    assert "Next archive target" in html


def test_continuous_pilot_controller_enable_path_changes_only_pilot_flags(monkeypatch, tmp_path):
    import importlib.util
    from arbscanner.archive import default_archive_root, load_runtime_state

    root_dir = Path(__file__).resolve().parents[1]
    script_path = root_dir / "scripts" / "archive_admin.py"
    spec = importlib.util.spec_from_file_location("archive_admin_test", script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)

    db_path = tmp_path / "pilot-enable.sqlite3"
    db = DB(db_path)
    db.set_setting("config", {
        "matched_market_archive_enabled": False,
        "matched_market_archive_required_before_prune": False,
        "unrelated_setting": "preserve-me",
    })
    with db.lock:
        db.conn.close()
    archive_root = default_archive_root(db_path)
    save_runtime_gate_report(archive_root, {
        "ok": True, "status": "PASS",
        "gate_protocol_version": RUNTIME_GATE_PROTOCOL_VERSION,
        "archive_schema_version": ARCHIVE_SCHEMA_VERSION,
    })
    monkeypatch.setattr(module, "duckdb_runtime_status", lambda: {"available": True, "version": "test", "message": None})
    rc = module.main(["--db", str(db_path), "pilot", "enable"])
    assert rc == 0

    check = DB(db_path)
    cfg = check.get_setting("config", {}) or {}
    assert cfg["matched_market_archive_enabled"] is True
    assert cfg["matched_market_archive_runtime_gate_required"] is True
    assert cfg["matched_market_archive_required_before_prune"] is False
    assert cfg["unrelated_setting"] == "preserve-me"
    with check.lock:
        check.conn.close()
    runtime = load_runtime_state(archive_root)
    assert runtime.get("pilot_start_hour")
    assert runtime.get("pilot_started_at")
    assert runtime.get("pilot_armed_at")


def test_temporary_archive_scripts_and_standalone_runner_are_removed():
    root = Path(__file__).resolve().parents[1]
    assert (root / "scripts" / "archive_admin.py").exists()
    for rel in (
        "scripts/archive_runtime_gate.py",
        "scripts/archive_continuous_pilot.py",
        "scripts/archive_live_pilot.py",
        "scripts/archive_matched_markets.py",
        "RUN_CONTINUOUS_ARCHIVE_PILOT.command",
        "STANDALONE_LIVE_PILOT_README.txt",
    ):
        assert not (root / rel).exists()
