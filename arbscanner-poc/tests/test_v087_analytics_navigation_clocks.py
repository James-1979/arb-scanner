from pathlib import Path

ROOT = Path(__file__).parents[1]
HTML = ROOT.joinpath("frontend", "index.html").read_text()


def _nav():
    return HTML.split('<div class="nav" id="nav"', 1)[1].split('<section id="dashboard"', 1)[0]


def test_primary_nav_removes_replay_and_sports_includes_engine_workspace():
    nav = _nav()
    assert 'data-tab="analytics"' in nav
    assert 'data-tab="replay"' not in nav
    assert 'data-tab="sports-engines" data-nav-child="sports"' in nav
    assert 'data-tab="monitor" data-nav-child="sports"' in nav
    assert 'data-tab="matched" data-nav-child="sports"' not in nav
    assert 'data-tab="executions" data-nav-child="sports"' not in nav
    assert '>Overview<' in nav
    assert '>Monitor<' in nav
    assert '>Opportunities<' not in nav
    assert '>Execution History<' not in nav


def test_execution_analysis_is_shared_but_opened_from_sports_or_racing_domains():
    assert 'data-nav-child="analytics" data-analytics-tab="execution"' not in HTML
    assert 'data-tab="sports-execution" data-nav-child="sports"' not in HTML
    assert 'data-tab="racing-execution" data-nav-child="racing"' not in HTML
    assert 'data-tab="sports-engines" data-nav-child="sports"' in HTML
    assert 'data-tab="racing-engines" data-nav-child="racing"' in HTML
    assert 'data-analytics-pane="execution"' in HTML
    assert 'id="executionsRows"' in HTML
    assert 'id="monitorTimingSurvivalCard"' in HTML
    assert 'emergency-hedge recovery' in HTML
    assert "function openExecutionAnalysis(domain='sports')" in HTML
    assert "domain:executionAnalysisDomain" in HTML


def test_replay_scenario_engine_is_embedded_in_analytics_scenarios():
    assert 'data-analytics-tab="scenarios"' in HTML
    assert 'data-analytics-pane="scenarios"' in HTML
    assert '<h2 style="margin:0">Historical Scenario</h2>' in HTML
    assert 'id="replayPeriod"' in HTML
    assert 'id="replayBetfairBalance"' in HTML
    assert 'id="capitalChart"' in HTML
    assert '<section id="replay" class="page">' not in HTML
    assert "if(id==='replay'){openAnalytics('replay');return}" in HTML


def test_dashboard_uses_four_analog_world_clocks():
    assert 'class="analog-clocks"' in HTML
    for prefix in ("dashClockLocal", "dashClockUtc", "dashClockNewYork", "dashClockSydney"):
        assert f'id="{prefix}Hour"' in HTML
        assert f'id="{prefix}Minute"' in HTML
        assert f'id="{prefix}Second"' in HTML
    assert 'function setAnalogClock(' in HTML
    assert "setAnalogClock('dashClockUtc','UTC',now)" in HTML
    assert "setAnalogClock('dashClockNewYork','America/New_York',now)" in HTML
    assert "setAnalogClock('dashClockSydney','Australia/Sydney',now)" in HTML


def test_settings_recovery_guards_remain_present():
    assert 'id="settingsAdvancedContent"' in HTML
    assert 'prepareInformationArchitecture();' in HTML
    assert 'id="preMinRoi"' in HTML
    assert 'id="ipMinRoi"' in HTML
    assert 'id="bfEnabled"' in HTML
    assert 'id="mbEnabled"' in HTML
