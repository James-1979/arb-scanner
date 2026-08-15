from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from arbscanner.api import API, OPERATING_MODES

ROOT = Path(__file__).resolve().parents[1]


def load_probe_module():
    path = ROOT / "scripts" / "refactor_probe.py"
    spec = importlib.util.spec_from_file_location("stage02_refactor_probe", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_live_execution_remains_centrally_locked(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    api = API(tmp_path / "live-lock.sqlite3")
    assert OPERATING_MODES["live"]["available"] is False
    result = api.set_operating_mode({"mode": "live"})
    assert result["ok"] is False
    assert "locked" in result["message"].lower()
    result = api.activate_job({"mode": "live", "name": "must-not-run"})
    assert result["ok"] is False
    assert "locked" in result["message"].lower()
    result = api.schedule_job({"mode": "live", "start_at": "2099-01-01T00:00:00+00:00"})
    assert result["ok"] is False
    assert "locked" in result["message"].lower()
    assert api.db.live_persistence_counts()["live_order_attempts"] == 0


def test_live_lifecycle_projections_do_not_fallback_to_sim_rows(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    api = API(tmp_path / "mode-ownership.sqlite3")
    marker = "STAGE02_SIM_ONLY_MARKER"
    now = "2026-08-15T12:00:00+00:00"
    with api.db.lock:
        cur = api.db.conn.execute(
            """INSERT INTO opportunities(
                 detected_at,event_key,event_name,event_start,market_name,edge_pct,expected_roi_pct,legs_json,
                 status,strategy,sport,section,qualification_status,is_demo
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,0)""",
            (now, marker, marker, now, "Match Odds", 2.0, 1.5, "[]", "settled", "1x2", "Football", "sports", "qualified"),
        )
        opportunity_id = int(cur.lastrowid)
        api.db.conn.execute(
            "INSERT INTO settlements(opportunity_id,settled_at,outcome,simulated_pnl,notes) VALUES(?,?,?,?,?)",
            (opportunity_id, now, "Home", 12.34, marker),
        )
        api.db.conn.commit()
    for method in ("live_results", "live_replay", "live_execution_activity", "live_performance"):
        payload = getattr(api, method)({"mode": "live"})
        assert payload.get("ok") is True
        assert marker not in json.dumps(payload, sort_keys=True)
    assert api.live_results({"mode": "live"})["count"] == 0
    assert api.live_replay({"mode": "live"})["count"] == 0


def test_frontend_keeps_mode_token_stale_response_gate():
    text = (ROOT / "frontend" / "index.html").read_text()
    assert "function modeRequestToken" in text
    assert "function modeRequestCurrent" in text
    assert "stale_context:true" in text
    assert "if(!modeRequestCurrent(token,true))return {ok:false,stale_context:true}" in text
    assert "clearLiveDashboardEconomicState" in text
    assert "loadLiveDashboard=async function" in text


def test_sql_write_classifier_marks_authority_audit_and_derived_tables():
    probe = load_probe_module()
    cases = {
        "UPDATE monitor_positions SET status='SETTLED' WHERE id=1": ("monitor_positions", "authority"),
        "INSERT INTO live_account_audit(event_type,status,occurred_at) VALUES('x','OK','y')": ("live_account_audit", "audit"),
        "INSERT OR REPLACE INTO exchange_market_discovery_state(hour_utc,built_at,completeness) VALUES('a','b','c')": ("exchange_market_discovery_state", "derived"),
    }
    for statement, expected in cases.items():
        table = probe.target_table(statement)
        assert table == expected[0]
        assert probe.classify_table(table) == expected[1]


def test_authority_integrity_observer_is_read_only(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    db_path = tmp_path / "integrity.sqlite3"
    api = API(db_path)
    before = api.db.conn.execute("SELECT COUNT(*) FROM opportunities").fetchone()[0]
    api.db.conn.close()
    probe = load_probe_module()
    report = probe.authority_integrity(db_path)
    assert report["ok"] is True
    conn = sqlite3.connect(db_path)
    after = conn.execute("SELECT COUNT(*) FROM opportunities").fetchone()[0]
    conn.close()
    assert before == after


def test_same_db_comparator_smoke(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    db_path = tmp_path / "fixture.sqlite3"
    api = API(db_path)
    api.db.conn.close()
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"schema_version": 1, "projections": [
        {"id": "live_replay", "method": "live_replay", "data": {"mode": "live"}}
    ]}))
    output = tmp_path / "comparison.json"
    command = [
        sys.executable, str(ROOT / "scripts" / "compare_refactor_builds.py"),
        "--reference-root", str(ROOT), "--candidate-root", str(ROOT),
        "--db", str(db_path), "--manifest", str(manifest), "--output", str(output),
        "--anchor", "2026-08-15T13:00:00+00:00"
    ]
    env = dict(os.environ)
    proc = subprocess.run(command, cwd=ROOT, env=env, text=True, capture_output=True)
    assert proc.returncode == 0, proc.stderr or proc.stdout
    report = json.loads(output.read_text())
    assert report["summary"] == {"blockers": 0, "pass": 1, "projections": 1, "warnings": 0}
