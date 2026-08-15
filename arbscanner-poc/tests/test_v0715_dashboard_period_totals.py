from pathlib import Path

from arbscanner.api import API


def test_period_pipeline_aggregates_scan_volume_and_conversion(tmp_path):
    api = API(tmp_path / "arb.sqlite3")
    for payload in (
        dict(markets_seen=100, matches_seen=50, opportunities_found=2, processed_candidates=40,
             positive_opportunities=4, qualified_count=2, executed_count=1, duration_ms=1000),
        dict(markets_seen=120, matches_seen=60, opportunities_found=3, processed_candidates=60,
             positive_opportunities=6, qualified_count=3, executed_count=2, duration_ms=3000),
    ):
        scan_id = api.db.start_scan()
        api.db.finish_scan(scan_id, statuses=[], **payload)

    r = api.pipeline_analytics({})
    assert r["ok"] is True
    p = r["pipeline"]
    assert p["scans"] == 2
    assert p["fetched"] == 220
    assert p["matched"] == 110
    assert p["processed"] == 100
    assert p["opportunities"] == 10
    assert p["qualified_observations"] == 5
    assert p["executed_observations"] == 3
    # Canonical qualified/executed counts are position-backed, not raw scan telemetry.
    assert p["qualified"] == 0
    assert p["executed"] == 0
    assert p["opportunity_rate_pct"] == 10.0
    assert p["qualification_rate_pct"] == 0.0
    assert p["execution_conversion_pct"] == 0.0
    assert p["avg_duration_ms"] == 2000


def test_dashboard_has_period_scanner_totals():
    html = Path(__file__).parents[1].joinpath("frontend", "index.html").read_text()
    assert "PoC 0.9.36" in html
    assert 'id="dashboardPipelinePeriod"' in html
    assert 'id="dashPeriodProcessed"' in html
    assert 'id="dashPeriodQualified"' in html
    assert 'id="dashPeriodExecuted"' in html
    assert 'id="dashPeriodExecRate"' in html
    assert 'id="dashPeriodScans"' in html
    assert "Scanner totals" in html
    assert "loadDashboardPipeline" in html
