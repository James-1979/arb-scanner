from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "frontend" / "index.html").read_text()


def test_release_and_replay_navigation_are_present():
    assert "PoC 0.9.36" in HTML
    nav = HTML.split('<div class="nav" id="nav"', 1)[1].split('<section id="dashboard"', 1)[0]
    assert 'data-nav-child="analytics" data-analytics-tab="replay"' in nav
    assert '>Replay<' in nav
    assert 'data-analytics-pane="replay"' in HTML
    assert '<h2 style="margin:0">Period Review</h2>' in HTML
    assert "if(id==='replay'){openAnalytics('replay');return}" in HTML


def test_execution_analysis_has_action_filter_rates_and_clickable_profile():
    assert 'id="executionsAction"' in HTML
    for value in ("clean", "price_moved", "LEG_FILLED", "LEG_FAILED", "LEG_PARTIAL", "EMERGENCY_HEDGE", "HEDGED", "PANIC", "settled"):
        assert f'value="{value}"' in HTML
    for element_id in ("executionFunnel", "executionActionBars", "execRateExecuted", "execRateClean", "execRateSettled", "execRateHedge"):
        assert f'id="{element_id}"' in HTML
    assert "function executionHasAction(" in HTML
    assert "function renderExecutionAnalysisGraphics(" in HTML
    assert "function setExecutionActionFilter(" in HTML


def test_individual_execution_review_is_retained_under_execution_analysis():
    assert 'id="executionDetailCard"' in HTML
    assert 'id="executionDetail"' in HTML
    assert "function renderExecutionDetailTimeline(" in HTML
    assert "Opportunity detected" in HTML
    assert "Settlement recorded" in HTML
    assert "EMERGENCY HEDGE" in HTML
    assert '>Detail</button>' in HTML


def test_scenarios_remain_separate_from_factual_timeline_replay():
    assert 'data-analytics-pane="scenarios"' in HTML
    assert '<h2 style="margin:0">Historical Scenario</h2>' in HTML
    assert 'id="replayBetfairBalance"' in HTML
    assert 'Review what ArbScanner did across a selected period' in HTML
