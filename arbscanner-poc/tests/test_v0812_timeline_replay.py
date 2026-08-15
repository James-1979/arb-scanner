from dataclasses import asdict
from pathlib import Path

from arbscanner.api import API
from arbscanner.models import Leg

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "frontend" / "index.html").read_text()


def _opp(api: API) -> int:
    legs = [
        Leg("Betfair delayed", "Alpha", 2.2, 500.0, 6.0, event_id="bf-e", market_id="bf-m", selection_id="a"),
        Leg("Matchbook", "Beta", 2.2, 500.0, 2.0, event_id="mb-e", market_id="mb-m", selection_id="b"),
    ]
    return api.db.add_opportunity(
        "alpha v beta", "Alpha v Beta", "2026-08-11T08:00:00+00:00", "Match Winner",
        9.0, 1.0, [asdict(x) for x in legs], [], 0.99, "v0812-replay", strategy="two-way", sport="Football",
    )


def test_timeline_replay_is_period_review_workspace():
    assert "PoC 0.9.36" in HTML
    assert '<h2 style="margin:0">Period Review</h2>' in HTML
    for element_id in (
        "timelineReplayPeriod", "timelineReplayPhase", "timelineReplaySport", "timelineReplayShow",
        "timelineReplaySearch", "timelineReplayPlay", "timelineReplayScrubber", "timelineReplayCanvas",
        "timelineReplayPositions", "timelineReplayWon", "timelineReplayLost", "timelineReplayProfit",
        "timelineReplayDeployed", "timelineReplayRoi", "timelineReplayBest", "timelineReplayWorst",
        "timelineReplayHedges", "timelineReplayEventDetail",
    ):
        assert f'id="{element_id}"' in HTML
    assert "function loadTimelineReplay(" in HTML
    assert "function buildTimelineReplayPositions(" in HTML
    assert "function timelineReplayToggle(" in HTML
    assert "function timelineReplayUpdateAt(" in HTML
    assert 'id="timelineReplayPnl"' not in HTML
    assert "What happened when" in HTML
    assert "EMERGENCY HEDGE" in HTML


def test_individual_forensic_replay_moved_into_execution_analysis():
    execution = HTML.split('<div class="analytics-pane" data-analytics-pane="execution">', 1)[1].split('<div class="analytics-pane" data-analytics-pane="market">', 1)[0]
    replay = HTML.split('<div class="analytics-pane" data-analytics-pane="replay">', 1)[1].split('<div class="analytics-pane" data-analytics-pane="scenarios">', 1)[0]
    assert 'id="executionDetailCard"' in execution
    assert 'id="executionDetail"' in execution
    assert "INDIVIDUAL EXECUTION" in execution
    assert "openExecutionDetailForOpportunity" in HTML
    assert "renderExecutionDetailTimeline" in HTML
    assert ">Detail</button>" in HTML
    assert 'id="executionDetailCard"' not in replay
    assert "Stored execution timeline" in HTML


def test_timeline_range_can_include_settlement_when_trade_started_before_window(tmp_path: Path):
    api = API(tmp_path / "timeline.sqlite3")
    oid = _opp(api)
    api.db.add_execution_run(
        oid,
        mode="monitor",
        execution_type="modeled_monitor",
        state="MONITOR_SETTLED",
        deployed=100.0,
        expected_profit=2.0,
        captured_profit=3.0,
        details={"monitor_position_opened": True, "monitor_stream": "pre_match", "execution_result": {"events": [], "fills": []}},
        started_at="2026-08-10T20:00:00+00:00",
        finished_at="2026-08-10T20:00:01+00:00",
    )
    api.db.conn.execute(
        "INSERT INTO settlements(opportunity_id,settled_at,outcome,simulated_pnl,notes) VALUES(?,?,?,?,?)",
        (oid, "2026-08-11T07:30:00+00:00", "Alpha", 3.0, "timeline test"),
    )
    api.db.conn.commit()

    normal = api.activity_analytics({
        "from_utc": "2026-08-11T07:00:00+00:00",
        "to_utc": "2026-08-11T08:00:00+00:00",
        "mode": "monitor",
        "limit": 100,
    })
    timeline = api.activity_analytics({
        "from_utc": "2026-08-11T07:00:00+00:00",
        "to_utc": "2026-08-11T08:00:00+00:00",
        "mode": "monitor",
        "timeline_range": True,
        "limit": 100,
    })
    assert normal["executions"] == []
    assert len(timeline["executions"]) == 1
    assert timeline["executions"][0]["settled_at"] == "2026-08-11T07:30:00+00:00"


def test_scenarios_remain_what_if_workspace():
    assert 'data-analytics-pane="scenarios"' in HTML
    assert '<h2 style="margin:0">Historical Scenario</h2>' in HTML
    assert 'id="replayBetfairBalance"' in HTML
    assert "Model alternative outcomes using recorded history" in HTML
