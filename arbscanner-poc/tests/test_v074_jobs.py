from datetime import datetime, timedelta, timezone
from pathlib import Path

from arbscanner.api import API
from arbscanner.db import DB


class FakeService:
    def __init__(self):
        self.loaded = False
        self.installed = False

    def status(self):
        return {"installed": self.installed, "loaded": self.loaded, "worker_path": "/tmp/fake-worker"}

    def install(self):
        self.installed = True
        self.loaded = True
        return {"ok": True, "message": "loaded", **self.status()}

    def uninstall(self):
        self.installed = False
        self.loaded = False
        return {"ok": True, "message": "removed", **self.status()}


def test_manual_activation_creates_frozen_job_and_stop_keeps_history(tmp_path: Path):
    api = API(tmp_path / "jobs.sqlite3")
    api.service = FakeService()
    started = api.activate_job({
        "mode": "monitor_timing",
        "name": "Evening football",
        "strategy": {
            "minimum_profit": 2.5,
            "minimum_net_roi_pct": 1.75,
            "execution_max_stake": 40,
            "quality_reference_bankroll": 500,
        },
    })
    assert started["ok"] is True
    current = started["state"]["jobs"]["current"]
    assert current["name"] == "Evening football"
    assert current["mode"] == "sim"
    assert current["strategy"]["minimum_profit"] == 2.5
    assert current["strategy"]["execution_max_stake"] == 40
    assert started["state"]["automation"]["running"] is True

    stopped = api.stop_job()
    assert stopped["ok"] is True
    assert stopped["state"]["jobs"]["current"] is None
    history = api.jobs_history({"limit": 10})["jobs"]
    assert history[0]["name"] == "Evening football"
    assert history[0]["status"] == "stopped"
    assert history[0]["finished_at"]


def test_scheduled_template_spawns_job_and_advances_daily_schedule(tmp_path: Path):
    db = DB(tmp_path / "schedule.sqlite3")
    first = datetime.now(timezone.utc) - timedelta(minutes=1)
    sid = db.create_schedule(
        "Morning scan", "watch", {"minimum_profit": 1.0}, first.isoformat(), 60, "daily", "Europe/London"
    )
    jid = db.spawn_due_schedule(datetime.now(timezone.utc))
    assert jid is not None
    active = db.active_job()
    assert active["id"] == jid
    assert active["schedule_id"] == sid
    assert active["trigger_type"] == "scheduled"
    schedules = db.schedules()
    row = next(x for x in schedules if x["id"] == sid)
    assert row["enabled"] == 1
    assert row["next_run_at"] > first.isoformat()


def test_job_stats_are_scoped_to_job_id(tmp_path: Path):
    db = DB(tmp_path / "stats.sqlite3")
    jid = db.create_job("MonitorTiming run", "monitor_timing", {"quality_reference_bankroll": 500}, start_now=True)
    scan = db.start_scan(job_id=jid)
    db.finish_scan(scan, markets_seen=20, matches_seen=4, opportunities_found=1)
    legs = [
        {"exchange": "Betfair delayed", "selection": "Home", "odds": 2.2, "liquidity": 100, "commission_pct": 0},
        {"exchange": "Matchbook", "selection": "Away", "odds": 2.2, "liquidity": 100, "commission_pct": 0},
    ]
    oid = db.add_opportunity("a v b", "A v B", None, "Winner", 9, 9, legs, [], 0.9, "sig", strategy="two-way", job_id=jid)
    db.add_scenario_run(oid, "£500", 500, 50, 5, 10, "liquidity", [], {"Home": 5, "Away": 5})
    db.add_execution_run(oid, "monitor_timing", "captured_stress", "STRESS_TESTED", expected_profit=5, captured_profit=4.5, job_id=jid)
    row = db.job_history(1)[0]
    assert row["stats"]["scans"] == 1
    assert row["stats"]["markets"] == 20
    assert row["stats"]["opportunities"] == 1
    assert row["stats"]["executions"] == 1
    assert row["stats"]["potential_profit"] == 5
    assert row["stats"]["captured_profit"] == 4.5
