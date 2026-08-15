from pathlib import Path

from arbscanner import __version__
from arbscanner.api import OPERATING_MODES

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "frontend" / "index.html").read_text()
DB = (ROOT / "arbscanner" / "db.py").read_text()
API = (ROOT / "arbscanner" / "api.py").read_text()


def test_v0816_version_and_safety_locks():
    assert __version__ == "0.9.36"
    assert OPERATING_MODES["live"]["available"] is False
    assert "PoC 0.9.36" in HTML
    assert "MONITOR only." in HTML
    assert "LIVE order placement remains hard-locked" in HTML


def test_replay_detail_no_longer_overlays_controls_and_pnl_chart_is_retired():
    assert '.timeline-event-detail.replay-detail-open{display:block;position:relative' in HTML
    assert 'max-height:180px' in HTML
    assert 'id="timelineReplayPnlChart"' not in HTML
    assert '.analytics-pane[data-analytics-pane="replay"].active' in HTML
    assert 'timeline-return-marker' in HTML
    assert '.timeline-return-marker:hover .return-value,.timeline-return-marker.selected .return-value{opacity:1}' in HTML


def test_execution_profitability_and_scroll_fix_are_present():
    execution = HTML.split('<div class="analytics-pane" data-analytics-pane="execution">', 1)[1].split('<div class="analytics-pane" data-analytics-pane="market">', 1)[0]
    assert "Profitability by execution path" in execution
    assert 'id="executionPathRows"' in execution
    assert 'id="executionHedgeProfitability"' in execution
    assert "function executionPathFor(row)" in HTML
    assert "function renderExecutionProfitability(rows)" in HTML
    assert 'overflow:auto;overscroll-behavior:contain' in HTML
    assert '.execution-action-dock{flex:0 0 auto;position:sticky;bottom:0' in HTML


def test_market_analysis_is_sortable_heatmap_and_discovery_workspace():
    market = HTML.split('<div class="analytics-pane" data-analytics-pane="market">', 1)[1].split('<div class="analytics-pane" data-analytics-pane="replay">', 1)[0]
    assert 'id="marketLeaderboardTable"' in market
    for key in ("sport", "market", "type", "activity", "opportunities", "qualified", "executed", "conversion", "capital", "returned", "pnl", "avgpnl", "roi"):
        assert f'data-market-sort="{key}"' in market
    assert 'id="marketHourMetric"' in market
    for metric in ("observations", "qualified", "executed", "pnl", "roi_pct", "deployed"):
        assert f'value="{metric}"' in market
    assert "Weekly market heatmap" in market
    assert 'id="marketHeatmapWeekLabel"' in market
    assert "Conversion / drop-off" not in market
    assert 'id="marketConversionFunnel"' not in market
    assert 'id="marketSportsPreDiscovery"' in market
    assert 'id="marketSportsInplayDiscovery"' in market
    assert 'id="marketRacingDiscovery"' in market


def test_market_backend_exposes_returns_and_hourly_execution_value():
    assert "'returned': round(float(st.get('returned') or 0.0), 4)" in DB
    assert "'deployed': round(float(st.get('settled_deployed') or 0.0), 4)" in DB
    assert "'execution_started_deployed': round(float(ex.get('deployed') or 0.0), 4)" in DB
    assert "'execution_hours': exec_hours" in DB
    assert 'for row in payload.get("execution_hours") or []' in API
    assert 'bucket["executed"] +=' in API
    assert 'bucket["pnl"] +=' in API


def test_filters_headers_and_tables_follow_global_ui_rules():
    assert '.analytics-embedded-head{display:none!important}' in HTML
    assert "function installGlobalFilterAccordions()" in HTML
    assert "function installSortableTableDelegation()" in HTML
    for identifier in ("performanceScope", "positionResultsPeriod", "executionsPeriod", "marketAnalysisPeriod", "timelineReplayPeriod", "monitorPhase", "racingMonStatus"):
        assert identifier in HTML


def test_scenarios_no_longer_owns_returns_by_sport():
    scenarios = HTML.split('<div class="analytics-pane" data-analytics-pane="scenarios">', 1)[1].split('</section>', 1)[0]
    assert "Returns by sport" not in scenarios
    assert "Saved capital comparison" in scenarios


def test_dashboard_utilisation_rag_and_clock_distribution():
    assert 'id="dashCapitalUtilisation"' in HTML
    assert "function utilisationClass(p)" in HTML
    assert "p<=33?'rag-green':p<=66?'rag-amber':'rag-red'" in HTML
    assert 'aspect-ratio:1/1!important' in HTML
    for clock_id in ('dashClockLocalBox','dashClockUtcBox','dashClockNewYorkBox','dashClockSydneyBox'):
        assert f'id="{clock_id}"' in HTML
    assert 'aspect-ratio:1/1!important' in HTML
    assert 'data-display-profile="macbook"' not in HTML.split("</style>", 1)[0]
