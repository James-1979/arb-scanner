from datetime import datetime, timedelta, timezone
from pathlib import Path

from arbscanner.db import DB
from arbscanner.engine import simulate_equal_return
from arbscanner.models import Leg, Scenario
from arbscanner.quality import assess_data_quality, quality_profile


def test_simulation_exposes_pounds_before_and_after_commission_and_limiting_leg():
    legs = [
        Leg("Matchbook", "Home", 2.2, 5.0, commission_pct=2.0),
        Leg("Betfair delayed", "Away", 2.2, 100.0, commission_pct=5.0),
    ]
    sim = simulate_equal_return(legs, Scenario("£500", 500))
    assert sim["executable"]
    assert sim["gross_profit"] > sim["expected_profit"]
    assert sim["commission_cost"] > 0
    assert sim["limiting_leg"]["selection"] == "Home"
    assert sim["limited_by"] == "liquidity"


def test_data_quality_calls_out_delayed_feed_and_old_local_capture():
    old = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()
    legs = [
        Leg("Betfair delayed", "Home", 2.1, 100, captured_at=old, source_latency_ms=6000,
            commission_pct=2, commission_source="configured fallback (Betfair API market rate unavailable)"),
        Leg("Matchbook", "Away", 2.1, 100, captured_at=old, source_latency_ms=100),
    ]
    dq = assess_data_quality(legs, 0.75, stale_after_seconds=90)
    assert dq["trust_band"] == "Low"
    assert dq["uses_delayed_feed"]
    assert dq["fallback_commission"]
    assert len(dq["warnings"]) >= 4


def test_quality_score_is_reduced_by_data_confidence_penalty():
    legs = [Leg("A", "x", 2.2, 100), Leg("B", "y", 2.2, 100)]
    sim = simulate_equal_return(legs, Scenario("£100", 100))
    base = quality_profile(sim, 1.0, 100)
    penalized = quality_profile(sim, 1.0, 100, {"penalty_points": 20, "warnings": ["test"]})
    assert penalized["quality_score"] == max(0, base["quality_score"] - 20)
    assert penalized["bankroll_after"] == round(100 + sim["expected_profit"], 4)


def test_db_lifecycle_health_summary_and_backup(tmp_path: Path):
    db = DB(tmp_path / "arb.sqlite3")
    scan = db.start_scan()
    db.upsert_track("track", scan, "e", "Event", "Match Odds", "1x2", 1.0, 0.2, 100, 2, 65, "Strong", 500, "recommended", "ok")
    db.finish_scan(scan, markets_seen=2, matches_seen=1, opportunities_found=1, statuses=[{"ok": True}])
    obs = db.track_observations_for("track")
    assert len(obs) == 1
    health = db.scanner_health()
    assert health["last_successful_scan"]["id"] == scan
    summary = db.observation_summary(24)
    assert summary["scans"] == 1
    destination = tmp_path / "backup" / "copy.sqlite3"
    db.backup_to(destination)
    assert destination.exists() and destination.stat().st_size > 0
