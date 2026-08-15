from pathlib import Path

from arbscanner import __version__
from arbscanner.api import OPERATING_MODES

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "frontend" / "index.html").read_text()


def test_v0815_version_and_execution_locks():
    assert __version__ == "0.9.36"
    assert OPERATING_MODES["live"]["available"] is False
    assert "PoC 0.9.36" in HTML
    assert "MONITOR only." in HTML
    assert "LIVE order placement remains hard-locked" in HTML


def test_results_is_first_class_settled_position_ledger():
    assert 'data-results-domain="sports"' in HTML
    assert 'data-results-domain="racing"' in HTML
    analytics_nav = HTML.split('aria-label="Analytics navigation"', 1)[1].split('</div>', 1)[0]
    assert '>Results<' not in analytics_nav
    assert 'data-analytics-pane="results"' in HTML
    for token in (
        'id="positionResultsPeriod"',
        '<option value="today" selected>Current day</option>',
        'id="positionResultsFrom"',
        'id="positionResultsTo"',
        'id="positionResultsOutcome"',
        'id="positionResultsHedge"',
        'id="positionResultsRows"',
        "function loadPositionResults()",
        "function openReplayAtExecution(id)",
        "openExecutionDetailForOpportunity",
    ):
        assert token in HTML


def test_dashboard_layout_is_automatic_and_clocks_remain_activity_aware():
    dashboard = HTML.split('<section id="dashboard"', 1)[1].split('</section>', 1)[0]
    assert 'data-display-profile-button="auto"' not in dashboard
    assert 'data-display-profile-button="macbook"' not in dashboard
    assert 'data-display-profile-button="screen16x9"' not in dashboard
    assert 'Dashboard layout is fully automatic' in HTML
    assert 'aspect-ratio:1/1!important' in HTML
    assert 'clock-activity-badge' in dashboard
    assert "function updateClockActivityFromRacing(rows)" in HTML
    assert "if(!top||top[1]<=0)return" in HTML


def test_execution_list_is_primary_and_actions_are_multi_select():
    execution = HTML.split('<div class="analytics-pane" data-analytics-pane="execution">', 1)[1].split('<div class="analytics-pane" data-analytics-pane="market">', 1)[0]
    assert 'id="executionActionToggles"' in execution
    assert 'class="card execution-list-card"' in execution
    assert "executionActionFilters.some" in HTML
    assert "function toggleExecutionActionFilter(key)" in HTML
    assert "function clearExecutionActionFilters()" in HTML
    assert '.execution-action-dock' in HTML
    assert '.execution-tablewrap{max-height:calc(100dvh - 425px)}' in HTML


def test_market_analysis_is_sortable_leaderboard_across_markets():
    market = HTML.split('<div class="analytics-pane" data-analytics-pane="market">', 1)[1].split('<div class="analytics-pane" data-analytics-pane="replay">', 1)[0]
    assert "Market leaderboard" in market
    assert 'id="marketLeaderboardTable"' in market
    for value in ("activity", "opportunities", "qualified", "executed", "conversion", "roi", "pnl"):
        assert f'data-market-sort="{value}"' in market or f'value="{value}"' in market
    assert "function renderMarketLeaderboard()" in HTML
    assert "Greyhounds" in HTML


def test_replay_keeps_custom_period_and_uses_timeline_pnl_labels():
    replay = HTML.split('<div class="analytics-pane" data-analytics-pane="replay">', 1)[1].split('<div class="analytics-pane" data-analytics-pane="scenarios">', 1)[0]
    assert '<option value="today" selected>Today</option>' in replay
    assert '<option value="7d">7 days</option>' in replay
    assert '<option value="24h">24 hours</option>' in replay
    assert '<option value="custom">Custom period</option>' in replay
    assert 'id="timelineReplayFrom"' in replay and 'id="timelineReplayTo"' in replay
    assert 'id="timelineReplayPnlChart"' not in replay
    assert '.timeline-event-detail.replay-detail-open' in HTML
    assert '.timeline-return-marker .return-value,.timeline-position-marker .position-value{display:none}' in HTML


def test_scenarios_settings_are_collapsed_model_controls():
    scenarios = HTML.split('<div class="analytics-pane" data-analytics-pane="scenarios">', 1)[1].split('</section>', 1)[0]
    assert '<details class="card scenario-settings-accordion">' in scenarios
    assert "MODELLED" in scenarios
    assert "Transactions selected by this scenario" in scenarios
    assert "Model alternative outcomes using recorded history" in HTML


def test_sports_overview_config_and_admin_boundaries():
    sports = HTML.split('<section id="sports"', 1)[1].split('</section>', 1)[0]
    config = HTML.split('<section id="sports-config"', 1)[1].split('</section>', 1)[0]
    admin = HTML.split('<section id="settings"', 1)[1].split('</section>', 1)[0]
    assert "Market coverage and scan detail" not in sports
    assert "Monitored market families" not in sports
    assert "Set the Sports betting/simulation strategy" in config
    assert "Strategy scope:" in config
    assert "<h1>Admin</h1>" in admin
    assert "Admin controls" in admin
    assert "MONITOR PORTFOLIO" not in admin
    assert ">Admin</span>" in HTML
