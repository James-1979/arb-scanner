from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from arbscanner import __version__
from arbscanner.api import API
from arbscanner.db import DB
from arbscanner.models import Leg


ROOT = Path(__file__).parents[1]
HTML = (ROOT / "frontend" / "index.html").read_text()


def _opportunity(db: DB, stamp: str, event: str = "Alpha v Beta", sport: str = "Football") -> int:
    legs = [
        asdict(Leg("Betfair delayed", "Alpha", 2.2, 100, 0, event_id="bf-e", market_id="bf-m", selection_id="a")),
        asdict(Leg("Matchbook", "Beta", 2.2, 100, 0, event_id="mb-e", market_id="mb-m", selection_id="b")),
    ]
    oid = db.add_opportunity(event.lower(), event, "2026-08-11T12:00:00+00:00", "Match Winner", 9, 9,
                             legs, [], 0.99, f"sig-{stamp}-{event}", strategy="two-way", sport=sport)
    db.conn.execute("UPDATE opportunities SET detected_at=? WHERE id=?", (stamp, oid))
    db.conn.commit()
    return oid


def test_version_and_visible_release_metadata_are_current():
    assert __version__ == "0.9.36"
    assert "PoC 0.9.36" in HTML
    assert '"version": "0.9.36"' in (ROOT / "arbscanner" / "api.py").read_text()


def test_startup_recent_rows_are_bounded_and_newest_first(tmp_path):
    api = API(tmp_path / "fast-state.sqlite3")
    for i in range(20):
        _opportunity(api.db, f"2026-08-11T09:{i:02d}:00+00:00", event=f"Event {i}")
    state = api.get_state()
    cards = state["dashboard"]["recent_cards"]
    assert len(cards) == 12
    assert cards[0]["event_name"] == "Event 19"
    assert cards[-1]["event_name"] == "Event 8"


def test_activity_analytics_can_omit_heavy_sections(tmp_path):
    api = API(tmp_path / "selective.sqlite3")
    oid = _opportunity(api.db, "2026-08-11T09:00:00+00:00")
    api.db.add_execution_run(oid, "monitor", "monitor", "COMPLETE", started_at="2026-08-11T09:01:00+00:00")
    api.db.settle(oid, "Alpha")
    # Keep the fixture deterministic across wall-clock dates: the selected Results
    # window is 11 Aug, so pin settlement observation time inside that window.
    api.db.conn.execute("UPDATE settlements SET settled_at=? WHERE opportunity_id=?", ("2026-08-11T09:30:00+00:00", oid))
    api.db.conn.commit()

    executions = api.activity_analytics({
        "from_utc": "2026-08-11T00:00:00+00:00", "to_utc": "2026-08-12T00:00:00+00:00",
        "include_results": False, "include_executions": True, "include_metrics": False, "include_all_time": False,
    })
    assert executions["ok"] is True
    assert len(executions["executions"]) == 1
    assert executions["results"] == []

    results = api.activity_analytics({
        "from_utc": "2026-08-11T00:00:00+00:00", "to_utc": "2026-08-12T00:00:00+00:00",
        "include_results": True, "include_executions": False, "include_metrics": False, "include_all_time": False,
    })
    assert results["executions"] == []
    assert len(results["results"]) == 1


def test_execution_history_filters_in_sql_and_monitor_includes_legacy_aliases(tmp_path):
    db = DB(tmp_path / "filter.sqlite3")
    good = _opportunity(db, "2026-08-11T08:00:00+00:00", "Alpha v Beta", "Football")
    old = _opportunity(db, "2026-07-01T08:00:00+00:00", "Old v Match", "Tennis")
    db.add_execution_run(good, "monitor_timing", "legacy", "COMPLETE", started_at="2026-08-11T08:10:00+00:00")
    db.add_execution_run(old, "monitor", "monitor", "COMPLETE", started_at="2026-07-01T08:10:00+00:00")
    rows = db.execution_history(
        mode="monitor", from_utc="2026-08-11T00:00:00+00:00", to_utc="2026-08-12T00:00:00+00:00",
        sport="Football", market="Winner", search="alpha",
    )
    assert len(rows) == 1
    assert rows[0]["opportunity_id"] == good


def test_v0817_read_indexes_exist(tmp_path):
    db = DB(tmp_path / "indexes.sqlite3")
    indexes = {r[0] for r in db.conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
    required = {
        "idx_opportunities_detected", "idx_opportunities_market_analysis", "idx_settlements_time",
        "idx_execution_runs_time", "idx_execution_runs_time_mode", "idx_monitor_positions_execution_run",
        "idx_monitor_positions_opened", "idx_monitor_positions_settled", "idx_matched_markets_analysis",
        "idx_scan_runs_time_kind",
    }
    assert required <= indexes


def test_legacy_operating_modes_alias_to_monitor_and_live_stays_locked(tmp_path):
    api = API(tmp_path / "modes.sqlite3")
    for alias in ("watch", "monitor_timing", "paper", "simulate", "find"):
        changed = api.set_operating_mode({"mode": alias})
        assert changed["ok"] is True
        assert changed["state"]["settings"]["mode"] == "sim"
    live = api.set_operating_mode({"mode": "live"})
    assert live["ok"] is False
    assert "locked" in live["message"].lower()
    assert live["state"]["settings"]["mode"] == "sim"
