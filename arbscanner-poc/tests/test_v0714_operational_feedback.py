from datetime import datetime, timezone
from pathlib import Path

from arbscanner.api import API


def _snapshot(api, exchange):
    api.db.add_snapshot(
        captured_at=datetime.now(timezone.utc).isoformat(),
        exchange=exchange,
        event_id="e1",
        event_name="A v B",
        market_id="m1",
        market_name="Match Odds",
        selection_id="s1",
        selection="A",
        side="BACK",
        odds=2.0,
        liquidity=100.0,
        source_latency_ms=50,
        commission_pct=2.0,
        commission_source="test",
        market_type="MATCH_ODDS",
        strategy="two-way",
        sport="Tennis",
        in_play=0,
        market_status="OPEN",
        raw_json="{}",
    )


def test_operational_status_exposes_latest_scan_funnel_and_connectivity(tmp_path):
    api = API(tmp_path / "arb.sqlite3")
    api.service.status = lambda: {"installed": True, "loaded": True, "worker_path": "/tmp/worker"}
    _snapshot(api, "Betfair delayed")
    _snapshot(api, "Matchbook")
    scan_id = api.db.start_scan()
    api.db.finish_scan(
        scan_id,
        markets_seen=1842,
        matches_seen=1126,
        opportunities_found=12,
        statuses=[
            {"exchange": "Betfair delayed", "ok": True, "markets": 1000, "latency_ms": 112, "message": "OK"},
            {"exchange": "Matchbook", "ok": True, "markets": 842, "latency_ms": 86, "message": "OK"},
        ],
        processed_candidates=684,
        positive_opportunities=12,
        qualified_count=4,
        executed_count=3,
        duration_ms=3210,
    )

    ops = api._operational_status()
    assert ops["pipeline"]["fetched"] == 1842
    assert ops["pipeline"]["matched"] == 1126
    assert ops["pipeline"]["processed"] == 684
    assert ops["pipeline"]["opportunities"] == 12
    assert ops["pipeline"]["qualified"] == 4
    assert ops["pipeline"]["executed"] == 3
    assert ops["pipeline"]["execution_conversion_pct"] == 75.0
    assert ops["scanner"]["duration_ms"] == 3210
    assert ops["scanner"]["next_poll_at"]
    feeds = {x["key"]: x for x in ops["feeds"]}
    assert feeds["betfair"]["state"] == "connected"
    assert feeds["betfair"]["latency_ms"] == 112
    assert feeds["matchbook"]["state"] == "connected"


def test_frontend_has_feedback_connectivity_and_conversion_funnel():
    html = Path(__file__).parents[1].joinpath("frontend", "index.html").read_text()
    assert "PoC 0.9.36" in html
    assert 'id="dashBetfairState"' in html
    assert 'id="dashMatchbookState"' in html
    assert 'id="dashNextPoll"' in html
    assert 'id="dashProcessed"' in html
    assert 'id="dashQualified"' in html
    assert 'id="dashExecuted"' in html
    assert 'id="dashExecRate"' in html
    assert 'id="dashConversionFailures"' in html
    assert "Scanning…" in html
    assert "Refreshing…" in html
    assert "Qualified → executed" in html
