"""Permanent regression coverage for fixes that previously shipped as hotfix files."""
from pathlib import Path
import shutil
import sqlite3
import subprocess
import time

import pytest

from arbscanner.api import API
from arbscanner.db import DB

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")


def test_analysis_runtime_state_is_initialized_before_first_view_load():
    script_head = HTML.split("<script>", 1)[1].split("function utilisationClass", 1)[0]
    for token in (
        "executionAnalysisRows=[]", "executionActionFilters=[]", "executionDetailSelectedId=null",
        "marketAnalysisRowsCache=[]", "marketAnalysisHoursCache=[]", "marketAnalysisReasonsCache=[]",
        "marketSortKey='activity'", "marketSortDir='desc'", "timelineReplayRows=[]",
        "timelineReplayPositions=[]", "timelineReplayRange=null", "timelineReplayProgress=0",
        "timelineReplaySelectedId=null", "timelineReplayPlaying=false", "timelineReplayLastFrame=0",
        "timelineReplayTimer=null",
    ):
        assert token in script_head


def test_timeline_replay_has_bounds_helper_for_every_period_option():
    assert "function timelineReplayBounds()" in HTML
    helper = HTML.split("function timelineReplayBounds(){", 1)[1].split("async function loadTimelineReplay()", 1)[0]
    for token in ("'today'", "'24h'", "'7d'", "'custom'"):
        assert token in helper
    assert "'previous_day'" not in helper
    assert "/^(\\d+)(h|d)$/" not in helper
    assert "return {from,to}" in helper


def test_analysis_loaders_no_longer_depend_on_undeclared_runtime_state():
    assert HTML.index("executionActionFilters=[]") < HTML.index("async function loadExecutionsView()")
    assert HTML.index("marketSortKey='activity'") < HTML.index("async function loadMarketAnalysis()")
    assert HTML.index("timelineReplayTimer=null") < HTML.index("async function loadTimelineReplay()")
    assert HTML.index("function timelineReplayBounds()") < HTML.index("async function loadTimelineReplay()")


def test_main_frontend_javascript_parses_as_a_complete_script(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    scripts = HTML.split("<script>")
    assert len(scripts) >= 3
    main_script = scripts[-1].split("</script>", 1)[0]
    js = tmp_path / "arbscanner-main.js"
    js.write_text(main_script)
    result = subprocess.run([node, "--check", str(js)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_current_database_opens_read_only_while_worker_holds_write_lock(tmp_path):
    path = tmp_path / "locked.sqlite3"
    seed = DB(path)
    seed.set_setting("mode", "monitor")
    seed.ensure_monitor_streams({"betfair": 250.0, "matchbook": 250.0})
    seed.conn.close()
    writer = sqlite3.connect(path, timeout=1.0)
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("BEGIN IMMEDIATE")
    writer.execute("UPDATE settings SET value=value WHERE key='mode'")
    started = time.monotonic()
    reader = DB(path)
    reader.set_setting("mode", "monitor")
    reader.ensure_monitor_streams({"betfair": 250.0, "matchbook": 250.0})
    assert reader.get_setting("mode") == "monitor"
    wallets = reader.monitor_wallet_snapshot(stream="pre_match")
    assert wallets["betfair"]["equity"] + wallets["matchbook"]["equity"] == 500.0
    assert time.monotonic() - started < 2.0
    assert reader.conn.execute("PRAGMA busy_timeout").fetchone()[0] == 30000
    reader.conn.close(); writer.rollback(); writer.close()


def test_failed_transaction_can_be_explicitly_released(tmp_path):
    db = DB(tmp_path / "rollback.sqlite3")
    db.conn.execute("INSERT INTO settings(key,value) VALUES('transient','1')")
    assert db.conn.in_transaction is True
    assert db.rollback_if_needed() is True
    assert db.conn.in_transaction is False
    assert db.get_setting("transient") is None


def test_racing_monitor_rows_have_selectable_state_and_detail_affordance():
    for token in (
        "racingMonitorSelectedKey", "function selectRacingMonitorRow(", "data-racing-monitor-index=",
        "addEventListener('click'", "addEventListener('keydown'", "Click / Enter for detail",
        "racing-monitor-row.is-selected", "Pricing & runner detail", "MB raw BACK", "MB raw LAY",
    ):
        assert token in HTML
    assert 'onclick="racingMonitorDetail(' not in HTML


def test_racing_monitor_api_attaches_pricing_detail_for_matched_discovery_row(tmp_path, monkeypatch):
    api = API(db_path=tmp_path / "monitor-detail.sqlite3")
    api.db.set_setting("racing_discovery_latest", {
        "observed_at": "2026-08-11T17:40:00+00:00",
        "summary": {"matched": 1, "by_exchange": {"Matchbook": 1, "Betfair delayed": 1}},
        "rows": [{"exchange": "Matchbook", "market_id": "mb-1", "event_id": "mb-e1", "event_name": "Towcester",
                  "event_start": "2026-08-11T18:42:00+00:00", "race_track": "towcester", "runner_count": 6,
                  "match_status": "matched", "matched_event_key": "towcester-win-1842",
                  "counterpart": {"exchange": "Betfair delayed", "market_id": "bf-1"}}],
    })
    matched_detail = {
        "event_key": "towcester-win-1842", "event_name": "Towcester", "event_start": "2026-08-11T18:42:00+00:00",
        "runner_count": 6, "book_analysis": {"runner_prices": [{"trap_number": 1, "display": "Dog One"}],
        "matchbook_side_audit": {"current_interpretation": "back", "raw_books_pct": {"back": 123.4, "lay": 97.8}}},
        "exchange_books_pct": {"Betfair delayed": 108.0, "Matchbook": 123.4}, "best_combined_book_pct": 104.0,
        "selected_cross_exchange_book_pct": 105.0, "best_price_book_pct": 105.0, "theoretical_edge_pct": -5.0,
        "gross_roi_pct": -4.8, "commission_impact_pct": 0.2, "net_roi_pct": -5.2,
        "selection_basis": "best_roi_non_positive", "reference_deployed": 100.0, "reference_profit": -5.2,
        "reference_stakes": [], "time_to_off_seconds": 120, "data_quality": {"band": "Excellent"},
        "status": "racing_research_only",
    }
    monkeypatch.setattr(api, "racing_overview", lambda data=None: {"rows": [matched_detail]})
    row = api.racing_monitor({})["rows"][0]
    assert row["price_state"] == "ready"
    assert row["net_roi_pct"] == -5.2
    assert row["pricing_detail"]["book_analysis"]["matchbook_side_audit"]["raw_books_pct"]["lay"] == 97.8
    assert row["pricing_detail"]["selection_basis"] == "best_roi_non_positive"
    assert row["pricing_detail"]["selected_cross_exchange_book_pct"] == 105.0
