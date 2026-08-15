from pathlib import Path
from datetime import datetime, timezone

from arbscanner.api import API
from arbscanner.db import DB
from arbscanner.replay import replay_analysis


def add_opp(db: DB, event_key="alpha v beta", event_name="Alpha v Beta", detected="2026-08-09T10:00:00+00:00"):
    legs = [
        {"exchange":"Matchbook","selection":"Alpha","odds":2.2,"liquidity":500.0,"commission_pct":0.0,"sport":"Football"},
        {"exchange":"Betfair delayed","selection":"Beta","odds":2.2,"liquidity":500.0,"commission_pct":0.0,"sport":"Football"},
    ]
    oid=db.add_opportunity(event_key,event_name,"2026-08-09T12:00:00+00:00","Match Winner",9.0,10.0,legs,[],0.99,f"sig-{detected}",strategy="two-way",sport="Football")
    # Tests need deterministic capture time for release-policy comparisons.
    db.conn.execute("UPDATE opportunities SET detected_at=? WHERE id=?",(detected,oid)); db.conn.commit()
    return oid


def test_stored_results_are_independent_of_scenario_and_group_duplicate_captures(tmp_path: Path):
    db=DB(tmp_path/"activity.sqlite3")
    a=add_opp(db,detected="2026-08-09T10:00:00+00:00")
    b=add_opp(db,detected="2026-08-09T10:05:00+00:00")
    db.settle(a,"Alpha")
    db.settle(b,"Alpha")
    rows=db.stored_result_history()
    assert len(rows)==1
    assert rows[0]["outcome"]=="Alpha"
    assert rows[0]["opportunity_count"]==2
    assert rows[0]["conflict"] is False


def test_activity_api_exposes_shared_monitor_timing_live_journal(tmp_path: Path):
    api=API(tmp_path/"activity-api.sqlite3")
    oid=add_opp(api.db)
    api.db.add_execution_run(oid,"monitor_timing","captured_stress","STRESS_TESTED",deployed=100,expected_profit=5,captured_profit=3,max_unhedged_exposure=25,details={"timed_rechecks":False})
    result=api.activity_analytics({"mode":"all"})
    assert result["ok"] is True
    assert result["execution_counts"]["monitor"]["count"]==1
    assert result["execution_counts"]["live"]["count"]==0
    assert result["executions"][0]["mode"]=="sim"
    assert result["executions"][0]["execution_leakage"]==2.0


def test_replay_release_policy_can_avoid_worker_delay_inflating_capital_lock(tmp_path: Path):
    db=DB(tmp_path/"release.sqlite3")
    oid=add_opp(db,detected="2026-08-09T10:00:00+00:00")
    # Direct settlement row with deliberately late observed time to model a worker that was offline.
    db.conn.execute("INSERT INTO settlements(opportunity_id,settled_at,outcome,simulated_pnl,notes) VALUES(?,?,?,?,?)",(oid,"2026-08-10T10:00:00+00:00","Alpha",None,"late poll")); db.conn.commit()
    estimated=replay_analysis(db,100.0,min_profit=0,min_deployed_roi_pct=0,release_policy="estimated_close")
    observed=replay_analysis(db,100.0,min_profit=0,min_deployed_roi_pct=0,release_policy="observed")
    assert estimated["filters"]["release_policy"]=="estimated_close"
    assert estimated["counts"]["release_estimated"]==1
    assert observed["counts"]["release_observed"]==1
    assert estimated["events"][0]["release_at"] < observed["events"][0]["release_at"]
